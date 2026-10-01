"""Setup charts and per-setup table for the R39 chart gate (plan U6, KTD11, R40).

Candlesticks are drawn with plain matplotlib (no mplfinance). Bars sit at their integer position in ``rl_bars`` so
weekend gaps do not stretch the chart; tick labels carry the bar open time (server time). Labels are English; the
report around the charts is Hebrew. AMENDMENT A1: the title and table carry the identifying-FVG and confirmation-FVG
middle-candle tick-volume ratios, and a tick-volume panel under the price panel highlights both middle candles when
rl_bars has tick_volume.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import random

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from . import conformance as cf  # noqa: E402

# Palette mirrors the archived deliver.py (validated categorical slots) plus the reference palette's slots 6-8.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
VIOLET, GREEN, RED = "#4a3aa7", "#008300", "#e34948"
INK, MUTED, SURFACE, GRID = "#1f1f1e", "#6b6a64", "#fcfcfb", "#e6e5df"
VOL_BASE = "#b5b3aa"  # context tick-volume bars: recessive neutral, darker than the grid

STAGE_LABELS = ("OB", "identifying FVG", "touch", "confirmation FVG", "placement", "fill", "entry", "SL", "TP",
                "exit")
FILLED = {"filled", "filled_late"}
SIDES = (("L", "long"), ("S", "short"))
TABLE_COLUMNS = ["setup", "category", "dir", "OB", "identifying FVG", "activation", "touch", "confirmation FVG",
                 "idFVG vol ratio", "cFVG vol ratio", "placement", "fill", "exit", "intended entry", "fill price", "SL", "TP", "planned RR",
                 "realized R", "net after costs (USD)", "reason"]
RUN_CONSTANTS = pathlib.Path(__file__).resolve().parents[1] / "run_constants.json"
VOLUME_LOOKBACK_HOURS = 24  # EA default (R41); used only when a setup row has no logged ratio


def has_tick_volume(bars: pd.DataFrame) -> bool:
    return "tick_volume" in bars and bool(np.isfinite(pd.to_numeric(bars["tick_volume"], errors="coerce")).any())


def fvg_vol_ratios(row, bars: pd.DataFrame, period_seconds: int, lookback_hours=VOLUME_LOOKBACK_HOURS):
    """(identifying, confirmation) middle-candle volume ratios: the logged value, else recomputed from the bars
    when all look-back bars are logged, else None."""
    out = []
    for ratio_f, c1_f in (("idfvg_vol_ratio", "idfvg_c1_time"), ("cfvg_vol_ratio", "cfvg_c1_time")):
        v = cf._flt(row, ratio_f)
        if v is None and cf._int(row, c1_f) is not None and has_tick_volume(bars):
            d = cf.fvg_volume(bars, cf._int(row, c1_f), period_seconds, lookback_hours)
            v = None if d is None else d["ratio"]
        out.append(v)
    return tuple(out)


def _fmt_ratio(v):
    return "-" if v is None else f"{v:.2f}"


# --- data helpers ----------------------------------------------------------------------------------------------
def position_net(deals: pd.DataFrame | None) -> dict[int, float]:
    """Net result per position id: profit + commission + swap over all its deals (balance rows excluded)."""
    if deals is None or len(deals) == 0:
        return {}
    d = deals[deals["type"] != 2]
    g = (d["profit"].astype(float) + d["commission"].astype(float) + d["swap"].astype(float)).groupby(
        d["position_id"].astype("int64")).sum()
    return {int(k): float(v) for k, v in g.items()}


def _contract_size(value=None) -> float:
    if value is not None:
        return float(value)
    try:
        return float(json.loads(RUN_CONSTANTS.read_text())["symbol_spec"]["contract_size"])
    except (OSError, KeyError, ValueError):
        return 100.0


def _net_of(row, nets):
    pos = cf._int(row, "position_id")
    return nets.get(pos) if pos is not None else None


# --- selection -------------------------------------------------------------------------------------------------
def select_examples(setups: pd.DataFrame, deals: pd.DataFrame, seed: int = 20260930, n_per_side: int = 3
                    ) -> pd.DataFrame:
    """Deterministic example set: up to n_per_side filled trades per direction covering a winner and a loser,
    plus one example of every rejection reason that occurred. ``attrs["missing"]`` lists unavailable categories."""
    rng = random.Random(seed)
    nets = position_net(deals)
    recs = sorted(setups.to_dict("records"), key=lambda r: cf._int(r, "setup_id"))
    chosen, missing = [], []
    for d, side in SIDES:
        filled = [r for r in recs if cf._str(r, "dir") == d and cf._str(r, "reason") in FILLED]
        win = [r for r in filled if (_net_of(r, nets) or 0) > 0 and _net_of(r, nets) is not None]
        lose = [r for r in filled if _net_of(r, nets) is not None and _net_of(r, nets) <= 0]
        unknown = [r for r in filled if _net_of(r, nets) is None]
        for pool in (win, lose, unknown):
            rng.shuffle(pool)
        picks = []
        for pool, cat in ((win, f"{side}_winner"), (lose, f"{side}_loser")):
            if pool and len(picks) < n_per_side:
                picks.append((pool.pop(0), cat))
            elif not pool:
                missing.append(cat)
        pools = [(win, f"{side}_winner"), (lose, f"{side}_loser"), (unknown, f"{side}_open")]
        k = 0
        while len(picks) < n_per_side and any(p for p, _ in pools):
            pool, cat = pools[k % len(pools)]
            if pool:
                picks.append((pool.pop(0), cat))
            k += 1
        if len(picks) < n_per_side:
            missing.append(f"{side}_filled_shortfall_{len(picks)}_of_{n_per_side}")
        chosen += picks
    reasons = sorted({cf._str(r, "reason") for r in recs} - FILLED - {None})
    for reason in reasons:
        pool = [r for r in recs if cf._str(r, "reason") == reason]
        chosen.append((rng.choice(pool), f"rejected_{reason}"))
    if not reasons:
        missing.append("rejections")
    cols = list(setups.columns) + ["category", "net"]
    out = pd.DataFrame([{**r, "category": c, "net": _net_of(r, nets)} for r, c in chosen], columns=cols)
    out.attrs["missing"] = missing
    return out


# --- drawing ---------------------------------------------------------------------------------------------------
def _fmt_bar(t):
    return "-" if t is None else dt.datetime.fromtimestamp(int(t), dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _fmt_ms(ms):
    if ms is None:
        return "-"
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _fmt_px(v):
    return "-" if v is None else f"{v:.2f}"


def _planned_rr(row):
    e, s, t = cf._flt(row, "entry"), cf._flt(row, "sl"), cf._flt(row, "tp")
    if None in (e, s, t) or e == s:
        return None
    return abs(t - e) / abs(e - s)


def draw_setup(ax, row, bars: pd.DataFrame, period_seconds: int, net=None, category=None, vax=None) -> list[str]:
    """Draw one setup on ``ax`` (and its tick volume on ``vax`` when given); returns the labels that were drawn."""
    row = dict(row)
    B = cf._Bars(bars, period_seconds)
    ms_bar = lambda f: None if cf._int(row, f) is None else B.bar_at_ms(cf._int(row, f))  # noqa: E731
    bi = lambda f: B.i(cf._int(row, f))  # noqa: E731
    sign = 1 if cf._str(row, "dir") == "L" else -1
    ob_i, idc1, idc3 = bi("ob_time"), bi("idfvg_c1_time"), bi("idfvg_c3_time")
    piv, brk, act, tch = bi("bos_pivot_time"), bi("bos_break_time"), bi("activation_time"), bi("touch_time")
    cc1, cc3 = bi("cfvg_c1_time"), bi("cfvg_c3_time")
    place_i, fill_i, exit_i, reason_i = (ms_bar(f) for f in ("place_time_msc", "fill_time_msc", "exit_time_msc",
                                                             "reason_time_msc"))
    if place_i is None and cf._int(row, "place_time_msc") is not None and cc3 is not None:
        place_i = min(cc3 + 1, B.n - 1)
    stage = [i for i in (ob_i, idc1, idc3, piv, brk, act, tch, cc1, cc3, place_i, fill_i, exit_i, reason_i)
             if i is not None]
    if not stage:
        raise ValueError(f"setup {row.get('setup_id')} has no stage inside the logged bars")
    left = max(min(stage) - 6, 0)
    right = min(max(stage) + 6, B.n - 1, left + 180)
    end = min(max(i for i in (fill_i, exit_i, reason_i, cc3, tch, act) if i is not None) + 2, right)
    drawn = []

    # candles
    xs = np.arange(left, right + 1)
    for x in xs:
        o, h, lo, c = B.o[x], B.h[x], B.l[x], B.c[x]
        up = c >= o
        ax.vlines(x, lo, h, color=INK if up else MUTED, linewidth=0.8, zorder=2)
        ax.add_patch(Rectangle((x - 0.32, min(o, c)), 0.64, max(abs(c - o), 1e-6), zorder=3, linewidth=0.8,
                               facecolor=SURFACE if up else MUTED, edgecolor=INK if up else MUTED))

    def box(x0, x1, lo, hi, color, label, alpha):
        ax.add_patch(Rectangle((x0 - 0.45, lo), x1 - x0 + 0.9, hi - lo, facecolor=color, alpha=alpha,
                               edgecolor=color, linewidth=1.2, zorder=1))
        ax.text(x0 - 0.4, hi, label, fontsize=7, color=INK, va="bottom", ha="left", zorder=6)
        drawn.append(label)

    ob_lo, ob_hi = cf._flt(row, "ob_low"), cf._flt(row, "ob_high")
    if ob_i is not None and ob_lo is not None:
        box(ob_i, end, ob_lo, ob_hi, BLUE, "OB", 0.12)
    if idc1 is not None and cf._flt(row, "idfvg_low") is not None:
        box(idc1, idc3 if idc3 is not None else idc1 + 2, cf._flt(row, "idfvg_low"), cf._flt(row, "idfvg_high"),
            VIOLET, "identifying FVG", 0.22)
    lvl = cf._flt(row, "bos_level")
    if lvl is not None and piv is not None:
        ax.hlines(lvl, piv, brk if brk is not None else end, color=MUTED, linestyle="--", linewidth=1.2, zorder=4)
        ax.text(piv, lvl, "BOS level", fontsize=7, color=INK, va="bottom")
        drawn.append("BOS")
    if act is not None:
        ax.axvline(act, color=MUTED, linestyle=":", linewidth=1, zorder=1)
        ax.text(act, 0.02, " activation", transform=ax.get_xaxis_transform(), fontsize=7, color=MUTED, rotation=90,
                va="bottom")
        drawn.append("activation")
    if tch is not None:
        y = B.l[tch] if sign > 0 else B.h[tch]
        ax.plot([tch], [y], marker="^" if sign > 0 else "v", markersize=9, color=ORANGE, markeredgecolor=SURFACE,
                zorder=7, linestyle="none")
        ax.annotate("touch", (tch, y), xytext=(0, -12 * sign), textcoords="offset points", fontsize=7, color=INK,
                    ha="center", va="top" if sign > 0 else "bottom")
        drawn.append("touch")
    if cc1 is not None and cf._flt(row, "cfvg_low") is not None:
        box(cc1, cc3 if cc3 is not None else cc1 + 2, cf._flt(row, "cfvg_low"), cf._flt(row, "cfvg_high"), AQUA,
            "confirmation FVG", 0.28)

    entry, sl, tp = cf._flt(row, "entry"), cf._flt(row, "sl"), cf._flt(row, "tp")
    x0 = cc3 if cc3 is not None else (tch if tch is not None else left)
    for val, color, style, label in ((entry, INK, "-", "entry"), (sl, RED, "--", "SL"), (tp, GREEN, "--", "TP")):
        if val is None:
            continue
        ax.hlines(val, x0, end + 0.5, color=color, linestyle=style, linewidth=1.4, zorder=5)
        ax.text(end + 0.6, val, f"{label} {val:.2f}", fontsize=7, color=INK, va="center", ha="left")
        drawn.append(label)
    if place_i is not None and entry is not None:
        ax.plot([place_i], [entry], marker="o", markersize=8, markerfacecolor=SURFACE, markeredgecolor=BLUE,
                markeredgewidth=1.6, zorder=8, linestyle="none")
        ax.annotate("placement", (place_i, entry), xytext=(0, 9), textcoords="offset points", fontsize=7,
                    color=INK, ha="center")
        drawn.append("placement")
    fp = cf._flt(row, "fill_price")
    if fill_i is not None and fp is not None:
        ax.plot([fill_i], [fp], marker="D", markersize=7, color=ORANGE, markeredgecolor=SURFACE, zorder=9,
                linestyle="none")
        ax.annotate("fill", (fill_i, fp), xytext=(0, -11), textcoords="offset points", fontsize=7, color=INK,
                    ha="center", va="top")
        drawn.append("fill")
    xp = cf._flt(row, "exit_price")
    if exit_i is not None and xp is not None:
        ax.plot([exit_i], [xp], marker="X", markersize=9, color=INK, markeredgecolor=SURFACE, zorder=9,
                linestyle="none")
        kind = cf._str(row, "exit_kind") or ""
        ax.annotate(f"exit ({kind})" if kind else "exit", (exit_i, xp), xytext=(0, 9), textcoords="offset points",
                    fontsize=7, color=INK, ha="center")
        drawn.append("exit")

    reason = cf._str(row, "reason") or "?"
    if reason_i is not None and reason not in FILLED:
        ax.axvline(reason_i, color=RED, linestyle=":", linewidth=1, zorder=1)
    ax.text(0.005, 0.985, f"reason: {reason}", transform=ax.transAxes, fontsize=8, color=INK, va="top",
            bbox=dict(facecolor=SURFACE, edgecolor=MUTED, boxstyle="round,pad=0.3"), zorder=10)
    drawn.append("reason")

    rr = _planned_rr(row)
    parts = [f"#{row.get('setup_id')} {'LONG' if sign > 0 else 'SHORT'}"]
    if category:
        parts.append(category)
    parts.append(f"reason {reason}")
    parts.append(f"net {net:+.2f} USD after costs" if net is not None and not pd.isna(net) else "net n/a")
    parts.append(f"planned RR {rr:.2f}" if rr is not None else "planned RR n/a")
    if entry is not None:
        parts.append(f"entry {entry:.2f} vs fill {fp:.2f}" if fp is not None else f"entry {entry:.2f}, not filled")
    id_r, c_r = fvg_vol_ratios(row, bars, period_seconds)
    vol_parts = [f"{name} vol {r:.2f}x" for name, r in (("idFVG", id_r), ("cFVG", c_r)) if r is not None]
    if vol_parts:
        parts.append(", ".join(vol_parts))
    ax.set_title(" | ".join(parts), fontsize=9, color=INK, loc="left")

    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=7)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_xlim(left - 1, right + 6)
    ticks = np.unique(np.linspace(left, right, min(8, right - left + 1)).astype(int))
    ax.set_xticks(ticks)
    ax.set_ylabel("price", fontsize=7, color=MUTED)
    labels_ax = ax
    if vax is not None and np.isfinite(B.v[left:right + 1]).any():
        _draw_volume(vax, B, left, right, ((idc1, VIOLET, "idFVG", id_r), (cc1, AQUA, "cFVG", c_r)))
        drawn.append("tick volume")
        vax.set_xlim(left - 1, right + 6)
        vax.set_xticks(ticks)
        ax.tick_params(labelbottom=False)
        labels_ax = vax
    labels_ax.set_xticklabels([_fmt_bar(B.t[i])[5:] for i in ticks])
    labels_ax.set_xlabel("bar open time (server, MM-DD HH:MM)", fontsize=7, color=MUTED)
    return drawn


def _draw_volume(vax, B, left, right, fvgs):
    """Tick-volume bars; the middle candle of each FVG wears that FVG's box colour and is labelled with its ratio."""
    colors = {}
    for c1, color, name, ratio in fvgs:
        if c1 is not None and left <= c1 + 1 <= right:
            colors[c1 + 1] = (color, f"{name} {ratio:.2f}x" if ratio is not None else name)
    for x in range(left, right + 1):
        v = B.v[x]
        if not np.isfinite(v):
            continue
        color, label = colors.get(x, (VOL_BASE, None))
        vax.add_patch(Rectangle((x - 0.36, 0), 0.72, v, facecolor=color, edgecolor=SURFACE, linewidth=0.6,
                                zorder=3))
        if label:
            vax.annotate(label, (x, v), xytext=(0, 2), textcoords="offset points", fontsize=7, color=INK,
                         ha="center", va="bottom", zorder=6)
    top = np.nanmax(B.v[left:right + 1])
    vax.set_ylim(0, top * 1.3 if top > 0 else 1)
    vax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        vax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        vax.spines[s].set_color(MUTED)
    vax.tick_params(colors=MUTED, labelsize=7)
    vax.grid(axis="y", color=GRID, linewidth=0.6)
    vax.set_ylabel("tick volume", fontsize=7, color=MUTED)


# --- rendering -------------------------------------------------------------------------------------------------
def _table_row(row, contract_size, ratios=(None, None)):
    rr = _planned_rr(row)
    net = row.get("net")
    net = None if net is None or pd.isna(net) else float(net)
    e, s, vol = cf._flt(row, "entry"), cf._flt(row, "sl"), cf._flt(row, "volume")
    realized = None
    if net is not None and None not in (e, s, vol) and e != s and vol > 0:
        realized = net / (abs(e - s) * vol * contract_size)
    return [str(cf._int(row, "setup_id")), row.get("category", ""), cf._str(row, "dir") or "-",
            _fmt_bar(cf._int(row, "ob_time")), _fmt_bar(cf._int(row, "idfvg_c3_time")),
            _fmt_bar(cf._int(row, "activation_time")), _fmt_bar(cf._int(row, "touch_time")),
            _fmt_bar(cf._int(row, "cfvg_c3_time")), _fmt_ratio(ratios[0]), _fmt_ratio(ratios[1]),
            _fmt_ms(cf._int(row, "place_time_msc")),
            _fmt_ms(cf._int(row, "fill_time_msc")), _fmt_ms(cf._int(row, "exit_time_msc")),
            _fmt_px(e), _fmt_px(cf._flt(row, "fill_price")), _fmt_px(s), _fmt_px(cf._flt(row, "tp")),
            "-" if rr is None else f"{rr:.2f}", "-" if realized is None else f"{realized:+.2f}",
            "-" if net is None else f"{net:+.2f}", cf._str(row, "reason") or "-"]


def render(setups: pd.DataFrame, bars: pd.DataFrame, deals: pd.DataFrame, out_dir, period_seconds: int,
           seed: int = 20260930, n_per_side: int = 3, contract_size=None) -> dict:
    """Charts (out_dir/charts/*.png) and a markdown table (out_dir/setups_table.md) for the selected examples."""
    out_dir = pathlib.Path(out_dir)
    charts_dir = out_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    sel = select_examples(setups, deals, seed=seed, n_per_side=n_per_side)
    missing = list(sel.attrs.get("missing", []))
    size = _contract_size(contract_size)
    charts, rows = [], []
    with_volume = has_tick_volume(bars)
    for row in sel.to_dict("records"):
        if with_volume:
            fig, (ax, vax) = plt.subplots(2, 1, figsize=(11, 6.4), facecolor=SURFACE, sharex=True,
                                          gridspec_kw={"height_ratios": [4, 1]})
        else:
            fig, ax = plt.subplots(figsize=(11, 5.2), facecolor=SURFACE)
            vax = None
        try:
            draw_setup(ax, row, bars, period_seconds, net=row.get("net"), category=row.get("category"), vax=vax)
            fig.tight_layout()
            path = charts_dir / f"setup_{cf._int(row, 'setup_id'):06d}_{row['category']}.png"
            fig.savefig(path, dpi=110, facecolor=SURFACE)
            charts.append(str(path))
        finally:
            plt.close(fig)
        rows.append(_table_row(row, size, fvg_vol_ratios(row, bars, period_seconds)))
    lines = [
        "# Pilot setups for the R39 chart gate",
        "",
        "Bar-time columns (OB, identifying FVG, activation, touch, confirmation FVG) show the bar open time in "
        "server time; for the two FVG columns this is candle 3. placement, fill and exit are tick times with "
        "milliseconds. Net is profit + commission + swap; realized R = net / (|intended entry - SL| x volume x "
        f"contract size {size:g}). idFVG / cFVG vol ratio = tick volume of the FVG's middle candle / mean tick "
        "volume of the VolumeLookbackHours of bars before it, as logged by the EA (recomputed from the bars with "
        f"{VOLUME_LOOKBACK_HOURS} h when not logged); the filter needs >= VolumeMultiplier (R41).",
        "",
        "| " + " | ".join(TABLE_COLUMNS) + " |",
        "|" + "|".join("---" for _ in TABLE_COLUMNS) + "|",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    lines += ["", "Chart stage labels: " + ", ".join(STAGE_LABELS) + ".", "",
              "Missing categories: " + (", ".join(missing) if missing else "none") + "."]
    table = out_dir / "setups_table.md"
    table.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"charts": charts, "table": str(table), "missing": missing}
