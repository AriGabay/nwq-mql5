"""Diagnostics of the closed (failed) OB-FVG research from saved WFO evidence only: no tester run, no optimization,
no rule or exit change. Series: the WFO procedure (5 OOS folds), the baseline (5 OOS folds), and the final candidate
run statically over Mar-Jul 2026 (May-Jul overlaps its training window; shown for reference only).

Definitions (per position; R = |intended entry - SL| in price):
- R after costs: net (profit + swap + commission) / (R x volume x contract size), as in the R40 trade table.
- R at planned levels (before costs): +planned RR for a TP exit, -1 for an SL exit, the after-cost value for a
  run-end exit. It removes slippage, swap and commission; spread is not removed (see spread_r).
- spread_r: the day's median spread / R, an indication of how much of one R the spread costs.
- MFE / MAE: most favourable / adverse excursion from the fill price, in R, from the logged M15 Bid bars. Longs use
  Bid (they exit at Bid); shorts use Bid + the day's median spread as Ask. Bar granularity: the fill bar may contain
  price before the fill and the exit bar price after the exit, so `strict` uses only bars strictly between them (a
  lower bound) and `inclusive` adds both (an upper bound).
- +1R then stop: SL exits whose MFE reached +1R. `definite` = reached on a bar strictly between fill and exit
  bars; `possible` = only on the fill or exit bar, where the order inside the bar is unknown.
Random-entry null: for every position, 20 random entries (seed 20260930) at bar opens of the same run, same
side, same R distance and the same 2R target, resolved on the M15 bars (+2R or -1R first; +1R or -1R first; hits
of both on one bar reported apart). It says what the stop/target geometry yields without the setup's timing.
Examples: per series, seed 20260930, two positions per category (TP winner; SL after a definite +1R; SL that never
reached +0.5R even inclusively), drawn uniformly without replacement, shown in time order.

python research/wfo_diagnostics.py -> results/diagnostics/{wfo_diagnostics.json, wfo_diagnostics.md, charts/}
"""
import json
import pathlib
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cli  # noqa: E402
from mt5r import charts_setups as cs, conformance as cf, evaluate, reports, trades  # noqa: E402

OUT = cli.RESULTS / "diagnostics"
SEED, P = 20260930, 900
CONTRACT = evaluate.CONTRACT_SIZE


def series_runs():
    folds = json.loads(cli.FOLDS.read_text())
    return {"procedure": [("wfo", f["oos"]["procedure"]["run_id"]) for f in folds],
            "baseline": [("wfo", f["oos"]["baseline"]["run_id"]) for f in folds],
            "candidate_static": [("robustness", "static_candidate")]}


def run_positions(folder: str, run_id: str) -> pd.DataFrame:
    d = cli.RESULTS / folder / run_id
    setups = pd.read_csv(d / f"rl_setups_{run_id}.csv", keep_default_na=False)
    deals = reports.read_deals(d / f"rl_deals_{run_id}.csv")
    days = reports.read_days(d / f"rl_days_{run_id}.csv")
    bars = cf.read_bars(d / f"rl_bars_{run_id}.csv")
    tab = trades.trade_table(setups, deals)
    s = cf.read_setups(d / f"rl_setups_{run_id}.csv")
    s = s[s["position_id"].notna()].copy()
    s["position_id"] = s["position_id"].astype(float)
    tab["position_id"] = pd.to_numeric(tab["position_id"]).astype(float)
    t = tab.merge(s[["position_id", "fill_time_msc", "exit_time_msc", "exit_price"]], on="position_id", how="left")
    spread = evaluate.spread_by_day(days)
    B = cf._Bars(bars, P)
    rows = []
    for r in t.to_dict("records"):
        risk = abs(float(r["intended_entry"]) - float(r["sl"])) if pd.notna(r["intended_entry"]) else np.nan
        sign = 1 if r["dir"] == "L" else -1
        day = pd.Timestamp(r["open_time"]).normalize()
        sp = float(spread.get(day, np.nan))
        # the last bar opening at or before the event (an exit can fall on a bar boundary or in a closure gap)
        at = lambda ms: int(np.searchsorted(B.t * 1000, int(ms), side="right")) - 1  # noqa: E731
        fb = at(r["fill_time_msc"]) if pd.notna(r["fill_time_msc"]) else None
        eb = at(r["exit_time_msc"]) if pd.notna(r["exit_time_msc"]) else B.n - 1
        fb = None if fb is None or fb < 0 else fb
        x = {"run_id": run_id, "setup_id": r["setup_id"], "dir": r["dir"], "open_time": r["open_time"],
             "exit_kind": r["exit_kind"], "net": r["net"], "risk_price": risk, "planned_rr": r["planned_rr"],
             "r_net": r["realized_r"], "spread_r": sp / risk if risk else np.nan}
        x["r_levels"] = (r["planned_rr"] if r["exit_kind"] == "tp" else -1.0 if r["exit_kind"] == "sl"
                         else r["realized_r"])
        if fb is None or not risk or not np.isfinite(risk):
            rows.append(x)
            continue
        fill = float(r["fill_price"])
        off = sp if sign < 0 else 0.0          # shorts close at Ask ~ Bid + spread
        hi, lo = B.h + off, B.l + off

        def exc(a, b):
            if b < a:
                return np.nan, np.nan
            fav = (hi[a:b + 1].max() - fill) if sign > 0 else (fill - lo[a:b + 1].min())
            adv = (fill - lo[a:b + 1].min()) if sign > 0 else (hi[a:b + 1].max() - fill)
            return fav / risk, adv / risk

        x["mfe_strict"], x["mae_strict"] = exc(fb + 1, eb - 1)
        x["mfe_incl"], x["mae_incl"] = exc(fb, eb)
        x["bars_held"] = eb - fb
        rows.append(x)
    return pd.DataFrame(rows)


def stats_of(df: pd.DataFrame) -> dict:
    w, l = df[df["net"] > 0], df[df["net"] <= 0]
    sl = df[df["exit_kind"] == "sl"]
    mfe_s, mfe_i = df["mfe_strict"].fillna(-np.inf), df["mfe_incl"].fillna(-np.inf)
    reach = lambda k: {"strict": int((mfe_s >= k).sum()), "inclusive": int((mfe_i >= k).sum())}
    one_def = sl[sl["mfe_strict"].fillna(-np.inf) >= 1.0]
    one_pos = sl[(sl["mfe_incl"].fillna(-np.inf) >= 1.0) & ~(sl["mfe_strict"].fillna(-np.inf) >= 1.0)]
    q = lambda s, p: round(float(np.nanquantile(s, p)), 3) if s.notna().any() else None
    return {
        "trades": int(len(df)), "net_usd": round(float(df["net"].sum()), 2),
        "win_rate": round(float((df["net"] > 0).mean()), 4) if len(df) else None,
        "avg_win_r": round(float(w["r_net"].mean()), 3) if len(w) else None,
        "avg_loss_r": round(float(l["r_net"].mean()), 3) if len(l) else None,
        "expectancy_r_after_costs": round(float(df["r_net"].mean()), 4),
        "expectancy_r_at_levels": round(float(df["r_levels"].mean()), 4),
        "exit_kinds": {k: int(v) for k, v in df["exit_kind"].value_counts().items()},
        "spread_r_median": q(df["spread_r"], .5), "risk_price_median": q(df["risk_price"], .5),
        "mfe_r_median": {"strict": q(df["mfe_strict"], .5), "inclusive": q(df["mfe_incl"], .5)},
        "mae_r_median": {"strict": q(df["mae_strict"], .5), "inclusive": q(df["mae_incl"], .5)},
        "reached_r": {f"{k:g}R": reach(k) for k in (0.5, 1.0, 1.5, 2.0)},
        "sl_exits": int(len(sl)),
        "sl_after_plus_1r": {"definite": int(len(one_def)), "possible_same_bar": int(len(one_pos))},
        "tp_exits_after_plus_1r_strict": int(((df["exit_kind"] == "tp") & (mfe_s >= 1.0)).sum()),
        "bars_held_median": q(df.get("bars_held", pd.Series(dtype=float)), .5),
    }


def race(B, sign, start, entry, risk, up_r, down_r, off):
    """First of +up_r / -down_r (in R from entry) on bars from `start`: 'up', 'down', 'same_bar' or None."""
    hi, lo = B.h + off, B.l + off
    for j in range(start, B.n):
        fav = (hi[j] - entry) if sign > 0 else (entry - lo[j])
        adv = (entry - lo[j]) if sign > 0 else (hi[j] - entry)
        u, d = fav >= up_r * risk, adv >= down_r * risk
        if u and d:
            return "same_bar"
        if u:
            return "up"
        if d:
            return "down"
    return None


def random_entry_null(folder: str, run_id: str, pos: pd.DataFrame, k: int = 20) -> pd.DataFrame:
    """For each position of the run, k random entries (seed 20260930) at bar opens of the same run, same side and same
    R: does +2R or -1R come first, and +1R or -1R first? Bar-level (same-bar hits reported apart). Diagnostic only."""
    d = cli.RESULTS / folder / run_id
    bars = cf.read_bars(d / f"rl_bars_{run_id}.csv")
    spread = evaluate.spread_by_day(reports.read_days(d / f"rl_days_{run_id}.csv"))
    B = cf._Bars(bars, P)
    rng = np.random.default_rng(SEED)
    rows = []
    for r in pos[pos["run_id"] == run_id].to_dict("records"):
        if not np.isfinite(r.get("risk_price", np.nan)):
            continue
        sign = 1 if r["dir"] == "L" else -1
        for j in rng.integers(0, B.n - 1, size=k):
            day = pd.Timestamp(int(B.t[j]), unit="s").normalize()
            off = float(spread.get(day, 0.2)) if sign < 0 else 0.0
            entry = B.o[j] + (float(spread.get(day, 0.2)) if sign > 0 else 0.0)   # buy at Ask, sell at Bid
            rows.append({"dir": r["dir"], "tp_vs_sl": race(B, sign, j, entry, r["risk_price"], r["planned_rr"], 1.0, off),
                         "one_r_vs_sl": race(B, sign, j, entry, r["risk_price"], 1.0, 1.0, off)})
    return pd.DataFrame(rows)


def actual_races(df: pd.DataFrame) -> dict:
    """Actual positions on the same bar method: +1R or -1R first, from the bar after the fill bar."""
    out = {}
    for side, g in (("all", df), ("long", df[df["dir"] == "L"]), ("short", df[df["dir"] == "S"])):
        out[side] = {"tp_first": int((g["exit_kind"] == "tp").sum()), "sl_first": int((g["exit_kind"] == "sl").sum())}
    return out


def null_summary(nul: pd.DataFrame) -> dict:
    out = {}
    for side, g in (("all", nul), ("long", nul[nul["dir"] == "L"]), ("short", nul[nul["dir"] == "S"])):
        res = {}
        for col, win in (("tp_vs_sl", "up"), ("one_r_vs_sl", "up")):
            c = g[col].value_counts()
            decided = int(c.get("up", 0) + c.get("down", 0))
            res[col] = {"up_first": int(c.get("up", 0)), "down_first": int(c.get("down", 0)),
                        "same_bar": int(c.get("same_bar", 0)), "unresolved": int(g[col].isna().sum()),
                        "share_up": round(c.get("up", 0) / decided, 4) if decided else None}
        out[side] = res
    return out


def drift(folder: str, runs: list) -> dict:
    first = cf.read_bars(cli.RESULTS / folder / runs[0] / f"rl_bars_{runs[0]}.csv")
    last = cf.read_bars(cli.RESULTS / folder / runs[-1] / f"rl_bars_{runs[-1]}.csv")
    a, b = float(first["open"].iloc[0]), float(last["close"].iloc[-1])
    return {"from": a, "to": b, "change_pct": round(100 * (b / a - 1), 2)}


def examples(name: str, df: pd.DataFrame) -> list:
    rng = np.random.default_rng(SEED)
    cats = {"tp_winner": df[df["exit_kind"] == "tp"],
            "sl_after_definite_1r": df[(df["exit_kind"] == "sl") & (df["mfe_strict"].fillna(-np.inf) >= 1.0)],
            "sl_never_half_r": df[(df["exit_kind"] == "sl") & (df["mfe_incl"].fillna(np.inf) < 0.5)]}
    out = []
    for cat, pool in cats.items():
        if pool.empty:
            continue
        idx = np.sort(rng.choice(len(pool), size=min(2, len(pool)), replace=False))
        for _, r in pool.iloc[idx].sort_values("open_time").iterrows():
            out.append({"series": name, "category": cat, **{k: r[k] for k in (
                "run_id", "setup_id", "dir", "open_time", "exit_kind", "net", "r_net", "r_levels", "risk_price",
                "spread_r", "mfe_strict", "mfe_incl", "mae_incl")}})
    return out


def chart(ex: dict, folder: str) -> str:
    d = cli.RESULTS / folder / ex["run_id"]
    setups = cf.read_setups(d / f"rl_setups_{ex['run_id']}.csv")
    bars = cf.read_bars(d / f"rl_bars_{ex['run_id']}.csv")
    row = setups[setups["setup_id"] == int(ex["setup_id"])].iloc[0]
    fig, ax = plt.subplots(figsize=(11, 5.2), facecolor=cs.SURFACE)
    try:
        cs.draw_setup(ax, row, bars, P, net=ex["net"], category=f"{ex['series']} {ex['category']}")
        fig.tight_layout()
        path = OUT / "charts" / f"{ex['series']}_{ex['category']}_{ex['run_id']}_{int(ex['setup_id']):06d}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=110, facecolor=cs.SURFACE)
    finally:
        plt.close(fig)
    return str(path.relative_to(cli.REPO).as_posix())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    res, all_ex = {"definitions": __doc__.split("Definitions")[1].split("python research")[0].strip()}, []
    for name, runs in series_runs().items():
        df = pd.concat([run_positions(f, r) for f, r in runs], ignore_index=True)
        df.to_csv(OUT / f"positions_{name}.csv", index=False)
        res[name] = {"overall": stats_of(df), "long": stats_of(df[df["dir"] == "L"]),
                     "short": stats_of(df[df["dir"] == "S"]), "actual_tp_vs_sl": actual_races(df)}
        nul = pd.concat([random_entry_null(f, r, df) for f, r in runs], ignore_index=True)
        res[name]["random_entry_null"] = null_summary(nul)
        res[name]["price_drift"] = drift(runs[0][0], [r for _, r in runs])
        if name != "candidate_static":
            folder = runs[0][0]
            for ex in examples(name, df):
                ex["chart"] = chart(ex, folder)
                all_ex.append(ex)
    res["examples"] = all_ex
    evaluate.save(res, OUT / "wfo_diagnostics.json")
    print(json.dumps({k: v["overall"] for k, v in res.items() if isinstance(v, dict) and "overall" in v},
                     indent=1, default=str))


if __name__ == "__main__":
    main()
