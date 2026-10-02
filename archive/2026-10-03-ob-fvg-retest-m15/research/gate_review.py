"""Gate-review diagnostics for the R39 chart gate (no new optimization; reads existing pilot evidence).

python research/gate_review.py [--m15-run RUN] [--m5-run RUN] [--folder pilot] [--out DIR] [SETUP_ID ...]
  -> <out>/{pilot_diagnostics.json, trades_shown.md, skips.md, volume_examples.md, chart_prices.json, ...}
Runs are read from results/<folder>/<run id>/ (default pilot_m15 / pilot_m5 under results/pilot). SETUP_IDs are added
to the up-to-6 filled setups shown in volume_examples.md (AMENDMENT A1, R41; AMENDMENT B: the filter applies to the
identifying FVG only, the confirmation-FVG ratio is informational).
"""
import argparse
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mt5r import conformance as cf, reports  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
RES = REPO / "results"
OUT = RES / "gate_review"
CONTRACT = 100.0
PERIOD_S = {"M5": 300, "M15": 900}
SHOWN = {"pilot_m15": [1161, 1424, 1398, 1151, 1668, 2111]}
SKIP_REASONS = ["skipped_volume", "skipped_too_close", "skipped_price_past", "skipped_cap", "skipped_market_closed",
                "skipped_broker_reject"]
FILLED = ("filled", "filled_late")
EXTRA_RUNS = (("smoke_m15_bos", "smoke"), ("gate_entry_ob_edge_m15", "gate_review"),
              ("gate_entry_ob_mid_m15", "gate_review"))


def _ts(sec):
    return pd.Timestamp(int(sec), unit="s").strftime("%Y-%m-%d %H:%M") if pd.notna(sec) else "-"


def _tms(ms):
    return pd.Timestamp(int(ms), unit="ms").strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if pd.notna(ms) else "-"


def _exists(run_id: str, folder: str) -> bool:
    return (RES / folder / run_id / f"rl_setups_{run_id}.csv").exists()


def load(run_id: str, folder: str):
    d = RES / folder / run_id
    setups = cf.read_setups(d / f"rl_setups_{run_id}.csv")
    bars = cf.read_bars(d / f"rl_bars_{run_id}.csv")
    deals = reports.read_deals(d / f"rl_deals_{run_id}.csv")
    days = reports.read_days(d / f"rl_days_{run_id}.csv")
    return setups, bars, deals, days


def positions(setups, deals):
    """One row per position from the MT5 deal records, joined to its setup row."""
    d = deals[deals["type"].isin([0, 1])].copy()
    d["position_id"] = pd.to_numeric(d["position_id"])
    ins, outs = d[d["entry"] == 0], d[d["entry"] != 0]
    g = d.groupby("position_id")
    p = pd.DataFrame({
        "open_time": ins.groupby("position_id")["time"].first(),
        "fill_deal_price": ins.groupby("position_id")["price"].first(),
        "lots": ins.groupby("position_id")["volume"].sum(),
        "close_time": outs.groupby("position_id")["time"].last(),
        "exit_deal_price": outs.groupby("position_id")["price"].last(),
        "exit_comment": outs.groupby("position_id")["comment"].last(),
        "gross": g["profit"].sum(), "commission": g["commission"].sum(), "swap": g["swap"].sum(),
    }).reset_index()
    p["net"] = p["gross"] + p["commission"] + p["swap"]
    s = setups[setups["position_id"].notna()].copy()
    s["position_id"] = s["position_id"].astype("int64")
    p = p.merge(s, on="position_id", how="left")
    p["risk_usd"] = (p["entry"] - p["sl"]).abs() * p["lots"] * CONTRACT
    p["r_net"] = p["net"] / p["risk_usd"]
    p["r_gross"] = p["gross"] / p["risk_usd"]
    # "before costs": the outcome at the intended prices (TP = +RR, SL = -1R); "after costs": realized net R.
    rr = (p["tp"] - p["entry"]).abs() / (p["entry"] - p["sl"]).abs()
    p["r_ideal"] = np.where(p["exit_kind"] == "tp", rr, np.where(p["exit_kind"] == "sl", -1.0, p["r_gross"]))
    return p


def max_equity_dd(days):
    peak = days["eq_max"].cummax().combine(days["bal_open"].cummax(), max)
    run_peak = np.maximum.accumulate(np.maximum(days["eq_max"].to_numpy(), days["bal_open"].to_numpy()))
    trough = days["eq_min"].to_numpy()
    dd_usd = (run_peak - trough).max()
    dd_pct = ((run_peak - trough) / run_peak).max() * 100
    del peak
    return float(dd_usd), float(dd_pct)


def side_stats(p):
    closed = p[p["close_time"].notna()]
    if closed.empty:
        return {"closed_trades": 0}
    wins, losses = closed[closed["net"] > 0], closed[closed["net"] <= 0]
    return {
        "closed_trades": int(len(closed)),
        "win_rate_pct": round(100 * len(wins) / len(closed), 1),
        "avg_win_r": round(float(wins["r_net"].mean()), 3) if len(wins) else None,
        "avg_loss_r": round(float(losses["r_net"].mean()), 3) if len(losses) else None,
        "expectancy_r_before_costs": round(float(closed["r_ideal"].mean()), 3),
        "expectancy_r_after_costs": round(float(closed["r_net"].mean()), 3),
        "expectancy_usd_after_costs": round(float(closed["net"].mean()), 2),
        "net_usd": round(float(closed["net"].sum()), 2),
        "swap_usd": round(float(closed["swap"].sum()), 2),
        "commission_usd": round(float(closed["commission"].sum()), 2),
        "exits_tp": int((closed["exit_kind"] == "tp").sum()), "exits_sl": int((closed["exit_kind"] == "sl").sum()),
        "exits_end": int((closed["exit_kind"] == "end").sum()),
    }


def diagnostics(runs=None, folder="pilot"):
    out = {}
    runs = runs or {"M5": "pilot_m5", "M15": "pilot_m15"}
    for tf in ("M5", "M15"):
        run = runs[tf]
        setups, bars, deals, days = load(run, folder)
        p = positions(setups, deals)
        dd_usd, dd_pct = max_equity_dd(days)
        out[tf] = {"all": side_stats(p), "long": side_stats(p[p["dir"] == "L"]),
                   "short": side_stats(p[p["dir"] == "S"]),
                   "final_balance": round(10000.0 + float(p["net"].sum()), 2),
                   "max_equity_dd_usd": round(dd_usd, 2), "max_equity_dd_pct": round(dd_pct, 2)}
    return out


def trades_table(run, folder, ids=None):
    setups, bars, deals, days = load(run, folder)
    p = positions(setups, deals)
    if ids is not None:
        p = p[p["setup_id"].isin(ids)]
    rows = ["| setup | dir | order price (intended entry) | MT5 fill price | SL | TP | MT5 exit price | exit | lots | "
            "planned risk USD | gross USD | swap USD | commission USD | net USD | R net | R before costs |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in p.sort_values("open_time").iterrows():
        rows.append(f"| {int(r.setup_id)} | {r.dir} | {r.entry:.2f} | {r.fill_deal_price:.2f} | {r.sl:.2f} | {r.tp:.2f} | "
                    f"{r.exit_deal_price:.2f} | {r.exit_kind} | {r.lots:.2f} | {r.risk_usd:.2f} | {r.gross:+.2f} | "
                    f"{r.swap:+.2f} | {r.commission:+.2f} | {r.net:+.2f} | {r.r_net:+.2f} | {r.r_ideal:+.2f} |")
    return "\n".join(rows), p


def chart_prices_check(run, folder):
    """Chart markers draw rl_setups fill_price/exit_price; compare them with the MT5 deal prices."""
    setups, bars, deals, days = load(run, folder)
    p = positions(setups, deals)
    p = p[p["setup_id"].notna()]
    fill_diff = (p["fill_price"] - p["fill_deal_price"]).abs()
    exit_diff = (p["exit_price"] - p["exit_deal_price"]).abs()
    closed = p["close_time"].notna()
    return {"positions": int(len(p)), "fill_price_mismatches": int((fill_diff > 1e-6).sum()),
            "exit_price_mismatches_closed": int((exit_diff[closed] > 1e-6).sum())}


def exposures_at(setups, t_ms):
    """Own positions + own pending orders open at time t_ms (from the setup log)."""
    s = setups
    pend = s["place_time_msc"].notna() & (s["place_time_msc"] <= t_ms) & (
        (s["fill_time_msc"].isna() & (s["reason_time_msc"].isna() | (s["reason_time_msc"] > t_ms)))
        | (s["fill_time_msc"] > t_ms))
    pos = s["fill_time_msc"].notna() & (s["fill_time_msc"] <= t_ms) & (
        s["exit_time_msc"].isna() | (s["exit_time_msc"] > t_ms))
    return int(pend.sum()), int(pos.sum())


def broker_rejections(run):
    """setup id -> 'retcode text' for placements the broker refused (journal of the last test in the run)."""
    import re
    logs = sorted((REPO / "runs" / run / "logs").glob("Tester__logs__*.log"))
    text = "\n".join(p.read_bytes().decode("utf-16", errors="replace") for p in logs)
    text = text[text.rfind("testing of Experts"):]
    return {int(m.group(1)): f"{m.group(2)} {m.group(3).strip()}"
            for m in re.finditer(r"setup #(\d+) placement rejected, retcode (\d+) (.*)", text)}


def skips(run, folder, tf, per_reason=3):
    setups, bars, deals, days = load(run, folder)
    rejected = broker_rejections(run)
    P = PERIOD_S[tf]
    spread = days.set_index("date")["spread_median"]
    bal = deals.sort_values(["time", "ticket"]).copy()
    bal["cum"] = (bal["profit"] + bal["commission"] + bal["swap"]).cumsum()
    lines = []
    for reason in SKIP_REASONS:
        rows = setups[setups["reason"] == reason]
        if reason == "skipped_price_past":       # show genuine cases and broker refusals side by side
            rows = pd.concat([rows[~rows["setup_id"].isin(rejected)].head(per_reason),
                              rows[rows["setup_id"].isin(rejected)].head(per_reason)])
        else:
            rows = rows.head(per_reason)
        lines.append(f"\n### {reason} ({int((setups['reason'] == reason).sum())} in {run})\n")
        if reason == "skipped_market_closed":
            lines.append("| setup | dir | entry | confirmation c3 closed | first market-closed refusal | placement attempts | "
                         "gave up at | closed bars since c3 at give-up |\n|---|---|---|---|---|---|---|---|")
        elif reason == "skipped_broker_reject":
            lines.append("| setup | dir | entry | SL | TP | confirmation c3 closed | rejected at | placement attempts | "
                         "broker refusal (journal) |\n|---|---|---|---|---|---|---|---|---|")
        elif reason in ("skipped_price_past", "skipped_too_close"):
            lines.append("| setup | dir | entry | placement tick (first bar after c3) | Bid at placement | median spread that day | "
                         "est. Ask | distance to entry (points; <= 0 = already past) | stops level (points) | broker refusal |\n|---|---|---|---|---|---|---|---|---|---|")
        elif reason == "skipped_volume":
            lines.append("| setup | dir | entry | SL | stop distance | balance at placement | risk 1% USD | "
                         "loss per 1.00 lot at SL | computed lots | broker min lots |\n|---|---|---|---|---|---|---|---|---|---|")
        else:
            lines.append("| setup | dir | entry | time | pending orders | open positions | total vs cap 3 |\n"
                         "|---|---|---|---|---|---|---|")
        for _, r in rows.iterrows():
            t_place = int(r["cfvg_c3_time"]) + P
            attempts = r.get("place_attempts")
            attempts = int(attempts) if pd.notna(attempts) else "-"
            if reason == "skipped_market_closed":
                t_end = r["reason_time_msc"] / 1000 if pd.notna(r["reason_time_msc"]) else np.nan
                closed = int(((bars["time"] > int(r["cfvg_c3_time"])) & (bars["time"] + P <= t_end)).sum())
                lines.append(f"| {int(r.setup_id)} | {r.dir} | {r.entry:.2f} | {_ts(t_place)} | "
                             f"{_tms(r.get('market_closed_first_msc'))} | {attempts} | {_tms(r['reason_time_msc'])} | "
                             f"{closed} |")
            elif reason == "skipped_broker_reject":
                lines.append(f"| {int(r.setup_id)} | {r.dir} | {r.entry:.2f} | {r.sl:.2f} | {r.tp:.2f} | {_ts(t_place)} | "
                             f"{_tms(r['reason_time_msc'])} | {attempts} | {rejected.get(int(r.setup_id), '-')} |")
            elif reason in ("skipped_price_past", "skipped_too_close"):
                nxt = bars[bars["time"] >= t_place].iloc[0]          # first tick after c3 closes (session gaps)
                bid, t_tick = float(nxt["open"]), int(nxt["time"])
                day = pd.Timestamp(t_tick, unit="s").strftime("%Y.%m.%d")
                sp = float(spread.get(day, np.nan))
                ask = bid + sp
                px = ask if r["dir"] == "L" else bid
                dist = (px - r["entry"]) * (1 if r["dir"] == "L" else -1) / 0.01
                lines.append(f"| {int(r.setup_id)} | {r.dir} | {r.entry:.2f} | {_ts(t_tick)} | {bid:.2f} | {sp:.2f} | {ask:.2f} | "
                             f"{dist:.0f} | {int(r.stops_level_pts) if pd.notna(r.stops_level_pts) else 20} | "
                             f"{rejected.get(int(r.setup_id), '-')} |")
            elif reason == "skipped_volume":
                closed_before = bal[pd.to_datetime(bal["time"], format="%Y.%m.%d %H:%M:%S") <
                                    pd.Timestamp(t_place, unit="s")]
                balance = float(closed_before["cum"].iloc[-1]) if len(closed_before) else 10000.0
                dist = abs(r["entry"] - r["sl"])
                risk = balance * 0.01
                loss_lot = dist * CONTRACT
                lines.append(f"| {int(r.setup_id)} | {r.dir} | {r.entry:.2f} | {r.sl:.2f} | {dist:.2f} | {balance:.2f} | "
                             f"{risk:.2f} | {loss_lot:.2f} | {risk / loss_lot:.4f} | 0.01 |")
            else:
                pend, pos = exposures_at(setups, t_place * 1000)
                lines.append(f"| {int(r.setup_id)} | {r.dir} | {r.entry:.2f} | {_ts(t_place)} | {pend} | {pos} | "
                             f"{pend + pos} / 3 |")
    return "\n".join(lines)


def volume_rows(setups, bars, period_s, ids=None, n=6, multiplier=2.0, lookback_hours=24):
    """Markdown table rows for both FVGs of up to n filled setups (lowest ids) plus ``ids``: middle candle, its tick
    volume, the average over the bars opening in the lookback window (AMENDMENT C), the ratio and pass/fail, all recomputed from rl_bars (logged ratio alongside).
    Pass/fail applies to the identifying FVG only; the confirmation FVG's last cell is "info" (AMENDMENT B)."""
    filled = setups[setups["reason"].isin(FILLED)].sort_values("setup_id")["setup_id"].astype(int).head(n).tolist()
    extra = [int(i) for i in (ids or []) if int(i) not in filled and (setups["setup_id"] == int(i)).any()]
    B = cf._Bars(bars, period_s)
    rows = [f"| setup | dir | reason | FVG | middle candle (bar open) | tick volume | window start | bars in window | "
            f"average | ratio | logged ratio | identifying FVG >= {multiplier:g}x |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for sid in filled + extra:
        r = setups[setups["setup_id"] == sid].iloc[0]
        for name, c1_f, logged_f in (("identifying", "idfvg_c1_time", "idfvg_vol_ratio"),
                                     ("confirmation", "cfvg_c1_time", "cfvg_vol_ratio")):
            logged = r.get(logged_f)
            logged = f"{logged:.4f}" if logged is not None and pd.notna(logged) else "-"
            d = cf.fvg_volume(B, r[c1_f], period_s, lookback_hours) if pd.notna(r[c1_f]) else None
            if d is None:
                why = "no such FVG" if pd.isna(r[c1_f]) else "middle candle not in rl_bars"
                rows.append(f"| {sid} | {r.dir} | {r.reason} | {name} | {why} | - | - | - | - | - | {logged} | - |")
                continue
            vol = "n/a" if d["tick_volume"] is None else f"{d['tick_volume']:.0f}"
            if d["ratio"] is None:
                why = {"no_history": "window starts before the first logged bar", "empty": "no bars in the window"}
                rows.append(f"| {sid} | {r.dir} | {r.reason} | {name} | {_ts(d['middle_time'])} | {vol} | "
                            f"{_ts(d['window_start'])} | {d['n'] if d['status'] != 'no_history' else 'n/a'} | "
                            f"n/a ({why.get(d['status'], 'no tick volume')}) | n/a | {logged} | "
                            f"{'info' if name == 'confirmation' else 'n/a'} |")
                continue
            ok = "info" if name == "confirmation" else "pass" if d["ratio"] >= multiplier else "FAIL"
            rows.append(f"| {sid} | {r.dir} | {r.reason} | {name} | {_ts(d['middle_time'])} | {vol} | "
                        f"{_ts(d['window_start'])} | {d['n']} | {d['average']:.2f} | {d['ratio']:.4f} | {logged} | {ok} |")
    return rows


def volume_examples(runs, folder, ids=None, multiplier=2.0, lookback_hours=24):
    parts = ["# Volume filter on the identifying FVG (R41): worked examples from rl_bars\n",
             f"ratio = tick volume of the FVG's middle candle (candle 2) / mean tick volume of the bars that open in the "
             f"{lookback_hours:g} wall-clock hours before it opens (AMENDMENT C: the candle itself excluded, closed "
             f"hours add no bars, nothing from before the window is used); the identifying FVG "
             f"qualifies when ratio >= {multiplier:g}. The confirmation FVG has no volume requirement (AMENDMENT B): "
             "its ratio is shown as informational (\"info\"). Averages and ratios are recomputed here from rl_bars; the logged ratio is the "
             "EA's own rl_setups value. Up to 6 filled setups per run (lowest setup ids) plus any ids given on the "
             "command line."]
    for tf, run in runs.items():
        parts.append(f"\n## {run} ({tf})\n")
        if not _exists(run, folder):
            parts.append(f"results/{folder}/{run} not found.")
            continue
        setups, bars, deals, days = load(run, folder)
        if not cf._Bars(bars, PERIOD_S[tf]).has_vol:
            parts.append("rl_bars has no tick_volume column (pre-amendment run): nothing to recompute.\n")
        parts += volume_rows(setups, bars, PERIOD_S[tf], ids, multiplier=multiplier, lookback_hours=lookback_hours)
    return "\n".join(parts) + "\n"


def bos_details(run, folder, ids):
    setups, bars, deals, days = load(run, folder)
    rows = ["| setup | dir | swing peak bar | swing confirmed (bar closed) | swing level | break close bar | OB candle | "
            "activation |", "|---|---|---|---|---|---|---|---|"]
    for _, r in setups[setups["setup_id"].isin(ids)].iterrows():
        conf_close = int(r["bos_pivot_conf_time"]) + PERIOD_S["M15"]
        rows.append(f"| {int(r.setup_id)} | {r.dir} | {_ts(r.bos_pivot_time)} | {_ts(r.bos_pivot_conf_time)} "
                    f"(closed {_ts(conf_close)}) | {r.bos_level:.2f} | {_ts(r.bos_break_time)} | {_ts(r.ob_time)} | "
                    f"{_ts(r.activation_time)} |")
    return "\n".join(rows)


def extra_charts(out=OUT):
    from mt5r import charts_setups
    made = {}
    for run, folder in (("gate_entry_ob_edge_m15", "gate_review"), ("gate_entry_ob_mid_m15", "gate_review")):
        if not _exists(run, folder):
            continue
        setups, bars, deals, days = load(run, folder)
        p = positions(setups, deals)
        closed = p[p["close_time"].notna() & p["exit_kind"].isin(["tp", "sl"])]
        ids = [int(closed[closed["dir"] == d]["setup_id"].iloc[0]) for d in ("L", "S")
               if (closed["dir"] == d).any()]
        res = charts_setups.render(setups[setups["setup_id"].isin(ids)], bars, deals, out / run, PERIOD_S["M15"])
        made[run] = {"ids": ids, "charts": [str(c) for c in res["charts"]]}
    return made


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--m15-run", default="pilot_m15", help="M15 run id (default pilot_m15)")
    ap.add_argument("--m5-run", default="pilot_m5", help="M5 run id (default pilot_m5)")
    ap.add_argument("--folder", default="pilot", help="results/<folder>/<run id> holds both runs (default pilot)")
    ap.add_argument("--out", default=str(OUT), help="output directory (default results/gate_review)")
    ap.add_argument("--shown", type=int, nargs="*", default=None,
                    help="setup ids for trades_shown.md (default: the charted ids of pilot_m15, else every position)")
    ap.add_argument("--volume-multiplier", type=float, default=2.0, help="VolumeMultiplier of the runs (default 2.0)")
    ap.add_argument("--volume-lookback-hours", type=float, default=24, help="VolumeLookbackHours (default 24)")
    ap.add_argument("--no-extra", action="store_true", help="skip the smoke/gate_review extra evidence")
    ap.add_argument("setup_ids", type=int, nargs="*", help="extra setup ids for volume_examples.md")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    runs, folder, m15 = {"M15": a.m15_run, "M5": a.m5_run}, a.folder, a.m15_run
    diag = diagnostics(runs, folder)
    (out / "pilot_diagnostics.json").write_text(json.dumps(diag, indent=1))
    md, _ = trades_table(m15, folder, a.shown if a.shown is not None else SHOWN.get(m15))
    (out / "trades_shown.md").write_text(f"# Charted trades from MT5 deal records ({m15}, M15)\n\n" + md + "\n")
    (out / "skips.md").write_text(f"# Skip reasons with the numbers behind them ({m15}, M15)\n\n"
                                  "Bid at placement = open of the bar after the confirmation candle 3 (the placement tick is "
                                  "that bar's first tick). Ask is estimated as Bid + that day's median spread. Balance at "
                                  "placement = deposit + net of deals closed before it. Exposures are reconstructed from the "
                                  "setup log. Market-closed rows: the first TRADE_RETCODE_MARKET_CLOSED refusal and the "
                                  "attempt count come from rl_setups; broker refusals come from the run journal.\n"
                                  + skips(m15, folder, "M15"))
    (out / "volume_examples.md").write_text(volume_examples(runs, folder, a.setup_ids, a.volume_multiplier,
                                                            a.volume_lookback_hours))
    chk = {r: chart_prices_check(r, folder) for r in (m15, a.m5_run)}
    if not a.no_extra:
        chk.update({r: chart_prices_check(r, f) for r, f in EXTRA_RUNS if _exists(r, f)})
        made = extra_charts(out)
        parts = ["# Extra gate evidence\n"]
        if _exists("smoke_m15_bos", "smoke"):
            parts += ["## FVG+BOS examples (smoke M15, ObMode=1)\n", bos_details("smoke_m15_bos", "smoke", [189, 200]),
                      "\n\n## Trades from MT5 deal records: FVG+BOS examples\n",
                      trades_table("smoke_m15_bos", "smoke", [189, 200])[0]]
        for run, m in made.items():
            parts += [f"\n\n## Trades from MT5 deal records: {run}\n", trades_table(run, "gate_review", m["ids"])[0]]
        (out / "extra_examples.md").write_text("\n".join(parts) + "\n")
        (out / "extra_charts.json").write_text(json.dumps(made, indent=1))
    (out / "chart_prices.json").write_text(json.dumps(chk, indent=1))
    print(json.dumps(diag, indent=1))
    print(json.dumps(chk, indent=1))


if __name__ == "__main__":
    main()
