"""Non-gating diagnostics of the M5 OB + M1 structure research (R32) from saved evidence only: no tester run, no
optimization, no rule or exit change. Series: the WFO procedure, fixed A and fixed B (5 OOS folds each), and the
frozen candidate's stability base run over Mar-Jul 2026 (May-Jul overlaps its final training window; reference only).

Definitions (per position; R = |intended entry - SL| in price):
- R after costs: net (profit + swap + commission) / (R x volume x contract size), as in the trade table.
- R at planned levels (before costs): +planned RR for a TP exit, -1 for an SL exit, the after-cost value otherwise.
- spread_r: the day's median spread / R.
- MFE / MAE: most favourable / adverse excursion from the fill price, in R, on the logged M1 Bid bars. Longs use
  Bid; shorts use Bid + the day's median spread as Ask. `strict` uses only bars strictly between the fill and exit
  bars (a lower bound); `inclusive` adds both (an upper bound).
- +1R then stop: SL exits whose MFE reached +1R; `definite` on a bar strictly between, `possible` only on the fill
  or exit bar.
Random-entry null: for every position, 20 random entries (seed 20260930) at M1 bar opens of the same run, same side,
same R distance and the same 2R target, resolved on the M1 bars (+2R or -1R first; +1R or -1R first; both on one
bar reported apart). It shows what the stop/target geometry yields without the setup's timing.

python research/wfo_diagnostics.py -> results/diagnostics/wfo_diagnostics.json
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cli  # noqa: E402
from mt5r import conformance_m1 as cm, evaluate, reports, trades  # noqa: E402

OUT = cli.RESULTS / "diagnostics"
SEED, P = 20260930, 60
CONTRACT = evaluate.CONTRACT_SIZE


def series_runs():
    folds = json.loads(cli.FOLDS.read_text())
    out = {w: [("wfo", f["oos"][w]["run_id"]) for f in folds] for w in cli.SERIES}
    if (cli.RESULTS / "robustness" / "stability_candidate").exists():
        out["candidate_static"] = [("robustness", "stability_candidate")]
    return out


def _load(folder, run_id):
    d = cli.RESULTS / folder / run_id
    run = cm.read_run(d, run_id)
    deals = reports.read_deals(d / f"rl_deals_{run_id}.csv")
    days = reports.read_days(d / f"rl_days_{run_id}.csv")
    setups = pd.read_csv(d / f"rl_setups_{run_id}.csv", keep_default_na=False)
    return run, deals, days, setups


def run_positions(folder: str, run_id: str):
    run, deals, days, setups = _load(folder, run_id)
    tab = trades.trade_table(setups, deals)
    s = run["setups"]
    s = s[s["position_id"].notna()].copy()
    s["position_id"] = s["position_id"].astype(float)
    tab["position_id"] = pd.to_numeric(tab["position_id"]).astype(float)
    t = tab.merge(s[["position_id", "fill_msc", "exit_msc"]], on="position_id", how="left")
    spread = evaluate.spread_by_day(days)
    B = cm._Bars(run["bars_m1"], P)
    t_ms = np.asarray(B.t, dtype=np.int64) * 1000
    hi_b, lo_b = np.asarray(B.h), np.asarray(B.l)
    rows = []
    for r in t.to_dict("records"):
        risk = abs(float(r["intended_entry"]) - float(r["sl"])) if pd.notna(r["intended_entry"]) else np.nan
        sign = 1 if r["dir"] == "L" else -1
        sp = float(spread.get(pd.Timestamp(r["open_time"]).normalize(), np.nan))
        at = lambda ms: int(np.searchsorted(t_ms, int(ms), side="right")) - 1  # noqa: E731
        fb = at(r["fill_msc"]) if pd.notna(r["fill_msc"]) else None
        eb = at(r["exit_msc"]) if pd.notna(r["exit_msc"]) else B.n - 1
        fb = None if fb is None or fb < 0 else fb
        x = {"run_id": run_id, "setup_id": r["setup_id"], "dir": r["dir"], "open_time": r["open_time"],
             "exit_kind": r["exit_kind"], "net": r["net"], "risk_price": risk, "planned_rr": r["planned_rr"],
             "r_net": r["realized_r"], "spread_r": sp / risk if risk else np.nan}
        x["r_levels"] = (r["planned_rr"] if r["exit_kind"] == "tp" else -1.0 if r["exit_kind"] == "sl"
                         else r["realized_r"])
        if fb is not None and risk and np.isfinite(risk):
            fill = float(r["fill_price"])
            off = sp if sign < 0 else 0.0
            hi, lo = hi_b + off, lo_b + off

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
    return pd.DataFrame(rows), B, spread


def stats_of(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"trades": 0}
    w, l = df[df["net"] > 0], df[df["net"] <= 0]
    sl = df[df["exit_kind"] == "sl"]
    col = lambda c: df[c] if c in df else pd.Series(np.nan, index=df.index)  # noqa: E731
    mfe_s, mfe_i = col("mfe_strict").fillna(-np.inf), col("mfe_incl").fillna(-np.inf)
    reach = lambda k: {"strict": int((mfe_s >= k).sum()), "inclusive": int((mfe_i >= k).sum())}  # noqa: E731
    sl_s = mfe_s[sl.index]
    sl_i = mfe_i[sl.index]
    q = lambda s, p: round(float(np.nanquantile(s, p)), 3) if s.notna().any() else None  # noqa: E731
    return {
        "trades": int(len(df)), "net_usd": round(float(df["net"].sum()), 2),
        "win_rate": round(float((df["net"] > 0).mean()), 4),
        "avg_win_r": round(float(w["r_net"].mean()), 3) if len(w) else None,
        "avg_loss_r": round(float(l["r_net"].mean()), 3) if len(l) else None,
        "expectancy_r_after_costs": round(float(df["r_net"].mean()), 4),
        "expectancy_r_at_levels": round(float(df["r_levels"].mean()), 4),
        "exit_kinds": {k: int(v) for k, v in df["exit_kind"].value_counts().items()},
        "spread_r_median": q(df["spread_r"], .5), "risk_price_median": q(df["risk_price"], .5),
        "mfe_r_median": {"strict": q(col("mfe_strict"), .5), "inclusive": q(col("mfe_incl"), .5)},
        "mae_r_median": {"strict": q(col("mae_strict"), .5), "inclusive": q(col("mae_incl"), .5)},
        "reached_r": {f"{k:g}R": reach(k) for k in (0.5, 1.0, 1.5, 2.0)},
        "sl_exits": int(len(sl)),
        "sl_after_plus_1r": {"definite": int((sl_s >= 1.0).sum()),
                             "possible_same_bar": int(((sl_i >= 1.0) & ~(sl_s >= 1.0)).sum())},
        "bars_held_median": q(col("bars_held"), .5),
    }


def race(hi, lo, sign, start, entry, risk, up_r, down_r):
    """First of +up_r / -down_r (in R from entry) from bar `start`: 'up', 'down', 'same_bar' or None."""
    if sign > 0:
        u = np.flatnonzero(hi[start:] - entry >= up_r * risk)
        d = np.flatnonzero(entry - lo[start:] >= down_r * risk)
    else:
        u = np.flatnonzero(entry - lo[start:] >= up_r * risk)
        d = np.flatnonzero(hi[start:] - entry >= down_r * risk)
    iu, idn = (u[0] if len(u) else None), (d[0] if len(d) else None)
    if iu is None and idn is None:
        return None
    if iu is not None and idn is not None and iu == idn:
        return "same_bar"
    if idn is None or (iu is not None and iu < idn):
        return "up"
    return "down"


def random_entry_null(pos: pd.DataFrame, B, spread, k: int = 20) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    hi_b, lo_b, o_b = np.asarray(B.h), np.asarray(B.l), np.asarray(B.o)
    rows = []
    for r in pos.to_dict("records"):
        if not np.isfinite(r.get("risk_price", np.nan)) or not np.isfinite(r.get("planned_rr", np.nan)):
            continue
        sign = 1 if r["dir"] == "L" else -1
        for j in rng.integers(0, B.n - 1, size=k):
            sp = float(spread.get(pd.Timestamp(int(B.t[j]), unit="s").normalize(), 0.2))
            off = sp if sign < 0 else 0.0
            entry = o_b[j] + (sp if sign > 0 else 0.0)          # buy at Ask, sell at Bid
            rows.append({"dir": r["dir"],
                         "tp_vs_sl": race(hi_b + off, lo_b + off, sign, j, entry, r["risk_price"], r["planned_rr"], 1.0),
                         "one_r_vs_sl": race(hi_b + off, lo_b + off, sign, j, entry, r["risk_price"], 1.0, 1.0)})
    return pd.DataFrame(rows)


def null_summary(nul: pd.DataFrame) -> dict:
    out = {}
    for side, g in (("all", nul), ("long", nul[nul["dir"] == "L"]), ("short", nul[nul["dir"] == "S"])):
        res = {}
        for c_ in ("tp_vs_sl", "one_r_vs_sl"):
            c = g[c_].value_counts()
            decided = int(c.get("up", 0) + c.get("down", 0))
            res[c_] = {"up_first": int(c.get("up", 0)), "down_first": int(c.get("down", 0)),
                       "same_bar": int(c.get("same_bar", 0)), "unresolved": int(g[c_].isna().sum()),
                       "share_up": round(c.get("up", 0) / decided, 4) if decided else None}
        out[side] = res
    return out


def actual_tp_vs_sl(df: pd.DataFrame) -> dict:
    out = {}
    for side, g in (("all", df), ("long", df[df["dir"] == "L"]), ("short", df[df["dir"] == "S"])):
        tp, sl = int((g["exit_kind"] == "tp").sum()), int((g["exit_kind"] == "sl").sum())
        out[side] = {"tp_first": tp, "sl_first": sl, "share_tp": round(tp / (tp + sl), 4) if tp + sl else None}
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    res = {"definitions": __doc__.split("Definitions")[1].split("python research")[0].strip()}
    for name, runs in series_runs().items():
        parts, nulls = [], []
        for f, r in runs:
            df, B, spread = run_positions(f, r)
            parts.append(df)
            nulls.append(random_entry_null(df, B, spread))
        df = pd.concat(parts, ignore_index=True)
        df.to_csv(OUT / f"positions_{name}.csv", index=False)
        res[name] = {"overall": stats_of(df), "long": stats_of(df[df["dir"] == "L"]),
                     "short": stats_of(df[df["dir"] == "S"]), "actual_tp_vs_sl": actual_tp_vs_sl(df),
                     "random_entry_null": null_summary(pd.concat(nulls, ignore_index=True))}
    evaluate.save(res, OUT / "wfo_diagnostics.json")
    print(json.dumps({k: v["overall"] for k, v in res.items() if isinstance(v, dict) and "overall" in v},
                     indent=1, default=str))


if __name__ == "__main__":
    main()
