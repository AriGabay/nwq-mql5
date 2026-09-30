"""Pipeline entry point: python research/cli.py <step>

Steps: wfo (U6 folds + final selection), robustness (U8), deliver (U9).
Every step reads research/preregistration.json and refuses to run when it has uncommitted changes.
"""
import argparse
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mt5r import curate, env, explog, pipeline, reports, wfo  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
P = pipeline.PREREG
AXES = P["grid"]
PARAMS = list(AXES)
DEFAULTS = {"PivL": 3, "PivR": 3, "SweepToSetupBars": 12, "VolumeMultiplier": 2.0, "ConfirmationBars": 6}


def prereg_committed() -> None:
    r = subprocess.run(["git", "status", "--porcelain", "research/preregistration.json"], cwd=REPO,
                       capture_output=True, text=True)
    if r.stdout.strip():
        raise SystemExit("preregistration.json has uncommitted changes; refusing to run")


def grid_for_window(cfg, tag: str, start: str, end: str):
    """Four complete optimizations (one per VolumeMultiplier) merged into the 576-row grid."""
    ranges = {k: tuple(v) for k, v in P["grid_ranges"].items()}
    parts, frames = [], []
    for vm in AXES["VolumeMultiplier"]:
        run_id = f"{tag}_vm{str(vm).replace('.', '')}"
        res, df = pipeline.run_optimization(cfg, run_id, "research", P["period"], start, end,
                                            {"VolumeMultiplier": vm}, ranges, role="train", purpose=f"{tag} grid")
        if df is None:
            raise SystemExit(f"optimization {run_id} failed")
        df["VolumeMultiplier"] = vm
        parts.append(df)
        curate.curate(run_id, "wfo")
    grid = wfo.merge_grids([p[PARAMS + ["profit", "trades", "eq_dd_pct", "recovery_factor", "custom", "sharpe"]]
                            for p in parts], AXES)
    rf = grid["recovery_factor"]
    grid["eq_dd_money"] = (grid["profit"] / rf).abs().where(rf != 0, 1.0)
    return grid


def baseline_trades(cfg, tag: str, start: str, end: str) -> int:
    res, rep = pipeline.run_single(cfg, f"{tag}_baseline_train", "research", P["period"], start, end,
                                   role="train_baseline_count", purpose=f"{tag} baseline trade count (T only)")
    return reports.summary(rep)["trades"]


def select_on(cfg, tag: str, start: str, end: str) -> dict:
    n_base = baseline_trades(cfg, tag, start, end)
    floor = wfo.trade_floor(n_base)
    grid = grid_for_window(cfg, tag, start, end)
    sel = wfo.select(grid, PARAMS, DEFAULTS, floor, P["selection"]["max_equity_dd_pct"])
    out = REPO / "results" / "wfo" / f"{tag}_selection"
    out.mkdir(parents=True, exist_ok=True)
    sel["scored"].to_csv(out / "scored_grid.csv", index=False)
    info = {"tag": tag, "train": [start, end], "baseline_trades": n_base, "trade_floor": floor,
            "status": sel["status"], "params": sel["params"],
            "eligible_passes": int(sel["scored"]["eligible"].sum())}
    (out / "selection.json").write_text(json.dumps(info, indent=1))
    explog.append({"id": f"{tag}_selection", "purpose": "R16 selection", "role": "selection", "status": sel["status"],
                   "notes": json.dumps(info)})
    return info


def cmd_wfo(args) -> None:
    prereg_committed()
    cfg = env.load_config()
    folds_out = REPO / "results" / "wfo" / "folds.json"
    done = json.loads(folds_out.read_text()) if folds_out.exists() else []
    deposits = {"procedure": P["deposit"], "baseline": P["deposit"]}
    for rec in done:
        deposits = rec["next_deposits"]
    for fold in P["folds"][len(done):]:
        k = fold["fold"]
        info = select_on(cfg, f"f{k}", *fold["train"])
        rec = {"fold": k, "selection": info, "test": fold["test"], "deposits": dict(deposits), "oos": {}}
        for who, params in (("procedure", info["params"]), ("baseline", {})):
            run_id = f"f{k}_oos_{who}"
            res, rep = pipeline.run_single(cfg, run_id, "research", P["period"], *fold["test"],
                                           deposit=deposits[who], overrides=params, role=f"oos_{who}",
                                           purpose=f"fold {k} OOS {who}")
            s = reports.summary(rep)
            final = deposits[who] + s["net_profit"]
            rec["oos"][who] = {**s, "final_balance": round(final, 2), "run_id": run_id}
            curate.curate(run_id, "wfo")
        deposits = {w: rec["oos"][w]["final_balance"] for w in rec["oos"]}
        rec["next_deposits"] = deposits
        done.append(rec)
        folds_out.write_text(json.dumps(done, indent=1))
        print(f"fold {k}: {info['status']} {info['params']} | OOS procedure {rec['oos']['procedure']['net_profit']:.2f}"
              f" baseline {rec['oos']['baseline']['net_profit']:.2f}", flush=True)
    final = REPO / "results" / "final_selection" / "selection.json"
    if not final.exists():
        info = select_on(cfg, "final", *P["final_train"])
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text(json.dumps(info, indent=1))
        print(f"final candidate: {info['status']} {info['params']}", flush=True)


FINAL = REPO / "results" / "final_selection" / "selection.json"
DELIV = REPO / "deliverables"


def tested_values(params: dict) -> dict:
    """Full delivered-EA input values as tested: code defaults + R10 + SignalTF=M15 + params."""
    vals = pipeline.base_values("v104", P["signal_tf_value"])
    vals.update(params)
    return vals


def cmd_freeze(args) -> None:
    """Write the frozen candidate and the original/baseline .set files (before any check-period run)."""
    from mt5r import setfile
    sel = json.loads(FINAL.read_text())
    DELIV.mkdir(exist_ok=True)
    orig_specs = setfile.parse_inputs((REPO / "original" / "new_test_v1.03.mq5").read_text())
    setfile.write_set(DELIV / "new_test_original.set", setfile.render_lines(orig_specs),
                      header="new_test original parameters: code defaults of v1.03 (all 25 inputs)")
    v104 = pipeline.specs("v104")
    setfile.write_set(DELIV / "new_test_baseline_m15.set", setfile.render_lines(v104, tested_values({})),
                      header="Baseline as tested: v1.03 code defaults + SignalTF=M15 + DrawObjects/DebugMode/PopupAlerts off")
    setfile.write_set(DELIV / "new_test_candidate.set", setfile.render_lines(v104, tested_values(sel["params"])),
                      header=f"Frozen candidate (R17 selection on {sel['train'][0]}-{sel['train'][1]}): {sel['params']}")
    print("frozen candidate:", sel["params"])


def _chain(cfg, tag, months, params, kind="research", period=None, deposit=None, execution_mode=0, role=""):
    """Run consecutive months with deposit chaining; returns list of evaluate.load_run dicts."""
    from mt5r import evaluate
    period = period or P["period"]
    dep = deposit or P["deposit"]
    out = []
    for start, end in months:
        run_id = f"{tag}_{start[:7].replace('.', '')}"
        pipeline.run_single(cfg, run_id, kind, period, start, end, deposit=dep, overrides=params,
                            execution_mode=execution_mode, role=role, purpose=tag)
        r = evaluate.load_run(run_id)
        out.append(r)
        dep = round(dep + r["summary"]["net_profit"], 2)
        curate.curate(run_id, "robustness")
    return out


def cmd_robustness(args) -> None:
    import numpy as np
    import pandas as pd
    from mt5r import evaluate, montecarlo, metrics
    prereg_committed()
    r = subprocess.run(["git", "status", "--porcelain", "deliverables/new_test_candidate.set"], cwd=REPO,
                       capture_output=True, text=True)
    if r.stdout.strip() or not (DELIV / "new_test_candidate.set").exists():
        raise SystemExit("candidate is not frozen and committed; run `freeze` and commit first")
    cfg = env.load_config()
    folds = json.loads((REPO / "results" / "wfo" / "folds.json").read_text())
    cand = json.loads(FINAL.read_text())["params"]
    out_dir = REPO / "results" / "robustness"
    months = [tuple(f["test"]) for f in P["folds"]]

    proc = evaluate.stitch([evaluate.load_run(f["oos"]["procedure"]["run_id"]) for f in folds])
    base = evaluate.stitch([evaluate.load_run(f["oos"]["baseline"]["run_id"]) for f in folds])
    fold_rows = [{"fold": f["fold"], "params": f["selection"]["params"], "status": f["selection"]["status"],
                  "procedure_net": f["oos"]["procedure"]["net_profit"], "baseline_net": f["oos"]["baseline"]["net_profit"],
                  "procedure_ret": f["oos"]["procedure"]["net_profit"] / f["deposits"]["procedure"],
                  "baseline_ret": f["oos"]["baseline"]["net_profit"] / f["deposits"]["baseline"],
                  "procedure_trades": f["oos"]["procedure"]["trades"]} for f in folds]

    # R22 random-delay execution on the same folds, deposits and sets
    delay = {}
    for who in ("procedure", "baseline"):
        nets = []
        for f in folds:
            params = f["selection"]["params"] if who == "procedure" else {}
            run_id = f"f{f['fold']}_oos_{who}_delay"
            _, rep = pipeline.run_single(cfg, run_id, "research", P["period"], *f["test"], deposit=f["deposits"][who],
                                         overrides=params, execution_mode=P["stress"]["random_delay_execution_mode"],
                                         role="stress_delay", purpose="R22 random delay")
            nets.append(reports.summary(rep)["net_profit"])
            curate.curate(run_id, "robustness")
        delay[who] = round(float(sum(nets)), 2)

    # criterion (j) and R23: static candidate and neighbors on Mar-Apr (out of sample for the candidate)
    static_c = _chain(cfg, "static_cand", months[:2], cand, role="static_candidate")
    static = {"candidate": round(sum(x["summary"]["net_profit"] for x in static_c), 2),
              "baseline": round(sum(fr["baseline_net"] for fr in fold_rows[:2]), 2)}
    nbrs = wfo.neighbors(cand, AXES)
    nb_rows = []
    for i, nb in enumerate(nbrs):
        oos = _chain(cfg, f"nb{i}_oos", months[:2], nb, role="neighbor_oos")
        ins = _chain(cfg, f"nb{i}_ins", months[2:], nb, role="neighbor_insample")
        nb_rows.append({"params": nb, "oos_mar_apr_net": round(sum(x["summary"]["net_profit"] for x in oos), 2),
                        "insample_may_jul_net": round(sum(x["summary"]["net_profit"] for x in ins), 2)})
    cand_ins = _chain(cfg, "static_cand_ins", months[2:], cand, role="candidate_insample")
    share = float(np.mean([r["oos_mar_apr_net"] > 0 for r in nb_rows])) if nb_rows else float("nan")
    neighbors = {"profitable_share": share, "rows": nb_rows,
                 "candidate_insample_may_jul_net": round(sum(x["summary"]["net_profit"] for x in cand_ins), 2)}

    # cross-trial Sharpe variance from the training grids (custom = daily Sharpe), passes with trades
    variances = []
    for f in folds:
        g = pd.read_csv(REPO / "results" / "wfo" / f"f{f['fold']}_selection" / "scored_grid.csv")
        v = g.loc[g["trades"] > 0, "custom"].var(ddof=1)
        if not np.isnan(v):
            variances.append(v)
    var_sr = float(np.mean(variances)) if variances else float("nan")

    acc = evaluate.evaluate(proc, base, fold_rows, delay, neighbors, static, var_sr, P)

    # check periods (not independent) and the sub-period, after the freeze
    checks = {}
    for label, (start, end) in (("aug_sep", P["check_period_not_independent"]),
                                ("sep16_29", P["independent_subperiod_if_verified"])):
        for who, params in (("candidate", cand), ("baseline", {})):
            run_id = f"check_{label}_{who}"
            pipeline.run_single(cfg, run_id, "research", P["period"], start, end, overrides=params,
                                role="check_period", purpose=f"R13/R14 {label}")
            x = evaluate.load_run(run_id)
            ev = metrics.trade_events(x["trades"])
            from mt5r import limits
            checks[f"{label}_{who}"] = {**x["summary"], "events": len(ev),
                                        "breach": limits.first_breach(x["days"], initial=x["deposit"])}
            curate.curate(run_id, "check_period")
    indep = "NOT independent" not in (REPO / "results" / "independence_check.md").read_text()
    sub = checks["sep16_29_candidate"]
    aug = checks["aug_sep_candidate"]
    recommended = (acc["_passed_all"] and aug["breach"] is None and aug["net_profit"] >= 0 and indep
                   and sub["events"] >= P["acceptance"]["recommended_requires_independent_min_events"]
                   and sub["net_profit"] >= 0 and sub["breach"] is None)

    # M5 reference baseline (R9), reference only
    m5 = _chain(cfg, "ref_m5_base", months, {"SignalTF": 5}, period="M5", role="m5_reference")
    m5_check = _chain(cfg, "ref_m5_base_check", [tuple(P["check_period_not_independent"])], {"SignalTF": 5},
                      period="M5", role="m5_reference")

    ev = metrics.trade_events(proc["trades"])
    shuffle = montecarlo.shuffle_paths(ev["ret"].to_numpy(), n_paths=P["stats"]["mc_paths"], seed=P["stats"]["seed"],
                                       initial=proc["initial"]) if len(ev) else {}
    evaluate.save({"acceptance": acc, "recommended": recommended, "independent_subperiod": indep,
                   "fold_rows": fold_rows, "delay_net": delay, "static": static, "neighbors": neighbors,
                   "var_sr": var_sr, "checks": checks,
                   "procedure_stitched": {"net": proc["net_profit"], "summary": metrics.summary(proc["trades"], proc["days"], proc["initial"])},
                   "baseline_stitched": {"net": base["net_profit"], "summary": metrics.summary(base["trades"], base["days"], base["initial"])},
                   "stress_spread": {k: evaluate.stressed_profit(proc, k) for k in P["stress"]["extra_spread_k"]},
                   "stress_spread_baseline": {k: evaluate.stressed_profit(base, k) for k in P["stress"]["extra_spread_k"]},
                   "shuffle_mc": {k: v for k, v in shuffle.items() if not hasattr(v, "__len__") or isinstance(v, dict)},
                   "m5_reference": {"oos_net": round(sum(x["summary"]["net_profit"] for x in m5), 2),
                                    "oos_trades": int(sum(x["summary"]["trades"] for x in m5)),
                                    "check_net": m5_check[0]["summary"]["net_profit"],
                                    "check_trades": m5_check[0]["summary"]["trades"]}},
                  REPO / "results" / "acceptance.json")
    print(json.dumps({"passed_all": acc["_passed_all"], "recommended": recommended}, indent=1))


def cmd_deliver(args) -> None:
    """R29 validation runs of the exported .set files (development window only), tables and charts."""
    from mt5r import deliver, ini as inimod, runner as runmod, setfile
    cfg = env.load_config()
    d = deliver.load()
    checks = {}
    for name in ("new_test_candidate.set", "new_test_baseline_m15.set"):
        lines = deliver.set_file_lines(DELIV / name)
        run_id = "validate_" + name.replace(".set", "")
        start, end = P["folds"][0]["test"][0], P["folds"][-1]["test"][1]
        text = inimod.render(expert="new_test.ex5", symbol=P["symbol"], period=P["period"], from_date=start,
                             to_date_inclusive=end, deposit=P["deposit"], report=f"reports\\{run_id}", set_lines=lines)
        res = runmod.run(cfg, run_id, text, "new_test.ex5", meta={"role": "set_validation", "set": name})
        rep = reports.parse_html(res.report)
        expected = setfile.read_set(DELIV / name)
        mism = pipeline.check_inputs_loaded(rep, expected)
        s = reports.summary(rep)
        checks[name] = {"inputs_in_file": len(expected), "mismatches": mism, "net_profit": s["net_profit"],
                        "trades": s["trades"], "period": [start, end], "build": rep["header"].get("Build")}
        explog.append({"id": run_id, "purpose": "R29 .set validation (dev window)", "role": "set_validation",
                       "status": "ok" if not mism else "mismatch", "net_profit": s["net_profit"], "trades": s["trades"]})
        curate.curate(run_id, "set_validation")
    (REPO / "results" / "set_validation.json").write_text(json.dumps(checks, indent=1, default=str))
    (DELIV / "parameter_table.md").write_text("# Parameter table\n\n" + deliver.parameter_table(d) + "\n")
    made = deliver.charts(d, DELIV / "charts")
    rec = DELIV / "new_test_recommended.set"
    if deliver.should_write_recommended(d["acc"]):
        rec.write_bytes((DELIV / "new_test_candidate.set").read_bytes())
    elif rec.exists():
        rec.unlink()
    print(json.dumps({"set_validation": {k: len(v["mismatches"]) for k, v in checks.items()}, "charts": made,
                      "recommended_written": rec.exists()}, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("wfo", "freeze", "robustness", "deliver"):
        sub.add_parser(name)
    args = ap.parse_args()
    {"wfo": cmd_wfo, "freeze": cmd_freeze, "robustness": cmd_robustness, "deliver": cmd_deliver}[args.cmd](args)


if __name__ == "__main__":
    main()
