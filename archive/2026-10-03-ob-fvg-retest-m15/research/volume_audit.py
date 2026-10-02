"""R41 volume audit (no optimization, no P&L): formula and window, examples, ratio distributions, tick
coverage and source-volume comparison, history gaps, the tick-rate ceiling question, and the setup funnel.

AMENDMENT C (2026-10-02): the average covers the bars that OPEN in the wall-clock window [open(m) - 24 h, open(m)),
m = the push bar (the identifying FVG's middle candle). This module recomputes it independently of the checker
(vectorised searchsorted here, a per-bar loop in conformance.vol_window; a test pins them equal).

python research/volume_audit.py [--runs-dir runs] -> results/volume_audit/{audit.json, audit.md}
"""
import argparse
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
LOOKBACK_S = HOURS * 3600
DAILY_BREAK = (0, 1)   # server hours without bars every trading day (00:00-01:00)


def bars(run, runs=RUNS):
    b = cf.read_bars(runs / run / f"rl_bars_{run}.csv").reset_index(drop=True)
    b["dt"] = pd.to_datetime(b["time"], unit="s")
    return b


def ratios(b, lookback_s=LOOKBACK_S):
    """Per bar m: ratio = v[m] / mean(v of bars opening in [t[m] - lookback_s, t[m])), plus that mean, the number of
    bars in the window and a status ("ok", "no_history": the first logged bar opens after the window start,
    "empty": no bar or no volume in the window). Ratio and mean are NaN unless the status is "ok"."""
    t = b["time"].to_numpy(np.int64)
    v = b["tick_volume"].to_numpy(float)
    idx = np.arange(len(t))
    start = t - int(lookback_s)
    j = np.searchsorted(t, start, side="left")
    cum = np.concatenate([[0.0], np.cumsum(v)])
    n = idx - j
    total = cum[idx] - cum[j]
    status = np.where(t[0] > start, "no_history", np.where((n <= 0) | ~(total > 0), "empty", "ok")).astype(object)
    n = np.where(status == "no_history", 0, n)
    ok = status == "ok"
    avg = np.full(len(t), np.nan)
    avg[ok] = total[ok] / n[ok]
    r = np.full(len(t), np.nan)
    r[ok] = v[ok] / avg[ok]
    return r, avg, n, status


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
    if not len(x):
        return {"n": 0}
    q = lambda p: round(float(np.quantile(x, p)), 3)
    return {"n": int(len(x)), "median": q(.5), "p90": q(.9), "p95": q(.95), "p99": q(.99),
            "max": round(float(x.max()), 3), "share_ge_2x_pct": round(100 * float((x >= K).mean()), 3)}


def gaps(b, sec):
    """Gaps between consecutive bars: daily break (00:00-01:00), weekend (>= 24 h), anything else = history gap."""
    t = b["time"].to_numpy(np.int64)
    d = np.diff(t)
    out = []
    for i in np.nonzero(d > sec)[0]:
        frm, to = pd.Timestamp(t[i] + sec, unit="s"), pd.Timestamp(t[i + 1], unit="s")
        hours = (to - frm).total_seconds() / 3600
        daily = frm.hour == DAILY_BREAK[0] and frm.minute == 0 and to.hour == DAILY_BREAK[1] and to.minute == 0 \
            and hours <= 1.0001
        kind = "weekend" if hours >= 24 else "daily_break" if daily else "history_gap"
        out.append({"from": frm, "to": to, "hours": round(hours, 2), "kind": kind, "after_bar": int(i)})
    return out


def window_facts(b, n, status, gp, idx):
    """How the 24 h windows of bars `idx` look: bar counts, and how many span a daily break, a weekend or a gap."""
    t = b["time"].to_numpy(np.int64)
    ok = idx[status[idx] == "ok"]
    out = {"bars_judged": int(len(idx)), "ok": int(len(ok)),
           "no_history_not_logged": int((status[idx] == "no_history").sum()),
           "empty": int((status[idx] == "empty").sum())}
    if len(ok):
        nn = n[ok]
        out.update({"bars_in_window_min": int(nn.min()), "bars_in_window_p5": int(np.quantile(nn, .05)),
                    "bars_in_window_median": int(np.median(nn)), "bars_in_window_max": int(nn.max())})
    for kind in ("daily_break", "weekend", "history_gap"):
        out[f"windows_spanning_{kind}"] = int(spans(gp, kind, t[ok]).sum()) if len(ok) else 0
    return out


def spans(gp, kind, push_open):
    """Per push-bar open time: does a gap of `kind` overlap its window [open - 24 h, open)?"""
    g = [x for x in gp if x["kind"] == kind]
    if not g:
        return np.zeros(len(push_open), dtype=bool)
    frm = np.array([int(x["from"].timestamp()) for x in g], dtype=np.int64)
    to = np.array([int(x["to"].timestamp()) for x in g], dtype=np.int64)
    # gaps are sorted and disjoint: the last gap starting before the push bar opens is the only candidate that
    # can still reach into the window (an earlier one ends before it starts)
    k = np.searchsorted(frm, push_open, side="left") - 1
    hit = k >= 0
    hit[hit] = to[k[hit]] > push_open[hit] - LOOKBACK_S
    return hit


def logged_vs_recomputed(run, b, r, n, gp, runs=RUNS):
    """EA-logged ratios (identifying and, informational, confirmation FVG) vs this recomputation, split by what
    the window spans; plus the window size of every activated setup's identifying FVG."""
    s = cf.read_setups(runs / run / f"rl_setups_{run}.csv")
    t = b["time"].to_numpy(np.int64)
    idx = {int(x): i for i, x in enumerate(t)}
    full = int(np.median(n[n > 0])) if (n > 0).any() else 0
    rows = []
    for _, row in s.iterrows():
        for c1_f, r_f in (("idfvg_c1_time", "idfvg_vol_ratio"), ("cfvg_c1_time", "cfvg_vol_ratio")):
            c1 = row[c1_f]
            if pd.isna(c1) or int(c1) not in idx or pd.isna(row[r_f]):
                continue
            m = idx[int(c1)] + 1
            if np.isfinite(r[m]):
                rows.append({"m": m, "diff": abs(r[m] - float(row[r_f]))})
    d = pd.DataFrame(rows)
    out = {"activated_setups": int(len(s)), "compared": int(len(d)),
           "max_abs_diff": round(float(d["diff"].max()), 5) if len(d) else None}
    if len(d):
        push = t[d["m"].to_numpy()]
        for kind in ("daily_break", "weekend", "history_gap"):
            hit = spans(gp, kind, push)
            out[f"spanning_{kind}"] = {"compared": int(hit.sum()),
                                       "max_abs_diff": round(float(d["diff"][hit].max()), 5) if hit.any() else None}
    # window size of each activated setup's identifying FVG (from the recomputation, where logged bars allow)
    ns = [int(n[idx[int(c1)] + 1]) for c1 in s["idfvg_c1_time"] if not pd.isna(c1) and int(c1) in idx]
    out["activated_window_bars"] = {"normal_window_bars": full, "known": len(ns),
                                    "below_half_normal": int(sum(x < full / 2 for x in ns)),
                                    "min": min(ns) if ns else None}
    return out


def example(b, r, avg, n, m):
    t = b["time"].to_numpy(np.int64)
    start = int(t[m]) - LOOKBACK_S
    j = int(np.searchsorted(t, start, side="left"))
    fmt = lambda x: pd.Timestamp(int(x), unit="s").strftime("%Y-%m-%d %H:%M")
    return {"push_bar": fmt(t[m]), "tick_volume": int(b["tick_volume"][m]), "window_start": fmt(start),
            "first_bar_in_window": fmt(t[j]), "last_bar_in_window": fmt(t[m - 1]), "bars_in_window": int(n[m]),
            "avg": round(float(avg[m]), 1), "ratio": round(float(r[m]), 3), "passes": bool(r[m] >= K)}


def examples(b, r, avg, n, ident, gp, k=5):
    ok = ident[np.isfinite(r[ident])]
    rng = np.random.default_rng(20260930)
    passing = ok[r[ok] >= K][:k]
    failing = ok[r[ok] < K]
    failing = np.sort(rng.choice(failing, size=min(k, len(failing)), replace=False)) if len(failing) else failing
    t = b["time"].to_numpy(np.int64)
    span_wk = ok[spans(gp, "weekend", t[ok])]
    weekend = span_wk[np.argsort(n[span_wk], kind="stable")][:2].tolist() + span_wk[n[span_wk] > 40][:1].tolist()
    return {"pass": [example(b, r, avg, n, m) for m in passing], "fail": [example(b, r, avg, n, m) for m in failing],
            "across_weekend": [example(b, r, avg, n, m) for m in weekend]}


def coverage(run, runs=RUNS):
    f = journal.run_facts(runs / run)
    return {k: f.get(k) for k in ("discarded_days", "discarded_minutes", "total_minute_bars",
                                  "every_tick_generation_used")}


def source_compare(b4, b1):
    j = b4[["time", "tick_volume"]].merge(b1[["time", "tick_volume"]], on="time", suffixes=("_real", "_m1"))
    x, y = j["tick_volume_real"].to_numpy(float), j["tick_volume_m1"].to_numpy(float)
    return {"bars_matched": int(len(j)), "identical_share_pct": round(100 * float(np.mean(x == y)), 1),
            "correlation": round(float(np.corrcoef(x, y)[0, 1]), 4),
            "median_ratio_real_over_m1": round(float(np.median(x / y)), 4)}


def tick_rate(b, sec):
    """Bar tick volume per second of bar length: rate = tick_volume / period seconds (an average over the bar, not
    a per-second count). A hard ceiling would show as a pile-up of bars at one maximum rate."""
    v = b["tick_volume"].to_numpy(float)
    rate = v / sec
    top = np.argsort(-v, kind="stable")[:10]
    mx = float(rate.max())
    rng_ = (b["high"] - b["low"]).to_numpy(float)
    vent = pd.qcut(rng_, 20, labels=False, duplicates="drop")
    by = pd.DataFrame({"v": v, "rng": rng_, "q": vent}).groupby("q").median()
    return {"formula": f"tick_volume / {sec} s", "max_tick_volume": int(v.max()), "max_rate_per_s": round(mx, 2),
            "max_rate_bar": b["dt"][int(np.argmax(v))].strftime("%Y-%m-%d %H:%M"),
            "p99_rate": round(float(np.quantile(rate, .99)), 2), "p999_rate": round(float(np.quantile(rate, .999)), 2),
            "bars_ge_95pct_of_max": int((rate >= .95 * mx).sum()), "bars_ge_99pct_of_max": int((rate >= .99 * mx).sum()),
            "bars_at_exact_max": int((v == v.max()).sum()),
            "top10": [{"bar": b["dt"][i].strftime("%Y-%m-%d %H:%M"), "tick_volume": int(v[i]),
                       "rate_per_s": round(float(rate[i]), 2), "range": round(float(rng_[i]), 2)} for i in top],
            "range_ventiles": {"lowest_range_median": round(float(by["rng"].iloc[0]), 2),
                               "lowest_volume_median": int(by["v"].iloc[0]),
                               "top5_ventiles_range_from_to": [round(float(by["rng"].iloc[-5]), 2),
                                                               round(float(by["rng"].iloc[-1]), 2)],
                               "top5_ventiles_volume_from_to": [int(by["v"].iloc[-5]), int(by["v"].iloc[-1])]}}


def monthly(b, r):
    d = pd.DataFrame({"month": b["dt"].dt.strftime("%Y-%m"), "r": r, "v": b["tick_volume"]})
    return {m: {"median_tick_volume": int(x["v"].median()),
                "cv_tick_volume": round(float(x["v"].std() / x["v"].mean()), 3),
                "ratio_p99": round(float(np.nanquantile(x["r"], .99)), 3),
                "share_ge_2x_pct": round(100 * float((x["r"] >= K).mean()), 3)} for m, x in d.groupby("month")}


def hourly_profile(b):
    hp = b.groupby(b["dt"].dt.hour)["tick_volume"].median()
    return {"min_hour_median": int(hp.min()), "max_hour_median": int(hp.max()),
            "peak_to_trough": round(float(hp.max() / hp.min()), 2),
            "overall_cv": round(float(b["tick_volume"].std() / b["tick_volume"].mean()), 3)}


def funnel(run, runs=RUNS):
    f = journal.run_facts(runs / run)["funnel"]
    s = cf.read_setups(runs / run / f"rl_setups_{run}.csv")
    reasons = s["reason"].value_counts().to_dict()
    keys = ("idfvg_rejected_volume", "idfvg_volume_no_history", "idfvg_volume_empty_window", "activated", "touched",
            "confirmed", "placed", "filled")
    return {**{k: f.get(k) for k in keys},
            "lost_by_reason": {k: int(v) for k, v in reasons.items() if k != "filled"}}


def audit(runs=RUNS):
    res = {}
    for tf, cfg in TF.items():
        b4 = bars(cfg["run"], runs)
        r, avg, n, status = ratios(b4)
        fvg, ident = fvg_middles(b4)
        gp = gaps(b4, cfg["sec"])
        allbars = np.arange(len(b4))
        res[tf] = {
            "window_all_bars": window_facts(b4, n, status, gp, allbars),
            "window_identifying_candidates": window_facts(b4, n, status, gp, ident),
            "history_gaps": [{**g, "from": str(g["from"]), "to": str(g["to"])} for g in gp if g["kind"] == "history_gap"],
            "gap_counts": {k: sum(g["kind"] == k for g in gp) for k in ("daily_break", "weekend", "history_gap")},
            "logged_vs_recomputed": logged_vs_recomputed(cfg["run"], b4, r, n, gp, runs),
            "dist_all_bars": dist(r), "dist_fvg_middles": dist(r[fvg]), "dist_identifying_candidates": dist(r[ident]),
            "examples": examples(b4, r, avg, n, ident, gp),
            "coverage_real_ticks": coverage(cfg["run"], runs),
            "tick_rate": tick_rate(b4, cfg["sec"]),
            "monthly": monthly(b4, r), "hourly_profile": hourly_profile(b4), "funnel": funnel(cfg["run"], runs),
        }
        if (runs / cfg["m1"]).exists():
            res[tf]["source_compare_model4_vs_m1"] = source_compare(b4, bars(cfg["m1"], runs))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", type=pathlib.Path, default=RUNS)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    res = audit(a.runs_dir)
    (OUT / "audit.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
