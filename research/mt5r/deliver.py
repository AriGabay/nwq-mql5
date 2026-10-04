"""Deliverable tables and charts (U10). Callers pass the data and the output paths; nothing is hard-coded."""
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .textio import read_text  # noqa: E402

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"   # validated categorical slots 1-3 (light surface)
INK, MUTED, SURFACE = "#1f1f1e", "#6b6a64", "#fcfcfb"


def parameter_table(prereg: dict, candidate: dict) -> str:
    """R32: default, tested range and candidate value per optimized input, plus the fixed rows."""
    grid, defaults, cat = prereg["grid"], prereg["defaults"], set(prereg.get("categorical", []))
    rows = ["| Input | Default | Candidate | Range tested | Note |", "|---|---|---|---|---|"]
    for p, values in grid.items():
        rng = ", ".join(str(v) for v in values) + (" (categorical)" if p in cat else "")
        why = ("unchanged: selected value equals the default" if candidate[p] == defaults[p]
               else "pre-registered selection on the final train window (KTD14)")
        rows.append(f"| `{p}` | {defaults[p]} | {candidate[p]} | {rng} | {why} |")
    rows.append("| all other inputs | code defaults | code defaults | not optimized | fixed by KTD14 "
                "(N = 3, buffer 20 points, risk 1%, 3 positions, RR 2.0, warm-up 30 days) |")
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


def charts(curves: dict, folds: list, mc: dict, out: pathlib.Path, window_label: str, stem: str) -> list:
    """Equity curves, per-fold bars and Monte Carlo percentiles.

    curves: {label: {"days": DataFrame(date, eq_close)}} (first = candidate); folds: rows with
    fold, label, candidate, baseline, candidate_trades; mc: block-bootstrap summary or {}.
    Returns the file names written under out.
    """
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    made = []

    fig, ax = plt.subplots(figsize=(9.5, 3.6), facecolor=SURFACE)
    _style(ax)
    first = next(iter(curves.values()))["days"]["date"]
    for (label, s), color in zip(curves.items(), (BLUE, ORANGE, AQUA)):
        d = s["days"]
        ax.plot(d["date"], d["eq_close"], color=color, linewidth=2, label=label)
        ax.annotate(f"{label}: {d['eq_close'].iloc[-1]:,.0f}", (d["date"].iloc[-1], d["eq_close"].iloc[-1]),
                    xytext=(4, 0), textcoords="offset points", fontsize=8, color=INK, va="center")
    ax.axhline(9000, color=MUTED, linewidth=1, linestyle="--")
    ax.text(first.iloc[0], 9010, "total loss limit 9,000", fontsize=7, color=MUTED)
    ax.set_title(f"Equity, {window_label} (USD, daily close, net after costs)", fontsize=10, color=INK, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="best")
    ax.set_xlim(right=first.iloc[-1] + (first.iloc[-1] - first.iloc[0]) * 0.35)
    made.append(_save(fig, out, f"{stem}_equity.png"))

    if folds:
        x = np.arange(len(folds))
        fig, ax = plt.subplots(figsize=(7, 3.2), facecolor=SURFACE)
        _style(ax)
        cv = [f["candidate"] for f in folds]
        bv = [f["baseline"] for f in folds]
        ax.bar(x - 0.2, cv, width=0.38, color=BLUE, label="WFO procedure")
        ax.bar(x + 0.2, bv, width=0.38, color=ORANGE, label="Baseline")
        ax.axhline(0, color=MUTED, linewidth=1)
        for i, (c, f) in enumerate(zip(cv, folds)):
            ax.text(i - 0.2, c, f"{c:+.0f}\n({f['candidate_trades']} tr)", ha="center",
                    va="bottom" if c >= 0 else "top", fontsize=7, color=INK)
        ax.margins(y=0.25)
        ax.set_xticks(x, [f["label"] for f in folds])
        ax.set_title(f"Net profit per fold, {window_label} (USD)", fontsize=10, color=INK, loc="left")
        ax.legend(frameon=False, fontsize=8)
        made.append(_save(fig, out, f"{stem}_by_fold.png"))

    keys = sorted(k for k in (mc or {}) if k.startswith("final_return_p"))
    if keys:
        fig, ax = plt.subplots(figsize=(6, 3), facecolor=SURFACE)
        _style(ax)
        vals = [mc[k] * 100 for k in keys]
        ax.bar(range(len(keys)), vals, color=BLUE, width=0.6)
        ax.set_xticks(range(len(keys)), [k.replace("final_return_", "") for k in keys])
        ax.axhline(0, color=MUTED, linewidth=1)
        for i, v in enumerate(vals):
            ax.text(i, v, f"{v:.1f}%", ha="center", va="bottom" if v >= 0 else "top", fontsize=8, color=INK)
        ax.set_title(f"Block-bootstrap 1-year return percentiles (breach prob {mc['breach_prob']:.1%})",
                     fontsize=9, color=INK, loc="left")
        made.append(_save(fig, out, f"{stem}_mc_return_percentiles.png"))
    return made


def _save(fig, out, name) -> str:
    fig.tight_layout()
    fig.savefig(out / name, dpi=150)
    plt.close(fig)
    return name


def should_write_recommended(acc_json: dict) -> bool:
    """Always False in this research (R33).

    A recommended .set needs validation on an independent period. Every real-tick period is exposed
    (R31: August-September is a non-independent historical check), so even a full acceptance pass yields only
    the candidate with a forward-test protocol. Kept as a function so the deliver step has one place that decides.
    """
    return False


def set_file_lines(path) -> list:
    """[TesterInputs] lines taken verbatim from a .set file (comments dropped)."""
    text = read_text(path, fallback="utf-8-sig")
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith(";")]
