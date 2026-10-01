"""Rule-conformance checker for the OB-FVG retest EA research output (plan U5, R4-R16, R36-R38, KTD2/KTD3/KTD7).

Every check recomputes the rule from the closed signal-timeframe bars in ``rl_bars``; the EA's own stage fields are
only the claims under test. Times follow the interface contract: bar times are bar OPEN times in epoch seconds, a
bar opened at T closes at T + period, and tick-event times (placement, fill, exit, reason) are epoch milliseconds.
A tick at time ms belongs to the bar with T*1000 <= ms < (T + period)*1000.

Window convention (KTD2): windows count k = 1 from the first bar after the reference bar and an event is valid
while k <= N. The OB look-back (R5, KTD3) inspects ImpulseWindowBars bars counted from the identifying FVG's
candle 1 inclusive (candle 1 is bar 1 of the look-back).

Volume filter (AMENDMENT A1, R41; AMENDMENT B): an identifying FVG qualifies only when its middle candle m has
tick_volume[m] / mean(tick_volume[m-N .. m-1]) >= VolumeMultiplier, N = VolumeLookbackHours*60 / period minutes,
counted in bars, and the OB search considers only such FVGs. The confirmation FVG has NO volume requirement: any FVG
(R7) after the touch confirms, and its logged cfvg_vol_ratio is informational (only checked for consistency with the
recomputed ratio, never against the threshold). Ratios are recomputed from rl_bars only when all N prior bars are
logged (warm-up history is not); otherwise the logged ratio is the only evidence. Bars without tick_volume
(pre-amendment runs) carry no volume evidence, so the volume rules are not applied to them. Market closed at placement (A2, R42): a placement may follow a
TRADE_RETCODE_MARKET_CLOSED refusal (market_closed_first_msc) on a later tick inside the order window.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

SETUP_COLUMNS = [
    "setup_id", "dir", "ob_mode", "entry_mode", "ob_time", "ob_high", "ob_low", "idfvg_c1_time", "idfvg_c3_time",
    "idfvg_low", "idfvg_high", "bos_pivot_time", "bos_pivot_conf_time", "bos_level", "bos_break_time",
    "activation_time", "touch_time", "cfvg_c1_time", "cfvg_c3_time", "cfvg_low", "cfvg_high", "entry", "sl", "tp",
    "volume", "stops_level_pts", "place_time_msc", "order_ticket", "fill_time_msc", "fill_price", "position_id",
    "exit_time_msc", "exit_price", "exit_kind", "reason", "reason_time_msc", "retest_seen_no_fill",
    "idfvg_vol_ratio", "cfvg_vol_ratio", "market_closed_first_msc", "place_attempts",  # AMENDMENT A3
]
INT_COLUMNS = [
    "setup_id", "ob_mode", "entry_mode", "ob_time", "idfvg_c1_time", "idfvg_c3_time", "bos_pivot_time",
    "bos_pivot_conf_time", "bos_break_time", "activation_time", "touch_time", "cfvg_c1_time", "cfvg_c3_time",
    "stops_level_pts", "place_time_msc", "order_ticket", "fill_time_msc", "position_id", "exit_time_msc",
    "reason_time_msc", "retest_seen_no_fill", "market_closed_first_msc", "place_attempts",
]
STR_COLUMNS = ["dir", "exit_kind", "reason"]
FLOAT_COLUMNS = [c for c in SETUP_COLUMNS if c not in INT_COLUMNS and c not in STR_COLUMNS]

EARLY_REASONS = {"expired_untouched", "invalidated_active", "invalidated_touched", "cancelled_no_fvg"}
SKIP_REASONS = {"skipped_price_past", "skipped_too_close", "skipped_sl_stops", "skipped_volume", "skipped_margin",
                "skipped_cap", "skipped_duplicate", "skipped_market_closed", "skipped_broker_reject"}
PENDING_REASONS = {"expired_unfilled", "invalidated_pending", "run_end_pending"}
FILL_REASONS = {"filled", "filled_late"}
REASONS = EARLY_REASONS | SKIP_REASONS | PENDING_REASONS | FILL_REASONS | {"invalidated_confirmed"}

OCCURRENCE_BY_REASON = {"cap_skip": "skipped_cap", "too_close_skip": "skipped_too_close",
                        "margin_skip": "skipped_margin", "volume_skip": "skipped_volume",
                        "duplicate_skip": "skipped_duplicate", "filled_late": "filled_late",
                        "invalidated_pending": "invalidated_pending",
                        "skipped_market_closed": "skipped_market_closed",
                        "skipped_broker_reject": "skipped_broker_reject"}
DEFAULT_PARAMS = dict(ObMode=0, EntryMode=0, ImpulseWindowBars=2, BosWindowBars=6, SwingStrength=3, ObMaxAgeBars=96,
                      FvgWindowBars=12, OrderExpiryBars=12, StopBufferPoints=10, RiskRR=2.0, point=0.01,
                      VolumeMultiplier=2.0, VolumeLookbackHours=24)
VOL_RATIO_RTOL = 1e-3      # logged vs recomputed ratio, relative
VOL_RATIO_ROUND = 0.5e-4   # the EA logs ratios with 4 decimals


# --- readers ---------------------------------------------------------------------------------------------------
def read_setups(path) -> pd.DataFrame:
    """rl_setups CSV: empty fields -> missing (NaN / <NA>), times and ids as nullable Int64."""
    df = pd.read_csv(path, dtype={**{c: "Int64" for c in INT_COLUMNS}, **{c: object for c in STR_COLUMNS}},
                     keep_default_na=False, na_values=[""])
    for c in FLOAT_COLUMNS:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def read_bars(path) -> pd.DataFrame:
    """rl_bars CSV: time (bar open, epoch seconds, int64), open, high, low, close, tick_volume (float; NaN for
    pre-amendment files without the column)."""
    df = pd.read_csv(path)
    df["time"] = df["time"].astype("int64")
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    df["tick_volume"] = pd.to_numeric(df["tick_volume"], errors="coerce").astype(float) if "tick_volume" in df \
        else np.nan
    return df.sort_values("time", kind="mergesort").reset_index(drop=True)


# --- helpers ---------------------------------------------------------------------------------------------------
def _missing(v) -> bool:
    if v is None or v is pd.NA:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _int(row, k):
    v = row.get(k)
    return None if _missing(v) or (isinstance(v, str) and v == "") else int(v)


def _flt(row, k):
    v = row.get(k)
    return None if _missing(v) or (isinstance(v, str) and v == "") else float(v)


def _str(row, k):
    v = row.get(k)
    return None if _missing(v) or v == "" else str(v)


class _Bars:
    def __init__(self, bars: pd.DataFrame, period: int):
        b = bars.sort_values("time", kind="mergesort")
        self.t = b["time"].astype("int64").to_numpy()
        self.o, self.h, self.l, self.c = (b[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))
        self.n = len(self.t)
        self.v = (pd.to_numeric(b["tick_volume"], errors="coerce").astype(float).to_numpy() if "tick_volume" in b
                  else np.full(self.n, np.nan))
        self.has_vol = bool(np.isfinite(self.v).any())
        self.P = int(period)
        self._idx = {int(x): i for i, x in enumerate(self.t)}

    def i(self, time):
        return None if time is None else self._idx.get(int(time))

    def warm(self, time) -> bool:
        """True when a bar time precedes the logged bars (warm-up history is not written to rl_bars)."""
        return self.n == 0 or time < self.t[0]

    def close_ms(self, i) -> int:
        return (int(self.t[i]) + self.P) * 1000

    def bar_at_ms(self, ms):
        j = int(np.searchsorted(self.t * 1000, ms, side="right")) - 1
        return j if 0 <= j < self.n and ms < self.close_ms(j) else None

    def is_fvg(self, c1, sign) -> bool:
        c3 = c1 + 2
        if c1 < 0 or c3 >= self.n:
            return False
        return self.l[c3] > self.h[c1] if sign > 0 else self.h[c3] < self.l[c1]

    def fvg_zone(self, c1, sign):
        c3 = c1 + 2
        return (self.h[c1], self.l[c3]) if sign > 0 else (self.h[c3], self.l[c1])

    def beyond(self, i, sign, ob_low, ob_high) -> bool:
        return self.c[i] < ob_low if sign > 0 else self.c[i] > ob_high

    def reaches(self, i, sign, level) -> bool:
        return self.l[i] <= level if sign > 0 else self.h[i] >= level

    def opposite(self, i, sign) -> bool:
        """The OB candle colour: bearish (close < open) for longs, bullish for shorts. A doji is neither."""
        return self.c[i] < self.o[i] if sign > 0 else self.c[i] > self.o[i]

    def vol_detail(self, m, N):
        """(tick volume of m, mean of the N bars before m, ratio), or None when not all N prior bars are logged."""
        if m is None or N <= 0 or m - N < 0 or m >= self.n:
            return None
        w = self.v[m - N:m]
        if not (np.isfinite(w).all() and np.isfinite(self.v[m])):
            return None
        avg = float(w.mean())
        if avg <= 0:
            return None
        return float(self.v[m]), avg, float(self.v[m]) / avg

    def vol_ratio(self, m, N):
        d = self.vol_detail(m, N)
        return None if d is None else d[2]

    def vol_pass(self, m, N, k):
        """True / False when recomputable; True without any volume evidence (pre-amendment bars); else None."""
        if not self.has_vol:
            return True
        r = self.vol_ratio(m, N)
        return None if r is None else r >= k


def vol_lookback_bars(period_seconds: int, lookback_hours) -> int:
    """N = VolumeLookbackHours*60 / period minutes (96 on M15, 288 on M5 for 24 h)."""
    return int(round(float(lookback_hours) * 3600 / int(period_seconds)))


def _vol_n(prm, P):
    return vol_lookback_bars(P, prm["VolumeLookbackHours"])


def fvg_volume(bars: pd.DataFrame, c1_time, period_seconds: int, lookback_hours=24):
    """Middle-candle volume facts of the FVG whose candle 1 opens at c1_time: dict(middle_time, tick_volume,
    average, ratio, n) with average/ratio None when the N prior bars are not all in ``bars``; None if c1 is unknown."""
    B = bars if isinstance(bars, _Bars) else _Bars(bars, period_seconds)
    c1 = B.i(c1_time)
    if c1 is None or c1 + 1 >= B.n:
        return None
    m, N = c1 + 1, vol_lookback_bars(period_seconds, lookback_hours)
    d = B.vol_detail(m, N)
    vol = float(B.v[m]) if np.isfinite(B.v[m]) else None
    return {"middle_time": int(B.t[m]), "tick_volume": vol, "average": None if d is None else d[1],
            "ratio": None if d is None else d[2], "n": N}


def _ob_search(B: _Bars, c1: int, sign: int, window: int):
    for k in range(int(window)):
        j = c1 - k
        if j < 0:
            return None
        if B.opposite(j, sign):
            return j
    return None


def _first_beyond(B, start, stop, sign, ob_low, ob_high):
    """First bar index in [start, stop] (clipped to the bars) that closes beyond the OB edge, else None."""
    for j in range(max(start, 0), min(stop, B.n - 1) + 1):
        if B.beyond(j, sign, ob_low, ob_high):
            return j
    return None


def _excused_fvgs(records):
    """(dir, c1_time, c3_time) -> setup ids confirmed by that FVG. R16: an FVG confirms only one OB."""
    used = {}
    for r in records:
        c1, c3, d = _int(r, "cfvg_c1_time"), _int(r, "cfvg_c3_time"), _str(r, "dir")
        if c1 is not None and c3 is not None and d:
            used.setdefault((d, c1, c3), set()).add(_int(r, "setup_id"))
    return used


def _is_excused(used, d, sid, B, c1):
    ids = used.get((d, int(B.t[c1]), int(B.t[c1 + 2])), set())
    return bool(ids - {sid})


def _simulate(B, sign, d, sid, ob_low, ob_high, act, prm, used):
    """Independent replay of the pre-placement stages (KTD2 order: close-beyond check before touch/confirmation).
    Any FVG (R7) confirms; the confirmation FVG has no volume requirement (AMENDMENT B)."""
    edge = ob_high if sign > 0 else ob_low
    tch = None
    for k in range(1, int(prm["ObMaxAgeBars"]) + 1):
        j = act + k
        if j >= B.n:
            return "incomplete", None, None
        if B.beyond(j, sign, ob_low, ob_high):
            return "invalidated_active", None, j
        if B.reaches(j, sign, edge):
            tch = j
            break
    if tch is None:
        return "expired_untouched", None, act + int(prm["ObMaxAgeBars"])
    for k in range(1, int(prm["FvgWindowBars"]) + 1):
        j = tch + k
        if j >= B.n:
            return "incomplete", tch, None
        if B.beyond(j, sign, ob_low, ob_high):
            return "invalidated_touched", tch, j
        if k >= 2 and B.is_fvg(j - 2, sign) and not _is_excused(used, d, sid, B, j - 2):
            return "confirmed", tch, j
    return "cancelled_no_fvg", tch, tch + int(prm["FvgWindowBars"])


def _stage_rank(reason):
    if reason in {"expired_untouched", "invalidated_active"}:
        return 1
    if reason in {"invalidated_touched", "cancelled_no_fvg"}:
        return 2
    if reason in SKIP_REASONS or reason == "invalidated_confirmed":
        return 3
    if reason in PENDING_REASONS:
        return 4
    if reason in FILL_REASONS:
        return 5
    return None


# --- checker ---------------------------------------------------------------------------------------------------
def check(setups: pd.DataFrame, bars: pd.DataFrame, period_seconds: int, params: dict) -> list[dict]:
    """Violations of the strategy rules, one dict {setup_id, rule, detail} per failed check."""
    prm = {**DEFAULT_PARAMS, **(params or {})}
    B = _Bars(bars, period_seconds)
    tol = 0.5 * float(prm["point"]) + 1e-9
    records = setups.to_dict("records")
    used = _excused_fvgs(records)
    ob_count = {}
    for r in records:
        key = (_str(r, "dir"), _int(r, "ob_time"))
        ob_count[key] = ob_count.get(key, 0) + 1

    out: list[dict] = []
    for r in records:
        sid = _int(r, "setup_id")

        def add(rule, detail, _sid=sid):
            out.append({"setup_id": _sid, "rule": rule, "detail": detail})

        _check_row(r, sid, B, prm, tol, used, ob_count, add)
    return out


def _check_row(r, sid, B, prm, tol, used, ob_count, add):
    d = _str(r, "dir")
    if d not in ("L", "S"):
        add("dir", f"dir={d!r}")
        return
    sign = 1 if d == "L" else -1
    P = B.P
    eq = lambda a, b: a is not None and b is not None and abs(a - b) <= tol  # noqa: E731

    def idx(field, required=True):
        """Bar index of a bar-time field; flags a time inside the logged range that has no bar."""
        tm = _int(r, field)
        if tm is None:
            return None
        i = B.i(tm)
        if i is None and required and not B.warm(tm):
            add("bars_missing", f"{field}={tm} is not a logged bar open time")
        return i

    ob_mode = prm.get("ObMode", _int(r, "ob_mode"))
    entry_mode = prm.get("EntryMode", _int(r, "entry_mode"))
    if _int(r, "ob_mode") is not None and _int(r, "ob_mode") != int(ob_mode):
        add("ob_mode_column", f"row ob_mode={_int(r, 'ob_mode')} but run ObMode={ob_mode}")
    if _int(r, "entry_mode") is not None and _int(r, "entry_mode") != int(entry_mode):
        add("entry_mode_column", f"row entry_mode={_int(r, 'entry_mode')} but run EntryMode={entry_mode}")

    ob_low, ob_high = _flt(r, "ob_low"), _flt(r, "ob_high")
    ob_i, c1, c3 = idx("ob_time"), idx("idfvg_c1_time"), idx("idfvg_c3_time")
    act = idx("activation_time")
    if ob_count.get((d, _int(r, "ob_time")), 0) > 1:
        add("ob_duplicate", f"OB candle {_int(r, 'ob_time')} produced more than one {d} setup (R12)")

    # identifying FVG (R7) and OB candle (R5, KTD3)
    if c1 is not None:
        if c3 is None or c3 != c1 + 2:
            add("idfvg_bars", "identifying FVG candle 3 is not two bars after candle 1")
        elif not B.is_fvg(c1, sign):
            add("idfvg_r7", f"bars {B.t[c1]}..{B.t[c3]} do not form a {'bullish' if sign > 0 else 'bearish'} FVG")
        else:
            lo, hi = B.fvg_zone(c1, sign)
            if not (eq(_flt(r, "idfvg_low"), lo) and eq(_flt(r, "idfvg_high"), hi)):
                add("idfvg_zone", f"row {_flt(r, 'idfvg_low')}-{_flt(r, 'idfvg_high')} vs bars {lo:.5f}-{hi:.5f}")
        expected = _ob_search(B, c1, sign, prm["ImpulseWindowBars"])
        if expected is None or expected != ob_i:
            exp_t = None if expected is None else int(B.t[expected])
            add("ob_candle", f"most recent opposite candle within {prm['ImpulseWindowBars']} bars from candle 1 "
                             f"is {exp_t}, row ob_time={_int(r, 'ob_time')}")
    if ob_i is not None and not (eq(ob_high, B.h[ob_i]) and eq(ob_low, B.l[ob_i])):
        add("ob_range", f"row {ob_low}-{ob_high} vs candle {B.l[ob_i]}-{B.h[ob_i]}")
    if _int(r, "idfvg_c1_time") is not None:
        _check_volume(r, B, prm, "idfvg_vol_ratio", None if c1 is None else c1 + 1, "idfvg_volume", add)

    # activation and FVG+BOS qualification (R6, KTD3)
    id_c3_t, act_t = _int(r, "idfvg_c3_time"), _int(r, "activation_time")
    if act_t is None:
        add("stage_fields", "activation_time missing")
        return
    if id_c3_t is not None and act_t < id_c3_t:
        add("activation_before_idfvg", f"activation {act_t} < identifying FVG c3 {id_c3_t}")
    if int(ob_mode) == 1:
        _check_bos(r, B, prm, sign, ob_i, id_c3_t, act_t, tol, add, idx)
    elif id_c3_t is not None and act_t != id_c3_t:
        add("activation_time", f"FVG mode activates at identifying c3 {id_c3_t}, row {act_t}")
    if c3 is not None and act is not None and ob_low is not None:
        fb = _first_beyond(B, c3 + 1, act - 1, sign, ob_low, ob_high)
        if fb is not None:
            add("close_beyond_before_activation", f"bar {B.t[fb]} closed beyond the OB before activation")
    if act is None or ob_low is None or ob_high is None:
        return

    # touch (R8)
    tch = idx("touch_time")
    edge = ob_high if sign > 0 else ob_low
    if tch is not None:
        if tch <= act:
            add("touch_not_after_activation", f"touch bar {B.t[tch]} is not after activation bar {B.t[act]}")
        elif tch - act > int(prm["ObMaxAgeBars"]):
            add("touch_window", f"touch k={tch - act} > ObMaxAgeBars={prm['ObMaxAgeBars']}")
        if not B.reaches(tch, sign, edge):
            add("touch_price", f"touch bar does not reach the OB edge {edge}")
        early = [j for j in range(act + 1, tch) if B.reaches(j, sign, edge)]
        if early:
            add("touch_not_first", f"bar {B.t[early[0]]} already reached the OB edge")

    # confirmation FVG (R9, R36)
    cc1, cc3 = idx("cfvg_c1_time"), idx("cfvg_c3_time")
    if _int(r, "cfvg_c1_time") is not None and _int(r, "touch_time") is None:
        add("stage_order", "confirmation FVG without a touch")
    if cc1 is not None and tch is not None:
        if _int(r, "cfvg_c1_time") == _int(r, "idfvg_c1_time"):
            add("cfvg_is_idfvg", "the identifying FVG was used as the confirmation FVG (R36)")
        if cc1 < tch:
            add("cfvg_c1_before_touch", f"confirmation c1 {B.t[cc1]} precedes touch {B.t[tch]} (R9, AE1)")
        if cc3 is None or cc3 != cc1 + 2:
            add("cfvg_bars", "confirmation FVG candle 3 is not two bars after candle 1")
        else:
            if cc3 - tch > int(prm["FvgWindowBars"]):
                add("cfvg_window", f"confirmation c3 k={cc3 - tch} > FvgWindowBars={prm['FvgWindowBars']}")
            _check_volume(r, B, prm, "cfvg_vol_ratio", cc1 + 1, None, add)   # informational (AMENDMENT B)
            if not B.is_fvg(cc1, sign):
                add("cfvg_r7", f"bars {B.t[cc1]}..{B.t[cc3]} do not form a qualifying FVG")
            else:
                lo, hi = B.fvg_zone(cc1, sign)
                if not (eq(_flt(r, "cfvg_low"), lo) and eq(_flt(r, "cfvg_high"), hi)):
                    add("cfvg_zone", f"row {_flt(r, 'cfvg_low')}-{_flt(r, 'cfvg_high')} vs bars {lo:.5f}-{hi:.5f}")
            for j in range(tch, cc1):   # every FVG counts, whatever its volume (AMENDMENT B)
                if (j + 2 - tch <= int(prm["FvgWindowBars"]) and j + 2 - tch >= 2 and B.is_fvg(j, sign)
                        and not _is_excused(used, d, _int(r, "setup_id"), B, j)):
                    add("cfvg_not_first", f"FVG {B.t[j]}..{B.t[j + 2]} qualified earlier")
                    break

    # stage ordering in milliseconds
    place, fill, exit_ = _int(r, "place_time_msc"), _int(r, "fill_time_msc"), _int(r, "exit_time_msc")
    cc3_t = _int(r, "cfvg_c3_time")
    mc = _int(r, "market_closed_first_msc")
    n_exp = int(prm["OrderExpiryBars"])
    if place is not None:
        if cc3_t is None:
            add("stage_order", "placement without a confirmation FVG")
        elif place < (cc3_t + P) * 1000:
            add("place_before_c3_close", f"place {place} < c3 close {(cc3_t + P) * 1000}")
        if cc3 is not None and mc is None and cc3 + 1 < B.n and place >= B.close_ms(cc3 + 1):
            add("place_not_first_bar", f"place {place} after the first bar after c3 without a market-closed refusal")
        if cc3 is not None and mc is not None and cc3 + n_exp < B.n and place >= B.close_ms(cc3 + n_exp):
            add("place_after_retry_window", f"retry placement {place} after {n_exp} closed bars since c3 (R42)")
        if mc is not None and place <= mc:
            add("place_before_market_closed", f"place {place} <= first market-closed refusal {mc} (R42)")
    if mc is not None:
        if cc3_t is None:
            add("stage_order", "market-closed refusal without a confirmation FVG")
        elif mc < (cc3_t + P) * 1000:
            add("market_closed_before_c3_close", f"refusal {mc} < c3 close {(cc3_t + P) * 1000}")
    attempts = _int(r, "place_attempts")
    if attempts is not None:
        if (place is not None or mc is not None) and attempts < 1:
            add("place_attempts", f"place_attempts={attempts} with a placement or refusal")
        elif place is not None and mc is not None and attempts < 2:
            add("place_attempts", f"place_attempts={attempts} for a placement after a market-closed refusal")
    if fill is not None:
        if place is None:
            add("stage_order", "fill without a placement")
        elif fill <= place:
            add("fill_before_place", f"fill {fill} <= place {place}")
    if exit_ is not None and fill is not None and exit_ < fill:
        add("exit_before_fill", f"exit {exit_} < fill {fill}")

    # no pre-fill stage after a close beyond the OB edge (R11)
    fb = _first_beyond(B, act + 1, B.n - 1, sign, ob_low, ob_high)
    if fb is not None:
        stages = []
        if tch is not None:
            stages.append(("touch", B.close_ms(tch)))
        if cc3 is not None:
            stages.append(("confirmation", B.close_ms(cc3)))
        if place is not None:
            stages.append(("placement", place))
        for name, deadline in stages:
            if B.close_ms(fb) <= deadline:
                add("close_beyond_before_stage", f"bar {B.t[fb]} closed beyond the OB before the {name}")
                break

    # prices (R10, R13)
    entry, sl, tp = _flt(r, "entry"), _flt(r, "sl"), _flt(r, "tp")
    if entry is not None:
        cl, ch = _flt(r, "cfvg_low"), _flt(r, "cfvg_high")
        m = int(entry_mode)
        exp = None
        if m == 0 and cl is not None and ch is not None:
            exp = ch if sign > 0 else cl
        elif m == 1 and cl is not None and ch is not None:
            exp = (cl + ch) / 2
        elif m == 2:
            exp = ob_high if sign > 0 else ob_low
        elif m == 3:
            exp = (ob_low + ob_high) / 2
        if exp is not None and not eq(entry, exp):
            add("entry", f"EntryMode {m} level {exp:.5f}, row entry {entry}")
        buf = float(prm["StopBufferPoints"]) * float(prm["point"])
        exp_sl = ob_low - buf if sign > 0 else ob_high + buf
        if sl is None or not eq(sl, exp_sl):
            add("sl", f"expected {exp_sl:.5f}, row {sl}")
        if sl is not None:
            exp_tp = entry + sign * float(prm["RiskRR"]) * abs(entry - sl)
            if tp is None or not eq(tp, exp_tp):
                add("tp", f"expected {exp_tp:.5f} ({prm['RiskRR']}R from entry {entry}), row {tp}")

    # reason codes (KTD7)
    reason = _str(r, "reason")
    if reason not in REASONS:
        add("reason_unknown", f"reason={reason!r}")
        return
    rank = _stage_rank(reason)
    if reason == "run_end_pending":         # the run ended at whatever pre-fill stage the setup had reached
        rank = (4 if not _missing(r.get("order_ticket")) else 3 if not _missing(r.get("cfvg_c3_time"))
                else 2 if not _missing(r.get("touch_time")) else 1)
    need = {2: ["touch_time"], 3: ["touch_time", "cfvg_c1_time", "cfvg_c3_time", "entry", "sl", "tp"],
            4: ["order_ticket", "place_time_msc"], 5: ["order_ticket", "place_time_msc", "fill_time_msc",
                                                         "fill_price", "position_id"]}
    for k in range(2, rank + 1):
        for f in need[k]:
            if _missing(r.get(f)):
                add("reason_fields", f"{reason} requires {f}")
    banned = {1: ["touch_time"], 2: ["cfvg_c1_time"], 3: ["order_ticket", "fill_time_msc"], 4: ["fill_time_msc"]}
    for f in banned.get(rank, []):
        if not _missing(r.get(f)):
            add("reason_fields", f"{reason} must not have {f}")
    if reason == "skipped_market_closed" and mc is None:
        add("reason_fields", f"{reason} requires market_closed_first_msc")

    kind, sim_tch, _ = _simulate(B, sign, d, _int(r, "setup_id"), ob_low, ob_high, act, prm, used)
    if kind != "incomplete":
        if reason in EARLY_REASONS and kind != reason:
            add("reason_inconsistent", f"row {reason}, bars give {kind}")
        if reason not in EARLY_REASONS and kind != "confirmed" and not (reason == "run_end_pending" and rank < 3):
            add("reason_inconsistent", f"row {reason} needs a confirmation, bars give {kind}")

    if cc3 is None or rank is None or rank < 4:
        return
    beyond_p = _first_beyond(B, cc3 + 1, cc3 + n_exp, sign, ob_low, ob_high)
    window_done = cc3 + n_exp < B.n
    trig = beyond_p if beyond_p is not None else (cc3 + n_exp if window_done else None)
    trig_ms = B.close_ms(trig) if trig is not None else math.inf
    if reason == "expired_unfilled" and (beyond_p is not None or not window_done):
        add("reason_inconsistent", "expired_unfilled but " + ("a bar closed beyond the OB" if beyond_p is not None
                                                             else "the order window did not elapse"))
    if reason == "invalidated_pending" and beyond_p is None:
        add("reason_inconsistent", "invalidated_pending without a close beyond the OB in the order window")
    if reason == "run_end_pending" and trig is not None:
        add("reason_inconsistent", "run_end_pending but the order window resolved inside the logged bars")
    if reason in PENDING_REASONS and entry is not None:
        last = min(trig if trig is not None else B.n - 1, B.n - 1)
        pb = B.bar_at_ms(place) if place is not None else None      # a market-closed retry starts later (R42)
        seen = any(B.reaches(j, sign, entry) for j in range(max(cc3 + 1, pb or 0), last + 1))
        flag = _int(r, "retest_seen_no_fill")
        if flag is not None and bool(flag) != seen:
            add("retest_flag", f"retest_seen_no_fill={flag} but bars {'reached' if seen else 'did not reach'} entry")

    if reason in FILL_REASONS and fill is not None:
        fp = _flt(r, "fill_price")
        if fp is not None and entry is not None and sign * (fp - entry) > tol:
            add("fill_price", f"fill {fp} worse than limit {entry}")
        fbar = B.bar_at_ms(fill)
        if fbar is not None and entry is not None and not B.reaches(fbar, sign, entry):
            add("fill_bar_not_reaching", f"fill bar {B.t[fbar]} never traded at the entry {entry}")
        if reason == "filled" and fill >= trig_ms:
            add("fill_after_cancel", f"fill {fill} at/after the cancellation trigger {trig_ms}; filled_late expected")
        if reason == "filled_late":
            if fill < trig_ms:
                add("filled_late_not_late", f"fill {fill} before the cancellation trigger {trig_ms}")
            elif trig is not None and trig + 1 < B.n and fill >= B.close_ms(trig + 1):
                add("fill_after_cancel", f"fill {fill} after the bar that executed the cancellation")


def _check_volume(r, B, prm, field, m, rule, add):
    """R41 on one FVG. With a ``rule`` (the identifying FVG) the recomputed (when all N prior bars are logged) and the
    logged middle-candle ratio must be >= VolumeMultiplier. Without one (the confirmation FVG, AMENDMENT B) the ratio
    is informational: no threshold, and an empty value is flagged only when the ratio is recomputable. Either way a
    logged ratio must agree with the recomputed one."""
    k = float(prm["VolumeMultiplier"])
    rec = B.vol_ratio(m, _vol_n(prm, B.P)) if m is not None else None
    logged = _flt(r, field)
    if rule is not None:
        if rec is not None and rec < k:
            add(rule, f"middle-candle tick volume ratio {rec:.4f} < VolumeMultiplier {k:g} (recomputed from rl_bars)")
        elif logged is not None and logged < k:
            add(rule, f"logged {field}={logged} < VolumeMultiplier {k:g}")
    if rec is not None and logged is not None and abs(logged - rec) > max(VOL_RATIO_RTOL * abs(rec), VOL_RATIO_ROUND):
        add("vol_ratio_mismatch", f"logged {field}={logged} vs recomputed {rec:.4f}")
    if logged is None and B.has_vol and field in r and (rule is not None or rec is not None):
        add("vol_ratio_missing", f"{field} empty for a reached FVG")


def _check_bos(r, B, prm, sign, ob_i, id_c3_t, act_t, tol, add, idx):
    fields = ["bos_pivot_time", "bos_pivot_conf_time", "bos_level", "bos_break_time"]
    if any(_missing(r.get(f)) for f in fields):
        add("bos_fields_missing", "FVG+BOS mode setup without pivot/break fields")
        return
    brk_t = _int(r, "bos_break_time")
    if id_c3_t is not None and act_t != max(id_c3_t, brk_t):
        add("activation_time", f"FVG+BOS activates at max(c3, break) = {max(id_c3_t, brk_t)}, row {act_t}")
    p, cf_, br = idx("bos_pivot_time"), idx("bos_pivot_conf_time"), idx("bos_break_time")
    level, S = _flt(r, "bos_level"), int(prm["SwingStrength"])
    if p is None or br is None:
        return
    if ob_i is not None and p >= ob_i:
        add("bos_pivot_not_before_ob", f"pivot peak {B.t[p]} is not before OB candle {B.t[ob_i]} (R6 structure)")
    conf = p + S
    if cf_ is not None and cf_ != conf:
        add("bos_pivot_conf_bar", f"confirmation bar should be peak + {S} bars")
    if conf >= br:
        add("bos_pivot_confirmed_after_break",
            f"pivot confirmation bar {B.t[conf] if conf < B.n else 'beyond bars'} is not before break {B.t[br]} "
            "(R6 look-ahead, AE7)")
    ext = B.h if sign > 0 else B.l
    strict = all(0 <= p + s * j < B.n and sign * (ext[p] - ext[p + s * j]) > 0
                 for j in range(1, S + 1) for s in (-1, 1))
    if not strict:
        add("bos_not_strict_pivot", f"bar {B.t[p]} is not a strict pivot of strength {S}")
    if level is None or abs(level - ext[p]) > tol:
        add("bos_level", f"level {level} vs pivot extreme {ext[p]}")
    if level is not None:
        if not sign * (B.c[br] - level) > 0:
            add("bos_break_close", f"break bar close {B.c[br]} does not close beyond {level}")
        if ob_i is not None and not (0 <= br - ob_i <= int(prm["BosWindowBars"])):
            add("bos_window", f"break k={br - ob_i} outside 0..BosWindowBars={prm['BosWindowBars']}")
        if ob_i is not None:
            for j in range(max(ob_i, conf + 1), br):
                if sign * (B.c[j] - level) > 0:
                    add("bos_break_not_first", f"bar {B.t[j]} already closed beyond {level}")
                    break


# --- behavioural-case counts (U5 fidelity) ---------------------------------------------------------------------
def occurrences(setups: pd.DataFrame, bars: pd.DataFrame, period_seconds: int, params: dict | None = None
                ) -> dict[str, int]:
    """How many setups exercised each behavioural case handed over from U2/U3 (contract keys first, then extras)."""
    prm = {**DEFAULT_PARAMS, **(params or {})}
    B = _Bars(bars, period_seconds)
    keys = ["touch_bar_close_beyond", "cfvg_c1_is_touch_bar", "idfvg_c1_is_ob", "cap_skip", "too_close_skip",
            "margin_skip", "volume_skip", "duplicate_skip", "filled_late", "invalidated_pending",
            "fill_then_close_beyond_same_bar", "long", "short", "bos_mode",
            "market_closed_retry_placed", "idfvg_volume_checked", "cfvg_volume_checked",
            "activation_bar_touch_ignored", "ob_search_skipped_doji", "duplicate_ob_candle"]
    keys += [k for k in OCCURRENCE_BY_REASON if k not in keys]
    N, k_vol = _vol_n(prm, B.P), float(prm["VolumeMultiplier"])
    occ = dict.fromkeys(keys, 0)
    records = setups.to_dict("records")
    used_obs = {}
    for r in records:
        d, reason = _str(r, "dir"), _str(r, "reason")
        sign = 1 if d == "L" else -1
        occ["long"] += d == "L"
        occ["short"] += d == "S"
        occ["bos_mode"] += _int(r, "ob_mode") == 1
        for k, code in OCCURRENCE_BY_REASON.items():
            occ[k] += reason == code
        occ["market_closed_retry_placed"] += (_int(r, "market_closed_first_msc") is not None
                                              and _int(r, "place_time_msc") is not None)
        for c1_field, key in (("idfvg_c1_time", "idfvg_volume_checked"), ("cfvg_c1_time", "cfvg_volume_checked")):
            fi = B.i(_int(r, c1_field))
            occ[key] += fi is not None and B.vol_ratio(fi + 1, N) is not None
        tt, c1t = _int(r, "touch_time"), _int(r, "cfvg_c1_time")
        occ["cfvg_c1_is_touch_bar"] += tt is not None and tt == c1t
        occ["idfvg_c1_is_ob"] += _int(r, "ob_time") is not None and _int(r, "ob_time") == _int(r, "idfvg_c1_time")
        ob_low, ob_high = _flt(r, "ob_low"), _flt(r, "ob_high")
        act, ob_i, c1 = B.i(_int(r, "activation_time")), B.i(_int(r, "ob_time")), B.i(_int(r, "idfvg_c1_time"))
        if ob_i is not None and d in ("L", "S"):
            used_obs[(d, ob_i)] = B.i(_int(r, "idfvg_c3_time"))
        if ob_i is not None and c1 is not None:
            occ["ob_search_skipped_doji"] += any(B.c[j] == B.o[j] for j in range(ob_i + 1, c1 + 1))
        if act is None or ob_low is None or ob_high is None or d not in ("L", "S"):
            continue
        edge = ob_high if sign > 0 else ob_low
        occ["activation_bar_touch_ignored"] += B.reaches(act, sign, edge)
        for j in range(act + 1, B.n):
            if B.reaches(j, sign, edge):
                occ["touch_bar_close_beyond"] += B.beyond(j, sign, ob_low, ob_high)
                break
        fill = _int(r, "fill_time_msc")
        if reason in FILL_REASONS and fill is not None:
            fb = B.bar_at_ms(fill)
            occ["fill_then_close_beyond_same_bar"] += fb is not None and B.beyond(fb, sign, ob_low, ob_high)
    # later identifying FVGs whose look-back lands on an OB candle that already produced a setup (dedup, KTD3)
    for (d, ob_i), id_c3 in used_obs.items():
        sign = 1 if d == "L" else -1
        for c1 in range(ob_i, min(ob_i + int(prm["ImpulseWindowBars"]), B.n - 2)):
            if (id_c3 is None or c1 + 2 > id_c3) and B.is_fvg(c1, sign) and B.vol_pass(c1 + 1, N, k_vol) is True \
                    and _ob_search(B, c1, sign, prm["ImpulseWindowBars"]) == ob_i:
                occ["duplicate_ob_candle"] += 1
    return {k: int(v) for k, v in occ.items()}
