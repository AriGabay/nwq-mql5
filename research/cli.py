"""OB-FVG retest research pipeline: python research/cli.py <step>  (plan KTD13)

Order: install -> smoke -> conformance -> optsmoke -> pilot -> charts -> STOP (R39 chart gate, user approval)
-> freeze-rules (commit) -> wfo -> freeze (commit) -> holdout -> robustness -> deliver.

Guards: every step that runs the tester refuses while the live terminal runs (runner.run). wfo, freeze, holdout,
robustness and deliver refuse on a missing or uncommitted pre-registration (git errors count as uncommitted).
holdout also refuses unless the frozen .set files are committed, and runs at most once per EA source hash
and .set hash (a run without a report may be repeated).
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from mt5r import compile as compmod  # noqa: E402
from mt5r import (curate, deliver, env, evaluate, explog, journal, limits, metrics, montecarlo,  # noqa: E402
                  pipeline, reports, runner, setfile, stats, textio, trades, wfo)

REPO = pathlib.Path(__file__).resolve().parents[1]
RUN = pipeline.RUN
RESULTS = REPO / "results"
DELIV = REPO / "deliverables"
FINAL = RESULTS / "final_selection" / "selection.json"
FOLDS = RESULTS / "wfo" / "folds.json"
PILOT = RESULTS / "pilot" / "pilot_summary.json"
PREREG_REL = "research/preregistration.json"
ORIG_SET, BASE_SET, CAND_SET = ("ob_fvg_retest_original.set", "ob_fvg_retest_baseline.set",
                                "ob_fvg_retest_candidate.set")
WFO_WINDOW = list(RUN["windows"]["wfo"])
DAYS_PER_MONTH, FILL_TARGET = 30.44, 15          # R20 / KTD12
TF_SECONDS = {1: 60, 5: 300, 15: 900, 30: 1800, 16385: 3600}   # SignalTF enum value -> bar seconds


# ------------------------------------------------------------------ guards and hashes
def _rel(p) -> str:
    p = pathlib.Path(p)
    try:
        return p.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(p)


def committed(path: str) -> bool:
    """True only for an existing, tracked file with no uncommitted changes. Any git error -> False."""
    if not (REPO / path).exists():
        return False
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", path], cwd=REPO, capture_output=True,
                             text=True)
    status = subprocess.run(["git", "status", "--porcelain", "--", path], cwd=REPO, capture_output=True, text=True)
    return tracked.returncode == 0 and status.returncode == 0 and not status.stdout.strip()


def prereg_committed() -> dict:
    if not committed(PREREG_REL):
        raise SystemExit("research/preregistration.json is missing or has uncommitted changes; refusing to run")
    return pipeline.prereg()


def sha(path) -> str:
    return textio.sha256(path)


def ea_sha() -> str:
    return sha(pipeline.EA_SRC)


def holdout_pending(log: list, ea_sha256: str, set_shas: dict) -> list:
    """Holdout runs still allowed: those without a completed run (status ok with a report) for the same
    EA source hash and .set hash. Failed, timed-out or infra_failure runs without a report do not count."""
    done = set()
    for e in log:
        who = str(e.get("role", ""))[len("holdout_"):] if str(e.get("role", "")).startswith("holdout_") else None
        if (who in set_shas and e.get("status") == "ok" and e.get("has_report")
                and e.get("ea_sha256") == ea_sha256 and e.get("set_sha256") == set_shas[who]):
            done.add(who)
    return [w for w in ("candidate", "baseline") if w not in done]


# ------------------------------------------------------------------ shared helpers
def _period_seconds(values: dict) -> int:
    return TF_SECONDS[int(values["SignalTF"])]


def _num(v):
    for f in (int, float):
        try:
            return f(v)
        except (TypeError, ValueError):
            pass
    return v


def run_values(run_id: str) -> dict:
    """Full EA inputs of an archived run: research-build code defaults + the values the runner sent."""
    man = json.loads((runner.RUNS / run_id / "manifest.json").read_text())
    vals = {s.name: _num(s.default) for s in pipeline.specs("research")}
    vals.update({k: _num(v) for k, v in man["meta"]["values"].items()})
    return vals


def conformance_report(run_id: str, dest: str) -> dict:
    """R26 conformance check (R17 setup rows vs bars) of one archived research run."""
    from mt5r import conformance
    d = runner.RUNS / run_id
    vals = run_values(run_id)
    period_s = _period_seconds(vals)
    params = {**vals, "point": RUN["symbol_spec"]["tick_size"]}
    setups = conformance.read_setups(d / f"rl_setups_{run_id}.csv")
    bars = conformance.read_bars(d / f"rl_bars_{run_id}.csv")
    viol = conformance.check(setups, bars, period_s, params)
    facts = journal.run_facts(d)
    out = {"run_id": run_id, "setups": len(setups), "bars": len(bars), "violations": len(viol),
           "violation_rows": viol[:500], "occurrences": conformance.occurrences(setups, bars, period_s, params),
           "journal": {k: facts[k] for k in ("warmup_bars", "stops_level_pts", "tick", "discarded_days",
                                             "discarded_minutes", "total_minute_bars", "funnel")}}
    dst = RESULTS / dest / run_id
    dst.mkdir(parents=True, exist_ok=True)
    evaluate.save(out, dst / "conformance.json")
    print(f"{run_id}: {len(setups)} setups, {len(viol)} conformance violations", flush=True)
    return out


def _md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    fmt = lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v)) else (
        f"{v:.2f}" if isinstance(v, float) else str(v))
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    rows += ["| " + " | ".join(fmt(v) for v in r) + " |" for r in df.itertuples(index=False, name=None)]
    return "\n".join(rows)


# ------------------------------------------------------------------ U5: install, smoke, conformance
def cmd_install(args) -> None:
    """Copy both EA sources into the isolated copy and compile them with its MetaEditor."""
    if runner.live_terminal_running():
        raise SystemExit("live MT5 terminal is running; install only while it is closed (R35)")
    cfg = env.load_config()
    env.assert_trade_safety(cfg)
    env.install_sources(cfg)
    out, bad = {"ea_sha256": ea_sha()}, []
    (RESULTS / "compile").mkdir(parents=True, exist_ok=True)
    for src in env.EA_SOURCES:
        r = compmod.compile_ea(cfg, src)
        (RESULTS / "compile" / f"{pathlib.Path(src).stem}.log").write_text(r["log"])
        out[src] = {k: r[k] for k in ("errors", "warnings", "ex5_exists")}
        if r["errors"] != 0 or not r["ex5_exists"]:
            bad.append(src)
    (RESULTS / "compile" / "compile.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    if bad:
        raise SystemExit(f"compile failed: {bad}")


def cmd_smoke(args) -> None:
    cfg = env.load_config()
    for period in args.period or ["M15", "M5"]:
        run_id = f"smoke_{period.lower()}"
        res, rep = pipeline.run_single(cfg, run_id, "research", period, args.start, args.end, role="smoke",
                                       purpose="U5 smoke", log_extra={"ea_sha256": ea_sha()})
        if rep is None:
            raise SystemExit(f"{run_id}: {res.status}, no report")
        curate.curate(run_id, "smoke")
        conformance_report(run_id, "smoke")


def cmd_conformance(args) -> None:
    for run_id in args.run_ids:
        conformance_report(run_id, args.dest)


def cmd_optsmoke(args) -> None:
    """8-pass optimization over the categorical inputs only (U5 step 6)."""
    cfg = env.load_config()
    run_id = f"optsmoke_{args.period.lower()}"
    ranges = {"ObMode": (0, 1, 1), "EntryMode": (0, 1, 3)}
    res, df = pipeline.run_optimization(cfg, run_id, "research", args.period, args.start, args.end, {}, ranges,
                                        role="optsmoke", purpose="U5 optimization smoke")
    if df is None:
        raise SystemExit(f"{run_id}: {res.status}, no optimization XML")
    curate.curate(run_id, "smoke")
    axes = {"ObMode": [0, 1], "EntryMode": [0, 1, 2, 3]}
    ints = all(pd.api.types.is_integer_dtype(df[c]) for c in axes)
    wfo.merge_grids([df], axes)             # raises on a missing or duplicate combination
    out = {"run_id": run_id, "passes": len(df), "integer_categorical_columns": ints, "merge_grids": "ok"}
    (RESULTS / "smoke" / "optsmoke.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    if len(df) != 8 or not ints:
        raise SystemExit("optimization smoke did not yield 8 clean passes")


# ------------------------------------------------------------------ U6: pilot and charts
def pilot_summary(per_tf: dict, window: list) -> dict:
    """R20: fills per month, funnel and reason counts only (no profit fields); timeframe by the fill rule."""
    days = evaluate.window_days(*window)
    runs = {}
    for period, x in per_tf.items():
        dl = x["deals"]
        fills = int((dl["type"].isin([0, 1]) & (dl["entry"] == 0)).sum())
        reasons = x["setups"]["reason"].astype(str).value_counts() if "reason" in x["setups"] else {}
        runs[period] = {"run_id": x["run_id"], "fills": fills, "days": days,
                        "fills_per_month": round(fills / days * DAYS_PER_MONTH, 4),
                        "funnel": {k: int(v) for k, v in x["funnel"].items()},
                        "reasons": {str(k): int(v) for k, v in dict(reasons).items()}}
    ok = [p for p in ("M15", "M5") if p in runs and runs[p]["fills_per_month"] >= FILL_TARGET]
    return {"window": list(window), "days_per_month": DAYS_PER_MONTH, "target_fills_per_month": FILL_TARGET,
            "rule": "longer timeframe averaging >= 15 fills per month; else M5 with the shortfall reported (R20)",
            "runs": runs, "chosen_period": ok[0] if ok else "M5", "shortfall": not ok}


def cmd_pilot(args) -> None:
    if pipeline.prereg() is not None:
        raise SystemExit("research/preregistration.json exists; the frequency pilot runs before R21")
    cfg = env.load_config()
    per_tf, conf = {}, {}
    for period in ("M5", "M15"):
        run_id = f"pilot_{period.lower()}"
        res, rep = pipeline.run_single(cfg, run_id, "research", period, *WFO_WINDOW, role="pilot",
                                       purpose="R20 frequency pilot", log_extra={"ea_sha256": ea_sha()})
        if rep is None:
            raise SystemExit(f"{run_id}: {res.status}, no report")
        curate.curate(run_id, "pilot")
        d = runner.RUNS / run_id
        per_tf[period] = {"run_id": run_id, "deals": reports.read_deals(d / f"rl_deals_{run_id}.csv"),
                          "setups": pd.read_csv(d / f"rl_setups_{run_id}.csv", keep_default_na=False),
                          "funnel": journal.run_facts(d)["funnel"]}
        conf[period] = conformance_report(run_id, "pilot")["violations"]
    out = pilot_summary(per_tf, WFO_WINDOW)
    for p, n in conf.items():
        out["runs"][p]["conformance_violations"] = n
    PILOT.parent.mkdir(parents=True, exist_ok=True)
    PILOT.write_text(json.dumps(out, indent=1))
    print(json.dumps({p: r["fills_per_month"] for p, r in out["runs"].items()}), "->", out["chosen_period"])
    print("Next: `charts`, then STOP for the user's chart review (R39).")


def cmd_charts(args) -> None:
    from mt5r import charts_setups, conformance
    run_id = args.run_id or f"pilot_{json.loads(PILOT.read_text())['chosen_period'].lower()}"
    d = runner.RUNS / run_id
    res = charts_setups.render(conformance.read_setups(d / f"rl_setups_{run_id}.csv"),
                               conformance.read_bars(d / f"rl_bars_{run_id}.csv"),
                               reports.read_deals(d / f"rl_deals_{run_id}.csv"), RESULTS / "pilot" / "charts",
                               _period_seconds(run_values(run_id)), seed=stats.SEED)
    print(json.dumps({k: [str(x) for x in v] if isinstance(v, list) else str(v) for k, v in res.items()}, indent=1))
    print("STOP: send the charts and table to the user; U7 starts only after approval (R39).")


# ------------------------------------------------------------------ U7: freeze rules
def cmd_freeze_rules(args) -> None:
    if pipeline.PREREG_PATH.exists():
        raise SystemExit("research/preregistration.json exists; the protocol is frozen once (R21)")
    if PILOT.exists():
        chosen = json.loads(PILOT.read_text())["chosen_period"]
        if chosen != args.period:
            raise SystemExit(f"the pilot rule chose {chosen}, not {args.period} (R20)")
    pilot_runs = sum(1 for e in explog.read() if e.get("role") == "pilot" and e.get("status") == "ok")
    p = pipeline.build_prereg(args.period, pilot_runs, ea_sha(), args.gate_change)
    pipeline.PREREG_PATH.write_text(json.dumps(p, indent=1) + "\n")
    print(f"wrote {_rel(pipeline.PREREG_PATH)}; commit it before `wfo` (R21)")


# ------------------------------------------------------------------ U8: WFO and freeze
def grid_for_window(cfg, P: dict, tag: str, start: str, end: str) -> pd.DataFrame:
    """One complete optimization over the pre-registered grid (all axes arithmetic)."""
    run_id = f"{tag}_grid"
    ranges = {k: tuple(v) for k, v in P["grid_ranges"].items()}
    _, df = pipeline.run_optimization(cfg, run_id, "research", P["period"], start, end, {}, ranges, role="train",
                                      purpose=f"{tag} grid")
    if df is None:
        raise SystemExit(f"optimization {run_id} failed")
    curate.curate(run_id, "wfo")
    names = list(P["grid"])
    grid = wfo.merge_grids([df[names + ["profit", "trades", "eq_dd_pct", "recovery_factor", "custom", "sharpe"]]],
                           P["grid"])
    rf = grid["recovery_factor"]
    grid["eq_dd_money"] = (grid["profit"] / rf).abs().where(rf != 0, 1.0)
    return grid


def select_on(cfg, P: dict, tag: str, start: str, end: str) -> dict:
    S = P["selection"]
    floor = wfo.trade_floor(S["train_months"], S["trade_floor_per_month"])
    grid = grid_for_window(cfg, P, tag, start, end)
    sel = wfo.select(grid, list(P["grid"]), P["defaults"], floor, S["max_equity_dd_pct"],
                     categorical=P["categorical"])
    out = RESULTS / "wfo" / f"{tag}_selection"
    out.mkdir(parents=True, exist_ok=True)
    sel["scored"].to_csv(out / "scored_grid.csv", index=False)
    info = {"tag": tag, "train": [start, end], "trade_floor": floor, "status": sel["status"],
            "params": sel["params"], "eligible_passes": int(sel["scored"]["eligible"].sum())}
    (out / "selection.json").write_text(json.dumps(info, indent=1))
    explog.append({"id": f"{tag}_selection", "purpose": "KTD12 selection", "role": "selection",
                   "status": sel["status"], "notes": json.dumps(info)})
    return info


def cmd_wfo(args) -> None:
    P = prereg_committed()
    cfg = env.load_config()
    done = json.loads(FOLDS.read_text()) if FOLDS.exists() else []
    deposits = {"procedure": P["deposit"], "baseline": P["deposit"]}
    for rec in done:
        deposits = rec["next_deposits"]
    for fold in P["folds"][len(done):]:
        k = fold["fold"]
        info = select_on(cfg, P, f"f{k}", *fold["train"])
        rec = {"fold": k, "selection": info, "test": fold["test"], "deposits": dict(deposits), "oos": {}}
        for who, params in (("procedure", info["params"]), ("baseline", {})):
            run_id = f"f{k}_oos_{who}"
            res, rep = pipeline.run_single(cfg, run_id, "research", P["period"], *fold["test"],
                                           deposit=deposits[who], overrides=params, role=f"oos_{who}",
                                           purpose=f"fold {k} OOS {who}", log_extra={"ea_sha256": ea_sha()})
            if rep is None:
                raise SystemExit(f"{run_id}: {res.status}, no report")
            s = reports.summary(rep)
            curate.curate(run_id, "wfo")
            rec["oos"][who] = {**s, "final_balance": round(deposits[who] + s["net_profit"], 2), "run_id": run_id,
                               "conformance_violations": conformance_report(run_id, "wfo")["violations"]}
        deposits = {w: rec["oos"][w]["final_balance"] for w in rec["oos"]}
        rec["next_deposits"] = deposits
        done.append(rec)
        FOLDS.parent.mkdir(parents=True, exist_ok=True)
        FOLDS.write_text(json.dumps(done, indent=1))
        print(f"fold {k}: {info['status']} {info['params']}", flush=True)
    if not FINAL.exists():
        info = select_on(cfg, P, "final", *P["final_train"])
        FINAL.parent.mkdir(parents=True, exist_ok=True)
        FINAL.write_text(json.dumps(info, indent=1))
        print(f"final candidate: {info['status']} {info['params']}", flush=True)


def tested_values(P: dict, params: dict) -> dict:
    vals = pipeline.base_values("delivered", P["signal_tf_value"])
    vals.update(params)
    return vals


def cmd_freeze(args) -> None:
    """Write original (code defaults), baseline (as tested) and candidate .set files before the holdout."""
    P = prereg_committed()
    if (DELIV / CAND_SET).exists() and committed(_rel(DELIV / CAND_SET)):
        raise SystemExit(f"{CAND_SET} is already frozen and committed (R23)")
    sel = json.loads(FINAL.read_text())
    DELIV.mkdir(exist_ok=True)
    spec = pipeline.specs("delivered")
    setfile.write_set(DELIV / ORIG_SET, setfile.render_lines(spec),
                      header="OB-FVG retest original parameters: code defaults of ob_fvg_retest.mq5 (R31)")
    setfile.write_set(DELIV / BASE_SET, setfile.render_lines(spec, tested_values(P, {})),
                      header=f"Baseline as tested: code defaults, SignalTF={P['period']}, run_constants risk inputs")
    setfile.write_set(DELIV / CAND_SET, setfile.render_lines(spec, tested_values(P, sel["params"])),
                      header=f"Frozen candidate ({sel['status']}, train {sel['train'][0]}-{sel['train'][1]}): "
                             f"{sel['params']}\nNOT VALIDATED: no independent period exists (R30)")
    rec = {"ea_sha256": ea_sha(), "prereg_sha256": sha(pipeline.PREREG_PATH), "selection": sel,
           "sets": {n: sha(DELIV / n) for n in (ORIG_SET, BASE_SET, CAND_SET)}}
    (FINAL.parent / "freeze.json").write_text(json.dumps(rec, indent=1))
    print(f"frozen candidate {sel['params']}; commit deliverables/*.set and results/final_selection before `holdout`")


# ------------------------------------------------------------------ U9: holdout and robustness
def cmd_holdout(args) -> None:
    P = prereg_committed()
    for name in (CAND_SET, BASE_SET):
        if not (DELIV / name).exists() or not committed(_rel(DELIV / name)):
            raise SystemExit(f"{name} is not frozen and committed; run `freeze` and commit the candidate first (R23)")
    ea = ea_sha()
    shas = {"candidate": sha(DELIV / CAND_SET), "baseline": sha(DELIV / BASE_SET)}
    todo = holdout_pending(explog.read(), ea, shas)
    if not todo:
        raise SystemExit("a completed holdout run already exists for this EA source and these .set files; "
                         "the holdout runs once (R24)")
    cfg = env.load_config()
    out_path = RESULTS / "holdout" / "holdout.json"
    out = json.loads(out_path.read_text()) if out_path.exists() else {}
    start, end = P["holdout"]
    for who in todo:
        vals = setfile.read_set(DELIV / (CAND_SET if who == "candidate" else BASE_SET))
        run_id = f"holdout_{who}"
        res, rep = pipeline.run_single(cfg, run_id, "research", P["period"], start, end, overrides=vals,
                                       role=f"holdout_{who}", purpose="R24 holdout (non-independent)",
                                       log_extra={"ea_sha256": ea, "set_sha256": shas[who]})
        if rep is None:
            print(f"{run_id}: {res.status}, no report; may be rerun", flush=True)
            continue
        curate.curate(run_id, "holdout")
        x = evaluate.load_run(run_id)
        out[who] = {"run_id": run_id, "window": [start, end], "independent": False,
                    "input_mismatches": pipeline.check_inputs_loaded(rep, vals),
                    "conformance_violations": conformance_report(run_id, "holdout")["violations"],
                    "net_profit": x["summary"]["net_profit"], "fills": len(x["trades"]),
                    "fills_per_month": round(len(x["trades"]) / evaluate.window_days(start, end) * DAYS_PER_MONTH, 4),
                    "first_breach": limits.first_breach(x["days"], initial=x["deposit"])}
    out["exposure_record"] = RUN["exposure_record"]
    evaluate.save(out, out_path)
    print(json.dumps({w: {k: out[w][k] for k in ("fills_per_month", "net_profit")} for w in out if w in shas},
                     indent=1, default=str))


def cmd_robustness(args) -> None:
    P = prereg_committed()
    if not committed(_rel(DELIV / CAND_SET)):
        raise SystemExit("candidate is not frozen and committed; run `freeze` and commit first")
    cfg = env.load_config()
    folds = json.loads(FOLDS.read_text())
    cand = json.loads(FINAL.read_text())["params"]
    bm = metrics.bar_minutes(P["period"])
    load = lambda who: evaluate.stitch([evaluate.load_run(f["oos"][who]["run_id"], RESULTS / "wfo") for f in folds])
    proc, base = load("procedure"), load("baseline")
    fold_rows = [{"fold": f["fold"], "test": f["test"], "params": f["selection"]["params"],
                  "status": f["selection"]["status"], "procedure_net": f["oos"]["procedure"]["net_profit"],
                  "baseline_net": f["oos"]["baseline"]["net_profit"],
                  "procedure_trades": f["oos"]["procedure"]["trades"],
                  "baseline_trades": f["oos"]["baseline"]["trades"]} for f in folds]

    # R25 parameter stability: static runs of the candidate and its ordinal neighbours over the OOS span
    start, end = P["folds"][0]["test"][0], P["folds"][-1]["test"][1]
    rows = []
    for i, params in enumerate([cand] + wfo.neighbors(cand, P["grid"], categorical=P["categorical"])):
        run_id = "static_candidate" if i == 0 else f"static_nb{i}"
        res, rep = pipeline.run_single(cfg, run_id, "research", P["period"], start, end, overrides=params,
                                       role="static_candidate" if i == 0 else "neighbor_static",
                                       purpose="R25 parameter stability", log_extra={"ea_sha256": ea_sha()})
        if rep is None:
            raise SystemExit(f"{run_id}: {res.status}, no report")
        curate.curate(run_id, "robustness")
        x = evaluate.load_run(run_id)
        rows.append({"run_id": run_id, "params": params, "net": x["summary"]["net_profit"], "fills": len(x["trades"]),
                     "daily_sharpe": stats.sharpe(evaluate.daily_pnl(x["days"]).to_numpy() / x["deposit"])})
    nb = rows[1:]
    share = float(np.mean([r["net"] > 0 for r in nb])) if nb else float("nan")
    sharpes = [r["daily_sharpe"] for r in rows if np.isfinite(r["daily_sharpe"])]
    var_sr = float(np.var(sharpes, ddof=1)) if len(sharpes) >= 2 else float("nan")

    hpath = RESULTS / "holdout" / "holdout_candidate"
    holdout = evaluate.stitch([evaluate.load_run("holdout_candidate", RESULTS / "holdout")]) if hpath.exists() else None
    acc = evaluate.evaluate(proc, base, fold_rows, {"profitable_share": share}, var_sr, holdout, P)

    S, A = P["stats"], P["acceptance"]
    ev = metrics.trade_events(proc["trades"], bm) if len(proc["trades"]) else pd.DataFrame({"ret": []})
    shuffle = (montecarlo.shuffle_paths(ev["ret"].to_numpy(), n_paths=S["mc_paths"], seed=S["seed"],
                                        initial=proc["initial"]) if len(ev) else {})
    summ = lambda s: metrics.summary(s["trades"], s["days"], s["initial"], bar_minutes=bm)
    evaluate.save({"acceptance": acc, "passed_all": acc["passed_all"], "recommended": False,
                   "fold_rows": fold_rows, "neighbors": {"profitable_share": share, "rows": rows},
                   "var_sr": var_sr, "var_sr_source": "sample variance of daily Sharpe over the static candidate "
                                                      "and neighbour runs on the OOS span",
                   "cost_stress": {w: evaluate.cost_stress(s, A["spread_stress_k"], A["stop_slippage_points"])
                                   for w, s in (("procedure", proc), ("baseline", base))},
                   "shuffle_mc": {k: v for k, v in shuffle.items() if np.ndim(v) == 0},
                   "procedure_stitched": summ(proc), "baseline_stitched": summ(base)},
                  RESULTS / "acceptance.json")
    print(json.dumps({"passed_all": acc["passed_all"], "recommended": False}, indent=1))


# ------------------------------------------------------------------ U10: deliverables
def cmd_deliver(args) -> None:
    """Validate the .set files on the delivered build, then tables, per-trade reports and charts."""
    P = prereg_committed()
    acc = json.loads((RESULTS / "acceptance.json").read_text())
    final = json.loads(FINAL.read_text())
    folds = json.loads(FOLDS.read_text())
    cfg = env.load_config()
    ex5 = pipeline.BUILDS["delivered"][0]
    start, end = P["folds"][0]["test"]
    checks = {}
    for name in (ORIG_SET, CAND_SET):
        run_id = "validate_" + name.replace(".set", "")
        text = pipeline._ini(ex5, P["period"], start, end, P["deposit"], run_id, deliver.set_file_lines(DELIV / name))
        res = runner.run(cfg, run_id, text, ex5, meta={"role": "set_validation", "set": name})
        rep = reports.parse_html(res.report) if res.report else None
        mism = pipeline.check_inputs_loaded(rep, setfile.read_set(DELIV / name)) if rep else ["no report"]
        checks[name] = {"mismatches": mism, "period": [start, end], "status": res.status}
        explog.append({"id": run_id, "purpose": "R31 .set validation", "role": "set_validation", "expert": ex5,
                       "status": "ok" if rep and not mism else "mismatch", "has_report": rep is not None})
        curate.curate(run_id, "set_validation")
    evaluate.save(checks, RESULTS / "set_validation.json")

    load = lambda who: evaluate.stitch([evaluate.load_run(f["oos"][who]["run_id"], RESULTS / "wfo") for f in folds])
    proc, base = load("procedure"), load("baseline")
    hold = {w: evaluate.stitch([evaluate.load_run(f"holdout_{w}", RESULTS / "holdout")])
            for w in ("candidate", "baseline") if (RESULTS / "holdout" / f"holdout_{w}").exists()}
    bm = metrics.bar_minutes(P["period"])
    rows = []
    for label, s in (("OOS candidate (WFO procedure)", proc), ("OOS baseline", base),
                     *((f"Holdout {w} (non-independent)", s) for w, s in hold.items())):
        m = metrics.summary(s["trades"], s["days"], s["initial"], bar_minutes=bm)
        rows.append({"series": label, "net_after_costs": s["net_profit"], "fills": m["n_trades"],
                     "win_rate": m["win_rate"], "profit_factor": m["profit_factor"],
                     "max_equity_dd_pct": m.get("max_equity_dd_pct"), "recovery_factor": m.get("recovery_factor")})
    DELIV.mkdir(exist_ok=True)
    (DELIV / "parameter_table.md").write_text("# Parameter table\n\n" + deliver.parameter_table(P, final["params"]) + "\n")
    (DELIV / "comparison.md").write_text("# Baseline vs candidate (net after costs)\n\n" + _md(pd.DataFrame(rows)) + "\n")
    cols = [c for c in trades.COLUMNS if c not in ("position_id",)]
    (DELIV / "trades_oos.md").write_text("# OOS trades, candidate (R40)\n\n" + _md(proc["table"][cols]) + "\n")
    if "candidate" in hold:
        (DELIV / "trades_holdout.md").write_text("# Holdout trades, candidate (R40, non-independent)\n\n"
                                                 + _md(hold["candidate"]["table"][cols]) + "\n")
    label = f"OOS {P['folds'][0]['test'][0]}-{P['folds'][-1]['test'][1]}"
    fold_bars = [{"fold": f["fold"], "label": f["test"][0][:7], "candidate": f["oos"]["procedure"]["net_profit"],
                  "baseline": f["oos"]["baseline"]["net_profit"], "candidate_trades": f["oos"]["procedure"]["trades"]}
                 for f in folds]
    mc = acc["acceptance"]["criteria"]["loss_limits"]["value"]["monte_carlo"]
    made = deliver.charts({"WFO procedure": proc, "Baseline": base}, fold_bars, mc, DELIV / "charts", label, "oos")
    if hold:
        made += deliver.charts({f"{w.title()}": s for w, s in hold.items()}, [], {}, DELIV / "charts",
                               f"holdout {P['holdout'][0]}-{P['holdout'][1]} (non-independent)", "holdout")
    shutil.copy2(pipeline.EA_SRC, DELIV / "ob_fvg_retest.mq5")
    if deliver.should_write_recommended(acc) or (DELIV / "ob_fvg_retest_recommended.set").exists():
        raise SystemExit("a recommended .set must not exist in this research (R30)")
    print(json.dumps({"set_validation": {k: len(v["mismatches"]) for k, v in checks.items()}, "charts": made},
                     indent=1))


# ------------------------------------------------------------------ entry point
COMMANDS = {"install": cmd_install, "smoke": cmd_smoke, "conformance": cmd_conformance, "optsmoke": cmd_optsmoke,
            "pilot": cmd_pilot, "charts": cmd_charts, "freeze-rules": cmd_freeze_rules, "wfo": cmd_wfo,
            "freeze": cmd_freeze, "holdout": cmd_holdout, "robustness": cmd_robustness, "deliver": cmd_deliver}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="copy EA sources into the isolated copy and compile both builds")
    s = sub.add_parser("smoke", help="one-month research-build runs + conformance")
    s.add_argument("--period", choices=["M5", "M15"], action="append")
    s.add_argument("--start", default="2026.03.01")
    s.add_argument("--end", default="2026.03.31")
    s = sub.add_parser("conformance", help="conformance check of archived research runs")
    s.add_argument("run_ids", nargs="+")
    s.add_argument("--dest", default="smoke")
    s = sub.add_parser("optsmoke", help="8-pass optimization smoke over ObMode x EntryMode")
    s.add_argument("--period", choices=["M5", "M15"], default="M15")
    s.add_argument("--start", default="2026.03.02")
    s.add_argument("--end", default="2026.03.06")
    sub.add_parser("pilot", help="R20 frequency pilot on M5 and M15 (no profit fields)")
    s = sub.add_parser("charts", help="R39 rule-conformance charts from a pilot run")
    s.add_argument("--run-id")
    s = sub.add_parser("freeze-rules", help="write research/preregistration.json (R21, KTD12)")
    s.add_argument("--period", choices=["M5", "M15"], required=True)
    s.add_argument("--gate-change", action="append", default=[], help="rule change at the gate, with its reason")
    for name, text in (("wfo", "walk-forward folds and final selection"), ("freeze", "write the .set files"),
                       ("holdout", "run the frozen holdout once"), ("robustness", "R25 evidence and R29 evaluation"),
                       ("deliver", "validate .set files, tables and charts")):
        sub.add_parser(name, help=text)
    args = ap.parse_args()
    COMMANDS[args.cmd](args)


if __name__ == "__main__":
    main()
