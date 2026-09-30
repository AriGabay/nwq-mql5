"""Evidence assembly and the R27/R28 acceptance evaluation (U8). Pure Python over archived runs."""
import json
import math
import pathlib

import numpy as np
import pandas as pd

from . import limits, metrics, montecarlo, reports, stats, stress

REPO = pathlib.Path(__file__).resolve().parents[2]
CONTRACT_SIZE = 100.0   # XAUUSD.s, derived in results/fidelity.md
BAR_MINUTES = 15


def load_run(run_id: str, root: pathlib.Path = None) -> dict:
    """days, trades (positions) and report summary of one research-build run."""
    d = (root or REPO / "runs") / run_id
    rep = reports.parse_html(next(d.glob(f"{run_id}.htm")))
    days = reports.read_days(d / f"rl_days_{run_id}.csv")
    deals = reports.read_deals(d / f"rl_deals_{run_id}.csv")
    deposit = float(deals.loc[deals["type"] == 2, "profit"].sum())
    trades = reports.trades_from_deals(deals, deposit)
    return {"days": days, "trades": trades, "summary": reports.summary(rep), "deposit": deposit, "report": rep}


def stitch(runs: list) -> dict:
    """Chronological concatenation of chained runs (each month counted once)."""
    days = pd.concat([r["days"] for r in runs], ignore_index=True).sort_values("date").reset_index(drop=True)
    trades = pd.concat([r["trades"] for r in runs], ignore_index=True).sort_values("open_time").reset_index(drop=True)
    return {"days": days, "trades": trades, "initial": runs[0]["deposit"],
            "net_profit": float(sum(r["summary"]["net_profit"] for r in runs))}


def daily_pnl(days: pd.DataFrame) -> pd.Series:
    """Equity change per day (close-to-close; first day vs its opening equity), indexed by date."""
    prev = days["eq_close"].shift(1)
    prev.iloc[0] = days["eq_open"].iloc[0]
    return pd.Series((days["eq_close"] - prev).values, index=days["date"])


def aligned_pnl(a_days, b_days) -> tuple:
    """Daily PnL of two paths on their common calendar (missing days = 0 PnL)."""
    a, b = daily_pnl(a_days), daily_pnl(b_days)
    idx = a.index.union(b.index)
    return a.reindex(idx, fill_value=0.0).values, b.reindex(idx, fill_value=0.0).values


def spread_by_day(days: pd.DataFrame) -> pd.Series:
    return pd.Series(days["spread_median"].values, index=days["date"].dt.normalize())


def stressed_profit(stitched: dict, k: float, commission_scale: float = 1.0) -> float:
    extra = stress.extra_cost(stitched["trades"], CONTRACT_SIZE, k, spread_by_day(stitched["days"]))
    s = stress.apply(stitched["trades"], extra, commission_scale)
    return float(s["profit_net_stressed"].sum())


def evaluate(proc: dict, base: dict, fold_rows: list, delay: dict, neighbors: dict, static: dict,
             var_sr: float, prereg: dict) -> dict:
    """Return per-criterion value/threshold/pass for R27 (a-j). Inputs are assembled by the CLI."""
    A, S = prereg["acceptance"], prereg["stats"]
    ev = metrics.trade_events(proc["trades"], bar_minutes=BAR_MINUTES)
    out = {}

    def crit(key, value, threshold, ok, note=""):
        out[key] = {"value": value, "threshold": threshold, "pass": bool(ok), "note": note}

    n_ev = len(ev)
    crit("a_min_oos_trade_events", n_ev, A["a_min_oos_trade_events"], n_ev >= A["a_min_oos_trade_events"],
         f"raw trades {len(proc['trades'])}")
    crit("b_stitched_profit", round(proc["net_profit"], 2), f"> 0 and > baseline {round(base['net_profit'], 2)}",
         proc["net_profit"] > 0 and proc["net_profit"] > base["net_profit"])
    a, b = aligned_pnl(proc["days"], base["days"])
    lo, hi = stats.bootstrap_mean_ci(a, S["bootstrap_resamples"], S["mean_block_days"], A["c_bootstrap_alpha"], S["seed"])
    p_le = stats.paired_prob_le_zero(a, b, S["bootstrap_resamples"], S["mean_block_days"], S["seed"])
    crit("c_bootstrap", {"ci90_mean_daily_pnl": [round(lo, 2), round(hi, 2)], "paired_prob_le_zero": round(p_le, 4)},
         f"lower > 0 and paired prob < {A['c_paired_prob_le_zero_max']}", lo > 0 and p_le < A["c_paired_prob_le_zero_max"])
    wins = sum(1 for r in fold_rows if r["procedure_ret"] >= r["baseline_ret"])
    crit("d_folds_beating_baseline", wins, A["d_min_folds_beating_baseline"], wins >= A["d_min_folds_beating_baseline"],
         "compared as fold returns (%) because deposits are chained separately")
    conc = metrics.concentration(ev) if n_ev else {"largest_event_share": math.nan, "net_without_top2": math.nan}
    crit("e_concentration", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in conc.items()},
         f"net after top-{A['e_remove_top_events']} > 0 and max share <= {A['e_max_single_event_share']}",
         n_ev > 0 and conc["net_without_top2"] > 0 and conc["largest_event_share"] <= A["e_max_single_event_share"])
    breach = limits.first_breach(proc["days"], initial=proc["initial"])
    mc = montecarlo.block_bootstrap_paths(proc["days"], horizon=S["mc_horizon_days"], n_paths=S["mc_paths"],
                                          mean_block=S["mean_block_days"], seed=S["seed"], initial=proc["initial"])
    mc_view = {k: v for k, v in mc.items() if k != "breach_day"}
    crit("f_loss_limits", {"stitched_first_breach": breach, "monte_carlo": mc_view},
         f"no breach and MC breach prob <= {A['f_mc_breach_prob_max']}",
         breach is None and mc["breach_prob"] <= A["f_mc_breach_prob_max"])
    s1 = stressed_profit(proc, A["g_spread_stress_k"])
    crit("g_spread_stress_1x", round(s1, 2), "> 0", s1 > 0,
         f"delay run net {delay.get('procedure')}" if delay else "")
    share = neighbors.get("profitable_share", math.nan)
    crit("h_neighbor_stability", share, A["h_min_neighbor_profitable_share"],
         share >= A["h_min_neighbor_profitable_share"], "Mar-Apr only (outside the candidate's training window)")
    r = np.asarray(a, float) / proc["initial"]
    if len(r) >= A["i_dsr_min_days"]:
        d = stats.dsr(r, S["dsr_trials"], var_sr)
        crit("i_dsr", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}, A["i_dsr_min"],
             d["psr_value"] >= A["i_dsr_min"])
    else:
        crit("i_dsr", f"{len(r)} days", f"gates only with >= {A['i_dsr_min_days']} days", True, "not gating")
    crit("j_static_candidate_mar_apr", {"candidate": static["candidate"], "baseline": static["baseline"]},
         "> 0 and > baseline", static["candidate"] > 0 and static["candidate"] > static["baseline"])
    out["_passed_all"] = all(v["pass"] for k, v in out.items() if not k.startswith("_"))
    return out


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
