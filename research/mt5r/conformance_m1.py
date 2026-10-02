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
import pathlib
from collections import Counter

import numpy as np
import pandas as pd

from . import m1_contract as mc

M1_S, M5_S = 60, 300
EPS = 1e-6
DEFAULT_PARAMS = dict(StructureVariant=0, ImpulseWindowBars=2, SwingStrengthM1=3, StopBufferPoints=20, RiskRR=2.0,
                      point=0.01)
RULES = ["ob_r3", "touch_r5", "break_r6", "return_r7", "second_break_r8", "pivot_causality_r9", "sc_r10", "hl_r11",
         "fvg_r12", "fvg_lapse_r13", "reaction_r14", "entry_r15", "competition_r16", "one_trade_r17", "sl_r18",
         "tp_r19", "cancel_r22", "tf_sync", "fields"]
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

BAR_INT = ["time", "warmup"]
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
    return out


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
    def __init__(self, setups, events, pivots, bars_m1, bars_m5, params):
        self.prm = {**DEFAULT_PARAMS, **(params or {})}
        self.N = int(self.prm["SwingStrengthM1"])
        self.pt = float(self.prm["point"])
        self.buf = float(self.prm["StopBufferPoints"]) * self.pt
        self.tol = 0.5 * self.pt + 1e-9
        self.variant = int(self.prm["StructureVariant"])
        self.out: list[dict] = []
        self.occ = dict.fromkeys(OCC_KEYS, 0)
        self.frames = dict(setups=setups, events=events, pivots=pivots, bars_m1=bars_m1, bars_m5=bars_m5)

    def add(self, sid, rule, detail):
        self.out.append({"setup_id": sid, "rule": rule, "detail": detail})

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
        self._candidates()
        self._simulate()
        self._check_pivots()
        for s in self.sims:
            self._compare(s)
        self._unconsumed()
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
                s.reason = "run_end_untouched"
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
        if out_i is None:
            if i + 1 < B.n:
                self.add(s.sid, "entry_r15", f"reaction at {B.t[i]} won R16 but no entry decision (fill or skip) "
                                             "is logged after it")
            return
        window = evs[:out_i + 1]
        for e in window:
            e["used"] = True
        first, outcome = window[0], window[-1]
        self.occ["market_closed_retry"] += sum(1 for e in window if e["kind"] == "entry_attempt") > 1
        fb = B.bar_at_ms(first["tick_msc"])
        if i + 1 < B.n and fb != i + 1:
            self.add(s.sid, "entry_r15", f"first decision {first['kind']} at {first['tick_msc']} is not on the first "
                                         f"tick after the reaction close (bar {B.t[i + 1]})")
        kind, anchor = self._anchor(s)
        if fb == i + 1:
            bid = B.o[i + 1]
            crossed = bid <= anchor - self.buf + EPS if s.sign > 0 else bid >= anchor + self.buf - EPS
            if crossed and first["kind"] != "skipped_stop_crossed":
                self.add(s.sid, "entry_r15", f"Bid {bid} at the first tick after the reaction already crosses the "
                                             f"stop (anchor {anchor} {'-' if s.sign > 0 else '+'} buffer): "
                                             f"skipped_stop_crossed expected, logged {first['kind']} (AE7)")
            if not crossed and first["kind"] == "skipped_stop_crossed":
                self.add(s.sid, "entry_r15", f"skipped_stop_crossed but Bid {bid} does not cross the stop")
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
        sl, price = e["lo"], e["price"]
        if sl is None or price is None:
            self.add(s.sid, "fields", "skipped_stop_crossed lacks price or lo=sl")
            return
        if not (price <= sl + self.tol if s.sign > 0 else price >= sl - self.tol):
            self.add(s.sid, "entry_r15", f"skipped_stop_crossed at price {price} but stop {sl} is not crossed")
        if s.sign > 0:
            if abs(sl - (anchor - self.buf)) > self.tol:
                self.add(s.sid, "sl_r18", f"skip SL {sl} != {kind} anchor {anchor} - buffer")
        else:
            spread = sl - anchor - self.buf
            exact = None if bid is None else price - bid
            if spread < -self.tol or (exact is not None and abs(spread - exact) > self.tol):
                self.add(s.sid, "sl_r18", f"skip SL {sl} != {kind} anchor {anchor} + buffer + spread "
                                          f"({'Ask - Bid = ' + format(exact, '.2f') if exact is not None else '>= 0'})")

    def _apply_outcome(self, s: _Sim, i):
        q, s.queued = s.queued, None
        e = q["ev"]
        if e["kind"] != "fill":
            self.occ[e["kind"]] += 1
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
        # R18
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
            proxy = None
            if bar is not None and np.isfinite(self.B.spread[bar]):
                proxy = self.B.spread[bar] * self.pt
            if spread < -tol or (proxy is not None and abs(spread - proxy) > tol):
                self.add(s.sid, "sl_r18", f"SL {sl} != {kind} anchor {anchor} + buffer {self.buf:.2f} + spread "
                                          f"(bar spread proxy {proxy})")
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

    def _piv_key(self, pid):
        p = self.logged_piv.get(pid)
        return None if p is None else (p["typ"], p["peak_time"])

    def _xkey(self, x):
        return (x.typ, self.B.t[x.peak])

    # ------------------------------------------------------------------
    def _compare(self, s: _Sim):
        B, sid, tol = self.B, s.sid, self.tol
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
            for x in exps:
                cand = pool.get(x["bar"])
                if cand:
                    self._details(s, x, cand.pop(0))
                elif kind != "reaction" or x.get("required", True):
                    self.add(sid, rule, f"expected {kind} at bar {B.t[x['bar']]} ({side}) is not logged")
            allowed_break = {x["bar"] for x in exp_by.get("cancelled_second_break", [])}
            for bar, rest in pool.items():
                for e in rest:
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
          bars_m5: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Violations of R3-R22 re-derived from the logged bars, one dict {setup_id, rule, detail} per failed check."""
    return _Replay(setups, events, pivots, bars_m1, bars_m5, params).run().out


def occurrences(setups: pd.DataFrame, events: pd.DataFrame, pivots: pd.DataFrame, bars_m1: pd.DataFrame,
                bars_m5: pd.DataFrame, params: dict | None = None) -> dict[str, int]:
    """How often the replay exercised each behavioural case (expected trajectory, not the EA's claims)."""
    return {k: int(v) for k, v in _Replay(setups, events, pivots, bars_m1, bars_m5, params).run().occ.items()}
