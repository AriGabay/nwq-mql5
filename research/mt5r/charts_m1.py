"""M5 + M1 setup charts and per-setup table for the chart gate (plan 2026-10-03-0013, U5, R26, R27, KTD12).

Input is the research log of the M5 OB + M1 structure EA, in the columns of ``m1_contract`` (KTD10): rl_setups,
rl_events, rl_pivots, rl_bars_m1, rl_bars_m5 and, optionally, rl_deals. Plain and gzipped CSVs both load.

Each example is one figure with two stacked panels on a time axis:
  * M5, from the OB candle to the exit or cancellation: OB box, identifying FVG box, touch marker, and the window of
    the M1 panel shaded;
  * M1, from the earlier of 30 minutes before the touch and the earliest pivot the setup's events reference, to the
    exit or cancellation (KTD12): pivots (peak + confirmation markers), reference level and HH/LL bar, origin,
    HL/LH, entry FVG, reaction candle, break and return markers, fill, SL, TP, exit and cancellation;
  * a timeline under the panels: every event of the setup numbered in the order it became known (tick time for tick
    events, the close of the bar for bar events, the close of the confirmation bar for pivots), with the same number
    placed on the M1 panel where that knowledge arrived. For ``shared_structure`` the other fills of the same
    structure are drawn too (their OB band, touch, reaction and fill).
Bars sit at integer positions inside each panel so weekend gaps do not stretch the chart; tick times map to a
fraction of their bar. Tick labels and the table carry server time (the EA's epoch values printed as UTC).

Selection (KTD12): for every variant present and each side, one seeded draw per category from the candidates of
that category (``CATEGORIES``); a category without candidates is listed as missing.
"""
from __future__ import annotations

import datetime as dt
import gzip
import io
import pathlib
import random

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from . import m1_contract as mc  # noqa: E402

# Palette and figure style follow the archived charts_setups.py.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
VIOLET, GREEN, RED = "#4a3aa7", "#008300", "#e34948"
INK, MUTED, SURFACE, GRID = "#1f1f1e", "#6b6a64", "#fcfcfb", "#e6e5df"
SHADE = "#b5b3aa"   # M1 window on the M5 panel: recessive neutral
LABEL_BOX = dict(facecolor=SURFACE, edgecolor="none", alpha=0.8, pad=0.5)   # keeps labels legible over candles

SEED = 20260930
M1_SECONDS, M5_SECONDS = 60, 300
TOUCH_LOOKBACK_S = 30 * 60   # KTD12: the M1 panel starts at most 30 minutes before the touch ...
M1_PAD, M5_PAD = 3, 3        # ... plus this many whole bars of margin on each side of a panel

# KTD12 categories, in report order. Definitions (per variant and side):
#   broken_returned            at least one return (R7) and not cancelled by a second break
#   second_break_cancel        reason cancelled_second_break (R8)
#   opposing_structure_cancel  reason cancelled_opposing_structure (R22)
#   return_break_same_bar      cancelled_second_break whose last return tick lies in the M1 bar of the cancelling close
#   winner / loser             filled; net > 0 / <= 0 from the deals (else exit kind tp / sl, else exit vs fill)
#   shared_structure           filled setups of different OBs that entered on one structure change (same sc_hh bar
#                              and entry FVG), each on its own reaction candle (R16, R17)
#   far_from_ob                filled; the entry lies beyond the OB edge in the trade direction by at least the 95th
#                              percentile of that distance among the fills of its variant and side
CATEGORIES = ("broken_returned", "second_break_cancel", "opposing_structure_cancel", "return_break_same_bar",
              "winner", "loser", "shared_structure", "far_from_ob")
FAR_PCTL = 95
# Most specific first, so a setup that fits several categories goes to the rarer one when others are available.
PICK_ORDER = ("return_break_same_bar", "shared_structure", "far_from_ob", "second_break_cancel",
              "opposing_structure_cancel", "broken_returned", "winner", "loser")
SIDES = (("L", "long"), ("S", "short"))
CANCEL_REASONS = ("cancelled_second_break", "cancelled_opposing_structure")
PIVOT_REF_KINDS = ("sc_hh", "sc_superseded", "hl", "hl_failed", "cancelled_opposing_structure")
TABLE_COLUMNS = ["setup", "variant", "dir", "category", "OB (bar, low-high)", "touch (tick @ Bid)", "breaks",
                 "returns", "structure change (bar @ ref level)", "HL/LH (peak @ level, conf bar)",
                 "entry FVG (low-high, c1 bar)", "reaction bar", "fill (tick @ price)", "SL", "SL anchor", "TP",
                 "exit (tick @ price, kind)", "net (USD)", "reason (time)"]

_SETUP_INT = {"setup_id", "ob_time", "idfvg_c1_time", "idfvg_c3_time", "identified_in_warmup", "touch_msc",
              "touch_bar_time", "ob_age_bars_touch", "breaks", "returns", "sc_bar_time", "origin_time",
              "ref_pivot_id", "hl_pivot_id", "fvg_c1_time", "reaction_bar_time", "entry_request_msc", "attempts",
              "buffer_pts", "fill_msc", "position_id", "ob_age_bars_entry", "exit_msc", "reason_msc"}
_SETUP_STR = {"dir", "variant", "sl_anchor", "exit_kind", "reason"}
_EVENT_INT = {"setup_id", "seq", "bar_time", "tick_msc", "ref_id", "ref_time"}
_EVENT_STR = {"kind", "detail"}
_PIVOT_INT = {"pivot_id", "peak_time", "conf_time", "replaced_by", "outside_bar"}
_PIVOT_STR = {"type"}
_BARS_INT = {"time", "warmup"}
_DEALS_INT = {"ticket", "position_id", "type", "entry", "magic"}


# --- readers ---------------------------------------------------------------------------------------------------
def _read_raw(path) -> pd.DataFrame:
    """A CSV as strings; gzip (by magic bytes) and UTF-16 with a byte-order mark are both accepted."""
    data = pathlib.Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    text = data.decode("utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("utf-8-sig")
    return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)


def _typed(df: pd.DataFrame, ints, strs) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for c in df.columns:
        s = df[c].astype(object).where(df[c] != "", None)
        if c in strs:
            out[c] = s
        elif c in ints:
            out[c] = pd.to_numeric(s, errors="coerce").round().astype("Int64")
        else:
            out[c] = pd.to_numeric(s, errors="coerce").astype(float)
    return out


def read_setups(path) -> pd.DataFrame:
    return _typed(_read_raw(path), _SETUP_INT, _SETUP_STR)


def read_events(path) -> pd.DataFrame:
    return _typed(_read_raw(path), _EVENT_INT, _EVENT_STR).sort_values(["setup_id", "seq"], kind="mergesort") \
        .reset_index(drop=True)


def read_pivots(path) -> pd.DataFrame:
    return _typed(_read_raw(path), _PIVOT_INT, _PIVOT_STR)


def read_bars(path) -> pd.DataFrame:
    df = _typed(_read_raw(path), _BARS_INT, set())
    df["time"] = df["time"].astype("int64")
    return df.sort_values("time", kind="mergesort").reset_index(drop=True)


def read_deals(path) -> pd.DataFrame:
    df = _typed(_read_raw(path), _DEALS_INT, {"time", "comment"})
    df["time"] = pd.to_datetime(df["time"], format="%Y.%m.%d %H:%M:%S")
    return df


READERS = {"setups": read_setups, "events": read_events, "pivots": read_pivots, "bars_m1": read_bars,
           "bars_m5": read_bars, "deals": read_deals, "days": _read_raw}


def load_run(folder, tag: str | None = None) -> dict:
    """All contract files of one run in ``folder`` (rl_<name>_<tag>.csv or .csv.gz). deals and days are optional."""
    folder = pathlib.Path(folder)
    run = {}
    for name in mc.FILES:
        stem = f"rl_{name}_{tag}" if tag else f"rl_{name}"
        found = sorted(p for p in folder.glob(stem + ("*" if not tag else "") + ".csv*")
                       if p.name.endswith((".csv", ".csv.gz")))
        if tag:
            found = [p for p in found if p.name in (stem + ".csv", stem + ".csv.gz")]
        if len(found) > 1:
            raise ValueError(f"several {name} files in {folder}: {[p.name for p in found]}")
        if not found:
            if name in ("deals", "days"):
                run[name] = None
                continue
            raise FileNotFoundError(f"{stem}.csv[.gz] not in {folder}")
        run[name] = READERS[name](found[0])
    return run


# --- small helpers ---------------------------------------------------------------------------------------------
def _missing(v) -> bool:
    if v is None or v is pd.NA:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _int(row, k):
    v = row.get(k)
    return None if _missing(v) or (isinstance(v, str) and v == "") else int(float(v))


def _flt(row, k):
    v = row.get(k)
    return None if _missing(v) or (isinstance(v, str) and v == "") else float(v)


def _str(row, k):
    v = row.get(k)
    return None if _missing(v) or v == "" else str(v)


def variant_name(v) -> str:
    """0 / 'A' -> 'A', 1 / 'B' -> 'B' (ENUM_STRUCTURE_VARIANT)."""
    if _missing(v):
        return "?"
    s = str(v).strip().upper()
    s = s[:-2] if s.endswith(".0") else s
    return {"0": "A", "1": "B", "SV_HH_ONLY": "A", "SV_HH_HL": "B"}.get(s, s)


def side_of(v) -> str:
    s = "" if _missing(v) else str(v).strip().upper()
    return "L" if s in ("L", "LONG", "1", "+1", "1.0") else "S" if s in ("S", "SHORT", "-1", "-1.0") else s


def position_net(deals: pd.DataFrame | None) -> dict[int, float]:
    """Net result per position id: profit + commission + swap over all its deals (balance rows excluded)."""
    if deals is None or len(deals) == 0:
        return {}
    d = deals[deals["type"].astype(int) != 2]
    g = (d["profit"].astype(float) + d["commission"].astype(float) + d["swap"].astype(float)).groupby(
        d["position_id"].astype("int64")).sum()
    return {int(k): float(v) for k, v in g.items()}


def _events_by_setup(events: pd.DataFrame | None) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    if events is None or len(events) == 0:
        return out
    e = events.copy()
    e["_sid"] = pd.to_numeric(e["setup_id"]).astype("int64")
    e["_seq"] = pd.to_numeric(e["seq"], errors="coerce").fillna(0)
    for r in e.sort_values(["_sid", "_seq"], kind="mergesort").to_dict("records"):
        out.setdefault(int(r["_sid"]), []).append(r)
    return out


def _of(evs, kind):
    return [e for e in evs if _str(e, "kind") == kind]


def _is_filled(row) -> bool:
    return _str(row, "reason") == "filled" or _int(row, "fill_msc") is not None


def _outcome(row, nets):
    """(net or None, 'win' | 'loss' | None) of a filled setup."""
    pos = _int(row, "position_id")
    net = nets.get(pos) if pos is not None else None
    if net is not None:
        return net, "win" if net > 0 else "loss"
    kind = _str(row, "exit_kind")
    if kind in ("tp", "sl"):
        return None, "win" if kind == "tp" else "loss"
    fp, xp = _flt(row, "fill_price"), _flt(row, "exit_price")
    if fp is None or xp is None:
        return None, None
    sign = 1 if side_of(row.get("dir")) == "L" else -1
    return None, "win" if (xp - fp) * sign > 0 else "loss"


def _sc_bar(row, evs):
    sc = _int(row, "sc_bar_time")
    if sc is None and _of(evs, "sc_hh"):
        sc = _int(_of(evs, "sc_hh")[-1], "bar_time")
    return sc


def _breaks(row, evs):
    """Break count incl. a second break, which the log carries as the cancelled_second_break event (R8)."""
    return max(_int(row, "breaks") or 0, len(_of(evs, "break")) + len(_of(evs, "cancelled_second_break")))


def _returns(row, evs):
    return max(_int(row, "returns") or 0, len(_of(evs, "return")))


def _same_bar_return_break(row, evs) -> bool:
    if _str(row, "reason") != "cancelled_second_break":
        return False
    cancel = _of(evs, "cancelled_second_break")
    rets = _of(evs, "return")
    if not cancel or not rets:
        return False
    return _int(rets[-1], "bar_time") == _int(cancel[-1], "bar_time")


def entry_distance(row) -> float | None:
    """Fill beyond the OB edge in the trade direction (long: fill - OB high; short: OB low - fill)."""
    fp, hi, lo = _flt(row, "fill_price"), _flt(row, "ob_high"), _flt(row, "ob_low")
    if fp is None or hi is None or lo is None:
        return None
    return fp - hi if side_of(row.get("dir")) == "L" else lo - fp


def _far(recs) -> set[int]:
    """Setup ids whose entry distance is at or above the FAR_PCTL percentile of their variant and side (and > 0)."""
    out, pools = set(), {}
    for r in recs:
        dist = entry_distance(r) if _is_filled(r) else None
        if dist is not None:
            pools.setdefault((variant_name(r.get("variant")), side_of(r.get("dir"))), []).append((dist, r))
    for pool in pools.values():
        cut = float(np.percentile([d for d, _ in pool], FAR_PCTL))
        out |= {_int(r, "setup_id") for d, r in pool if d >= cut and d > 0}
    return out


def _stacks(recs, by_sid) -> dict[int, list[int]]:
    """setup id -> ids of the other fills of its structure (same variant, side, sc_hh bar and entry FVG)."""
    groups: dict[tuple, list[dict]] = {}
    for r in recs:
        if not _is_filled(r):
            continue
        sid = _int(r, "setup_id")
        key = (variant_name(r.get("variant")), side_of(r.get("dir")), _sc_bar(r, by_sid.get(sid, [])),
               _int(r, "fvg_c1_time"), _flt(r, "fvg_low"), _flt(r, "fvg_high"))
        if key[2] is None:
            continue
        groups.setdefault(key, []).append(r)
    out = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        ids = sorted(_int(m, "setup_id") for m in members)
        for m in members:
            sid = _int(m, "setup_id")
            out[sid] = [i for i in ids if i != sid]
    return out


# --- selection -------------------------------------------------------------------------------------------------
def select_examples(setups: pd.DataFrame, events: pd.DataFrame | None, deals: pd.DataFrame | None = None,
                    seed: int = SEED, n_per_category: int = 1, variants=None) -> pd.DataFrame:
    """Seeded KTD12 examples: for each variant present (or ``variants``) and each side, ``n_per_category`` setups per
    category, drawn with a generator seeded by (seed, variant, side, category) from the candidates sorted by setup
    id, preferring setups not yet picked for another category. The result does not depend on the row order of the
    inputs. ``attrs["missing"]`` lists '<variant>_<side>_<category>' combinations without candidates."""
    nets = position_net(deals)
    by_sid = _events_by_setup(events)
    recs = sorted(setups.to_dict("records"), key=lambda r: _int(r, "setup_id"))
    stacks = _stacks(recs, by_sid)
    far = _far(recs)
    present = sorted({variant_name(r.get("variant")) for r in recs})
    variants = [variant_name(v) for v in variants] if variants is not None else present

    def member(r, cat):
        sid, reason, evs = _int(r, "setup_id"), _str(r, "reason"), by_sid.get(_int(r, "setup_id"), [])
        if cat == "broken_returned":
            return _returns(r, evs) > 0 and reason != "cancelled_second_break"
        if cat == "second_break_cancel":
            return reason == "cancelled_second_break"
        if cat == "opposing_structure_cancel":
            return reason == "cancelled_opposing_structure"
        if cat == "return_break_same_bar":
            return _same_bar_return_break(r, evs)
        if cat == "shared_structure":
            return sid in stacks
        if cat == "far_from_ob":
            return sid in far
        if cat in ("winner", "loser") and _is_filled(r):
            return _outcome(r, nets)[1] == ("win" if cat == "winner" else "loss")
        return False

    chosen, missing, used = [], [], set()
    for v in variants:
        for d, side in SIDES:
            rows = [r for r in recs if variant_name(r.get("variant")) == v and side_of(r.get("dir")) == d]
            for cat in PICK_ORDER:
                pool = [r for r in rows if member(r, cat)]
                if not pool:
                    missing.append(f"{v}_{side}_{cat}")
                    continue
                rng = random.Random(f"{seed}|{v}|{d}|{cat}")
                fresh = [r for r in pool if _int(r, "setup_id") not in used]
                stale = [r for r in pool if _int(r, "setup_id") in used]
                k = min(n_per_category, len(pool))
                picks = rng.sample(fresh, min(k, len(fresh)))
                picks += rng.sample(stale, k - len(picks))
                for r in picks:
                    used.add(_int(r, "setup_id"))
                    chosen.append((v, d, cat, r))
    order = {c: i for i, c in enumerate(CATEGORIES)}
    sides = {side: i for i, (_, side) in enumerate(SIDES)}
    missing.sort(key=lambda m: (m.split("_", 2)[0], sides[m.split("_", 2)[1]], order[m.split("_", 2)[2]]))
    chosen.sort(key=lambda x: (x[0], [s for s, _ in SIDES].index(x[1]), order[x[2]], _int(x[3], "setup_id")))
    cols = list(setups.columns) + ["category", "net", "related"]
    out = pd.DataFrame([{**r, "category": c, "net": _outcome(r, nets)[0] if _is_filled(r) else None,
                         "related": ", ".join(f"#{i}" for i in stacks.get(_int(r, "setup_id"), []))
                         if c == "shared_structure" else ""}
                        for _, _, c, r in chosen], columns=cols)
    out.attrs["missing"] = missing
    return out


# --- windows (KTD12) -------------------------------------------------------------------------------------------
def _end_s(row, evs) -> int | None:
    """Exit tick, else cancellation (reason time or the close of the cancelling bar), else the last logged time."""
    if _int(row, "exit_msc") is not None:
        return _int(row, "exit_msc") // 1000
    if _of(evs, "exit") and _int(_of(evs, "exit")[-1], "tick_msc") is not None:
        return _int(_of(evs, "exit")[-1], "tick_msc") // 1000
    if _int(row, "reason_msc") is not None:
        return _int(row, "reason_msc") // 1000
    reason = _str(row, "reason")
    if reason in CANCEL_REASONS and _of(evs, reason):
        return _int(_of(evs, reason)[-1], "bar_time") + M1_SECONDS
    times = [_int(e, "tick_msc") // 1000 for e in evs if _int(e, "tick_msc") is not None]
    times += [_int(e, "bar_time") + M1_SECONDS for e in evs if _int(e, "bar_time") is not None]
    return max(times) if times else None


def referenced_pivots(row, evs) -> set[int]:
    ids = {_int(e, "ref_id") for e in evs if _str(e, "kind") in PIVOT_REF_KINDS}
    ids |= {_int(row, "ref_pivot_id"), _int(row, "hl_pivot_id")}
    return {i for i in ids if i is not None}


M1_AFTER_FILL_S = 3600   # the M1 panel ends at most an hour after a fill


def windows(row, events: pd.DataFrame | None, pivots: pd.DataFrame | None) -> dict:
    """KTD12 windows in epoch seconds (server time): m5 = (OB candle open, exit or cancellation); m1 = (earlier of
    touch - 30 min and the earliest peak of a pivot the setup's events reference, same end, but at most one hour
    after a fill)."""
    row = dict(row)
    evs = _events_by_setup(events).get(_int(row, "setup_id"), []) if events is not None else []
    end = _end_s(row, evs)
    touch_ms = _int(row, "touch_msc")
    if touch_ms is None and _of(evs, "touch"):
        touch_ms = _int(_of(evs, "touch")[0], "tick_msc")
    starts = []
    if touch_ms is not None:
        starts.append(touch_ms // 1000 - TOUCH_LOOKBACK_S)
    if pivots is not None and len(pivots):
        ids = referenced_pivots(row, evs)
        peaks = pivots.loc[pd.to_numeric(pivots["pivot_id"]).isin(ids), "peak_time"]
        starts += [int(p) for p in peaks if not _missing(p)]
    starts += [_int(e, "ref_time") for e in _of(evs, "cancelled_opposing_structure")
               if _int(e, "ref_time") is not None]   # peak of the broken L1 / H1
    if not starts:
        starts = [(end or _int(row, "ob_time")) - TOUCH_LOOKBACK_S]
    m1_end = end
    fill_ms = _int(row, "fill_msc")
    if fill_ms is not None and end is not None and end > fill_ms // 1000 + M1_AFTER_FILL_S:
        m1_end = fill_ms // 1000 + M1_AFTER_FILL_S       # a long-held position: the exit stays on the M5 panel
    return {"m5": (_int(row, "ob_time"), end), "m1": (min(starts), m1_end)}


# --- timeline --------------------------------------------------------------------------------------------------
EVENT_TEXT = {**{k: k for k in mc.SKIP_EVENTS}, "touch": "touch (first tick in the OB)", "break": "break: M1 close beyond the OB far edge",
              "return": "return: renewed touch after the break", "sc_hh": "structure change",
              "sc_superseded": "structure change superseded by a newer reference",
              "fvg_fixed": "entry FVG fixed", "fvg_none": "no entry FVG in the move", "fvg_lapsed": "entry FVG lapsed",
              "hl": "HL/LH confirmed", "hl_failed": "HL/LH failed (variant B)", "reaction": "reaction candle closed",
              "lost_competition": "reaction lost R16", "entry_attempt": "Market order sent", "fill": "fill",
              "exit": "exit", "cancelled_second_break": "cancelled: second break",
              "cancelled_opposing_structure": "cancelled: opposing structure"}


def _pivot(pivots, pid):
    if pivots is None or pid is None or not len(pivots):
        return None
    p = pivots[pd.to_numeric(pivots["pivot_id"]) == pid]
    return None if len(p) == 0 else p.iloc[0].to_dict()


def _hm(t_s):
    return "-" if t_s is None else dt.datetime.fromtimestamp(int(t_s), dt.timezone.utc).strftime("%m-%d %H:%M")


def timeline(row, evs, pivots, tag="") -> list[dict]:
    """Events of one setup in the order they became known. Each item: known_ms (tick time, or the close of the bar
    that decided it), when (printed), what, price, note, and the marker position (mark_s, mark_px) on the M1 panel.
    Pivot-based events name the pivot's peak bar and the close of its confirmation bar (conf_time + 1 min)."""
    row = dict(row)
    long_ = side_of(row.get("dir")) == "L"
    items = []

    def add(known_ms, what, price=None, note="", mark_s=None, tick=False):
        when = (_fmt_ms(known_ms)[5:] if tick else _hm(known_ms // 1000) + " close") if known_ms is not None else "-"
        items.append(dict(known_ms=known_ms, when=when, what=(tag + what), price=price, note=note,
                          mark_s=mark_s if mark_s is not None else (known_ms / 1000 if known_ms else None),
                          mark_px=price))

    def piv_note(pid, role):
        p = _pivot(pivots, pid)
        if p is None:
            return f"{role} pivot #{pid}"
        return (f"{role} {'high' if str(p.get('type')).upper().startswith('H') else 'low'} {_fmt_px(_flt(p, 'level'))}"
                f" peaked {_hm(_int(p, 'peak_time'))}, confirmed at the {_hm(_int(p, 'conf_time'))} close")

    c3 = _int(row, "idfvg_c3_time")
    if c3 is not None and not tag:
        add((c3 + M5_SECONDS) * 1000, "OB identified (M5 identifying FVG closed)", None,
            f"OB candle {_hm(_int(row, 'ob_time'))}, zone {_fmt_px(_flt(row, 'ob_low'))}-{_fmt_px(_flt(row, 'ob_high'))}")
    for e in evs:
        k = _str(e, "kind")
        if k not in EVENT_TEXT or k == "exit" or (tag and k not in ("touch", "reaction", "fill", "lost_competition")):
            continue
        ms_, bt, px = _int(e, "tick_msc"), _int(e, "bar_time"), _flt(e, "price")
        tick = k in ("touch", "return", "entry_attempt", "fill", "exit") or k in mc.SKIP_EVENTS and ms_ is not None \
            and k != "lost_competition"
        known = ms_ if tick and ms_ is not None else ((bt + M1_SECONDS) * 1000 if bt is not None else ms_)
        note, mark_px = "", px
        if k in ("sc_hh", "sc_superseded"):
            note = piv_note(_int(e, "ref_id"), "crossed reference")
            if k == "sc_hh" and _int(e, "ref_time") is not None:
                note += f"; origin bar {_hm(_int(e, 'ref_time'))}"
        elif k == "fvg_fixed":
            note = f"zone {_fmt_px(_flt(e, 'lo'))}-{_fmt_px(_flt(e, 'hi'))}, candle 1 {_hm(_int(e, 'ref_time'))}"
            mark_px = _flt(e, "hi") if long_ else _flt(e, "lo")
        elif k in ("hl", "hl_failed"):
            note = piv_note(_int(e, "ref_id"), "HL" if long_ else "LH")
        elif k == "cancelled_opposing_structure":
            note = piv_note(_int(e, "ref_id"), "H2" if long_ else "L2") + \
                f"; close beyond {'L1' if long_ else 'H1'} peaked {_hm(_int(e, 'ref_time'))}"
        elif k == "lost_competition":
            note = f"winner #{_int(e, 'ref_id')}"
        elif k == "entry_attempt":
            note = f"Bid {_fmt_px(_flt(e, 'lo'))} / Ask {_fmt_px(_flt(e, 'hi'))}"
        elif k == "fill":
            note = f"SL {_fmt_px(_flt(e, 'lo'))}, TP {_fmt_px(_flt(e, 'hi'))}"
        elif k in mc.SKIP_EVENTS:
            note = _str(e, "detail") or ""
        add(known, EVENT_TEXT[k], px if k != "fvg_fixed" else None, note,
            mark_s=(known / 1000 if known is not None else None), tick=tick)
        items[-1]["mark_px"] = mark_px
    if not tag and _int(row, "exit_msc") is not None:
        add(_int(row, "exit_msc"), f"exit ({_str(row, 'exit_kind') or '?'})", _flt(row, "exit_price"), tick=True)
    items.sort(key=lambda it: (it["known_ms"] is None, it["known_ms"] or 0))
    return items


def _draw_timeline(ax, items, x_of, ax1, P1):
    """Numbered markers on the M1 panel and the timeline table on ``ax`` (axis off)."""
    stack: dict[int, int] = {}
    for n, it in enumerate(items, 1):
        it["n"] = n
        x = x_of(it["mark_s"]) if it["mark_s"] is not None else None
        if x is None or it["mark_px"] is None:
            continue
        key = int(round(x))
        k = stack.get(key, 0)
        stack[key] = k + 1
        ax1.annotate(str(n), (x, it["mark_px"]), xytext=(9, -11 - 9 * k), textcoords="offset points", fontsize=6.5,
                     color=SURFACE, ha="center", va="center", zorder=12,
                     bbox=dict(boxstyle="circle,pad=0.18", facecolor=VIOLET, edgecolor="none", alpha=0.9))
    ax.axis("off")
    head = f"{'#':>2}  {'known at (server)':<23}{'event':<50}{'price':>9}  note"
    lines = [f"{it['n']:>2}  {it['when']:<23}{it['what'][:49]:<50}{_fmt_px(it['price']) if it['price'] is not None else '':>9}"
             f"  {it['note']}" for it in items]
    cols = 1 if len(lines) <= 26 else 2
    per = -(-len(lines) // cols)
    for c in range(cols):
        chunk = [head] + lines[c * per:(c + 1) * per]
        ax.text(0.0 + 0.5 * c, 1.0, "\n".join(chunk), transform=ax.transAxes, fontsize=6.3 if cols == 1 else 5.6,
                family="monospace", va="top", ha="left", color=INK)
    ax.set_title("Timeline: when each fact became known (tick time, or the close of the deciding M1 bar; violet "
                 "numbers on the M1 panel)", fontsize=8, color=MUTED, loc="left")
    return per


# --- drawing ---------------------------------------------------------------------------------------------------
class _Panel:
    """Bars of one panel at integer x; times map to x (tick times to a fraction of their bar)."""

    def __init__(self, bars: pd.DataFrame, period: int, t0: int, t1: int):
        b = bars[(bars["time"] >= t0) & (bars["time"] <= t1)].sort_values("time", kind="mergesort")
        if len(b) == 0:
            raise ValueError(f"no bars between {_fmt_s(t0)} and {_fmt_s(t1)}")
        self.period = period
        self.t = b["time"].astype("int64").to_numpy()
        self.o, self.h, self.l, self.c = (b[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))
        self.n = len(b)

    def i(self, time_s):
        """Index of the bar containing ``time_s`` (None outside the panel)."""
        if time_s is None:
            return None
        k = int(np.searchsorted(self.t, time_s, side="right")) - 1
        if k < 0 or time_s >= self.t[-1] + self.period:
            return None
        return k

    def x(self, time_s):
        """Position of a time inside its bar: bar k spans [k - 0.5, k + 0.5)."""
        if time_s is None:
            return None
        if time_s < self.t[0]:
            return -0.5
        k = int(np.searchsorted(self.t, time_s, side="right")) - 1
        frac = min(max((time_s - self.t[k]) / self.period, 0.0), 1.0)
        return k - 0.5 + frac


def _fmt_s(t):
    return "-" if t is None else dt.datetime.fromtimestamp(int(t), dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _fmt_ms(ms):
    if ms is None:
        return "-"
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _fmt_px(v):
    return "-" if v is None else f"{v:.2f}"


def _candles(ax, P: _Panel):
    xs = np.arange(P.n)
    up = P.c >= P.o
    rng = float(np.nanmax(P.h) - np.nanmin(P.l)) or 1.0
    ax.vlines(xs, P.l, P.h, colors=np.where(up, INK, MUTED), linewidth=0.7, zorder=2)
    ax.bar(xs, np.maximum(np.abs(P.c - P.o), rng * 0.002), bottom=np.minimum(P.o, P.c), width=0.64,
           color=np.where(up, SURFACE, MUTED), edgecolor=np.where(up, INK, MUTED), linewidth=0.7, zorder=3)


def _style(ax, P: _Panel, ylabel, margin_right=8):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=7)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_xlim(-1, P.n - 1 + margin_right)
    ticks = np.unique(np.linspace(0, P.n - 1, min(10, P.n)).astype(int))
    ax.set_xticks(ticks)
    ax.set_xticklabels([_fmt_s(P.t[i])[5:] for i in ticks])
    ax.set_ylabel(ylabel, fontsize=7, color=MUTED)


def _label(ax, x, y, text, dy=8, color=INK, ha="center", dx=0):
    ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=7, color=color, ha=ha,
                va="center" if dy == 0 else "bottom" if dy > 0 else "top", zorder=11, bbox=LABEL_BOX)


def _box(ax, x0, x1, lo, hi, color, alpha, label, drawn, below=False):
    ax.add_patch(Rectangle((x0, min(lo, hi)), max(x1 - x0, 0.3), abs(hi - lo), facecolor=color, alpha=alpha,
                           edgecolor=color, linewidth=1.1, zorder=1))
    ax.text(x0 + 0.1, min(lo, hi) if below else max(lo, hi), label, fontsize=7, color=INK,
            va="top" if below else "bottom", ha="left", zorder=11, bbox=LABEL_BOX)
    drawn.append(label)


def draw_setup(ax5, ax1, row, run: dict, net=None, category=None, related=None, ax_tl=None) -> dict:
    """Draw one setup on the M5 axes ``ax5`` and the M1 axes ``ax1`` (and its timeline on ``ax_tl``). ``related``:
    '#12, #14' - other fills of the same structure, drawn on the M1 panel. Returns the drawn labels, the KTD12 windows
    and the first/last bar time of each panel."""
    row = dict(row)
    sid = _int(row, "setup_id")
    evs = _events_by_setup(run.get("events")).get(sid, [])
    pivots = run.get("pivots")
    w = windows(row, run.get("events"), pivots)
    (m5_t0, end), (m1_t0, m1_end) = w["m5"], w["m1"]
    if end is None or m5_t0 is None:
        raise ValueError(f"setup {sid} has no OB time or no end")
    sign = 1 if side_of(row.get("dir")) == "L" else -1
    long_ = sign > 0
    ob_lo, ob_hi = _flt(row, "ob_low"), _flt(row, "ob_high")
    reason = _str(row, "reason") or "?"
    drawn: list[str] = []

    # ---------------- M5 panel ----------------
    P5 = _Panel(run["bars_m5"], M5_SECONDS, m5_t0 - M5_PAD * M5_SECONDS, end + M5_PAD * M5_SECONDS)
    x5_end = P5.x(end)
    ax5.axvspan(P5.x(m1_t0), P5.x(m1_end), color=SHADE, alpha=0.22, zorder=0, linewidth=0)
    ax5.text(P5.x(m1_t0), 0.98, " M1 window", transform=ax5.get_xaxis_transform(), fontsize=7, color=MUTED,
             va="top", ha="left")
    drawn.append("M1 window")
    _candles(ax5, P5)
    ob_i = P5.i(m5_t0)
    if ob_i is not None and ob_lo is not None:
        _box(ax5, ob_i - 0.45, x5_end, ob_lo, ob_hi, BLUE, 0.12, "OB", drawn)
    c1, c3 = P5.i(_int(row, "idfvg_c1_time")), P5.i(_int(row, "idfvg_c3_time"))
    if c1 is not None and _flt(row, "idfvg_low") is not None:
        _box(ax5, c1 - 0.45, (c3 if c3 is not None else c1 + 2) + 0.45, _flt(row, "idfvg_low"),
             _flt(row, "idfvg_high"), VIOLET, 0.22, "identifying FVG", drawn)
    touch = (_of(evs, "touch") or [{}])[0]
    touch_ms = _int(row, "touch_msc") or _int(touch, "tick_msc")
    touch_px = _flt(touch, "price") or (ob_hi if long_ else ob_lo)
    for ax, P in ((ax5, P5),):
        if touch_ms is not None:
            ax.plot([P.x(touch_ms / 1000)], [touch_px], marker="v" if long_ else "^", markersize=9, color=ORANGE,
                    markeredgecolor=SURFACE, linestyle="none", zorder=8)
            _label(ax, P.x(touch_ms / 1000), touch_px, "touch", dy=9 if long_ else -9)
    fill_ms, fill_px = _int(row, "fill_msc"), _flt(row, "fill_price")
    exit_ms, exit_px = _int(row, "exit_msc"), _flt(row, "exit_price")
    if fill_ms is not None and fill_px is not None:
        ax5.plot([P5.x(fill_ms / 1000)], [fill_px], marker="D", markersize=6, color=ORANGE,
                 markeredgecolor=SURFACE, linestyle="none", zorder=9)
    if exit_ms is not None and exit_px is not None:
        ax5.plot([P5.x(exit_ms / 1000)], [exit_px], marker="X", markersize=8, color=INK, markeredgecolor=SURFACE,
                 linestyle="none", zorder=9)
    if reason in CANCEL_REASONS:
        ax5.axvline(x5_end, color=RED, linestyle=":", linewidth=1.1, zorder=4)
    _style(ax5, P5, "M5 price", margin_right=4)
    ax5.set_title(f"M5 - OB candle {_fmt_s(m5_t0)} to {'exit' if exit_ms else reason} {_fmt_s(end)} "
                  f"(server time)", fontsize=8, color=MUTED, loc="left")

    # ---------------- M1 panel ----------------
    m1_lo = (m1_t0 // M1_SECONDS) * M1_SECONDS - M1_PAD * M1_SECONDS
    P1 = _Panel(run["bars_m1"], M1_SECONDS, m1_lo, m1_end + M1_PAD * M1_SECONDS)
    x1_end = P1.x(m1_end)
    exit_on_m1 = exit_ms is not None and exit_ms // 1000 <= m1_end
    _candles(ax1, P1)
    if ob_lo is not None:
        ax1.axhspan(ob_lo, ob_hi, color=BLUE, alpha=0.07, zorder=0, linewidth=0)
        for lvl, name in ((ob_hi, "OB high"), (ob_lo, "OB low")):
            ax1.axhline(lvl, color=BLUE, linestyle="-", linewidth=0.8, alpha=0.6, zorder=1)
            ax1.text(-0.6, lvl, f"{name} {lvl:.2f}", fontsize=7, color=BLUE, ha="left",
                     va="bottom" if name == "OB high" else "top")
        drawn.append("OB zone")

    # pivots: hollow circle at the peak, square at the close of the confirmation bar, dotted between them
    piv_by_id = {}
    if pivots is not None and len(pivots):
        pv = pivots[(pivots["peak_time"] >= P1.t[0]) & (pivots["peak_time"] <= P1.t[-1])]
        for p in pv.to_dict("records"):
            pid, lvl = _int(p, "pivot_id"), _flt(p, "level")
            xp, xc = P1.i(_int(p, "peak_time")), P1.i(_int(p, "conf_time"))
            if xp is None or lvl is None:
                continue
            piv_by_id[pid] = (xp, lvl, p)
            alpha = 0.35 if _int(p, "replaced_by") is not None else 1.0
            high = str(p.get("type", "")).upper().startswith(("H", "1"))
            ax1.plot([xp], [lvl], marker="o", markersize=5, markerfacecolor=SURFACE, markeredgecolor=INK,
                     markeredgewidth=1.0, alpha=alpha, linestyle="none", zorder=7)
            if xc is not None:
                ax1.plot([xp, xc + 0.5], [lvl, lvl], color=MUTED, linestyle=":", linewidth=0.8, alpha=alpha,
                         zorder=6)
                ax1.plot([xc + 0.5], [lvl], marker="s", markersize=3.5, color=MUTED, alpha=alpha, linestyle="none",
                         zorder=7)
            ax1.annotate("H" if high else "L", (xp, lvl), xytext=(0, 4 if high else -4), textcoords="offset points",
                         fontsize=6, color=MUTED, ha="center", va="bottom" if high else "top", alpha=alpha)
        if piv_by_id:
            drawn.append("pivots")

    def piv(pid):
        if pid in piv_by_id:
            return piv_by_id[pid]
        if pivots is None or pid is None:
            return None
        p = pivots[pd.to_numeric(pivots["pivot_id"]) == pid]
        if len(p) == 0:
            return None
        p = p.iloc[0].to_dict()
        return P1.x(_int(p, "peak_time")), _flt(p, "level"), p

    # structure changes: reference level from its pivot's peak to the HH/LL bar, origin bar
    sc_name = "HH" if long_ else "LL"
    scs = _of(evs, "sc_hh")
    for k, e in enumerate(scs):
        bar = P1.i(_int(e, "bar_time"))
        ref = piv(_int(e, "ref_id"))
        lvl = _flt(e, "price") if _flt(e, "price") is not None else (ref[1] if ref else None)
        if bar is None or lvl is None:
            continue
        last = k == len(scs) - 1          # earlier, superseded or lapsed structure changes are drawn faintly
        x0 = ref[0] if ref and ref[0] is not None else bar - 3
        y = P1.h[bar] if long_ else P1.l[bar]
        if not last:
            ax1.hlines(lvl, x0, bar + 0.5, color=MUTED, linestyle=":", linewidth=0.8, zorder=4)
            ax1.plot([bar], [y], marker="*", markersize=6, color=MUTED, linestyle="none", zorder=7)
            continue
        ax1.hlines(lvl, x0, bar + 0.5, color=INK, linestyle="--", linewidth=1.1, zorder=5)
        ax1.text(x0, lvl, f"ref {'high' if long_ else 'low'} {lvl:.2f}", fontsize=7, color=INK,
                 va="bottom" if long_ else "top", ha="left", zorder=6)
        ax1.plot([bar], [y], marker="*", markersize=10, color=INK, markeredgecolor=SURFACE, linestyle="none",
                 zorder=8)
        _label(ax1, bar, y, sc_name + (f" (#{k + 1} of {len(scs)})" if len(scs) > 1 else ""), dy=8 * sign)
        o = P1.i(_int(e, "ref_time"))
        if o is not None:
            yo = P1.l[o] if long_ else P1.h[o]
            ax1.plot([o], [yo], marker="^" if long_ else "v", markersize=5, color=MUTED, linestyle="none",
                     zorder=7)
            _label(ax1, o, yo, "origin", dy=-8 * sign, color=MUTED)
        drawn += ["reference level", sc_name]

    # HL / LH (and a failed one in variant B)
    hl_name = "HL" if long_ else "LH"
    for kind, color, text in (("hl", INK, hl_name), ("hl_failed", RED, hl_name + " failed")):
        for e in _of(evs, kind):
            p = piv(_int(e, "ref_id"))
            if p is None or p[0] is None:
                continue
            ax1.plot([p[0]], [p[1]], marker="o", markersize=9, markerfacecolor="none", markeredgecolor=color,
                     markeredgewidth=1.6, linestyle="none", zorder=8)
            _label(ax1, p[0], p[1], text, dy=-10 * sign, color=color)
            drawn.append(hl_name if kind == "hl" else hl_name + " failed")
    if not _of(evs, "hl") and _int(row, "hl_pivot_id") is not None:
        p = piv(_int(row, "hl_pivot_id"))
        if p is not None and p[0] is not None:
            ax1.plot([p[0]], [p[1]], marker="o", markersize=9, markerfacecolor="none", markeredgecolor=INK,
                     markeredgewidth=1.6, linestyle="none", zorder=8)
            _label(ax1, p[0], p[1], hl_name, dy=-10 * sign)
            drawn.append(hl_name)

    # R22 cancellation: H2 / L2 pivot and the broken L1 / H1 level
    for e in _of(evs, "cancelled_opposing_structure"):
        p = piv(_int(e, "ref_id"))
        if p is not None and p[0] is not None:
            _label(ax1, p[0], p[1], "H2 (lower high)" if long_ else "L2 (higher low)", dy=10 * sign, color=RED)
        l1 = P1.i(_int(e, "ref_time"))
        if l1 is not None:
            lvl = P1.l[l1] if long_ else P1.h[l1]
            ax1.hlines(lvl, l1, x1_end, color=RED, linestyle="--", linewidth=1.0, zorder=5)
            ax1.text(l1, lvl, "L1" if long_ else "H1", fontsize=7, color=RED, va="top" if long_ else "bottom")
        drawn.append("opposing structure")

    # entry FVG boxes, each to its lapse, the fill or the end
    lapses = [P1.i(_int(e, "bar_time")) for e in _of(evs, "fvg_lapsed")]
    fill_x = P1.x(fill_ms / 1000) if fill_ms is not None else None
    fvgs = [(_int(e, "ref_time"), _flt(e, "lo"), _flt(e, "hi"), P1.i(_int(e, "bar_time")))
            for e in _of(evs, "fvg_fixed")]
    if not fvgs and _int(row, "fvg_c1_time") is not None:
        fvgs = [(_int(row, "fvg_c1_time"), _flt(row, "fvg_low"), _flt(row, "fvg_high"), None)]
    for k, (c1_t, lo, hi, fixed) in enumerate(fvgs):
        x0 = P1.i(c1_t)
        if x0 is None or lo is None or hi is None:
            continue
        stop = next((x for x in lapses if x is not None and x > (fixed or x0)), None)
        x1 = stop + 0.5 if stop is not None else (fill_x if fill_x is not None else x1_end)
        if k < len(fvgs) - 1:             # earlier entry FVGs: faint box, no text
            ax1.fill_between([x0 - 0.45, x1], lo, hi, color=AQUA, alpha=0.10, linewidth=0, zorder=2)
            continue
        _box(ax1, x0 - 0.45, x1, lo, hi, AQUA, 0.28, "entry FVG", drawn, below=long_)
        if stop is not None:
            _label(ax1, stop, lo if long_ else hi, "FVG lapsed", dy=-8 * sign, color=RED)

    # reaction candles (R16 losses noted)
    lost = {_int(e, "bar_time"): _int(e, "ref_id") for e in _of(evs, "lost_competition")}
    reacts = sorted({_int(e, "bar_time") for e in _of(evs, "reaction")} | {_int(row, "reaction_bar_time")} - {None})
    for k, rt in enumerate(reacts):
        x = P1.i(rt)
        if x is None:
            continue
        ax1.axvspan(x - 0.5, x + 0.5, color=AQUA, alpha=0.22, zorder=0, linewidth=0)
        text = "reaction" + (f" (lost R16 to #{lost[rt]})" if rt in lost else "")
        ax1.text(x, 0.02 + 0.32 * (k % 2), text, transform=ax1.get_xaxis_transform(), fontsize=7, color=INK,
                 rotation=90,
                 va="bottom", ha="center", zorder=11)
        drawn.append("reaction")

    # touch, breaks and returns
    if touch_ms is not None:
        ax1.plot([P1.x(touch_ms / 1000)], [touch_px], marker="v" if long_ else "^", markersize=9, color=ORANGE,
                 markeredgecolor=SURFACE, linestyle="none", zorder=9)
        _label(ax1, P1.x(touch_ms / 1000), touch_px, "touch", dy=9 if long_ else -9)
        drawn.append("touch")
    for k, e in enumerate(_of(evs, "break")):
        x = P1.i(_int(e, "bar_time"))
        if x is None:
            continue
        y = _flt(e, "price") if _flt(e, "price") is not None else P1.c[x]
        ax1.plot([x], [y], marker="x", markersize=8, markeredgewidth=2, color=RED, linestyle="none", zorder=9)
        _label(ax1, x, y, f"break {k + 1}", dy=0, dx=-7, ha="right", color=RED)
        drawn.append("break")
    for e in _of(evs, "cancelled_second_break"):   # R8: the second break is logged as the cancellation
        x = P1.i(_int(e, "bar_time"))
        if x is None:
            continue
        y = _flt(e, "price") if _flt(e, "price") is not None else P1.c[x]
        ax1.plot([x], [y], marker="x", markersize=8, markeredgewidth=2, color=RED, linestyle="none", zorder=9)
        _label(ax1, x, y, "second break (R8)", dy=-10 * sign, color=RED)
        drawn.append("second break")
    for k, e in enumerate(_of(evs, "return")):
        ms_ = _int(e, "tick_msc")
        x = P1.x(ms_ / 1000) if ms_ is not None else P1.i(_int(e, "bar_time"))
        if x is None:
            continue
        y = _flt(e, "price") if _flt(e, "price") is not None else (ob_lo if long_ else ob_hi)
        ax1.plot([x], [y], marker="o", markersize=7, markerfacecolor=SURFACE, markeredgecolor=BLUE,
                 markeredgewidth=1.8, linestyle="none", zorder=9)
        _label(ax1, x, y, f"return {k + 1}", dy=9 * sign, color=BLUE)
        drawn.append("return")

    # fill, SL, TP, exit
    sl, tp = _flt(row, "sl"), _flt(row, "tp")
    if fill_x is not None and fill_px is not None:
        x_stop = P1.x(exit_ms / 1000) if exit_on_m1 else x1_end
        for val, color, name in ((sl, RED, "SL"), (tp, GREEN, "TP")):
            if val is None:
                continue
            ax1.hlines(val, fill_x, x_stop, color=color, linestyle="--", linewidth=1.4, zorder=5)
            ax1.text(x_stop + 1.0, val, f"{name} {val:.2f}", fontsize=7, color=color, va="center", ha="left")
            drawn.append(name)
        ax1.hlines(fill_px, fill_x, x_stop, color=INK, linestyle="-", linewidth=0.9, alpha=0.6, zorder=5)
        ax1.plot([fill_x], [fill_px], marker="D", markersize=7, color=ORANGE, markeredgecolor=SURFACE,
                 linestyle="none", zorder=10)
        _label(ax1, fill_x, fill_px, f"fill {fill_px:.2f}", dy=8 * sign, dx=-6, ha="right")
        drawn.append("fill")
    if exit_on_m1 and exit_px is not None:
        x = P1.x(exit_ms / 1000)
        ax1.plot([x], [exit_px], marker="X", markersize=9, color=INK, markeredgecolor=SURFACE, linestyle="none",
                 zorder=10)
        kind = _str(row, "exit_kind") or ""
        _label(ax1, x, exit_px, f"exit ({kind})" if kind else "exit", dy=10, dx=-4, ha="right")
        drawn.append("exit")
    elif exit_ms is not None:
        ax1.text(x1_end, 0.02, f"exit {_fmt_s(exit_ms // 1000)} on the M5 panel ", transform=ax1.get_xaxis_transform(),
                 fontsize=7, color=MUTED, ha="right", va="bottom")
    if reason in CANCEL_REASONS:
        ax1.axvline(x1_end, color=RED, linestyle=":", linewidth=1.2, zorder=4)
        ax1.text(x1_end, 0.98, reason + " ", transform=ax1.get_xaxis_transform(), fontsize=7, color=RED,
                 rotation=90, va="top", ha="right")
        drawn.append("cancellation")

    # other fills of the same structure (shared_structure): their OB band, touch, reaction and fill
    items = timeline(row, evs, pivots)
    rel_ids = [int(x) for x in str(related or "").replace("#", " ").replace(",", " ").split()]
    setups = run.get("setups")
    all_evs = _events_by_setup(run.get("events")) if rel_ids else {}
    for rid in rel_ids:
        if setups is None:
            break
        rr = setups[pd.to_numeric(setups["setup_id"]) == rid]
        if len(rr) == 0:
            continue
        rr = rr.iloc[0].to_dict()
        lo, hi = _flt(rr, "ob_low"), _flt(rr, "ob_high")
        if lo is not None and hi is not None:
            ax1.axhspan(lo, hi, facecolor="none", edgecolor=VIOLET, hatch="///", alpha=0.35, linewidth=0.6, zorder=0)
            ax1.text(P1.n - 1, hi if long_ else lo, f"#{rid} OB {lo:.2f}-{hi:.2f} ", fontsize=7, color=VIOLET,
                     ha="right", va="bottom" if long_ else "top")
        fms, fpx = _int(rr, "fill_msc"), _flt(rr, "fill_price")
        if fms is not None and fpx is not None and P1.i(fms // 1000) is not None:
            ax1.plot([P1.x(fms / 1000)], [fpx], marker="D", markersize=6, color=VIOLET, markeredgecolor=SURFACE,
                     linestyle="none", zorder=10)
            _label(ax1, P1.x(fms / 1000), fpx, f"#{rid} fill {fpx:.2f}", dy=-9 * sign, color=VIOLET)
        rt = _int(rr, "reaction_bar_time")
        if rt is not None and P1.i(rt) is not None:
            ax1.axvspan(P1.i(rt) - 0.5, P1.i(rt) + 0.5, color=VIOLET, alpha=0.10, zorder=0, linewidth=0)
        items += timeline(rr, all_evs.get(rid, []), pivots, tag=f"#{rid} ")
        drawn.append("related fills")
    items.sort(key=lambda it: (it["known_ms"] is None, it["known_ms"] or 0))
    if ax_tl is not None:
        _draw_timeline(ax_tl, items, lambda s_: P1.x(s_) if P1.i(s_) is not None else None, ax1, P1)
        drawn.append("timeline")
    _style(ax1, P1, "M1 price")
    ax1.set_xlabel("bar open time (server, MM-DD HH:MM)", fontsize=7, color=MUTED)
    ax1.set_title(f"M1 - from {_fmt_s(m1_t0)} (earlier of touch - 30 min and the earliest referenced pivot) to "
                  f"{_fmt_s(m1_end)}{' (1 h after the fill)' if m1_end != end else ''}; "
                  "pivots: o peak, square = close of confirmation bar", fontsize=8, color=MUTED,
                  loc="left")

    parts = [f"#{sid} variant {variant_name(row.get('variant'))} {'LONG' if long_ else 'SHORT'}"]
    if category:
        parts.append(category + (f" (structure shared with {related})" if related else ""))
    parts.append(f"reason {reason}")
    if fill_px is not None:
        parts.append(f"net {net:+.2f} USD after costs" if net is not None and not _missing(net) else "net n/a")
    parts.append(f"breaks {_breaks(row, evs)}, returns {_returns(row, evs)}")
    if _str(row, "sl_anchor"):
        parts.append(f"SL anchor {_str(row, 'sl_anchor')}")
    dist = entry_distance(row)
    if dist is not None:
        parts.append(f"entry {dist:+.2f} beyond the OB edge, stop {abs(_flt(row, 'fill_price') - _flt(row, 'sl')):.2f}")
    title = " | ".join(parts)
    return {"drawn": drawn, "windows": w, "title": title, "m5_bars": (int(P5.t[0]), int(P5.t[-1])),
            "m1_bars": (int(P1.t[0]), int(P1.t[-1])), "timeline": items}


# --- table and rendering ---------------------------------------------------------------------------------------
def _table_row(row, evs, pivots):
    sid = _int(row, "setup_id")
    touch = (_of(evs, "touch") or [{}])[0]
    touch_ms = _int(row, "touch_msc") or _int(touch, "tick_msc")
    touch_px = _flt(touch, "price")
    sc = _sc_bar(row, evs)
    sc_ev = _of(evs, "sc_hh")
    ref_lvl = _flt(sc_ev[-1], "price") if sc_ev else None
    hl_id = _int(row, "hl_pivot_id")
    if hl_id is None and _of(evs, "hl"):
        hl_id = _int(_of(evs, "hl")[-1], "ref_id")
    hl = "-"
    if hl_id is not None and pivots is not None and len(pivots):
        p = pivots[pd.to_numeric(pivots["pivot_id"]) == hl_id]
        if len(p):
            p = p.iloc[0].to_dict()
            hl = f"{_fmt_s(_int(p, 'peak_time'))} @ {_fmt_px(_flt(p, 'level'))}, conf {_fmt_s(_int(p, 'conf_time'))}"
    fvg = "-"
    if _flt(row, "fvg_low") is not None:
        fvg = f"{_fmt_px(_flt(row, 'fvg_low'))}-{_fmt_px(_flt(row, 'fvg_high'))}, c1 {_fmt_s(_int(row, 'fvg_c1_time'))}"
    elif _of(evs, "fvg_fixed"):
        e = _of(evs, "fvg_fixed")[-1]
        fvg = f"{_fmt_px(_flt(e, 'lo'))}-{_fmt_px(_flt(e, 'hi'))}, c1 {_fmt_s(_int(e, 'ref_time'))}"
    anchor = _str(row, "sl_anchor")
    anchor = "-" if anchor is None else f"{anchor} @ {_fmt_px(_flt(row, 'sl_anchor_price'))}"
    fill = "-" if _int(row, "fill_msc") is None else \
        f"{_fmt_ms(_int(row, 'fill_msc'))} @ {_fmt_px(_flt(row, 'fill_price'))}"
    ex = "-" if _int(row, "exit_msc") is None else \
        f"{_fmt_ms(_int(row, 'exit_msc'))} @ {_fmt_px(_flt(row, 'exit_price'))} ({_str(row, 'exit_kind') or '?'})"
    net = row.get("net")
    category = row.get("category", "") + (f" (with {row['related']})" if row.get("related") else "")
    reason = _str(row, "reason") or "-"
    if _int(row, "reason_msc") is not None:
        reason += f" ({_fmt_ms(_int(row, 'reason_msc'))})"
    return [str(sid), variant_name(row.get("variant")), side_of(row.get("dir")), category,
            f"{_fmt_s(_int(row, 'ob_time'))} ({_fmt_px(_flt(row, 'ob_low'))}-{_fmt_px(_flt(row, 'ob_high'))})",
            f"{_fmt_ms(touch_ms)} @ {_fmt_px(touch_px)}" if touch_ms is not None else "-",
            str(_breaks(row, evs)), str(_returns(row, evs)),
            "-" if sc is None else f"{_fmt_s(sc)} @ {_fmt_px(ref_lvl)}", hl, fvg,
            _fmt_s(_int(row, "reaction_bar_time")), fill, _fmt_px(_flt(row, "sl")), anchor, _fmt_px(_flt(row, "tp")),
            ex, "-" if net is None or _missing(net) else f"{float(net):+.2f}", reason]


def render(run: dict, out_dir, seed: int = SEED, n_per_category: int = 1, variants=None) -> dict:
    """Charts (out_dir/charts/*.png) and a markdown table (out_dir/setups_table.md) for the seeded KTD12 examples
    of one run (``load_run``)."""
    out_dir = pathlib.Path(out_dir)
    charts_dir = out_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    sel = select_examples(run["setups"], run.get("events"), run.get("deals"), seed=seed,
                          n_per_category=n_per_category, variants=variants)
    missing = list(sel.attrs.get("missing", []))
    by_sid = _events_by_setup(run.get("events"))
    charts, rows = [], []
    for row in sel.to_dict("records"):
        sid = _int(row, "setup_id")
        n_ev = len(timeline(row, by_sid.get(sid, []), run.get("pivots"))) + 4 * len(str(row.get("related") or "").split())
        tl_h = 0.55 + 0.13 * min(n_ev + 1, 27)
        fig, (ax5, ax1, axt) = plt.subplots(3, 1, figsize=(14, 9.5 + tl_h), facecolor=SURFACE,
                                            gridspec_kw={"height_ratios": [2, 3, tl_h / 1.6]})
        try:
            info = draw_setup(ax5, ax1, row, run, net=row.get("net"), category=row["category"],
                              related=row.get("related") or None, ax_tl=axt)
            fig.suptitle(info["title"], fontsize=10, color=INK, x=0.01, ha="left")
            fig.tight_layout()
            v, d = variant_name(row.get("variant")), dict(SIDES)[side_of(row.get("dir"))]
            path = charts_dir / f"{v}_{d}_{row['category']}_setup{_int(row, 'setup_id'):06d}.png"
            fig.savefig(path, dpi=110, facecolor=SURFACE)
            charts.append(str(path))
        finally:
            plt.close(fig)
        rows.append(_table_row(row, by_sid.get(_int(row, "setup_id"), []), run.get("pivots")))
    lines = [
        "# Setup examples for the chart gate (R26, R27, KTD12)",
        "",
        f"Selection: for every variant present and each side, {n_per_category} setup(s) per KTD12 category, drawn "
        f"with seed {seed} (generator seeded by seed, variant, side and category; candidates sorted by setup id; "
        "setups not yet shown for another category preferred). Categories: " + ", ".join(CATEGORIES) + ".",
        "",
        "Times are server time. Bar columns show the bar open time; touch, fill and exit are tick times with "
        "milliseconds. Net is profit + commission + swap of the position from the deals. Prices are Bid unless "
        "noted; 'ref level' is the pivot level the structure-changing close crossed. Each chart carries a timeline "
        "of when every fact became known (tick time, or the close of the deciding M1 bar; a pivot is known at the "
        "close of its confirmation bar).",
        "",
        "| " + " | ".join(TABLE_COLUMNS) + " |",
        "|" + "|".join("---" for _ in TABLE_COLUMNS) + "|",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    lines += ["", "Missing categories: " + (", ".join(missing) if missing else "none") + "."]
    table = out_dir / "setups_table.md"
    table.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"charts": charts, "table": str(table), "missing": missing, "examples": sel}
