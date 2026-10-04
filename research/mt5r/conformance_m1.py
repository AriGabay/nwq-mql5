"""Independent M5/M1 conformance checker for the M5 OB + M1 structure EA (plan 2026-10-03-0013, U4; R5-R19, R22,
R25, KTD2-KTD8, KTD11).

The checker re-derives every rule from the logged bars (``rl_bars_m1``/``rl_bars_m5``, warm-up bars included) and
compares the result with the EA's claims in ``rl_events``, ``rl_setups`` and ``rl_pivots`` (log contract:
``m1_contract.py``). It never reads the EA source. Times follow the contract: bar times are bar OPEN times in epoch
seconds, a bar opened at T closes at T + period, tick times are epoch milliseconds, and a tick at ms belongs to the
bar with T*1000 <= ms < (T + period)*1000. Every logged bar is a closed bar whose close the EA processed.

Replay (KTD2, KTD3). One global causal pivot sequence is rebuilt bar by bar from the first logged M1 bar. At the
close of bar i the candidate p = i - N is a pivot high when its high is strictly above the highs of the N bars on
each side (lows mirror); it is confirmed at bar p + N (conf_time = open time of bar p + N). Of two consecutive
same-type pivots only the more extreme stays (tie: the earlier); the replaced one gets ``replaced_by``. An outside bar
that is both appends first the type opposite to the current last element (the high first on an empty sequence).
For each setup the replay then runs, per M1 bar: the tick phase (queued entry outcome, touch R5, return R7) and the
close phase in the KTD2 order: pivots, break episode R6 / second break R8, R22, R13 lapse, R10, R12, R11, R14;
finally the R16 competition of the reaction candles of that close.

Semantics checked (plan text plus the EA unit's resolutions of its open points):
  * R5 touch: the first M1 bar opening at/after candle 3's close whose Bid range reaches the OB near edge (KTD11:
    minute level). A touch on a warm-up bar ends the setup as ``warmup_dropped`` (no events expected).
  * R6/R7/R8: the touch bar's close is the first post-touch close (KTD4); consecutive closes beyond the far edge are
    one episode; the return is the first later bar whose Bid range is back in the zone; after a return any close
    beyond the far edge cancels (KTD6: return and second break in one bar cancel).
  * R10: the reference is the latest pivot high (long) of the sequence at that close; each reference yields at most
    one structure change per setup; a newer one supersedes the live one (KTD5). Origin: lowest low (long) from the
    reference peak to the HH bar inclusive, ties -> the latest bar.
  * R12: at the close of bar k+1 the entry FVG is the trade-direction FVG with candle 1 >= origin and middle <= k,
    latest candle 3 first, skipping a candidate already closed beyond its far edge by a close in (c3, k+1]. R13
    lapse checks start at the first close after k+1.
  * R11: the HL is the first pivot low (long) entering the sequence with its peak after the HH bar; not above the
    origin low -> variant B ``hl_failed`` ends the structure change, variant A keeps it (the HL only feeds R18).
  * R14: green (long) close above the FVG upper boundary with low <= it, on a bar strictly after the HH bar, the FVG
    candle 3 and (variant B) the HL confirmation bar, and outside a break episode.
  * R16: per direction and reaction candle the latest touch wins (ties: later OB candle, then lower setup id); the
    others log ``lost_competition`` and keep waiting.
  * R15/KTD7: the winner's first decision (attempt or skip) lies on the first tick after the reaction close (bar
    k+1 of the reaction); at that tick Bid = the bar open, so a crossed stop (long Bid <= SL; short Ask >= SL, i.e.
    Bid >= anchor + buffer) must be ``skipped_stop_crossed``. Skips never end a setup. Market-closed retries continue
    while the setup stays READY and outside a break episode.
  * R18: long SL = min(OB low, structure low) - buffer, structure low = the HL, or in variant A without an HL the
    latest pivot low of the sequence at entry; anchor 'ob' on a tie. Short: max(...) + buffer + spread at entry; the
    spread is Ask - Bid of a logged skip when available, else the decision bar's ``spread`` column (a proxy).
  * R19: TP = fill +/- RiskRR x |fill - SL| (within half a point). R17: one fill per setup.
  * R22: every lower high H2 (peak after the touch bar) that enters the sequence forms its own candidate from the two
    elements before it at that moment (H1, L1); it completes on a close below L1 and voids on a close above H2, only
    by closes after H2's confirmation bar; candidates are independent and the first completion cancels.
  * tf_sync (KTD11): each M5 bar equals the aggregate of its M1 bars.
Violations are dicts {setup_id, rule, detail}; one rule name per requirement (``RULES``).
"""
from __future__ import annotations

import bisect
import math
import pathlib
from collections import Counter

import numpy as np
import pandas as pd

from . import m1_contract as mc
from . import trailing as trl

M1_S, M5_S = 60, 300
EPS = 1e-6
DEFAULT_PARAMS = dict(StructureVariant=0, ImpulseWindowBars=2, SwingStrengthM1=3, StopBufferPoints=20, RiskRR=2.0,
                      point=0.01, RiskPercent=1.0, MaxExposures=3, contract_size=100, lot_step=0.01, lot_min=0.01)
RULES = ["ob_r3", "touch_r5", "break_r6", "return_r7", "second_break_r8", "pivot_causality_r9", "sc_r10", "hl_r11",
         "fvg_r12", "fvg_lapse_r13", "reaction_r14", "entry_r15", "competition_r16", "one_trade_r17", "sl_r18",
         "tp_r19", "exit_sltp", "risk_r20", "cap_r21", "cancel_r22", "tf_sync", "fields", "trail_r23"]
# What the logs cannot prove (coverage report): each key counts cases the checker could not verify, or verified only
# in part, and why. A case listed here is NOT evidence of conformance.
UNVERIFIABLE = {
    "tick_within_minute": "touch/return: the minute and a Bid inside that bar at the zone edge are verified; which "
                          "tick of the minute (tick_msc) is the EA's claim - the bars carry minute OHLC only",
    "touch_after_last_logged_bar": "touch tick inside the final M1 bar, which never closes and is never logged",
    "short_entry_ask": "short SL spread: Ask - Bid at the entry tick is the EA's own log (bars carry Bid only); the "
                       "checker verifies SL = anchor + buffer + that logged spread and the Bid against the bar open",
    "short_sl_spread_proxy": "short fill without a logged Bid/Ask: the bar spread column is only a proxy",
    "short_exit_ask": "short exits trigger on Ask: only the necessary Bid conditions are checked (no earlier bar whose "
                      "Bid high reaches the SL; exit-bar Bid low at/below the TP)",
    "entry_price_ask": "long request/fill price is an Ask (not in the bars); the volume check uses the logged price",
    "skipped_stops_level": "broker stops/freeze level at that tick is not logged per tick",
    "skipped_margin": "free margin and the margin requirement at that tick are not logged",
    "skipped_broker_reject": "the server reply cannot be re-derived from bars",
    "market_closed_retry": "the market-closed reply and the retry ticks cannot be re-derived from bars",
    "risk_without_deals": "no rl_deals: the balance at entry, hence R20, cannot be rebuilt",
    "exit_same_bar_as_fill": "fill and exit in one M1 bar: tick order inside the bar is not in the logs",
    "exit_after_last_logged_bar": "exit tick inside the final M1 bar, which is never logged",
    "short_trail_ask": "short trail activation and best price are Ask-based (not in the bars): a bar whose Bid low "
                       "reaches E - R0 without a logged activation is only a necessary condition, so it is counted "
                       "here, not flagged (plan 2026-10-05-0007, KTD7)",
    "level_in_session_open_bar": "SL/TP level reached only inside the first M1 bar after a quote gap (daily break, "
                                 "weekend). Quotes start 01:00, the trade session 01:01, so nothing executes in that "
                                 "minute (research/session_probe.py: Market orders refused 169/169 at 01:00, filled "
                                 "169/169 at 01:01; replicas exit as the EA did). The later exit needs the tick log; "
                                 "results/pilot/gate_decisions.md holds the pre-registered sensitivity",
}
RULE_BY_KIND = {"touch": "touch_r5", "break": "break_r6", "return": "return_r7",
                "cancelled_second_break": "second_break_r8", "sc_hh": "sc_r10", "sc_superseded": "sc_r10",
                "fvg_fixed": "fvg_r12", "fvg_none": "fvg_r12", "fvg_lapsed": "fvg_lapse_r13", "hl": "hl_r11",
                "hl_failed": "hl_r11", "reaction": "reaction_r14", "lost_competition": "competition_r16",
                "cancelled_opposing_structure": "cancel_r22"}
BAR_KINDS = ["break", "cancelled_second_break", "sc_hh", "sc_superseded", "fvg_fixed", "fvg_none", "fvg_lapsed",
             "hl", "hl_failed", "reaction", "lost_competition", "cancelled_opposing_structure"]
TICK_KINDS = ["touch", "return"]
PIVOT_REF_KINDS = ["sc_hh", "sc_superseded", "hl", "hl_failed", "cancelled_opposing_structure"]
SKIPS = [k for k in mc.SKIP_EVENTS if k != "lost_competition"]
OUTCOME_KINDS = {"fill", *SKIPS}
DECISION_KINDS = {"entry_attempt", *OUTCOME_KINDS}

BAR_FLOAT = ["open", "high", "low", "close", "tick_volume", "spread"]
PIVOT_INT = ["pivot_id", "peak_time", "conf_time", "replaced_by", "outside_bar"]
PIVOT_FLOAT = ["level"]
PIVOT_STR = ["type"]
EVENT_INT = ["setup_id", "seq", "bar_time", "tick_msc", "ref_id", "ref_time"]
EVENT_FLOAT = ["price", "lo", "hi"]
EVENT_STR = ["kind", "detail"]
SETUP_STR = ["dir", "variant", "sl_anchor", "exit_kind", "reason"]
SETUP_INT = ["setup_id", "ob_time", "idfvg_c1_time", "idfvg_c3_time", "identified_in_warmup", "touch_msc",
             "touch_bar_time", "breaks", "returns", "sc_bar_time", "origin_time", "ref_pivot_id", "hl_pivot_id",
             "fvg_c1_time", "reaction_bar_time", "entry_request_msc", "attempts", "fill_msc", "position_id",
             "exit_msc", "reason_msc"]
SETUP_FLOAT = [c for c in mc.SETUP_COLUMNS if c not in SETUP_STR and c not in SETUP_INT]

OCC_KEYS = ["long", "short", "touched", "touch_bar_opens_in_zone", "warmup_dropped", "run_end_untouched",
            "run_end_waiting", "break", "touch_bar_break", "return", "return_and_break_same_bar",
            "cancelled_second_break", "cancelled_opposing_structure", "lh_candidates", "lh_voided", "sc_hh",
            "sc_on_touch_bar", "sc_superseded", "fvg_fixed", "fvg_c3_before_k1", "fvg_skipped_closed_beyond",
            "fvg_none", "fvg_lapsed", "hl", "hl_failed", "hl_failed_variant_a", "reaction", "reaction_at_k1",
            "reaction_on_return_bar", "competition", "lost_competition", "filled", *SKIPS, "market_closed_retry",
            "retry_dropped", "sl_anchor_ob", "sl_anchor_hl", "sl_anchor_pivot", "pivots", "pivots_replaced",
            "outside_bar_pivots", "candidates_m5"]


# --- readers ---------------------------------------------------------------------------------------------------
def _norm(df: pd.DataFrame, ints, floats, strs) -> pd.DataFrame:
    df = df.copy()
    for c in ints:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce").round().astype("Int64")
    for c in floats:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    for c in strs:
        if c in df:
            df[c] = [None if _missing(v) or str(v) == "" else str(v) for v in df[c]]
            df[c] = df[c].astype(object)
    return df


def _csv(path, strs):
    return pd.read_csv(path, dtype={c: object for c in strs}, keep_default_na=False, na_values=[""])


def read_bars(path) -> pd.DataFrame:
    """rl_bars_m1 / rl_bars_m5 (plain or .gz): time int64, prices float, spread float, warmup int (0 if absent)."""
    return _bars(_csv(path, []))


def _bars(df: pd.DataFrame) -> pd.DataFrame:
    df = _norm(df, [], BAR_FLOAT, [])
    df["time"] = pd.to_numeric(df["time"]).astype("int64")
    df["warmup"] = (pd.to_numeric(df["warmup"], errors="coerce").fillna(0).astype("int64") if "warmup" in df
                    else np.zeros(len(df), dtype="int64"))
    for c in ("tick_volume", "spread"):
        if c not in df:
            df[c] = np.nan
    return df.sort_values("time", kind="mergesort").reset_index(drop=True)


def read_pivots(path) -> pd.DataFrame:
    return _norm(_csv(path, PIVOT_STR), PIVOT_INT, PIVOT_FLOAT, PIVOT_STR)


def read_events(path) -> pd.DataFrame:
    return _norm(_csv(path, EVENT_STR), EVENT_INT, EVENT_FLOAT, EVENT_STR)


def read_setups(path) -> pd.DataFrame:
    return _norm(_csv(path, SETUP_STR), SETUP_INT, SETUP_FLOAT, SETUP_STR)


READERS = {"setups": read_setups, "events": read_events, "pivots": read_pivots, "bars_m1": read_bars,
           "bars_m5": read_bars}


def read_run(folder, tag) -> dict:
    """The five checker inputs of one run: rl_<name>_<tag>.csv or .csv.gz in ``folder``."""
    folder = pathlib.Path(folder)
    out = {}
    for name, reader in READERS.items():
        for suffix in (".csv", ".csv.gz"):
            p = folder / f"rl_{name}_{tag}{suffix}"
            if p.exists():
                out[name] = reader(p)
                break
        else:
            raise FileNotFoundError(folder / f"rl_{name}_{tag}.csv[.gz]")
    for suffix in (".csv", ".csv.gz"):                 # optional: R20 needs the balance history
        p = folder / f"rl_deals_{tag}{suffix}"
        if p.exists():
            out["deals"] = pd.read_csv(p)
            break
    for name in mc.TRAIL_FILES:                        # optional: written only by a trailed run (trail_r23)
        p = folder / f"rl_{name}_{tag}.csv"
        if p.exists():
            out[name] = pd.read_csv(p, keep_default_na=False, na_values=[""])
    return out


# --- trail_r23: the 1R trailing stop (plan 2026-10-05-0007, KTD7) ------------------------------------------------
def stop_path(moves: pd.DataFrame) -> dict:
    """position_id -> [(tick_msc, accepted stop)] of the accepted modifications, in order."""
    out = {}
    if moves is None or not len(moves):
        return out
    acc = moves[moves["outcome"].astype(str) == "accepted"]
    for r in acc.to_dict("records"):
        out.setdefault(int(r["position_id"]), []).append((int(r["tick_msc"]), float(r["accepted_sl"])))
    return out


def stop_at(path: list, sl0: float, ms: int) -> float:
    """The stop in force just before ``ms``: the last accepted move strictly earlier, else SL0."""
    cur = sl0
    for t, v in path or []:
        if t < ms:
            cur = v
        else:
            break
    return cur


# retry policy of plan 2026-10-05-0128 (KTD1-KTD3), restated here so the checker stays independent of the model
TOO_MANY_REQUESTS = 10024           # TRADE_RETCODE_TOO_MANY_REQUESTS
REJECT_WAIT_MS = 1000               # no request of a position within 1 s of its rejected request
BACKOFF_S = (1, 2, 4, 8, 16, 30)    # EA-wide hold after the n-th consecutive TOO_MANY_REQUESTS


def parse_trailing(v):
    """EnableTrailingStop from run values: True/False, "true"/"false", 1/0; None (not set) is the code default false.
    Anything else is returned unchanged and reported by trail_presence."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("true", "false", "1", "0"):
        return v.strip().lower() in ("true", "1")
    return v


def trail_presence(trailing, trail, moves) -> list:
    """A run with EnableTrailingStop must carry both trail files; a run without it must carry none (plan 0128 R13)."""
    def bad(detail):
        return [{"setup_id": None, "rule": "trail_r23", "detail": detail}]
    if not isinstance(trailing, bool):
        return bad(f"EnableTrailingStop value {trailing!r} cannot be read")
    if trailing and (trail is None or moves is None):
        missing = [n for n, f in (("rl_trail", trail), ("rl_sl_moves", moves)) if f is None]
        return bad(f"trailing run: {', '.join(missing)} missing")
    if not trailing and (trail is not None or moves is not None):
        return bad("trail files present in a trail-off run")
    return []


def check_retry_policy(moves: pd.DataFrame, sid_of: dict) -> list:
    """All positions' requests merged in time order (stable on file order): the 1 s wait after a position's
    rejection, the EA-wide backoff after TOO_MANY_REQUESTS (cleared by an accepted request) and at most one retry
    per tick. A retry is a request of a position whose last request was rejected, or any request while the
    TOO_MANY_REQUESTS streak is above zero. Requests held by the stops or freeze level (not_sent) come after the
    wait and backoff gates, so they must respect them too, but use no retry slot."""
    viol = []
    if moves is None or not len(moves):
        return viol
    rej, streak, until, slot = {}, 0, None, None
    for m in moves.reset_index(drop=True).sort_values("tick_msc", kind="mergesort").to_dict("records"):
        pid, ms, outcome = int(m["position_id"]), int(m["tick_msc"]), str(m["outcome"])

        def add(detail):
            viol.append({"setup_id": sid_of.get(pid), "rule": "trail_r23", "detail": f"position {pid}: {detail}"})
        if pid in rej and ms - rej[pid] < REJECT_WAIT_MS:
            add(f"request at {ms}, {ms - rej[pid]} ms after its rejection")
        if until is not None and ms < until:
            add(f"request at {ms} during the EA backoff (until {until})")
        if outcome.startswith("not_sent:"):
            continue
        if pid in rej or streak > 0:
            if slot == ms:
                add(f"second retry on tick {ms}")
            slot = ms
        if outcome == "accepted":
            rej.pop(pid, None)
            streak, until = 0, None
        elif outcome == "rejected":
            rej[pid] = ms
            if int(m["retcode"]) == TOO_MANY_REQUESTS:
                streak += 1
                until = ms + BACKOFF_S[min(streak, len(BACKOFF_S)) - 1] * 1000
    return viol


def check_trails(setups: pd.DataFrame, trail: pd.DataFrame, moves: pd.DataFrame, B: "_Bars", tick: float):
    """Every trailed position against the reference model (research/mt5r/trailing.py) and the M1 Bid bars.

    Returns (violations, checked, unverifiable): violations as {setup_id, rule, detail}, the number of positions
    checked, and a dict of key -> [setup ids] of cases the bars cannot prove."""
    tol = tick / 2 + 1e-9
    viol, unv, checked = [], {}, 0
    rows = {int(r["position_id"]): r for r in setups.to_dict("records") if not _missing(r.get("position_id"))}
    mv = moves if moves is not None else pd.DataFrame(columns=mc.SL_MOVE_COLUMNS)
    if trail is None:
        return viol, checked, unv
    lacking = [c for c in mc.TRAIL_COLUMNS if c not in trail.columns]
    if lacking:
        return [{"setup_id": None, "rule": "fields", "detail": f"rl_trail lacks contract columns {lacking}"}], 0, unv
    # completeness (plan 0128 R12/R13): exactly one row per filled position, none for anything else
    filled = {pid for pid, r in rows.items() if not _missing(r.get("fill_price"))}
    counts = trail["position_id"].astype(int).value_counts().to_dict()
    for pid in sorted(filled - set(counts)):
        viol.append({"setup_id": _int(rows[pid].get("setup_id")), "rule": "trail_r23",
                     "detail": f"position {pid} is filled but has no rl_trail row"})
    for pid, n in sorted(counts.items()):
        if n > 1:
            viol.append({"setup_id": None, "rule": "trail_r23", "detail": f"{n} rl_trail rows for position {pid}"})
        if pid not in filled:
            viol.append({"setup_id": None, "rule": "trail_r23",
                         "detail": f"rl_trail row for position {pid} has no filled rl_setups row"})
    sid_of = {int(t["position_id"]): _int(t.get("setup_id")) for t in trail.to_dict("records")}
    viol.extend(check_retry_policy(mv, sid_of))
    for t in trail.to_dict("records"):
        pid, sid = int(t["position_id"]), _int(t.get("setup_id"))
        bad = []
        add = bad.append
        checked += 1
        sign = _sign(t["dir"])
        E, sl0, r0, tp = float(t["fill_price"]), float(t["sl0"]), float(t["r0"]), float(t["tp"])
        r = rows.get(pid)
        if r is None:
            continue                                   # reported by the completeness check above
        final_state, roundtrip = _str(t.get("state_final")), _str(t.get("state_roundtrip"))
        own_n = int((mv["position_id"].astype(int) == pid).sum()) if len(mv) else 0
        if final_state == "not_trailed" or roundtrip.startswith("not_trailed:"):
            if not (final_state == "not_trailed" and roundtrip.startswith("not_trailed:")):
                add(f"state_final {final_state} with state_roundtrip {roundtrip}")
            if own_n:
                add(f"not trailed but has {own_n} stop request(s)")
            if _int(t.get("activated_msc")) is not None:
                add("not trailed but activated")
            if abs(float(t["final_sl"]) - sl0) > tol:
                add(f"not trailed but its stop {t['final_sl']} != SL0 {sl0}")
            for b in bad:
                viol.append({"setup_id": sid, "rule": "trail_r23", "detail": f"position {pid}: {b}"})
            continue
        if roundtrip != "ok":
            add(f"state round trip at the fill: {roundtrip}")
        if final_state != "ok":
            add(f"stored state at close: {final_state}")
        if abs(E - float(r["fill_price"])) > tol or abs(sl0 - float(r["sl"])) > tol or abs(tp - float(r["tp"])) > tol:
            add(f"E/SL0/TP {E}/{sl0}/{tp} differ from rl_setups {r['fill_price']}/{r['sl']}/{r['tp']}")
        if abs(r0 - abs(E - sl0)) > tol:
            add(f"R0 {r0} != |E - SL0| {abs(E - sl0):.5f}")
        cur_sl = sl0                                   # the replayed stop on the position
        own = mv[mv["position_id"].astype(int) == pid].sort_values("tick_msc", kind="mergesort")
        prev_best, accepted = None, 0
        closed_min, held = None, None                  # market-closed minute; (minute, value) held back (not_sent)
        act_ms = _int(t.get("activated_msc"))
        for m in own.to_dict("records"):
            ms, bid, ask, best = int(m["tick_msc"]), float(m["bid"]), float(m["ask"]), float(m["best"])
            req, before, after = float(m["requested_sl"]), float(m["sl_before"]), float(m["accepted_sl"])
            outcome = str(m["outcome"])
            minute = ms // 60000
            if closed_min == minute:
                add(f"move at {ms}: request in a minute already answered market closed")
            if held is not None and held[0] == minute and abs(held[1] - req) <= tol:
                add(f"move at {ms}: the held-back value {req} was requested again in the same minute")
            if outcome in ("accepted", "rejected") and (req - cur_sl) * sign < tick - tol:
                add(f"move at {ms}: request {req} does not improve the stop {cur_sl} by a tick")
            j = B.bar_at_ms(ms)
            if j is not None and not (B.l[j] - tol <= bid <= B.h[j] + tol):
                add(f"move at {ms}: Bid {bid} outside its M1 bar [{B.l[j]}, {B.h[j]}]")
            if prev_best is not None and (best - prev_best) * sign < -tol:
                add(f"move at {ms}: best price {best} went back from {prev_best}")
            if (sign > 0 and best < bid - tol) or (sign < 0 and best > ask + tol):
                add(f"move at {ms}: best {best} behind the current {'Bid' if sign > 0 else 'Ask'}")
            if not trl.activated(sign, E, r0, best if sign > 0 else bid, best if sign < 0 else ask, tick):
                add(f"move at {ms}: request before +1R (best {best}, E {E}, R0 {r0})")
            if act_ms is None or act_ms > ms:
                add(f"move at {ms} before the logged activation {act_ms}")
            want = trl.requested_sl(sign, best, r0, tick)
            if abs(req - want) > tol:
                add(f"move at {ms}: requested {req} != model {want} for best {best}")
            if abs(before - cur_sl) > tol:
                add(f"move at {ms}: stop before {before} != replayed stop {cur_sl}")
            if outcome == "accepted":
                if abs(after - req) > tol:
                    add(f"move at {ms}: accepted stop {after} != request {req}")
                accepted += 1
                cur_sl = after
            elif outcome == "rejected":
                if (after - cur_sl) * sign < -tol:
                    add(f"move at {ms}: stop read back {after} retreats from {cur_sl}")
                cur_sl = after
                if int(m["retcode"]) == trl.RETCODE_MARKET_CLOSED:
                    closed_min = minute
            elif outcome.startswith("not_sent:"):
                if abs(after - before) > tol:
                    add(f"move at {ms}: not_sent changed the stop {before} -> {after}")
                held = (minute, req)
            else:
                add(f"move at {ms}: unknown outcome {outcome!r}")
            prev_best = best
        if act_ms is not None:
            ab, aa = _flt(t.get("activation_bid")), _flt(t.get("activation_ask"))
            if ab is None or aa is None or not trl.activated(sign, E, r0, ab, aa, tick):
                add(f"activation at {act_ms} with Bid/Ask {ab}/{aa} is not at +1R")
            j = B.bar_at_ms(act_ms)
            if j is not None and ((sign > 0 and B.h[j] < E + r0 - tol) or (sign < 0 and B.l[j] > E - r0 + tol)):
                add(f"activation at {act_ms}: its M1 bar never reaches E {'+' if sign > 0 else '-'} R0 on Bid")
        if abs(float(t["final_sl"]) - cur_sl) > tol:
            add(f"final stop {t['final_sl']} != replayed stop {cur_sl}")
        if int(t["accepted"]) != accepted:
            add(f"accepted count {t['accepted']} != {accepted} accepted rows")
        kind = _str(r.get("exit_kind"))
        if kind == "trail" and accepted == 0:
            add("exit classified trail although the stop was never moved")
        if kind == "sl" and accepted > 0:
            add("exit classified sl although the stop had been moved")
        if str(t.get("exit_kind")) != str(kind):
            add(f"rl_trail exit {t.get('exit_kind')} != rl_setups exit {kind}")
        # completeness on the bars: a bar between the fill and the exit that reaches +1R must see an activation, and
        # a long's best price is at least every such bar's Bid high
        f, x = B.bar_at_ms(_int(r.get("fill_msc"))), B.bar_at_ms(_int(r.get("exit_msc")))
        if f is not None:
            last = x if x is not None else B.n
            span = [j for j in range(f + 1, last) if not B.gap_open[j]]
            if sign > 0:
                hits = [j for j in span if B.h[j] >= E + r0 - 1e-9]
                if hits and (act_ms is None or act_ms >= B.close_ms(hits[0])):
                    add(f"bar {B.t[hits[0]]} reaches +1R on Bid but no activation is logged by its close")
                top = max((B.h[j] for j in span), default=None)
                if top is not None and act_ms is not None and float(t["best_price"]) < top - tol:
                    add(f"final best {t['best_price']} below the highest Bid {top} between fill and exit")
            else:
                hits = [j for j in span if B.l[j] <= E - r0 + 1e-9]
                if hits and (act_ms is None or act_ms >= B.close_ms(hits[0])):
                    unv.setdefault("short_trail_ask", []).append(sid)
        for b in bad:
            viol.append({"setup_id": sid, "rule": "trail_r23", "detail": f"position {pid}: {b}"})
    return viol, checked, unv


# --- helpers ---------------------------------------------------------------------------------------------------
def _missing(v) -> bool:
    if v is None or v is pd.NA:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _int(v):
    return None if _missing(v) or v == "" else int(v)


def _flt(v):
    return None if _missing(v) or v == "" else float(v)


def _str(v):
    return None if _missing(v) or v == "" else str(v)


def _sign(d):
    d = (d or "").strip().upper()
    return 1 if d in ("L", "LONG", "BUY") else -1 if d in ("S", "SHORT", "SELL") else 0


def _variant(v):
    v = (_str(v) or "").strip().upper()
    return {"0": 0, "A": 0, "1": 1, "B": 1, "0.0": 0, "1.0": 1}.get(v)


def _ptype(v):
    v = (_str(v) or "").strip().upper()
    return "H" if v in ("H", "HIGH") else "L" if v in ("L", "LOW") else None


class _Bars:
    def __init__(self, df: pd.DataFrame, period: int):
        b = _bars(df)
        self.P = int(period)
        self.t = [int(x) for x in b["time"]]
        self.o, self.h, self.l, self.c = ([float(x) for x in b[k]] for k in ("open", "high", "low", "close"))
        self.spread = [float(x) for x in b["spread"]]
        self.warm = [int(x) for x in b["warmup"]]
        self.n = len(self.t)
        self._idx = {x: i for i, x in enumerate(self.t)}
        self.l_np, self.h_np = np.asarray(self.l), np.asarray(self.h)
        # first bar after a quote gap > 5 min (daily break, weekend): quotes arrive, nothing executes
        self.gap_open = [i > 0 and self.t[i] - self.t[i - 1] > 300 for i in range(self.n)]

    def i(self, time):
        return None if time is None else self._idx.get(int(time))

    def close_ms(self, i) -> int:
        return (self.t[i] + self.P) * 1000

    def bar_at_ms(self, ms):
        if ms is None or self.n == 0:
            return None
        j = bisect.bisect_right(self.t, ms // 1000) - 1
        return j if 0 <= j and ms < self.close_ms(j) else None

    def first_at_or_after(self, time_s) -> int:
        return bisect.bisect_left(self.t, int(time_s))

    def is_fvg(self, c1, sign) -> bool:
        c3 = c1 + 2
        if c1 < 0 or c3 >= self.n:
            return False
        return self.l[c3] > self.h[c1] + EPS if sign > 0 else self.h[c3] < self.l[c1] - EPS

    def fvg_zone(self, c1, sign):
        """(lower, upper) boundary of the FVG whose candle 1 is c1."""
        return (self.h[c1], self.l[c1 + 2]) if sign > 0 else (self.h[c1 + 2], self.l[c1])


class _Piv:
    __slots__ = ("uid", "typ", "peak", "conf", "level", "outside", "replaced_by", "entered", "prev1", "prev2")

    def __init__(self, uid, typ, peak, conf, level, outside):
        self.uid, self.typ, self.peak, self.conf, self.level, self.outside = uid, typ, peak, conf, level, outside
        self.replaced_by, self.entered, self.prev1, self.prev2 = None, False, None, None


class _Sequence:
    """The global alternating M1 pivot sequence of KTD3, updated once per M1 close."""

    def __init__(self, B: _Bars, N: int):
        self.B, self.N = B, int(N)
        self.seq: list[_Piv] = []
        self.raw: list[_Piv] = []
        self.last = {"H": None, "L": None}

    def update(self, i) -> list[_Piv]:
        B, N, p = self.B, self.N, i - self.N
        if p - N < 0 or N < 1:
            return []
        is_h = all(B.h[p] > B.h[p + s * j] + EPS for j in range(1, N + 1) for s in (-1, 1))
        is_l = all(B.l[p] < B.l[p + s * j] - EPS for j in range(1, N + 1) for s in (-1, 1))
        if not (is_h or is_l):
            return []
        if is_h and is_l:
            types = ["L", "H"] if self.seq and self.seq[-1].typ == "H" else ["H", "L"]
        else:
            types = ["H"] if is_h else ["L"]
        entered = []
        for typ in types:
            x = _Piv(len(self.raw), typ, p, i, B.h[p] if typ == "H" else B.l[p], len(types) == 2)
            self.raw.append(x)
            if self.seq and self.seq[-1].typ == typ:
                old = self.seq[-1]
                if not (x.level > old.level + EPS if typ == "H" else x.level < old.level - EPS):
                    continue                                  # less extreme or tie: the earlier one stays
                old.replaced_by = x
                self.seq[-1] = x
            else:
                self.seq.append(x)
            x.entered = True
            x.prev1 = self.seq[-2] if len(self.seq) >= 2 else None
            x.prev2 = self.seq[-3] if len(self.seq) >= 3 else None
            self.last[typ] = x
            entered.append(x)
        return entered


class _Sim:
    """Expected trajectory of one logged setup."""

    def __init__(self, row: dict, variant: int):
        self.row = row
        self.sid = _int(row.get("setup_id"))
        self.sign = _sign(_str(row.get("dir")))
        self.ob_lo, self.ob_hi = _flt(row.get("ob_low")), _flt(row.get("ob_high"))
        self.ob_time = _int(row.get("ob_time"))
        self.c3_time = _int(row.get("idfvg_c3_time"))
        self.touch_msc = _int(row.get("touch_msc"))
        self.variant = variant
        self.touch = None
        self.status = "untouched"
        self.reason = None
        self.broken, self.break_bar, self.returns, self.episodes, self.second = False, None, 0, 0, False
        self.used_refs: set[int] = set()
        self.sc = None
        self.cands: list[dict] = []
        self.queued = None
        self.exp: list[dict] = []
        self.lost_at: set[int] = set()
        self.fill_ms = None

    @property
    def own(self):           # the swing type of the setup's structure low (long) / high (short)
        return "L" if self.sign > 0 else "H"

    @property
    def opp(self):
        return "H" if self.sign > 0 else "L"

    def beyond_ob(self, c):
        return c < self.ob_lo - EPS if self.sign > 0 else c > self.ob_hi + EPS

    def add(self, kind, bar, **info):
        self.exp.append(dict(kind=kind, bar=bar, **info))

    def end(self, reason, bar):
        self.status, self.reason = "ended", reason
        self.add(reason, bar)


# --- the replay ------------------------------------------------------------------------------------------------
class _Replay:
    def __init__(self, setups, events, pivots, bars_m1, bars_m5, params, deals=None, trail=None, moves=None,
                 trailing=False):
        self.prm = {**DEFAULT_PARAMS, **(params or {})}
        self.trail, self.moves, self.trailing = trail, moves, trailing
        self.paths = stop_path(moves)
        self.N = int(self.prm["SwingStrengthM1"])
        self.pt = float(self.prm["point"])
        self.buf = float(self.prm["StopBufferPoints"]) * self.pt
        self.tol = 0.5 * self.pt + 1e-9
        self.variant = int(self.prm["StructureVariant"])
        self.out: list[dict] = []
        self.occ = dict.fromkeys(OCC_KEYS, 0)
        self.frames = dict(setups=setups, events=events, pivots=pivots, bars_m1=bars_m1, bars_m5=bars_m5)
        self.deals = deals
        self.checked = Counter()
        self.unv = Counter()
        self.unv_ex: dict[str, list] = {}

    def add(self, sid, rule, detail):
        self.out.append({"setup_id": sid, "rule": rule, "detail": detail})

    def chk(self, rule, n=1):
        """Count one evaluated check of a rule (coverage report); violations are counted separately."""
        self.checked[rule] += n

    def nochk(self, key, sid=None):
        """Count one case the logs cannot prove (UNVERIFIABLE[key])."""
        self.unv[key] += 1
        ex = self.unv_ex.setdefault(key, [])
        if sid is not None and len(ex) < 5 and sid not in ex:
            ex.append(sid)

    def coverage(self) -> dict:
        failed = Counter(v["rule"] for v in self.out)
        rules = {r: {"checked": int(self.checked[r]), "failed": int(failed[r]),
                     "passed": int(max(self.checked[r] - failed[r], 0))} for r in RULES if self.checked[r] or failed[r]}
        unv = {k: {"cases": int(n), "why": UNVERIFIABLE[k], "example_setups": self.unv_ex.get(k, [])}
               for k, n in self.unv.items() if n}
        return {"rules": rules, "unverifiable": unv}

    # ------------------------------------------------------------------
    def run(self):
        f = self.frames
        need = {"setups": mc.SETUP_COLUMNS, "events": mc.EVENT_COLUMNS, "pivots": mc.PIVOT_COLUMNS,
                "bars_m1": mc.BARS_COLUMNS, "bars_m5": mc.BARS_COLUMNS}
        fatal = False
        for name, cols in need.items():
            miss = [c for c in cols if c not in f[name]]
            if miss:
                self.add(None, "fields", f"{name} lacks contract columns {miss}")
                fatal = fatal or name in ("setups", "events") or any(c in miss for c in ("time", "high", "low"))
        if fatal:
            return self
        self.setups = _norm(f["setups"], SETUP_INT, SETUP_FLOAT, SETUP_STR)
        self.events = _norm(f["events"], EVENT_INT, EVENT_FLOAT, EVENT_STR)
        self.pivots = _norm(f["pivots"], PIVOT_INT, PIVOT_FLOAT, PIVOT_STR)
        self.B = _Bars(f["bars_m1"], M1_S)
        self.B5 = _Bars(f["bars_m5"], M5_S)
        self._tf_sync()
        self._prepare()
        self._prepare_money()
        self._candidates()
        self._simulate()
        self._check_pivots()
        for s in self.sims:
            self._compare(s)
            self._check_exit(s)
        self._unconsumed()
        self.out.extend(trail_presence(self.trailing, self.trail, self.moves))
        if self.trail is not None:
            viol, n, unv = check_trails(self.setups, self.trail, self.moves, self.B, self.pt)
            self.out.extend(viol)
            self.chk("trail_r23", n)
            for key, sids in unv.items():
                for sid in sids:
                    self.nochk(key, sid)
        return self

    # ------------------------------------------------------------------
    def _tf_sync(self):
        B, B5 = self.B, self.B5
        if B.n == 0:
            return
        for j in range(B5.n):
            T = B5.t[j]
            if T < B.t[0] or T + M5_S > B.t[-1] + M1_S:
                continue
            a, b = bisect.bisect_left(B.t, T), bisect.bisect_left(B.t, T + M5_S)
            if a >= b:
                self.add(None, "tf_sync", f"M5 bar {T} has no M1 bars")
                continue
            self.chk("tf_sync")
            agg = (B.o[a], max(B.h[a:b]), min(B.l[a:b]), B.c[b - 1])
            got = (B5.o[j], B5.h[j], B5.l[j], B5.c[j])
            if any(abs(x - y) > EPS for x, y in zip(agg, got)):
                self.add(None, "tf_sync", f"M5 bar {T} OHLC {got} != aggregate of its M1 bars {agg}")

    def _prepare(self):
        self.logged_piv = {}
        for r in self.pivots.to_dict("records"):
            pid = _int(r.get("pivot_id"))
            if pid is not None:
                self.logged_piv[pid] = dict(typ=_ptype(r.get("type")), peak_time=_int(r.get("peak_time")),
                                            conf_time=_int(r.get("conf_time")), level=_flt(r.get("level")),
                                            replaced_by=_int(r.get("replaced_by")), outside=_int(r.get("outside_bar")))
        rows = self.setups.to_dict("records")
        ids = [_int(r.get("setup_id")) for r in rows]
        for sid, n in Counter(ids).items():
            if n > 1:
                self.add(sid, "fields", "setup_id appears more than once in rl_setups")
        self.ev_by = {}
        known = set(ids)
        for e in self.events.to_dict("records"):
            e = dict(setup_id=_int(e.get("setup_id")), seq=_int(e.get("seq")), kind=_str(e.get("kind")),
                     bar_time=_int(e.get("bar_time")), tick_msc=_int(e.get("tick_msc")), price=_flt(e.get("price")),
                     lo=_flt(e.get("lo")), hi=_flt(e.get("hi")), ref_id=_int(e.get("ref_id")),
                     ref_time=_int(e.get("ref_time")), detail=_str(e.get("detail")), used=False)
            if e["kind"] not in mc.EVENT_KINDS:
                self.add(e["setup_id"], "fields", f"unknown event kind {e['kind']!r}")
                continue
            if e["setup_id"] not in known:
                self.add(e["setup_id"], "fields", f"event {e['kind']} for a setup id not in rl_setups")
                continue
            self.ev_by.setdefault(e["setup_id"], []).append(e)
        for lst in self.ev_by.values():
            lst.sort(key=lambda e: (e["seq"] if e["seq"] is not None else 0))
        self.sims = []
        for r in rows:
            s = _Sim(r, self.variant)
            v = _variant(r.get("variant"))
            if v is not None and v != self.variant:
                self.add(s.sid, "fields", f"row variant={r.get('variant')} but run StructureVariant={self.variant}")
            if _str(r.get("reason")) not in mc.REASONS:
                self.add(s.sid, "fields", f"reason={_str(r.get('reason'))!r} is not a contract reason")
            if s.sign == 0 or s.ob_lo is None or s.ob_hi is None or s.c3_time is None:
                self.add(s.sid, "fields", "setup lacks dir, OB levels or idfvg_c3_time")
                continue
            self.occ["long" if s.sign > 0 else "short"] += 1
            self.sims.append(s)

    # ------------------------------------------------------------------
    def _candidates(self):
        """R3 on the closed M5 bars: rebuild every OB / identifying-FVG candidate and match it with rl_setups."""
        B5, W = self.B5, int(self.prm["ImpulseWindowBars"])
        used, cands = set(), {}
        for j in range(2, B5.n):
            c1 = j - 2
            for sign in (1, -1):
                if not B5.is_fvg(c1, sign):
                    continue
                ob = None
                for k in range(W):
                    x = c1 - k
                    if x < 0:
                        break
                    if (B5.c[x] < B5.o[x] - EPS) if sign > 0 else (B5.c[x] > B5.o[x] + EPS):
                        ob = x
                        break
                if ob is None or ob in used:
                    continue
                lo, hi = B5.l[ob], B5.h[ob]
                if any((B5.c[x] < lo - EPS) if sign > 0 else (B5.c[x] > hi + EPS) for x in range(ob + 1, j + 1)):
                    continue
                used.add(ob)
                cands[(sign, B5.t[ob])] = dict(ob=ob, c1=c1, c3=j, zone=B5.fvg_zone(c1, sign))
        self.occ["candidates_m5"] = len(cands)
        eq = lambda a, b: a is not None and abs(a - b) <= self.tol  # noqa: E731
        seen = Counter((s.sign, s.ob_time) for s in self.sims)
        for s in self.sims:
            key = (s.sign, s.ob_time)
            self.chk("ob_r3")
            if seen[key] > 1:
                self.add(s.sid, "ob_r3", f"OB candle {s.ob_time} carries {seen[key]} setups (each candle once)")
            c = cands.get(key)
            if c is None:
                self.add(s.sid, "ob_r3", f"no {'long' if s.sign > 0 else 'short'} OB candidate at M5 {s.ob_time} "
                                         "under the approved definition")
                continue
            r = s.row
            if _int(r.get("idfvg_c1_time")) != B5.t[c["c1"]] or s.c3_time != B5.t[c["c3"]]:
                self.add(s.sid, "ob_r3", f"identifying FVG {B5.t[c['c1']]}..{B5.t[c['c3']]}, row "
                                         f"{_int(r.get('idfvg_c1_time'))}..{s.c3_time}")
            if not (eq(s.ob_lo, B5.l[c["ob"]]) and eq(s.ob_hi, B5.h[c["ob"]])):
                self.add(s.sid, "ob_r3", f"OB range {B5.l[c['ob']]}-{B5.h[c['ob']]}, row {s.ob_lo}-{s.ob_hi}")
            lo, hi = c["zone"]
            if not (eq(_flt(r.get("idfvg_low")), lo) and eq(_flt(r.get("idfvg_high")), hi)):
                self.add(s.sid, "ob_r3", f"identifying FVG zone {lo}-{hi}, row {_flt(r.get('idfvg_low'))}-"
                                         f"{_flt(r.get('idfvg_high'))}")
            w = _int(r.get("identified_in_warmup"))
            if w is not None and w != B5.warm[c["c3"]]:
                self.add(s.sid, "fields", f"identified_in_warmup={w} but candle 3 warm-up flag={B5.warm[c['c3']]}")
        have = set(seen)
        for (sign, ob_t), c in cands.items():
            self.chk("ob_r3")
            if (sign, ob_t) not in have:
                self.add(None, "ob_r3", f"{'long' if sign > 0 else 'short'} candidate OB {ob_t} (identifying FVG "
                                        f"{B5.t[c['c1']]}..{B5.t[c['c3']]}) has no rl_setups row")

    # ------------------------------------------------------------------
    def _simulate(self):
        B = self.B
        self.seq = _Sequence(B, self.N)
        touch_at: dict[int, list[_Sim]] = {}
        for s in self.sims:
            start = B.first_at_or_after(s.c3_time + M5_S)
            if start >= B.n:
                continue
            reach = (B.l_np[start:] <= s.ob_hi + EPS) if s.sign > 0 else (B.h_np[start:] >= s.ob_lo - EPS)
            if reach.any():
                touch_at.setdefault(start + int(np.argmax(reach)), []).append(s)
        active: list[_Sim] = []
        for i in range(B.n):
            # tick phase: queued outcomes (KTD2 step 5 of earlier ticks), touch and return (step 4)
            for s in active:
                if s.queued is not None and s.queued["bar"] == i and s.status == "active":
                    self._apply_outcome(s, i)
            for s in touch_at.get(i, []):
                s.touch = i
                if B.warm[i]:
                    s.status, s.reason = "ended", "warmup_dropped"
                    self.occ["warmup_dropped"] += 1
                    continue
                s.status = "active"
                self.occ["touched"] += 1
                if (B.o[i] <= s.ob_hi + EPS) if s.sign > 0 else (B.o[i] >= s.ob_lo - EPS):
                    self.occ["touch_bar_opens_in_zone"] += 1
                s.add("touch", i)
                active.append(s)
            for s in active:
                if s.status == "active" and s.broken and i > s.break_bar and (
                        B.h[i] >= s.ob_lo - EPS if s.sign > 0 else B.l[i] <= s.ob_hi + EPS):
                    s.broken, s.returns = False, s.returns + 1
                    s.return_bar = i
                    s.add("return", i)
                    self.occ["return"] += 1
            # close phase
            entered = self.seq.update(i)
            for x in entered:
                self.occ["pivots"] += 1
                self.occ["outside_bar_pivots"] += x.outside
            reacting: list[_Sim] = []
            for s in active:
                if s.status == "active" and self._close(s, i, entered):
                    reacting.append(s)
            self._compete(reacting, i)
            active = [s for s in active if s.status == "active"]
        self.occ["pivots_replaced"] = sum(1 for x in self.seq.raw if x.replaced_by is not None)
        for s in self.sims:
            if s.status == "active" and s.queued is not None and s.queued["bar"] is None:
                self._apply_outcome(s, None)
            if s.status == "untouched":
                # a touch tick inside the final M1 bar, which never closes and is never logged, has no bar evidence
                tm = _int(s.row.get("touch_msc"))
                after_log = self.B.n and tm is not None and tm >= self.B.close_ms(self.B.n - 1)
                s.reason = "run_end_waiting" if after_log else "run_end_untouched"
                if after_log:
                    self.nochk("touch_after_last_logged_bar", s.sid)
            elif s.status == "active":
                s.reason = "run_end_waiting"
            if s.reason in ("run_end_untouched", "run_end_waiting"):
                self.occ[s.reason] += 1

    def _close(self, s: _Sim, i: int, entered) -> bool:
        """KTD2 steps 2-8 for one setup at the close of bar i; True when bar i is a reaction candle for it."""
        B, sign, c = self.B, s.sign, self.B.c[i]
        # 2. break episode (R6) and second break (R8)
        if s.beyond_ob(c):
            if s.broken:
                pass
            elif s.returns >= 1:
                s.second = True
                self.occ["cancelled_second_break"] += 1
                self.occ["return_and_break_same_bar"] += getattr(s, "return_bar", None) == i
                s.end("cancelled_second_break", i)
                return False
            else:
                s.broken, s.break_bar, s.episodes = True, i, s.episodes + 1
                s.add("break", i, price=c)
                self.occ["break"] += 1
                self.occ["touch_bar_break"] += i == s.touch
        # 3. opposing structure (R22): new candidates from lower highs entering now; completion / void by closes
        for x in entered:
            if (x.typ == s.opp and x.peak > s.touch and x.prev2 is not None and x.prev2.typ == x.typ
                    and (x.level < x.prev2.level - EPS if sign > 0 else x.level > x.prev2.level + EPS)):
                s.cands.append(dict(h2=x, l1=x.prev1, h1=x.prev2))
                self.occ["lh_candidates"] += 1
        done = [cd for cd in s.cands if cd["h2"].conf < i
                and (c < cd["l1"].level - EPS if sign > 0 else c > cd["l1"].level + EPS)]
        if done:
            s.add("cancelled_opposing_structure", i, ok=[(cd["h2"], cd["l1"]) for cd in done])
            s.status, s.reason = "ended", "cancelled_opposing_structure"
            self.occ["cancelled_opposing_structure"] += 1
            return False
        keep = []
        for cd in s.cands:
            if cd["h2"].conf < i and (c > cd["h2"].level + EPS if sign > 0 else c < cd["h2"].level - EPS):
                self.occ["lh_voided"] += 1
            else:
                keep.append(cd)
        s.cands = keep
        sc = s.sc
        # 4. entry FVG lapse (R13)
        if sc is not None and sc["fvg"] is not None and i > sc["fixed_bar"]:
            lo, hi = sc["fvg"][1], sc["fvg"][2]
            if c < lo - EPS if sign > 0 else c > hi + EPS:
                s.add("fvg_lapsed", i)
                self.occ["fvg_lapsed"] += 1
                s.sc = sc = None
        # 5. structure change (R10)
        ref = self.seq.last[s.opp]
        if ref is not None and ref.uid not in s.used_refs and (
                c > ref.level + EPS if sign > 0 else c < ref.level - EPS):
            s.used_refs.add(ref.uid)
            if sc is not None:
                s.add("sc_superseded", i, ref=ref)
                self.occ["sc_superseded"] += 1
            rng = range(ref.peak, i + 1)
            origin = (min(rng, key=lambda j: (B.l[j], -j)) if sign > 0 else min(rng, key=lambda j: (-B.h[j], -j)))
            s.sc = sc = dict(k=i, ref=ref, origin=origin, olevel=B.l[origin] if sign > 0 else B.h[origin],
                             state="pending", fvg=None, fixed_bar=None, hl=None, hl_decided=False)
            s.add("sc_hh", i, ref=ref, origin=origin)
            self.occ["sc_hh"] += 1
            self.occ["sc_on_touch_bar"] += i == s.touch
        # 6. entry FVG (R12) at the close of bar k+1
        if sc is not None and sc["state"] == "pending" and i == sc["k"] + 1:
            chosen = None
            for c1 in range(sc["k"] - 1, sc["origin"] - 1, -1):
                if c1 < 0 or not B.is_fvg(c1, sign):
                    continue
                lo, hi = B.fvg_zone(c1, sign)
                if any((B.c[j] < lo - EPS) if sign > 0 else (B.c[j] > hi + EPS) for j in range(c1 + 3, i + 1)):
                    self.occ["fvg_skipped_closed_beyond"] += 1
                    continue
                chosen = (c1, lo, hi)
                break
            if chosen is None:
                s.add("fvg_none", i)
                self.occ["fvg_none"] += 1
                s.sc = sc = None
            else:
                sc.update(fvg=chosen, fixed_bar=i, state="armed" if self.variant == 1 else "ready")
                s.add("fvg_fixed", i, fvg=chosen)
                self.occ["fvg_fixed"] += 1
                self.occ["fvg_c3_before_k1"] += chosen[0] + 2 < i
        # 7. HL / LH (R11)
        if sc is not None and sc["fvg"] is not None and not sc["hl_decided"]:
            for x in entered:
                if x.typ == s.own and x.peak > sc["k"]:
                    sc["hl_decided"] = True
                    if x.level > sc["olevel"] + EPS if sign > 0 else x.level < sc["olevel"] - EPS:
                        sc["hl"] = x
                        s.add("hl", i, piv=x)
                        self.occ["hl"] += 1
                        if self.variant == 1:
                            sc["state"] = "ready"
                    elif self.variant == 1:
                        s.add("hl_failed", i, piv=x)
                        self.occ["hl_failed"] += 1
                        s.sc = sc = None
                    else:
                        self.occ["hl_failed_variant_a"] += 1
                    break
        # KTD7: a market-closed retry stops once the setup leaves READY or is in a break episode
        if s.queued is not None and (sc is None or sc["state"] != "ready" or s.broken):
            s.queued = None
            self.occ["retry_dropped"] += 1
        # 8. reaction candle (R14)
        if sc is None or sc["state"] != "ready" or s.broken or s.queued is not None:
            return False
        c1, lo, hi = sc["fvg"]
        if not (i > sc["k"] and i > c1 + 2 and (self.variant == 0 or i > sc["hl"].conf)):
            return False
        if sign > 0:
            ok = B.l[i] <= hi + EPS and c > B.o[i] + EPS and c > hi + EPS
        else:
            ok = B.h[i] >= lo - EPS and c < B.o[i] - EPS and c < lo - EPS
        return ok

    # ------------------------------------------------------------------
    def _anchor(self, s: _Sim):
        """R18 (kind, level) from the replay state of the setup now."""
        sc = s.sc
        struct, kind = None, None
        if sc is not None and sc["hl"] is not None:
            struct, kind = sc["hl"].level, "hl"
        elif self.variant == 0 and self.seq.last[s.own] is not None:
            struct, kind = self.seq.last[s.own].level, "pivot"
        if s.sign > 0:
            return (kind, struct) if struct is not None and struct < s.ob_lo - EPS else ("ob", s.ob_lo)
        return (kind, struct) if struct is not None and struct > s.ob_hi + EPS else ("ob", s.ob_hi)

    def _compete(self, reacting: list[_Sim], i: int):
        B = self.B
        for sign in (1, -1):
            group = [s for s in reacting if s.sign == sign]
            if not group:
                continue
            key = lambda s: (s.touch_msc if s.touch_msc is not None else B.t[s.touch] * 1000,  # noqa: E731
                             s.ob_time or 0, -(s.sid or 0))
            win = max(group, key=key)
            self.occ["reaction"] += len(group)
            self.occ["competition"] += len(group) > 1
            for s in group:
                s.add("reaction", i, required=s is win)
                self.occ["reaction_at_k1"] += i == s.sc["k"] + 1
                self.occ["reaction_on_return_bar"] += getattr(s, "return_bar", None) == i
                if s is not win:
                    s.add("lost_competition", i, winner=win.sid)
                    s.lost_at.add(i)
                    self.occ["lost_competition"] += 1
            self._decide(win, i)

    def _decide(self, s: _Sim, i: int):
        """R15 for the R16 winner of reaction bar i: the logged decision on the first tick after its close."""
        B = self.B
        evs = [e for e in self.ev_by.get(s.sid, []) if e["kind"] in DECISION_KINDS and not e["used"]
               and e["tick_msc"] is not None and e["tick_msc"] >= B.close_ms(i)]
        evs.sort(key=lambda e: (e["tick_msc"], e["seq"] or 0))
        out_i = next((k for k, e in enumerate(evs) if e["kind"] in OUTCOME_KINDS), None)
        self.chk("entry_r15")
        if out_i is None:
            if i + 1 < B.n:
                self.add(s.sid, "entry_r15", f"reaction at {B.t[i]} won R16 but no entry decision (fill or skip) "
                                             "is logged after it")
            return
        window = evs[:out_i + 1]
        for e in window:
            e["used"] = True
        first, outcome = window[0], window[-1]
        attempts = [e for e in window if e["kind"] == "entry_attempt"]
        self.occ["market_closed_retry"] += len(attempts) > 1
        if len(attempts) > 1:
            self.nochk("market_closed_retry", s.sid)
        last = attempts[-1] if attempts else None    # the attempt that filled carries Bid (lo) and Ask (hi)
        s.req_px = last["price"] if last else None
        s.entry_spread = (last["hi"] - last["lo"]) if last and last["lo"] is not None and last["hi"] is not None             else None
        fb = B.bar_at_ms(first["tick_msc"])
        if i + 1 < B.n and fb != i + 1:
            self.add(s.sid, "entry_r15", f"first decision {first['kind']} at {first['tick_msc']} is not on the first "
                                         f"tick after the reaction close (bar {B.t[i + 1]})")
        kind, anchor = self._anchor(s)
        if fb == i + 1:
            bid = B.o[i + 1]
            crossed = bid <= anchor - self.buf + EPS if s.sign > 0 else bid >= anchor + self.buf - EPS
            # skip precedence (the EA's order, the plan fixes none): a full cap (R21) is checked before the stop,
            # so with both true the logged skip is skipped_cap - no order either way; cap_r21 verifies the count
            if crossed and first["kind"] not in ("skipped_stop_crossed", "skipped_cap"):
                self.add(s.sid, "entry_r15", f"Bid {bid} at the first tick after the reaction already crosses the "
                                             f"stop (anchor {anchor} {'-' if s.sign > 0 else '+'} buffer): "
                                             f"skipped_stop_crossed expected, logged {first['kind']} (AE7)")
            if not crossed and first["kind"] == "skipped_stop_crossed":
                self.add(s.sid, "entry_r15", f"skipped_stop_crossed but Bid {bid} does not cross the stop")
            if first["kind"] == "entry_attempt" and first["lo"] is not None and abs(first["lo"] - bid) > self.tol:
                self.add(s.sid, "entry_r15", f"first attempt Bid {first['lo']} != open {bid} of bar {B.t[i + 1]}: "
                                             "not the first tick after the reaction close")
        for e in window:
            if e["kind"] == "skipped_stop_crossed":
                self._check_skip_sl(s, e, kind, anchor, B.o[i + 1] if e is first and fb == i + 1 else None)
        s.queued = dict(ev=outcome, bar=B.bar_at_ms(outcome["tick_msc"]), reaction=i)
        if s.queued["bar"] is None and outcome["tick_msc"] < B.close_ms(B.n - 1):
            self.add(s.sid, "fields", f"{outcome['kind']} tick {outcome['tick_msc']} lies in no logged M1 bar")
            s.queued = None
        elif s.queued["bar"] is not None and s.queued["bar"] <= i:
            s.queued = None

    def _check_skip_sl(self, s, e, kind, anchor, bid):
        """skipped_stop_crossed: the EA tests the stop on the trigger side (long Bid <= SL, short Ask >= SL) and logs
        the request price (long: the Ask, short: the Bid) with lo = SL. bid: the bar-open Bid when this skip is the
        first tick after the reaction close, else None. The Ask of a short skip is not logged."""
        self.chk("sl_r18")
        sl, price = e["lo"], e["price"]
        if sl is None or price is None:
            self.add(s.sid, "fields", "skipped_stop_crossed lacks price or lo=sl")
            return
        if s.sign > 0:
            if bid is not None and bid > sl + self.tol:
                self.add(s.sid, "entry_r15", f"skipped_stop_crossed but Bid {bid} is above the long stop {sl}")
            if abs(sl - (anchor - self.buf)) > self.tol:
                self.add(s.sid, "sl_r18", f"skip SL {sl} != {kind} anchor {anchor} - buffer")
        else:
            # SL = anchor + buffer + spread, so Ask >= SL is Bid >= anchor + buffer: checkable on the logged Bid
            if price < anchor + self.buf - self.tol:
                self.add(s.sid, "entry_r15", f"skipped_stop_crossed but Bid {price} is below {kind} anchor {anchor} "
                                             "+ buffer: the short stop is not crossed")
            if bid is not None and abs(price - bid) > self.tol:
                self.add(s.sid, "entry_r15", f"skip Bid {price} != open {bid}: not the first tick after the reaction")
            if sl - anchor - self.buf < -self.tol:
                self.add(s.sid, "sl_r18", f"skip SL {sl} is below {kind} anchor {anchor} + buffer (spread < 0)")

    def _apply_outcome(self, s: _Sim, i):
        q, s.queued = s.queued, None
        e = q["ev"]
        if e["kind"] != "fill":
            self.occ[e["kind"]] += 1
            if e["kind"] == "skipped_cap":
                self._check_cap(s, e["tick_msc"], int(e["detail"]) if (e["detail"] or "").isdigit() else -1)
            elif e["kind"] == "skipped_volume":
                try:
                    lots = float(e["detail"])
                except (TypeError, ValueError):
                    lots = None
                self._check_risk(s, e["tick_msc"], e["price"], e["lo"], lots, skip=True)
            elif e["kind"] in UNVERIFIABLE:
                self.nochk(e["kind"], s.sid)
            return
        if s.broken:
            self.add(s.sid, "entry_r15", "fill while the OB is in a break episode (R7)")
        kind, anchor = self._anchor(s)
        self.occ[f"sl_anchor_{kind}"] += 1
        self._check_fill(s, e, kind, anchor, i)
        s.status, s.reason, s.fill_ms = "ended", "filled", e["tick_msc"]
        self.occ["filled"] += 1

    def _check_fill(self, s, e, kind, anchor, bar):
        r, tol = s.row, self.tol
        fill = e["price"] if e["price"] is not None else _flt(r.get("fill_price"))
        sl = e["lo"] if e["lo"] is not None else _flt(r.get("sl"))
        tp = e["hi"] if e["hi"] is not None else _flt(r.get("tp"))
        for col, ev_val in (("fill_price", e["price"]), ("sl", e["lo"]), ("tp", e["hi"])):
            rv = _flt(r.get(col))
            if rv is not None and ev_val is not None and abs(rv - ev_val) > tol:
                self.add(s.sid, "fields", f"rl_setups {col}={rv} but the fill event carries {ev_val}")
        if _int(r.get("fill_msc")) is not None and _int(r.get("fill_msc")) != e["tick_msc"]:
            self.add(s.sid, "fields", f"rl_setups fill_msc={_int(r.get('fill_msc'))} != fill event {e['tick_msc']}")
        if fill is None or sl is None:
            self.add(s.sid, "fields", "fill without price or SL")
            return
        # R21 and R20 at the fill
        self._check_cap(s, e["tick_msc"], None)
        px = getattr(s, "req_px", None)
        self._check_risk(s, e["tick_msc"], px if px is not None else _flt(r.get("request_price")), sl,
                         _flt(r.get("volume")), skip=False)
        # R18
        self.chk("sl_r18")
        self.chk("tp_r19")
        row_kind, row_anchor = _str(r.get("sl_anchor")), _flt(r.get("sl_anchor_price"))
        row_buf = _flt(r.get("buffer_pts"))
        if row_kind != kind:
            self.add(s.sid, "sl_r18", f"sl_anchor={row_kind!r}, R18 gives {kind!r} at {anchor}")
        if row_anchor is None or abs(row_anchor - anchor) > tol:
            self.add(s.sid, "sl_r18", f"sl_anchor_price={row_anchor}, R18 gives {anchor}")
        if row_buf is None or abs(row_buf - float(self.prm["StopBufferPoints"])) > 1e-9:
            self.add(s.sid, "sl_r18", f"buffer_pts={row_buf}, run StopBufferPoints={self.prm['StopBufferPoints']}")
        if s.sign > 0:
            if abs(sl - (anchor - self.buf)) > tol:
                self.add(s.sid, "sl_r18", f"SL {sl} != {kind} anchor {anchor} - buffer {self.buf:.2f}")
        else:
            spread = sl - anchor - self.buf
            exact = getattr(s, "entry_spread", None)      # Ask - Bid logged on the entry attempt
            self.nochk("short_entry_ask" if exact is not None else "short_sl_spread_proxy", s.sid)
            if exact is None and bar is not None and np.isfinite(self.B.spread[bar]):
                exact = self.B.spread[bar] * self.pt      # fallback for logs without Bid/Ask on the attempt
            if spread < -tol or (exact is not None and abs(spread - exact) > tol):
                self.add(s.sid, "sl_r18", f"SL {sl} != {kind} anchor {anchor} + buffer {self.buf:.2f} + spread "
                                          f"{exact}")
        # R19
        exp_tp = fill + s.sign * float(self.prm["RiskRR"]) * abs(fill - sl)
        if tp is None or abs(tp - exp_tp) > tol:
            self.add(s.sid, "tp_r19", f"TP {tp} != fill {fill} {'+' if s.sign > 0 else '-'} "
                                      f"{self.prm['RiskRR']} x |fill - SL| = {exp_tp:.2f}")

    # ------------------------------------------------------------------
    def _check_pivots(self):
        B, seq = self.B, self.seq
        replay = {(x.typ, B.t[x.peak]): x for x in seq.raw}
        logged_key = {}
        for pid, p in self.logged_piv.items():
            key = (p["typ"], p["peak_time"])
            logged_key[pid] = key
            self.chk("pivot_causality_r9")
            x = replay.get(key)
            if x is None:
                self.add(None, "pivot_causality_r9", f"pivot {pid} ({p['typ']} {p['peak_time']}) is not a strict "
                                                     f"pivot of strength {self.N} in rl_bars_m1")
                continue
            if p["conf_time"] != B.t[x.conf]:
                self.add(None, "pivot_causality_r9", f"pivot {pid} conf_time {p['conf_time']} != open of bar p+N "
                                                     f"{B.t[x.conf]}")
            if p["level"] is None or abs(p["level"] - x.level) > self.tol:
                self.add(None, "pivot_causality_r9", f"pivot {pid} level {p['level']} != {x.level}")
            if p["outside"] is not None and bool(p["outside"]) != x.outside:
                self.add(None, "pivot_causality_r9", f"pivot {pid} outside_bar={p['outside']}, bars give {x.outside}")
        self.key_to_pid = {k: pid for pid, k in logged_key.items()}
        for x in seq.raw:
            if not x.entered:
                continue
            self.chk("pivot_causality_r9")
            key = (x.typ, B.t[x.peak])
            pid = self.key_to_pid.get(key)
            if pid is None:
                self.add(None, "pivot_causality_r9", f"sequence pivot {x.typ} peaking {B.t[x.peak]} (confirmed "
                                                     f"{B.t[x.conf]}) is missing from rl_pivots")
                continue
            exp_rb = None if x.replaced_by is None else (x.replaced_by.typ, B.t[x.replaced_by.peak])
            rb = self.logged_piv[pid]["replaced_by"]
            got_rb = None if rb is None else logged_key.get(rb, ("?", rb))
            if got_rb != exp_rb:
                self.add(None, "pivot_causality_r9", f"pivot {pid} replaced_by {rb}, compression gives {exp_rb}")

    # ------------------------------------------------------------------
    def _prepare_money(self):
        """Positions from rl_setups (R21) and, with rl_deals, the balance at any ms (R20): the balance deals plus the
        net result of every position whose exit came strictly before."""
        self.positions = []
        for r in self.setups.to_dict("records"):
            f = _int(r.get("fill_msc"))
            if f is not None:
                x = _int(r.get("exit_msc"))
                self.positions.append((f, x if x is not None else 2 ** 62, _int(r.get("setup_id")),
                                       _int(r.get("position_id"))))
        self.money, self.deal_volume = None, {}
        d = self.deals
        if d is None or len(d) == 0:
            return
        d = d.copy()
        for c in ("type", "entry", "position_id", "volume", "profit", "commission", "swap"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        net = (d["profit"] + d["commission"] + d["swap"]).fillna(0)
        deposit = float(net[d["type"] == 2].sum())
        trade = d["type"].isin([0, 1])
        by_pos = net[trade].groupby(d.loc[trade, "position_id"]).sum().to_dict()
        ins = d[trade & (d["entry"] == 0)]
        self.deal_volume = dict(zip(ins["position_id"].astype(int), ins["volume"].astype(float)))
        closed = sorted((x, by_pos.get(pid, 0.0)) for f, x, _, pid in self.positions
                        if pid is not None and x < 2 ** 62)
        self.money = (deposit, [c[0] for c in closed], np.cumsum([c[1] for c in closed]).tolist())

    def _balance(self, ms) -> float:
        deposit, times, cum = self.money
        k = bisect.bisect_left(times, ms)          # exits strictly before ms
        return deposit + (cum[k - 1] if k else 0.0)

    def _check_cap(self, s, ms, logged_open):
        """R21: a fill needs fewer than MaxExposures open positions; skipped_cap (logged_open not None) needs at
        least that many, and the logged count must match."""
        if ms is None:
            return
        self.chk("cap_r21")
        n = sum(1 for f, x, sid, _ in self.positions if sid != s.sid and f < ms < x)
        cap = int(self.prm["MaxExposures"])
        if logged_open is None and n >= cap:
            self.add(s.sid, "cap_r21", f"fill at {ms} with {n} positions already open (cap {cap})")
        elif logged_open is not None and (n < cap or n != logged_open):
            self.add(s.sid, "cap_r21", f"skipped_cap at {ms} logs {logged_open} open, positions give {n} (cap {cap})")

    def _check_risk(self, s, ms, px, sl, lots, skip):
        """R20: lots = floor(balance x RiskPercent / (|price - SL| x contract) / step) x step. A short's SL already
        holds the entry spread (R18), so |Bid - SL| includes it."""
        if self.money is None:
            self.nochk("risk_without_deals", s.sid)
            return
        if ms is None or px is None or sl is None:
            return
        self.chk("risk_r20")
        if s.sign > 0:
            self.nochk("entry_price_ask", s.sid)
        step = float(self.prm["lot_step"])
        bal = self._balance(ms)
        dist = abs(px - sl)
        if dist <= 0:
            self.add(s.sid, "risk_r20", f"zero stop distance at {ms}")
            return
        raw = bal * float(self.prm["RiskPercent"]) / 100.0 / (dist * float(self.prm["contract_size"])) / step
        ok = {round(float(np.floor(raw + 1e-9)) * step, 8)}
        if abs(raw - round(raw)) < 1e-6:                 # a boundary case: either rounding is acceptable
            ok |= {round((round(raw) - 1) * step, 8), round(round(raw) * step, 8)}
        if lots is None or not any(abs(lots - v) <= step / 2 for v in ok):
            self.add(s.sid, "risk_r20", f"{'skip' if skip else 'fill'} at {ms}: lots {lots}, "
                                        f"{self.prm['RiskPercent']}% of balance {bal:.2f} over |{px} - {sl}| gives "
                                        f"{sorted(ok)}")
        if skip and min(ok) >= float(self.prm["lot_min"]) - 1e-9:
            self.add(s.sid, "risk_r20", f"skipped_volume at {ms} but the size {min(ok)} is tradeable")
        pid = _int(s.row.get("position_id"))
        if not skip and pid is not None and pid in self.deal_volume and lots is not None \
                and abs(self.deal_volume[pid] - lots) > step / 2:
            self.add(s.sid, "risk_r20", f"rl_setups volume {lots} != deal volume {self.deal_volume[pid]}")

    def _check_exit(self, s):
        """SL/TP execution at minute level: the exit bar reaches the level and no M1 bar strictly between the fill
        bar and the exit bar reached SL or TP first. Longs trigger on Bid (fully in the bars); shorts trigger on Ask,
        so only necessary Bid conditions are checked for them."""
        r, B = s.row, self.B
        if s.reason != "filled" or _str(r.get("reason")) != "filled":
            return
        fill_ms, ex_ms, kind = _int(r.get("fill_msc")), _int(r.get("exit_msc")), _str(r.get("exit_kind"))
        sl, tp, xp = _flt(r.get("sl")), _flt(r.get("tp")), _flt(r.get("exit_price"))
        if ex_ms is None or kind not in ("sl", "trail", "tp") or sl is None or tp is None:
            return
        # the stop in force: SL0, or the last accepted trail move before that moment (plan 2026-10-05-0007)
        path = self.paths.get(_int(r.get("position_id")), [])
        at = lambda ms: stop_at(path, sl, ms)
        stop_x = at(ex_ms)
        self.chk("exit_sltp")
        f, x = B.bar_at_ms(fill_ms), B.bar_at_ms(ex_ms)
        if x is None:
            self.nochk("exit_after_last_logged_bar", s.sid)
            return
        if f == x:
            self.nochk("exit_same_bar_as_fill", s.sid)
        lo_j = f + 1 if f is not None else x
        bad = []
        if s.sign > 0:
            if kind in ("sl", "trail") and not (B.l[x] <= stop_x + EPS and (xp is None or xp <= stop_x + self.tol)):
                bad.append(f"{kind.upper()} exit at {ex_ms} @ {xp}: bar low {B.l[x]} does not reach the stop {stop_x}")
            if kind == "tp" and not (B.h[x] >= tp - EPS and (xp is None or xp >= tp - self.tol)):
                bad.append(f"TP exit at {ex_ms} @ {xp}: bar high {B.h[x]} does not reach TP {tp}")
            hit = [j for j in range(lo_j, x) if B.l[j] <= at(B.t[j] * 1000) + EPS or B.h[j] >= tp - EPS]
        else:
            self.nochk("short_exit_ask", s.sid)
            if kind in ("sl", "trail") and xp is not None and xp < stop_x - self.tol:
                bad.append(f"{kind.upper()} exit at {ex_ms} @ {xp} below the short stop {stop_x}")
            if kind == "tp" and not (B.l[x] <= tp + EPS and (xp is None or xp <= tp + self.tol)):
                bad.append(f"TP exit at {ex_ms} @ {xp}: bar low {B.l[x]} does not reach TP {tp}")
            hit = [j for j in range(lo_j, x) if B.h[j] >= at(B.t[j] * 1000) - EPS]
        if hit and all(B.gap_open[j] for j in hit):
            self.nochk("level_in_session_open_bar", s.sid)
        first = next((j for j in hit if not B.gap_open[j]), None)
        if first is not None:
            bad.append(f"bar {B.t[first]} between the fill and the {kind} exit at {ex_ms} already reaches "
                       f"{'SL or TP' if s.sign > 0 else 'the SL with its Bid'}")
        for b in bad:
            self.add(s.sid, "exit_sltp", b)

    def _piv_key(self, pid):
        p = self.logged_piv.get(pid)
        return None if p is None else (p["typ"], p["peak_time"])

    def _xkey(self, x):
        return (x.typ, self.B.t[x.peak])

    # ------------------------------------------------------------------
    def _compare(self, s: _Sim):
        B, sid = self.B, s.sid
        logged = self.ev_by.get(s.sid, [])
        exp_by: dict[str, list[dict]] = {}
        for x in s.exp:
            exp_by.setdefault(x["kind"], []).append(x)
        log_by: dict[str, list[dict]] = {}
        for e in logged:
            if e["kind"] in TICK_KINDS:
                e["bar"] = B.bar_at_ms(e["tick_msc"]) if e["tick_msc"] is not None else None
                if e["tick_msc"] is None:
                    self.add(sid, "fields", f"{e['kind']} event without tick_msc")
                elif e["bar_time"] is not None and e["bar"] is not None and e["bar_time"] != B.t[e["bar"]]:
                    self.add(sid, "fields", f"{e['kind']} bar_time {e['bar_time']} is not the bar of its tick")
            elif e["kind"] in BAR_KINDS:
                e["bar"] = B.i(e["bar_time"])
                if e["bar"] is None:
                    self.add(sid, "fields", f"{e['kind']} bar_time {e['bar_time']} is not a logged M1 bar")
                    continue
            else:
                continue
            log_by.setdefault(e["kind"], []).append(e)
        side = "long" if s.sign > 0 else "short"
        for kind in TICK_KINDS + BAR_KINDS:
            rule = RULE_BY_KIND[kind]
            exps, logs = exp_by.get(kind, []), [e for e in log_by.get(kind, []) if e.get("bar") is not None]
            pool = {}
            for e in logs:
                pool.setdefault(e["bar"], []).append(e)
            self.chk(rule, len(exps))
            for x in exps:
                cand = pool.get(x["bar"])
                if cand:
                    self._details(s, x, cand.pop(0))
                elif kind != "reaction" or x.get("required", True):
                    self.add(sid, rule, f"expected {kind} at bar {B.t[x['bar']]} ({side}) is not logged")
            allowed_break = {x["bar"] for x in exp_by.get("cancelled_second_break", [])}
            for bar, rest in pool.items():
                for e in rest:
                    self.chk(rule)
                    if kind == "break" and bar in allowed_break:
                        continue
                    if kind == "touch":
                        exp_t = exp_by.get("touch")
                        where = (f"first bar reaching the OB edge is {B.t[exp_t[0]['bar']]}" if exp_t else
                                 "no bar reaches the OB edge after candle 3" if s.reason != "warmup_dropped" else
                                 "the touch happened on a warm-up bar")
                        self.add(sid, rule, f"touch claimed in bar {B.t[bar]}: {where}")
                    else:
                        self.add(sid, rule, f"logged {kind} at bar {B.t[bar]} is not derived from the bars")
        for e in log_by.get("fvg_fixed", []):
            self._fvg_in_move(s, e)
        for e in logged:
            if e["kind"] in PIVOT_REF_KINDS:
                self._pivot_use(s, e)
        # rl_setups row
        r = s.row
        reason = _str(r.get("reason"))
        self.chk({"filled": "entry_r15", "cancelled_second_break": "second_break_r8",
                  "cancelled_opposing_structure": "cancel_r22"}.get(s.reason, "touch_r5"))
        self.chk("one_trade_r17")
        if reason != s.reason and reason in mc.REASONS:
            pair = {reason, s.reason}
            rule = ("second_break_r8" if "cancelled_second_break" in pair else
                    "cancel_r22" if "cancelled_opposing_structure" in pair else
                    "entry_r15" if "filled" in pair else
                    "touch_r5" if pair & {"warmup_dropped", "run_end_untouched"} else "fields")
            self.add(sid, rule, f"rl_setups reason={reason}, the bars give {s.reason}")
        tchs = exp_by.get("touch", [])
        tb = _int(r.get("touch_bar_time"))
        if tchs and tb is not None and tb != B.t[tchs[0]["bar"]]:
            self.add(sid, "touch_r5", f"touch_bar_time {tb}, first bar reaching the OB edge is {B.t[tchs[0]['bar']]}")
        ltouch = log_by.get("touch", [])
        if ltouch and s.touch_msc is not None and ltouch[0]["tick_msc"] != s.touch_msc:
            self.add(sid, "fields", f"touch_msc {s.touch_msc} != touch event tick {ltouch[0]['tick_msc']}")
        if s.status != "untouched" and s.reason != "warmup_dropped" and s.touch_msc is None:
            self.add(sid, "fields", "touched setup without touch_msc")
        rets = _int(r.get("returns"))
        if rets is not None and rets != s.returns:
            self.add(sid, "return_r7", f"returns={rets}, bars give {s.returns}")
        brk = _int(r.get("breaks"))
        if brk is not None and brk not in (s.episodes, s.episodes + s.second):
            self.add(sid, "break_r6", f"breaks={brk}, bars give {s.episodes} break episode(s)"
                                      f"{' + the second break' if s.second else ''}")
        fills = [e for e in logged if e["kind"] == "fill"]
        if len(fills) > 1:
            self.add(sid, "one_trade_r17", f"{len(fills)} fills logged for one setup")

    def _details(self, s, x, e):
        B, sid, tol, kind = self.B, s.sid, self.tol, x["kind"]
        rule = RULE_BY_KIND[kind]
        if kind in ("touch", "return"):
            self.nochk("tick_within_minute", sid)
            edge = (s.ob_hi if s.sign > 0 else s.ob_lo) if kind == "touch" else (s.ob_lo if s.sign > 0 else s.ob_hi)
            p, i = e["price"], x["bar"]
            bad = p is None or not (B.l[i] - tol <= p <= B.h[i] + tol)
            if not bad:
                inside = p <= edge + tol if (kind == "touch") == (s.sign > 0) else p >= edge - tol
                bad = not inside
            if bad:
                self.add(sid, rule, f"{kind} Bid {p} is not reachable in bar {B.t[i]} at the zone edge {edge}")
            return
        if kind == "break" and (e["price"] is None or abs(e["price"] - x["price"]) > tol):
            self.add(sid, rule, f"break price {e['price']} != bar close {x['price']}")
        elif kind in ("sc_hh", "sc_superseded"):
            ref = x["ref"]
            if self._piv_key(e["ref_id"]) != self._xkey(ref):
                self.add(sid, rule, f"{kind} at {B.t[x['bar']]} references pivot {e['ref_id']}, the latest "
                                    f"{ref.typ} pivot is the one peaking {B.t[ref.peak]}")
            if kind == "sc_hh":
                if e["ref_time"] != B.t[x["origin"]]:
                    self.add(sid, rule, f"origin {e['ref_time']} != {B.t[x['origin']]}")
                if e["price"] is not None and abs(e["price"] - ref.level) > tol:
                    self.add(sid, rule, f"sc_hh price {e['price']} != reference level {ref.level}")
        elif kind == "fvg_fixed":
            c1, lo, hi = x["fvg"]
            if e["ref_time"] != B.t[c1] or e["lo"] is None or e["hi"] is None or abs(e["lo"] - lo) > tol \
                    or abs(e["hi"] - hi) > tol:
                self.add(sid, rule, f"entry FVG c1 {e['ref_time']} {e['lo']}-{e['hi']}, R12 gives c1 {B.t[c1]} "
                                    f"{lo}-{hi}")
        elif kind in ("hl", "hl_failed"):
            p = x["piv"]
            if self._piv_key(e["ref_id"]) != self._xkey(p) or e["price"] is None or abs(e["price"] - p.level) > tol:
                self.add(sid, rule, f"{kind} references pivot {e['ref_id']} @ {e['price']}, R11 gives the "
                                    f"{p.typ} pivot peaking {B.t[p.peak]} @ {p.level}")
        elif kind == "lost_competition":
            if e["ref_id"] != x["winner"]:
                self.add(sid, rule, f"lost_competition at {B.t[x['bar']]} names winner {e['ref_id']}, R16 gives "
                                    f"setup {x['winner']}")
        elif kind == "cancelled_opposing_structure":
            ok = [(self._xkey(h2), B.t[l1.peak]) for h2, l1 in x["ok"]]
            if (self._piv_key(e["ref_id"]), e["ref_time"]) not in ok:
                self.add(sid, rule, f"cancellation references pivot {e['ref_id']} / broken level peaking "
                                    f"{e['ref_time']}, R22 gives {ok}")

    def _fvg_in_move(self, s, e):
        """R12 on a logged entry FVG: candle 1 at/after the origin, middle candle at/before the HH bar."""
        B = self.B
        if e.get("bar") is None:
            return
        self.chk("fvg_r12")
        scs = [x for x in s.exp if x["kind"] == "sc_hh" and x["bar"] < e["bar"]]
        c1 = B.i(e["ref_time"])
        if c1 is None:
            self.add(s.sid, "fvg_r12", f"fvg_fixed ref_time {e['ref_time']} is not a logged M1 bar")
            return
        if not B.is_fvg(c1, s.sign):
            self.add(s.sid, "fvg_r12", f"bars {B.t[c1]}.. do not form a {'bullish' if s.sign > 0 else 'bearish'} FVG")
        if not scs:
            self.add(s.sid, "fvg_r12", "fvg_not_in_move: no structure change precedes the logged entry FVG")
            return
        sc = scs[-1]
        if c1 < sc["origin"] or c1 + 1 > sc["bar"]:
            self.add(s.sid, "fvg_r12", f"fvg_not_in_move: FVG candle 1 {B.t[c1]} / middle {B.t[min(c1 + 1, B.n - 1)]}"
                                       f" outside origin {B.t[sc['origin']]} .. HH bar {B.t[sc['bar']]}")

    def _pivot_use(self, s, e):
        """R9: an event may use a pivot only once its confirmation bar p+N has closed."""
        B = self.B
        p = self.logged_piv.get(e["ref_id"])
        if p is None:
            self.add(s.sid, "fields", f"{e['kind']} ref_id {e['ref_id']} is not in rl_pivots")
            return
        ev_bar, peak = B.i(e["bar_time"]), B.i(p["peak_time"])
        if ev_bar is None or peak is None:
            return
        self.chk("pivot_causality_r9")
        if peak + self.N > ev_bar:
            self.add(s.sid, "pivot_causality_r9", f"{e['kind']} at {B.t[ev_bar]} uses pivot {e['ref_id']} whose "
                                                  f"confirmation bar (peak + {self.N}) closes later")
        elif p["conf_time"] is not None and p["conf_time"] > e["bar_time"]:
            self.add(s.sid, "pivot_causality_r9", f"{e['kind']} at {e['bar_time']} uses pivot {e['ref_id']} logged "
                                                  f"as confirmed at {p['conf_time']}")

    def _unconsumed(self):
        B = self.B
        sims = {s.sid: s for s in self.sims}
        for sid, lst in self.ev_by.items():
            s = sims.get(sid)
            for e in lst:
                if e["kind"] not in DECISION_KINDS or e["used"]:
                    continue
                ms = e["tick_msc"]
                if s is not None and s.fill_ms is not None and ms is not None and ms > s.fill_ms:
                    self.add(sid, "one_trade_r17", f"{e['kind']} at {ms} after the setup's fill")
                elif s is not None and ms is not None and (B.bar_at_ms(ms) or 0) - 1 in s.lost_at:
                    self.add(sid, "competition_r16", f"{e['kind']} at {ms} for a setup that lost R16 on that candle")
                else:
                    self.add(sid, "entry_r15", f"{e['kind']} at {ms} follows no qualifying reaction candle won under "
                                               "R16")


# --- public API ------------------------------------------------------------------------------------------------
def check(setups: pd.DataFrame, events: pd.DataFrame, pivots: pd.DataFrame, bars_m1: pd.DataFrame,
          bars_m5: pd.DataFrame, params: dict | None = None, deals: pd.DataFrame | None = None,
          trail: pd.DataFrame | None = None, moves: pd.DataFrame | None = None,
          trailing=False) -> list[dict]:
    """Violations of R3-R22 (and trail_r23 for a trailed run) re-derived from the logged bars, one dict
    {setup_id, rule, detail} per failed check. ``trailing`` is the run's EnableTrailingStop (parse_trailing)."""
    return _Replay(setups, events, pivots, bars_m1, bars_m5, params, deals, trail, moves, trailing).run().out


def occurrences(setups: pd.DataFrame, events: pd.DataFrame, pivots: pd.DataFrame, bars_m1: pd.DataFrame,
                bars_m5: pd.DataFrame, params: dict | None = None,
                deals: pd.DataFrame | None = None) -> dict[str, int]:
    """How often the replay exercised each behavioural case (expected trajectory, not the EA's claims)."""
    return {k: int(v) for k, v in _Replay(setups, events, pivots, bars_m1, bars_m5, params, deals).run().occ.items()}


def full(setups: pd.DataFrame, events: pd.DataFrame, pivots: pd.DataFrame, bars_m1: pd.DataFrame,
         bars_m5: pd.DataFrame, params: dict | None = None, deals: pd.DataFrame | None = None,
         trail: pd.DataFrame | None = None, moves: pd.DataFrame | None = None, trailing=False) -> dict:
    """One replay: violations, occurrences and coverage (checked / failed / passed per rule, plus the cases the
    logs cannot prove, which are not evidence of conformance). ``trailing`` is the run's EnableTrailingStop."""
    r = _Replay(setups, events, pivots, bars_m1, bars_m5, params, deals, trail, moves, trailing).run()
    return {"violations": r.out, "occurrences": {k: int(v) for k, v in r.occ.items()}, "coverage": r.coverage()}
