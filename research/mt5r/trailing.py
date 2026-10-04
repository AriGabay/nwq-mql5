"""Reference model of the 1R trailing stop (plan docs/plans/2026-10-05-0007-feat-trailing-stop-1r-plan.md).

The user's rule as executable spec, mirrored by ob_m1_structure.mq5 (ManageTrails) and replayed by the independent
checker (conformance_m1, rule trail_r23):

- E = actual fill, SL0 = stop accepted on the position, R0 = |E - SL0|; R0 never changes (R1).
- Long: best = highest Bid since the fill, active from Bid >= E + R0, requested SL = best - R0 rounded down (R2).
- Short: best = lowest Ask since the fill, active from Ask <= E - R0, requested SL = best + R0 rounded up; no extra
  spread term (R3).
- Continuous, only in favour, never deactivated; the TP is never moved (R4, R5).
- A request is sent only when it improves the stop on the position by at least one tick and passes the stops and
  freeze levels; a value that failed is not re-sent before it changes or a new M1 bar opens, and after a
  market-closed answer nothing is sent before the next M1 bar (R11, R12, KTD3).
- Accepted only with retcode DONE and the stop read back from the position equal to the request, the TP unchanged
  (R10).
"""
import math
from dataclasses import dataclass, field

RETCODE_DONE = 10009        # TRADE_RETCODE_DONE
RETCODE_MARKET_CLOSED = 10018   # TRADE_RETCODE_MARKET_CLOSED: no request until the next M1 bar
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


@dataclass
class TrailState:
    direction: int              # 1 long, -1 short
    fill: float                 # E
    sl0: float                  # stop accepted on the position at the fill
    tp: float                   # never changes
    sl: float                   # stop currently on the position (read back)
    best: float = None
    active: bool = False
    activated_msc: int = None
    last_failed: float = None   # last value rejected or not sent
    last_failed_bar: int = None
    closed_bar: int = None      # M1 bar in which the broker answered market closed
    r0: float = field(init=False)

    def __post_init__(self):
        self.r0 = abs(self.fill - self.sl0)
        if self.best is None:
            self.best = self.fill


@dataclass
class Decision:
    kind: str                   # "none" | "send" | "not_sent"
    requested: float = None
    tp: float = None
    reason: str = ""
    activated_now: bool = False


def decide(st: TrailState, bid: float, ask: float, msc: int, bar_time: int, stops_points: int, freeze_points: int,
           point: float, tick: float) -> Decision:
    """One tick for one position: update the best price, activation, and whether to send a modification."""
    if st.direction == 1:
        st.best = max(st.best, bid)
    else:
        st.best = min(st.best, ask)
    now = False
    if not st.active:
        if not activated(st.direction, st.fill, st.r0, bid, ask, tick):
            return Decision("none")
        st.active, st.activated_msc, now = True, msc, True
    if st.closed_bar is not None and st.closed_bar == bar_time:
        return Decision("none", activated_now=now)          # trading is closed in this minute: not allowed
    req = requested_sl(st.direction, st.best, st.r0, tick)
    improves = req >= st.sl + tick - EPS if st.direction == 1 else req <= st.sl - tick + EPS
    if not improves:
        return Decision("none", activated_now=now)
    if st.last_failed is not None and abs(req - st.last_failed) < tick / 2 and st.last_failed_bar == bar_time:
        return Decision("none", activated_now=now)
    price = bid if st.direction == 1 else ask
    if (price - req) * st.direction < stops_points * point - EPS:
        st.last_failed, st.last_failed_bar = req, bar_time
        return Decision("not_sent", req, st.tp, "stops_level", now)
    if freeze_points > 0:
        freeze = freeze_points * point
        if (price - st.sl) * st.direction <= freeze + EPS or (st.tp - price) * st.direction <= freeze + EPS:
            st.last_failed, st.last_failed_bar = req, bar_time
            return Decision("not_sent", req, st.tp, "freeze_level", now)
    return Decision("send", req, st.tp, "", now)


def on_result(st: TrailState, requested: float, retcode: int, sl_read: float, tp_read: float, tick: float,
              bar_time: int) -> str:
    """Accepted only when the broker reports DONE and the position really carries the requested stop and the
    unchanged TP; the state always takes the stop read back from the position."""
    st.sl = sl_read
    ok = (retcode == RETCODE_DONE and abs(sl_read - requested) <= tick / 2 + EPS
          and abs(tp_read - st.tp) <= tick / 2 + EPS)
    if ok:
        st.last_failed = st.last_failed_bar = None
        return "accepted"
    st.last_failed, st.last_failed_bar = requested, bar_time
    if retcode == RETCODE_MARKET_CLOSED:
        st.closed_bar = bar_time
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
