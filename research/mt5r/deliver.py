"""Deliverable tables and charts (U9). Every number comes from results/ files; missing keys raise."""
import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import evaluate  # noqa: E402
from .pipeline import DEFAULTS, PARAMS  # noqa: E402,F401
from .textio import read_text  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"   # validated categorical slots 1-3 (light surface)
INK, MUTED, SURFACE = "#1f1f1e", "#6b6a64", "#fcfcfb"


def load() -> dict:
    return {"acc": json.loads((REPO / "results" / "acceptance.json").read_text()),
            "folds": json.loads((REPO / "results" / "wfo" / "folds.json").read_text()),
            "final": json.loads((REPO / "results" / "final_selection" / "selection.json").read_text()),
            "prereg": json.loads((REPO / "research" / "preregistration.json").read_text())}


def parameter_table(d: dict) -> str:
    grid = d["prereg"]["grid"]
    cand = d["final"]["params"]
    rows = ["| Input | Original (code default) | Candidate | Range tested | Reason |", "|---|---|---|---|---|"]
    for p in PARAMS:
        why = ("unchanged: selected value equals the default" if cand[p] == DEFAULTS[p]
               else "highest neighbor-smoothed after-cost recovery factor on 2026.05.01-07.31 (R16/R17)")
        rows.append(f"| `{p}` | {DEFAULTS[p]} | {cand[p]} | {', '.join(str(v) for v in grid[p])} | {why} |")
    rows.append("| `SignalTF` | M5 (v1.03 had no input; chart was M5) | M15 | M15 only | user decision (research on 15-minute bars) |")
    rows.append("| all other inputs | code defaults | code defaults | not optimized | fixed by protocol (risk 1%, 3 positions, logic switches) |")
    return "\n".join(rows)


def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(axis="y", color="#e6e5df", linewidth=0.6)
    ax.set_axisbelow(True)


def charts(d: dict, out: pathlib.Path) -> list:
    out.mkdir(parents=True, exist_ok=True)
    made = []
    curated = REPO / "results" / "wfo"
    proc = evaluate.stitch([evaluate.load_run(f["oos"]["procedure"]["run_id"], curated) for f in d["folds"]])
    base = evaluate.stitch([evaluate.load_run(f["oos"]["baseline"]["run_id"], curated) for f in d["folds"]])

    fig, ax = plt.subplots(figsize=(9.5, 3.6), facecolor=SURFACE)
    _style(ax)
    for s, color, label in ((proc, BLUE, "WFO procedure"), (base, ORANGE, "Baseline (code defaults, M15)")):
        ax.plot(s["days"]["date"], s["days"]["eq_close"], color=color, linewidth=2, label=label)
        ax.annotate(f"{label}: {s['days']['eq_close'].iloc[-1]:,.0f}", (s["days"]["date"].iloc[-1], s["days"]["eq_close"].iloc[-1]),
                    xytext=(4, 0), textcoords="offset points", fontsize=8, color=INK, va="center")
    ax.axhline(9000, color=MUTED, linewidth=1, linestyle="--")
    ax.text(proc["days"]["date"].iloc[0], 9010, "total loss limit 9,000", fontsize=7, color=MUTED)
    ax.set_title("Stitched OOS equity, Mar-Jul 2026 (USD, daily close)", fontsize=10, color=INK, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="center left", bbox_to_anchor=(0.0, 0.42))
    ax.set_xlim(right=proc["days"]["date"].iloc[-1] + (proc["days"]["date"].iloc[-1] - proc["days"]["date"].iloc[0]) * 0.35)
    fig.tight_layout()
    fig.savefig(out / "oos_equity.png", dpi=150)
    plt.close(fig)
    made.append("oos_equity.png")

    folds = d["folds"]
    x = np.arange(len(folds))
    fig, ax = plt.subplots(figsize=(7, 3.2), facecolor=SURFACE)
    _style(ax)
    pv = [f["oos"]["procedure"]["net_profit"] for f in folds]
    bv = [f["oos"]["baseline"]["net_profit"] for f in folds]
    ax.bar(x - 0.2, pv, width=0.38, color=BLUE, label="WFO procedure")
    ax.bar(x + 0.2, bv, width=0.38, color=ORANGE, label="Baseline")
    ax.axhline(0, color=MUTED, linewidth=1)
    for i, (pv_i, bv_i, f) in enumerate(zip(pv, bv, folds)):
        ax.text(i - 0.2, pv_i, f"{pv_i:+.0f}\n({f['oos']['procedure']['trades']} tr)", ha="center",
                va="bottom" if pv_i >= 0 else "top", fontsize=7, color=INK)
        if bv_i == 0:
            ax.text(i + 0.2, 0, "no trades", ha="center", va="bottom", fontsize=7, color=MUTED)
    ax.margins(y=0.25)
    ax.set_xticks(x, [f["test"][0][:7] for f in folds])
    ax.set_title("OOS net profit per fold (USD)", fontsize=10, color=INK, loc="left")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "oos_by_fold.png", dpi=150)
    plt.close(fig)
    made.append("oos_by_fold.png")

    mc = d["acc"]["acceptance"]["f_loss_limits"]["value"]["monte_carlo"]
    if mc:
        fig, ax = plt.subplots(figsize=(6, 3), facecolor=SURFACE)
        _style(ax)
        keys = sorted(k for k in mc if k.startswith("final_return_p"))
        vals = [mc[k] * 100 for k in keys]
        ax.bar(range(len(keys)), vals, color=BLUE, width=0.6)
        ax.set_xticks(range(len(keys)), [k.replace("final_return_", "") for k in keys])
        ax.axhline(0, color=MUTED, linewidth=1)
        for i, v in enumerate(vals):
            ax.text(i, v, f"{v:.1f}%", ha="center", va="bottom" if v >= 0 else "top", fontsize=8, color=INK)
        ax.set_title(f"Block-bootstrap 1-year return percentiles (breach prob {mc['breach_prob']:.1%})",
                     fontsize=9, color=INK, loc="left")
        fig.tight_layout()
        fig.savefig(out / "mc_return_percentiles.png", dpi=150)
        plt.close(fig)
        made.append("mc_return_percentiles.png")
    return made


def should_write_recommended(acc_json: dict) -> bool:
    """R28: only when every R27 criterion passed AND the combined recommended flag (checks + independence) holds."""
    return bool(acc_json.get("recommended")) and bool(acc_json["acceptance"].get("_passed_all"))


def set_file_lines(path) -> list:
    """[TesterInputs] lines taken verbatim from a .set file (comments dropped)."""
    text = read_text(path, fallback="utf-8-sig")
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith(";")]
