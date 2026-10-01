"""Evidence assembly and the R29 acceptance evaluation (KTD12 thresholds from the pre-registration)."""
import datetime as dt
import json
import math
import pathlib

import numpy as np
import pandas as pd

from . import limits, metrics, montecarlo, reports, stats, stress, trades

REPO = pathlib.Path(__file__).resolve().parents[2]
CONTRACT_SIZE = 100.0   # XAUUSD.s (run_constants symbol_spec.contract_size)
POINT = 0.01            # XAUUSD.s, 2 digits: slippage points -> price
bar_minutes = metrics.bar_minutes
RECOMMENDED_NOTE = ("R30: no recommended .set in this research - no independent period exists (the holdout is "
                    "non-independent, R24); the candidate is delivered as not validated with a forward-test protocol.")


def load_run(run_id: str, root: pathlib.Path = None) -> dict:
    """days, trades (positions), R40 trade table and report summary of one research-build run."""
    d = (root or REPO / "runs") / run_id
    rep = reports.parse_html(next(d.glob(f"{run_id}.htm")))
    days = reports.read_days(d / f"rl_days_{run_id}.csv")
    deals = reports.read_deals(d / f"rl_deals_{run_id}.csv")
    deposit = float(deals.loc[deals["type"] == 2, "profit"].sum())
    sp = d / f"rl_setups_{run_id}.csv"
    setups = pd.read_csv(sp, keep_default_na=False) if sp.exists() else pd.DataFrame(columns=["position_id"])
    return {"days": days, "trades": reports.trades_from_deals(deals, deposit), "table": trades.trade_table(setups, deals),
            "setups": setups, "summary": reports.summary(rep), "deposit": deposit, "report": rep}


def stitch(runs: list) -> dict:
    """Chronological concatenation of chained runs (each month counted once)."""
    days = pd.concat([r["days"] for r in runs], ignore_index=True).sort_values("date").reset_index(drop=True)
    cat = lambda key, col: pd.concat([r[key] for r in runs if not r[key].empty] or [runs[0][key]],
                                     ignore_index=True).sort_values(col).reset_index(drop=True)
    return {"days": days, "trades": cat("trades", "open_time"), "table": cat("table", "open_time"),
            "initial": runs[0]["deposit"], "net_profit": float(sum(r["summary"]["net_profit"] for r in runs))}


def window_days(start: str, end: str) -> int:
    """Calendar days in an inclusive YYYY.MM.DD window."""
    f = lambda s: dt.datetime.strptime(s, "%Y.%m.%d").date()
    return (f(end) - f(start)).days + 1


def daily_pnl(days: pd.DataFrame) -> pd.Series:
    """Equity change per day (close-to-close; first day vs its opening equity), indexed by date."""
    if days.empty:
        return pd.Series(dtype=float)
    return pd.Series((days["eq_close"] - metrics.previous_equity(days)).values, index=days["date"])


def spread_by_day(days: pd.DataFrame) -> pd.Series:
    return pd.Series(days["spread_median"].values, index=days["date"].dt.normalize())


def cost_stress(stitched: dict, k_spread: float, slippage_points: float) -> dict:
    """Net after +k x the day's median spread on every trade and adverse slippage on every SL exit."""
    tab = stitched["table"]
    if tab.empty:
        return {"stressed_net": 0.0, "spread_cost": 0.0, "slippage_cost": 0.0, "stop_exits": 0}
    extra = stress.extra_cost(tab, CONTRACT_SIZE, k_spread, spread_by_day(stitched["days"]))
    slip = stress.stop_slippage_cost(tab, CONTRACT_SIZE, slippage_points, POINT)
    return {"stressed_net": float(tab["net"].sum() - extra.sum() - slip.sum()), "spread_cost": float(extra.sum()),
            "slippage_cost": float(slip.sum()), "stop_exits": int((slip > 0).sum())}


def _f(x, nd=4) -> float:
    """Finite float for JSON; NaN/inf become 0.0 (the criterion's pass flag is computed separately)."""
    x = float(x)
    return round(x, nd) if math.isfinite(x) else 0.0


def _frequency(n: int, days: int, per_month: float) -> dict:
    return {"fills": int(n), "days": int(days), "fills_per_month": _f(n / days * per_month if days else 0.0)}


def trial_sharpe_variance(scored_grid_paths) -> dict:
    """DSR cross-trial variance: per training grid, the sample variance of the Custom column (the EA's
    OnTester daily Sharpe, one value per optimization pass) over passes with trades; averaged across grids."""
    per_grid, passes = [], 0
    for p in scored_grid_paths:
        g = pd.read_csv(p)
        x = g.loc[g["trades"] > 0, "custom"].astype(float)
        x = x[np.isfinite(x)]
        passes += len(x)
        if len(x) >= 2:
            per_grid.append(float(x.var(ddof=1)))
    return {"var_sr": float(np.mean(per_grid)) if per_grid else float("nan"), "passes": passes,
            "grids": len(per_grid), "source": "optimization Custom column (EA OnTester daily Sharpe) of the "
                                              "training grids, passes with trades, variance averaged over grids"}


def evaluate(proc: dict, base: dict, fold_rows: list, neighbors: dict, var_sr: float, holdout, prereg: dict) -> dict:
    """R29 criteria on the chained OOS (proc) and the holdout; thresholds come from the pre-registration.

    proc/base/holdout are stitched dicts (days, trades, table, initial, net_profit); fold_rows carry
    procedure_net per fold; neighbors carries profitable_share. Empty OOS fails every criterion.
    """
    A, S = prereg["acceptance"], prereg["stats"]
    bm = bar_minutes(prereg["period"])
    tr, days = proc["trades"], proc["days"]
    empty = len(tr) == 0
    crit = {}

    def c(key, value, threshold, ok, note=""):
        crit[key] = {"value": value, "threshold": threshold, "pass": bool(ok) and not (empty and key != "holdout"),
                     "note": note}

    oos_days = window_days(prereg["folds"][0]["test"][0], prereg["folds"][-1]["test"][1])
    fq = _frequency(len(tr), oos_days, A["days_per_month"])
    c("oos_frequency", fq, f">= {A['min_fills_per_month']} fills per {A['days_per_month']} days",
      fq["fills_per_month"] >= A["min_fills_per_month"])

    net, bnet = float(proc["net_profit"]), float(base["net_profit"])
    c("oos_net", {"net": _f(net, 2), "baseline_net": _f(bnet, 2)}, "> 0 and > baseline net", net > 0 and net > bnet)

    pnl = daily_pnl(days).to_numpy(float)
    lo = hi = 0.0
    if not empty and len(pnl) >= 2:
        lo, hi = stats.bootstrap_mean_ci(pnl, S["bootstrap_resamples"], S["mean_block_days"], A["bootstrap_alpha"],
                                         S["seed"])
    c("bootstrap_ci", {"ci_mean_daily_pnl": [_f(lo, 2), _f(hi, 2)], "n_days": len(pnl)},
      f"{1 - A['bootstrap_alpha']:.0%} lower bound > 0 (block {S['mean_block_days']}, "
      f"{S['bootstrap_resamples']} resamples, seed {S['seed']})", lo > 0)

    k = sum(1 for r in fold_rows if r["procedure_net"] > 0)
    c("positive_folds", k, f">= {A['min_positive_folds']} of {len(fold_rows)}", k >= A["min_positive_folds"])

    ev = metrics.trade_events(tr, bm) if not empty else pd.DataFrame({"profit_net": []})
    top = int(A["remove_top_events"])
    wo = float(ev["profit_net"].sum() - ev["profit_net"].nlargest(top).sum())
    c("top_events_removed", {"n_events": len(ev), "bar_minutes": bm, "net_without_top": _f(wo, 2)},
      f"net after removing the top {top} trade events > 0", wo > 0)

    breach = limits.first_breach(days, initial=proc["initial"]) if not days.empty else None
    mc = (montecarlo.block_bootstrap_paths(days, horizon=S["mc_horizon_days"], n_paths=S["mc_paths"],
                                           mean_block=S["mean_block_days"], seed=S["seed"], initial=proc["initial"])
          if len(days) >= 2 else {"breach_prob": 1.0})
    mc_view = {k2: (int(v) if isinstance(v, (int, np.integer)) else _f(v)) for k2, v in mc.items()
               if k2 != "breach_day" and isinstance(v, (int, float, np.integer, np.floating))}
    c("loss_limits", {"stitched_breach": int(breach is not None), "monte_carlo": mc_view},
      f"no stitched breach and MC breach prob <= {A['mc_breach_prob_max']} over {S['mc_horizon_days']} days",
      breach is None and mc["breach_prob"] <= A["mc_breach_prob_max"],
      f"first breach {breach}" if breach else ("no daily records" if len(days) < 2 else ""))

    cs = cost_stress(proc, A["spread_stress_k"], A["stop_slippage_points"])
    c("cost_stress", {k2: _f(v, 2) if isinstance(v, float) else v for k2, v in cs.items()},
      f"> 0 after +{A['spread_stress_k']}x spread and {A['stop_slippage_points']} points on every SL exit",
      cs["stressed_net"] > 0)

    share = neighbors.get("profitable_share", math.nan) if neighbors else math.nan
    share = float(share) if share is not None else math.nan
    c("neighbors", _f(share), f">= {A['min_neighbor_profitable_share']}",
      math.isfinite(share) and share >= A["min_neighbor_profitable_share"])

    r = pnl / proc["initial"]
    if empty or len(r) < 2:
        c("dsr", {"n_days": len(r), "psr_value": 0.0}, A["dsr_min"], False, "no trades")
    elif len(r) < A["dsr_min_days"]:
        c("dsr", {"n_days": len(r)}, f"gates only with >= {A['dsr_min_days']} days", True, "not gating")
    else:
        d = stats.dsr(r, S["dsr_trials"], var_sr)
        c("dsr", {"n_days": len(r), "psr_value": _f(d["psr_value"]), "sr": _f(d["sr"]), "sr0": _f(d["sr0"]),
                  "trials": S["dsr_trials"], "var_sr": _f(var_sr, 8)}, A["dsr_min"],
          math.isfinite(d["psr_value"]) and d["psr_value"] >= A["dsr_min"])

    hd = window_days(*prereg["holdout"])
    if holdout is None:
        c("holdout", {**_frequency(0, hd, A["days_per_month"]), "net": 0.0, "breach": 0}, "", False, "not run")
    else:
        hf = _frequency(len(holdout["trades"]), hd, A["days_per_month"])
        hb = limits.first_breach(holdout["days"], initial=holdout["initial"]) if not holdout["days"].empty else None
        hn = float(holdout["net_profit"])
        c("holdout", {**hf, "net": _f(hn, 2), "breach": int(hb is not None)},
          f">= {A['holdout_min_fills_per_month']} fills/month, net > 0, no breach (non-independent)",
          len(holdout["trades"]) > 0 and hf["fills_per_month"] >= A["holdout_min_fills_per_month"] and hn > 0
          and hb is None, f"first breach {hb}" if hb else "")

    return {"criteria": crit, "passed_all": all(v["pass"] for v in crit.values()),
            "recommended": False, "recommended_note": RECOMMENDED_NOTE}


def jsonable(o):
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.ndarray,)):
        return o.tolist()
    if isinstance(o, (pd.Timestamp,)):
        return str(o)
    return o


def save(obj, path) -> None:
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(jsonable(obj), indent=1, default=str))
