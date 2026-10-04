"""Evidence assembly and the acceptance evaluation (KTD14 thresholds from the pre-registration)."""
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
RECOMMENDED_NOTE = ("R33: no recommended .set in this research - no independent period exists (August-September is "
                    "a non-independent historical check, R31); at most candidate.set with a forward-test protocol.")


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


def fills_per_month(n: int, start: str, end: str, per_month: float = 30.44) -> float:
    """Fills per `per_month` days over an inclusive YYYY.MM.DD window (KTD14 frequency)."""
    return round(n / window_days(start, end) * per_month, 4)


def daily_pnl(days: pd.DataFrame) -> pd.Series:
    """Equity change per day (close-to-close; first day vs its opening equity), indexed by date."""
    if days.empty:
        return pd.Series(dtype=float)
    return pd.Series((days["eq_close"] - metrics.previous_equity(days)).values, index=days["date"])


def spread_by_day(days: pd.DataFrame) -> pd.Series:
    return pd.Series(days["spread_median"].values, index=days["date"].dt.normalize())


def cost_stress(stitched: dict, k_spread: float, slippage_points: float, entry_points: float = 0.0) -> dict:
    """Net after +k x the day's median spread on every trade, adverse slippage on every SL exit and on every
    Market entry (KTD14)."""
    tab = stitched["table"]
    if tab.empty:
        return {"stressed_net": 0.0, "spread_cost": 0.0, "slippage_cost": 0.0, "entry_slippage_cost": 0.0,
                "stop_exits": 0}
    extra = stress.extra_cost(tab, CONTRACT_SIZE, k_spread, spread_by_day(stitched["days"]))
    slip = stress.stop_slippage_cost(tab, CONTRACT_SIZE, slippage_points, POINT)
    entry = stress.entry_slippage_cost(tab, CONTRACT_SIZE, entry_points, POINT)
    return {"stressed_net": float(tab["net"].sum() - extra.sum() - slip.sum() - entry.sum()),
            "spread_cost": float(extra.sum()), "slippage_cost": float(slip.sum()),
            "entry_slippage_cost": float(entry.sum()), "stop_exits": int((slip > 0).sum())}


def drawdowns(stitched: dict) -> dict:
    """Two drawdowns of one (stitched) series, reported apart (gate decision): the closed-trade balance drawdown
    from the position results in exit order, and the equity drawdown from the daily equity records (running
    peak of daily equity high vs each day's equity low). Percent of the running peak."""
    out = {"balance_dd_pct": 0.0, "equity_dd_pct": 0.0}
    tab, days, init = stitched["table"], stitched["days"], float(stitched["initial"])
    if not tab.empty:
        bal = init + tab.sort_values("close_time", kind="mergesort")["net"].cumsum().to_numpy(float)
        bal = np.concatenate([[init], bal])
        peak = np.maximum.accumulate(bal)
        out["balance_dd_pct"] = _f(float(np.max((peak - bal) / peak) * 100), 3)
    if not days.empty:
        hi = np.maximum.accumulate(np.concatenate([[init], days["eq_max"].to_numpy(float)]))[:-1]
        hi = np.maximum(hi, days["eq_open"].to_numpy(float))
        out["equity_dd_pct"] = _f(float(np.max((hi - days["eq_min"].to_numpy(float)) / hi) * 100), 3)
    return out


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


def evaluate(series: dict, base, fold_rows: list, stability_share, var_sr: float, prereg: dict,
             window: list = None, bases: dict = None) -> dict:
    """KTD14 criteria on one chained OOS series; thresholds come from the pre-registration.

    series/base are stitched dicts (days, trades, table, initial, net_profit); base None means the series is the
    baseline itself. fold_rows carry "net" per fold used; the positive-fold rule is a strict majority of them.
    stability_share None means not run for this series: the criterion is reported as not evaluated and left
    out of passed_all (evaluated_all says so). window: the OOS span [start, end] (default: all folds).
    August-September is not a criterion (R31). An empty series fails every evaluated criterion.
    bases: named baseline series (numeric_v1 KTD10); when given, oos_net needs net > 0 and > every baseline's net.
    """
    A, S = prereg["acceptance"], prereg["stats"]
    bm = metrics.bar_minutes(A.get("event_bar", prereg.get("chart_period", "M1")))
    tr, days = series["trades"], series["days"]
    empty = len(tr) == 0
    crit = {}

    def c(key, value, threshold, ok, note="", evaluated=True):
        crit[key] = {"value": value, "threshold": threshold, "evaluated": evaluated,
                     "pass": (bool(ok) and not empty) if evaluated else None, "note": note}

    window = window or [prereg["folds"][0]["test"][0], prereg["folds"][-1]["test"][1]]
    fq = _frequency(len(tr), window_days(*window), A["days_per_month"])
    c("oos_frequency", fq, f">= {A['min_fills_per_month']} fills per {A['days_per_month']} days",
      fq["fills_per_month"] >= A["min_fills_per_month"])

    net = float(series["net_profit"])
    if bases:
        bn = {k: _f(float(b["net_profit"]), 2) for k, b in bases.items()}
        c("oos_net", {"net": _f(net, 2), "baselines": bn, "margins": {k: _f(net - v, 2) for k, v in bn.items()}},
          "> 0 and > the net of every baseline (" + ", ".join(bn) + ")", net > 0 and all(net > v for v in bn.values()))
    elif base is None:
        c("oos_net", {"net": _f(net, 2), "baseline_net": _f(net, 2)}, "> 0 (this series is the baseline)", net > 0)
    else:
        bnet = float(base["net_profit"])
        c("oos_net", {"net": _f(net, 2), "baseline_net": _f(bnet, 2)}, "> 0 and > baseline net",
          net > 0 and net > bnet)

    pnl = daily_pnl(days).to_numpy(float)
    lo = hi = 0.0
    if not empty and len(pnl) >= 2:
        lo, hi = stats.bootstrap_mean_ci(pnl, S["bootstrap_resamples"], S["mean_block_days"], A["bootstrap_alpha"],
                                         S["seed"])
    c("bootstrap_ci", {"ci_mean_daily_pnl": [_f(lo, 2), _f(hi, 2)], "n_days": len(pnl)},
      f"{1 - A['bootstrap_alpha']:.0%} lower bound > 0 (block {S['mean_block_days']}, "
      f"{S['bootstrap_resamples']} resamples, seed {S['seed']})", lo > 0)

    k, n = sum(1 for r in fold_rows if r["net"] > 0), len(fold_rows)
    c("positive_folds", k, f"strict majority: >= {n // 2 + 1} of {n}", k >= n // 2 + 1)

    ev = metrics.trade_events(tr, bm) if not empty else pd.DataFrame({"profit_net": []})
    top = int(A["remove_top_events"])
    wo = float(ev["profit_net"].sum() - ev["profit_net"].nlargest(top).sum())
    c("top_events_removed", {"n_events": len(ev), "bar_minutes": bm, "net_without_top": _f(wo, 2)},
      f"net after removing the top {top} trade events > 0", wo > 0)

    breach = limits.first_breach(days, initial=series["initial"]) if not days.empty else None
    mc = (montecarlo.block_bootstrap_paths(days, horizon=S["mc_horizon_days"], n_paths=S["mc_paths"],
                                           mean_block=S["mean_block_days"], seed=S["seed"], initial=series["initial"])
          if len(days) >= 2 else {"breach_prob": 1.0})
    mc_view = {k2: (int(v) if isinstance(v, (int, np.integer)) else _f(v)) for k2, v in mc.items()
               if k2 != "breach_day" and isinstance(v, (int, float, np.integer, np.floating))}
    c("loss_limits", {"stitched_breach": int(breach is not None), "monte_carlo": mc_view},
      f"no stitched breach and MC breach prob <= {A['mc_breach_prob_max']} over {S['mc_horizon_days']} days",
      breach is None and mc["breach_prob"] <= A["mc_breach_prob_max"],
      f"first breach {breach}" if breach else ("no daily records" if len(days) < 2 else ""))

    cs = cost_stress(series, A["spread_stress_k"], A["stop_slippage_points"], A.get("entry_slippage_points", 0))
    c("cost_stress", {k2: _f(v, 2) if isinstance(v, float) else v for k2, v in cs.items()},
      f"> 0 after +{A['spread_stress_k']}x spread, {A['stop_slippage_points']} points on every SL exit and "
      f"{A.get('entry_slippage_points', 0)} points on every Market entry", cs["stressed_net"] > 0)

    if stability_share is None:
        c("stability", 0.0, f">= {A['min_stability_profitable_share']}", False, "not run for this series",
          evaluated=False)
    else:
        share = float(stability_share)
        c("stability", _f(share), f">= {A['min_stability_profitable_share']} "
                                  f"{A.get('stability_label', 'of the 4 KTD15 perturbations')}",
          math.isfinite(share) and share >= A["min_stability_profitable_share"])

    r = pnl / series["initial"]
    if empty or len(r) < 2:
        c("dsr", {"n_days": len(r), "psr_value": 0.0}, A["dsr_min"], False, "no trades")
    elif len(r) < A["dsr_min_days"]:
        c("dsr", {"n_days": len(r)}, f"gates only with >= {A['dsr_min_days']} days", True, "not gating")
    else:
        d = stats.dsr(r, S["dsr_trials"], var_sr)
        value = {"n_days": len(r), "psr_value": _f(d["psr_value"]), "sr": _f(d["sr"]), "sr0": _f(d["sr0"]),
                 "trials": S["dsr_trials"], "var_sr": _f(var_sr, 8)}
        if "dsr_trials_sensitivity" in S:            # reported only, never gating (numeric_v1 KTD9)
            ds = stats.dsr(r, S["dsr_trials_sensitivity"], var_sr)
            value["sensitivity"] = {"trials": S["dsr_trials_sensitivity"], "psr_value": _f(ds["psr_value"]),
                                    "sr0": _f(ds["sr0"])}
        c("dsr", value, A["dsr_min"], math.isfinite(d["psr_value"]) and d["psr_value"] >= A["dsr_min"])

    done = [v for v in crit.values() if v["evaluated"]]
    return {"criteria": crit, "passed_all": all(v["pass"] for v in done),
            "evaluated_all": len(done) == len(crit), "drawdowns": drawdowns(series),
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
