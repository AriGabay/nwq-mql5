"""R41 volume audit (no optimization, no P&L): formula and window, examples, ratio distributions, tick
coverage and source-volume comparison, and the setup funnel after the volume filter.

python research/volume_audit.py -> results/volume_audit/{audit.json, audit.md}
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mt5r import conformance as cf, journal  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
RUNS, OUT = REPO / "runs", REPO / "results" / "volume_audit"
TF = {"M15": {"run": "pilot_m15", "m1": "audit_m1ohlc_m15", "sec": 900},
      "M5": {"run": "pilot_m5", "m1": "audit_m1ohlc_m5", "sec": 300}}
K, HOURS = 2.0, 24


def bars(run):
    b = cf.read_bars(RUNS / run / f"rl_bars_{run}.csv").reset_index(drop=True)
    b["dt"] = pd.to_datetime(b["time"], unit="s")
    return b


def ratios(b, n):
    """ratio[m] = v[m] / mean(v[m-n .. m-1]); the bar itself is excluded and only earlier bars are used."""
    v = b["tick_volume"].to_numpy(float)
    prev_sum = pd.Series(v).shift(1).rolling(n).sum().to_numpy()
    return v * n / prev_sum, prev_sum / n


def fvg_middles(b):
    h, l = b["high"].to_numpy(), b["low"].to_numpy()
    o, c = b["open"].to_numpy(), b["close"].to_numpy()
    m = np.arange(1, len(b) - 1)
    bull = l[m + 1] > h[m - 1]
    bear = h[m + 1] < l[m - 1]
    # identifying-FVG candidates: an opposite candle exists at candle 1 or the bar before it (ImpulseWindowBars=2)
    c1 = m - 1
    opp_bull = (c[c1] < o[c1]) | ((c1 - 1 >= 0) & (c[np.maximum(c1 - 1, 0)] < o[np.maximum(c1 - 1, 0)]))
    opp_bear = (c[c1] > o[c1]) | ((c1 - 1 >= 0) & (c[np.maximum(c1 - 1, 0)] > o[np.maximum(c1 - 1, 0)]))
    fvg = m[bull | bear]
    ident = m[(bull & opp_bull) | (bear & opp_bear)]
    return fvg, ident


def dist(x):
    x = x[np.isfinite(x)]
    q = lambda p: round(float(np.quantile(x, p)), 3)
    return {"n": int(len(x)), "median": q(.5), "p90": q(.9), "p95": q(.95), "p99": q(.99),
            "max": round(float(x.max()), 3), "share_ge_2x_pct": round(100 * float((x >= K).mean()), 3)}


def window_span(b, n):
    """Wall-clock span covered by the n bars before each bar (bars, not hours: weekends and closures skipped)."""
    t = b["time"].to_numpy()
    span_h = (t[n:] - t[:-n]) / 3600.0
    return {"bars": n, "median_hours": round(float(np.median(span_h)), 2),
            "share_exactly_24h_pct": round(100 * float(np.mean(np.isclose(span_h, HOURS))), 1),
            "max_hours": round(float(span_h.max()), 1)}


def logged_vs_recomputed(run, b, r):
    s = cf.read_setups(RUNS / run / f"rl_setups_{run}.csv")
    idx = {t: i for i, t in enumerate(b["time"])}
    diffs, n_cmp = [], 0
    for _, row in s.iterrows():
        c1 = row["idfvg_c1_time"]
        if pd.isna(c1) or int(c1) not in idx or pd.isna(row["idfvg_vol_ratio"]):
            continue
        m = idx[int(c1)] + 1
        if np.isfinite(r[m]):
            diffs.append(abs(r[m] - float(row["idfvg_vol_ratio"])))
            n_cmp += 1
    return {"activated_setups": int(len(s)), "compared": n_cmp,
            "max_abs_diff": round(float(max(diffs)), 5) if diffs else None}


def examples(b, r, avg, n, ident, passing: bool, k=5):
    pick = ident[np.isfinite(r[ident]) & ((r[ident] >= K) if passing else (r[ident] < K))]
    if not passing:
        pick = np.random.default_rng(20260930).choice(pick, size=min(k, len(pick)), replace=False)
        pick.sort()
    else:
        pick = pick[:k]
    return [{"push_bar": b["dt"][m].strftime("%Y-%m-%d %H:%M"), "tick_volume": int(b["tick_volume"][m]),
             "avg_prev_bars": round(float(avg[m]), 1), "bars_in_avg": n,
             "window_from": b["dt"][m - n].strftime("%Y-%m-%d %H:%M"),
             "window_to": b["dt"][m - 1].strftime("%Y-%m-%d %H:%M"), "ratio": round(float(r[m]), 3),
             "passes": bool(r[m] >= K)} for m in pick]


def coverage(run):
    f = journal.run_facts(RUNS / run)
    return {k: f.get(k) for k in ("discarded_days", "discarded_minutes", "total_minute_bars",
                                  "every_tick_generation_used")}


def source_compare(b4, b1):
    j = b4[["time", "tick_volume"]].merge(b1[["time", "tick_volume"]], on="time", suffixes=("_real", "_m1"))
    x, y = j["tick_volume_real"].to_numpy(float), j["tick_volume_m1"].to_numpy(float)
    return {"bars_matched": int(len(j)), "identical_share_pct": round(100 * float(np.mean(x == y)), 1),
            "correlation": round(float(np.corrcoef(x, y)[0, 1]), 4),
            "median_ratio_real_over_m1": round(float(np.median(x / y)), 4)}


def monthly(b, r):
    d = pd.DataFrame({"month": b["dt"].dt.strftime("%Y-%m"), "r": r, "v": b["tick_volume"]})
    g = d.groupby("month")
    return {m: {"median_tick_volume": int(x["v"].median()), "cv_tick_volume": round(float(x["v"].std() / x["v"].mean()), 3),
                "ratio_p99": round(float(np.nanquantile(x["r"], .99)), 3),
                "share_ge_2x_pct": round(100 * float((x["r"] >= K).mean()), 3)} for m, x in g}


def hourly_profile(b):
    hp = b.groupby(b["dt"].dt.hour)["tick_volume"].median()
    return {"min_hour_median": int(hp.min()), "max_hour_median": int(hp.max()),
            "peak_to_trough": round(float(hp.max() / hp.min()), 2),
            "overall_cv": round(float(b["tick_volume"].std() / b["tick_volume"].mean()), 3)}


def funnel(run):
    f = journal.run_facts(RUNS / run)["funnel"]
    s = cf.read_setups(RUNS / run / f"rl_setups_{run}.csv")
    reasons = s["reason"].value_counts().to_dict()
    return {"identifying_fvgs_rejected_by_volume": f.get("idfvg_rejected_volume"), "activated": f.get("activated"),
            "touched": f.get("touched"), "confirmed": f.get("confirmed"), "placed": f.get("placed"),
            "filled": f.get("filled"), "lost_by_reason": {k: int(v) for k, v in reasons.items() if k != "filled"}}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    res = {}
    for tf, cfg in TF.items():
        n = HOURS * 3600 // cfg["sec"]
        b4, b1 = bars(cfg["run"]), bars(cfg["m1"])
        r, avg = ratios(b4, n)
        r1, _ = ratios(b1, n)
        fvg, ident = fvg_middles(b4)
        res[tf] = {
            "window": window_span(b4, n),
            "logged_vs_recomputed": logged_vs_recomputed(cfg["run"], b4, r),
            "dist_all_bars": dist(r), "dist_fvg_middles": dist(r[fvg]), "dist_identifying_candidates": dist(r[ident]),
            "dist_all_bars_m1_history": dist(r1),
            "examples_pass": examples(b4, r, avg, n, ident, True), "examples_fail": examples(b4, r, avg, n, ident, False),
            "coverage_real_ticks": coverage(cfg["run"]), "source_compare_model4_vs_m1": source_compare(b4, b1),
            "monthly": monthly(b4, r), "hourly_profile": hourly_profile(b4), "funnel": funnel(cfg["run"]),
        }
    (OUT / "audit.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
