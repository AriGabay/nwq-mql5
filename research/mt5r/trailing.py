"""Reference model of the 1R trailing stop (plans docs/plans/2026-10-05-0007-feat-trailing-stop-1r-plan.md and
docs/plans/2026-10-05-0128-fix-trailing-retry-persistence-plan.md).

The user's rule as executable spec, mirrored by ob_m1_structure.mq5 (ManageTrails) and replayed independently by the
checker (conformance_m1, rule trail_r23):

- E = actual fill, SL0 = stop accepted on the position, R0 = |E - SL0|; R0 never changes.
- Long: best = highest Bid since the fill, active from Bid >= E + R0, requested SL = best - R0 rounded down.
- Short: best = lowest Ask since the fill, active from Ask <= E - R0, requested SL = best + R0 rounded up; no extra
  spread term. Continuous, only in favour, never deactivated; the TP is never moved.
- Every tick updates the best price, also while a request is held (plan 0128 R2). A request is sent only when it
  improves the stop on the position by at least one tick and passes the stops and freeze levels.
- Retry policy (plan 0128 KTD1-KTD3), in this order of gates: market-closed bar (no request until the next M1 bar),
  1 second after the position's last rejected request, the EA-wide backoff after TOO_MANY_REQUESTS (1, 2, 4, 8, 16,
  then 30 s; only a verified success clears the streak), and at most one retry per tick across the EA. A retry is a
  request for a position whose last request was rejected, or any request while the streak is above zero. A value held
  back by the stops or freeze level is logged once per value and M1 bar and starts no wait.
- Accepted only with retcode DONE and the stop read back from the position equal to the request, the TP unchanged.
- Persistence (plan 0128 KTD4): the stored state (terminal global variables) is written whenever the best price or
  the activation changes; it is flushed to disk at registration, at activation, once 10 s of tick time passed since
  the last flush while there are unflushed changes, and at deinitialization.
"""
import copy
import math
from dataclasses import dataclass, field

RETCODE_DONE = 10009                # TRADE_RETCODE_DONE
RETCODE_MARKET_CLOSED = 10018       # TRADE_RETCODE_MARKET_CLOSED: no request until the next M1 bar
RETCODE_TOO_MANY_REQUESTS = 10024   # TRADE_RETCODE_TOO_MANY_REQUESTS: EA-wide backoff
RETRY_WAIT_MS = 1000                # after a rejected request, per position
BACKOFF_S = (1, 2, 4, 8, 16, 30)    # after the n-th consecutive TOO_MANY_REQUESTS, capped at the last step
FLUSH_MS = 10_000                   # periodic flush of unflushed stored state
EPS = 1e-9


def round_tick(p: float, tick: float, up: bool, digits: int = 2) -> float:
    """The EA's RoundTick: ceil/floor on the tick grid with a 1e-9 tolerance on the quotient."""
    q = p / tick
    r = math.ceil(q - 1e-9) if up else math.floor(q + 1e-9)
    return round(r * tick, digits)


def requested_sl(direction: int, best: float, r0: float, tick: float) -> float:
    """Long: best Bid - R0 rounded down; short: best Ask + R0 rounded up (never beyond the exact level)."""
    return round_tick(best - r0, tick, up=False) if direction == 1 else round_tick(best + r0, tick, up=True)


def activated(direction: int, fill: float, r0: float, bid: float, ask: float, tick: float) -> bool:
    half = tick / 2
    return bid >= fill + r0 - half if direction == 1 else ask <= fill - r0 + half


def backoff_ms(streak: int) -> int:
    """The EA-wide hold after the streak-th consecutive TOO_MANY_REQUESTS answer."""
    return BACKOFF_S[min(streak, len(BACKOFF_S)) - 1] * 1000


@dataclass
class TrailState:
    direction: int              # 1 long, -1 short
    fill: float                 # E
    sl0: float                  # stop accepted on the position at the fill
    tp: float                   # never changes
    sl: float                   # stop currently on the position (read back)
    ticket: int = 0
    best: float = None
    active: bool = False
    activated_msc: int = None
    rejected_msc: int = None    # tick time of the last rejected request; None after a verified success
    closed_bar: int = None      # M1 bar in which the broker answered market closed
    not_sent_value: float = None    # last value held back by the stops or freeze level ...
    not_sent_bar: int = None        # ... and its M1 bar (logged once per value and bar)
    r0: float = field(init=False)

    def __post_init__(self):
        self.r0 = abs(self.fill - self.sl0)
        if self.best is None:
            self.best = self.fill


@dataclass
class Store:
    """Terminal global variables (mem) and what was flushed to disk (disk), keyed by position ticket."""
    mem: dict = field(default_factory=dict)
    disk: dict = field(default_factory=dict)
    dirty: bool = False
    last_flush_msc: int = None


@dataclass
class EaContext:
    """State shared by all positions of the EA: the backoff, the per-tick retry slot and the stored state."""
    streak: int = 0
    backoff_until_msc: int = None
    slot_msc: int = None        # tick on which a retry was already sent
    store: Store = field(default_factory=Store)


@dataclass
class Decision:
    kind: str                   # "none" | "send" | "not_sent"
    requested: float = None
    tp: float = None
    reason: str = ""
    activated_now: bool = False
    retry: bool = False


def store(st: TrailState, ctx: EaContext) -> None:
    ctx.store.mem[st.ticket] = {"dir": st.direction, "e": st.fill, "sl0": st.sl0, "tp": st.tp, "best": st.best,
                                "act": st.active, "actms": st.activated_msc}
    ctx.store.dirty = True


def flush(ctx: EaContext, msc: int) -> None:
    ctx.store.disk = copy.deepcopy(ctx.store.mem)
    ctx.store.dirty = False
    ctx.store.last_flush_msc = msc


def flush_due(ctx: EaContext, msc: int) -> bool:
    last = ctx.store.last_flush_msc
    return ctx.store.dirty and (last is None or msc - last >= FLUSH_MS)


def register(st: TrailState, ctx: EaContext, msc: int) -> None:
    """At the fill: the state is stored and flushed at once (SL0 cannot be recovered from the position later)."""
    store(st, ctx)
    flush(ctx, msc)


def deinit(ctx: EaContext, states, msc: int) -> None:
    for st in states:
        store(st, ctx)
    flush(ctx, msc)


def restore(saved: dict, ticket: int) -> TrailState:
    """Rebuild a position's state from stored values only; SL0 is never taken from the position's current stop."""
    v = saved[ticket]
    return TrailState(direction=v["dir"], fill=v["e"], sl0=v["sl0"], tp=v["tp"], sl=v["sl0"], ticket=ticket,
                      best=v["best"], active=v["act"], activated_msc=v["actms"])


def decide(st: TrailState, ctx: EaContext, bid: float, ask: float, msc: int, bar_time: int, stops_points: int,
           freeze_points: int, point: float, tick: float) -> Decision:
    """One tick for one position: update and store the best price, activation, and whether to send a request."""
    best = max(st.best, bid) if st.direction == 1 else min(st.best, ask)
    if best != st.best:
        st.best = best
        store(st, ctx)
    now = False
    if not st.active:
        if not activated(st.direction, st.fill, st.r0, bid, ask, tick):
            return Decision("none")
        st.active, st.activated_msc, now = True, msc, True
        store(st, ctx)
        flush(ctx, msc)
    req = requested_sl(st.direction, st.best, st.r0, tick)
    improves = req >= st.sl + tick - EPS if st.direction == 1 else req <= st.sl - tick + EPS
    if not improves:
        return Decision("none", activated_now=now)
    if st.closed_bar is not None and st.closed_bar == bar_time:
        return Decision("none", reason="market_closed", activated_now=now)
    if st.rejected_msc is not None and msc - st.rejected_msc < RETRY_WAIT_MS:
        return Decision("none", reason="retry_wait", activated_now=now)
    if ctx.backoff_until_msc is not None and msc < ctx.backoff_until_msc:
        return Decision("none", reason="backoff", activated_now=now)
    retry = st.rejected_msc is not None or ctx.streak > 0
    if retry and ctx.slot_msc == msc:
        return Decision("none", reason="retry_slot", activated_now=now)
    if st.not_sent_value is not None and abs(req - st.not_sent_value) < tick / 2 and st.not_sent_bar == bar_time:
        return Decision("none", activated_now=now)
    price = bid if st.direction == 1 else ask
    if (price - req) * st.direction < stops_points * point - EPS:
        st.not_sent_value, st.not_sent_bar = req, bar_time
        return Decision("not_sent", req, st.tp, "stops_level", now)
    if freeze_points > 0:
        freeze = freeze_points * point
        if (price - st.sl) * st.direction <= freeze + EPS or (st.tp - price) * st.direction <= freeze + EPS:
            st.not_sent_value, st.not_sent_bar = req, bar_time
            return Decision("not_sent", req, st.tp, "freeze_level", now)
    if retry:
        ctx.slot_msc = msc
    return Decision("send", req, st.tp, "", now, retry)


def on_result(st: TrailState, ctx: EaContext, requested: float, retcode: int, sl_read: float, tp_read: float,
              tick: float, bar_time: int, msc: int) -> str:
    """Accepted only when the broker reports DONE and the position really carries the requested stop and the
    unchanged TP; the state always takes the stop read back from the position. Only TOO_MANY_REQUESTS advances the
    EA-wide streak, and only a verified success clears it."""
    st.sl = sl_read
    ok = (retcode == RETCODE_DONE and abs(sl_read - requested) <= tick / 2 + EPS
          and abs(tp_read - st.tp) <= tick / 2 + EPS)
    if ok:
        st.rejected_msc = None
        ctx.streak, ctx.backoff_until_msc = 0, None
        return "accepted"
    st.rejected_msc = msc
    if retcode == RETCODE_MARKET_CLOSED:
        st.closed_bar = bar_time
    if retcode == RETCODE_TOO_MANY_REQUESTS:
        ctx.streak += 1
        ctx.backoff_until_msc = msc + backoff_ms(ctx.streak)
    return "rejected"


class FixedStopOnly(ValueError):
    """A research tool that assumes one stop for the whole trade was given a trailed run."""


def refuse_trailed(tool: str, setups=None, trail=None) -> None:
    """Tools that re-price or classify exits against a fixed SL refuse a trailed run (plan KTD9)."""
    if is_trailed_run(setups, trail):
        raise FixedStopOnly(f"{tool} assumes a fixed stop (SL0 for the whole trade); this run has a trailing stop "
                            "(plan 2026-10-05-0007, KTD9) - refusing")


def is_trailed_run(setups=None, trail=None) -> bool:
    """A run is trailed when any exit is 'trail' or its rl_trail table has rows (plan KTD9)."""
    if trail is not None and len(trail):
        return True
    return setups is not None and "exit_kind" in setups and (setups["exit_kind"].astype(str) == "trail").any()
