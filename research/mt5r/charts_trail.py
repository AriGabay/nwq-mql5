"""Charts of the 1R trailing stop (plan docs/plans/2026-10-05-0007-feat-trailing-stop-1r-plan.md, U4).

One PNG per selected position:
- the M1 Bid bars (high-low band and close) from shortly before the fill to after the exit;
- E, SL0 and TP;
- the stop actually on the position, as a step line from the accepted modifications (rl_sl_moves);
- the activation and the exit.

Everything is read from the research logs. Nothing is re-simulated.
"""
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

PAD_MIN = 15


def stop_series(moves: pd.DataFrame, position_id: int, sl0: float, fill_ms: int, exit_ms: int) -> list:
    """[(ms, stop)] from the fill to the exit: SL0, then every accepted modification, held to the exit."""
    out = [(fill_ms, sl0)]
    if moves is not None and len(moves):
        own = moves[(moves["position_id"].astype(int) == int(position_id))
                    & (moves["outcome"].astype(str) == "accepted")].sort_values("tick_msc", kind="mergesort")
        out += [(int(r.tick_msc), float(r.accepted_sl)) for r in own.itertuples()]
    out.append((exit_ms, out[-1][1]))
    return out


def select(trail: pd.DataFrame, per_class: int = 2) -> list:
    """Deterministic picks: the first ``per_class`` positions of each exit class (trail, tp, sl, end) and side."""
    picks = []
    for kind in ("trail", "tp", "sl", "end"):
        for d in ("L", "S"):
            sub = trail[(trail["exit_kind"].astype(str) == kind) & (trail["dir"].astype(str) == d)]
            picks += [int(x) for x in sub["position_id"].head(per_class)]
    return picks


def draw(bars_m1: pd.DataFrame, setup: dict, trail_row: dict, moves: pd.DataFrame, path) -> list:
    """Draw one position; returns its stop series (for tests)."""
    fill_ms, exit_ms = int(setup["fill_msc"]), int(setup["exit_msc"])
    E, sl0, tp = float(trail_row["fill_price"]), float(trail_row["sl0"]), float(trail_row["tp"])
    r0 = float(trail_row["r0"])
    sign = 1 if str(trail_row["dir"]) == "L" else -1
    lo_t, hi_t = fill_ms // 1000 - PAD_MIN * 60, exit_ms // 1000 + PAD_MIN * 60
    b = bars_m1[(bars_m1["time"] >= lo_t) & (bars_m1["time"] <= hi_t)]
    bt = pd.to_datetime(b["time"], unit="s")
    series = stop_series(moves, trail_row["position_id"], sl0, fill_ms, exit_ms)
    fig, ax = plt.subplots(figsize=(11, 5.2))
    ax.fill_between(bt, b["low"], b["high"], step="post", color="#9fb3c8", alpha=0.45, label="M1 Bid high-low")
    ax.plot(bt, b["close"], color="#3b5b7a", lw=0.8, drawstyle="steps-post", label="M1 Bid close")
    span = [pd.to_datetime(fill_ms, unit="ms"), pd.to_datetime(exit_ms, unit="ms")]
    ax.hlines(E, *span, color="#555555", ls=":", lw=1.1, label=f"E {E:.2f}")
    ax.hlines(sl0, *span, color="#e34948", ls="--", lw=1.0, label=f"SL0 {sl0:.2f}")
    ax.hlines(tp, *span, color="#008300", ls="--", lw=1.0, label=f"TP {tp:.2f} (fixed)")
    ax.hlines(E + sign * r0, *span, color="#c58b00", ls=":", lw=0.9, label=f"+1R {E + sign * r0:.2f}")
    st = pd.DataFrame(series, columns=["ms", "sl"])
    ax.step(pd.to_datetime(st["ms"], unit="ms"), st["sl"], where="post", color="#b00020", lw=1.8,
            label="stop on the position")
    act = trail_row.get("activated_msc")
    if act is not None and not pd.isna(act) and str(act) != "":
        px = float(trail_row["activation_bid"] if sign > 0 else trail_row["activation_ask"])
        ax.plot([pd.to_datetime(int(float(act)), unit="ms")], [px], marker="^" if sign > 0 else "v", ms=10,
                color="#c58b00", ls="none", label="trail activated")
    xp = float(setup["exit_price"])
    ax.plot([pd.to_datetime(exit_ms, unit="ms")], [xp], marker="X", ms=10, color="black", ls="none",
            label=f"exit ({setup['exit_kind']}) {xp:.2f}")
    price_r = (xp - E) * sign / r0 if r0 else float("nan")
    ax.set_title(f"setup {trail_row['setup_id']} position {trail_row['position_id']} "
                 f"{'LONG' if sign > 0 else 'SHORT'} - exit {setup['exit_kind']} at {price_r:+.2f}R (price, vs R0 "
                 f"{r0:.2f}) - development run, not validation", fontsize=10)
    ax.legend(loc="best", fontsize=7.5)
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return series


def render(run: dict, out_dir, tag: str, per_class: int = 2) -> list:
    """Charts for the deterministic picks of one trailed run (run = conformance_m1.read_run output)."""
    trail, moves, setups = run["trail"], run.get("sl_moves"), run["setups"]
    by_pos = {int(r["position_id"]): r for r in setups.to_dict("records")
              if r.get("position_id") is not None and not pd.isna(r.get("position_id"))}
    rows = {int(r["position_id"]): r for r in trail.to_dict("records")}
    out = []
    for pid in select(trail, per_class):
        s, t = by_pos.get(pid), rows[pid]
        if s is None:
            continue
        p = pathlib.Path(out_dir) / f"{tag}_{t['dir']}_{t['exit_kind']}_pos{pid}.png"
        draw(run["bars_m1"], s, t, moves, p)
        out.append(p)
    return out
