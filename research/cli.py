"""M5 OB + M1 structure research pipeline: python research/cli.py <step>  (plan KTD13)

Strategy: M5 OB + M1 structure EA (plan docs/plans/2026-10-03-0013-feat-m5-ob-m1-structure-ea-plan.md).
Order: setup (once per machine, then one manual GUI login by the user) -> install -> smoke -> conformance -> optsmoke -> pilot -> charts -> STOP (R27 chart gate, user approval)
-> freeze-rules (commit) -> wfo -> freeze (commit) -> holdout (August-September once) -> augsep-sensitivity
-> robustness -> deliver.

Guards: every step that runs the tester refuses while the live terminal runs (runner.run). wfo, freeze, holdout,
robustness and deliver refuse on a missing or uncommitted pre-registration (git errors count as uncommitted).
holdout also refuses unless the frozen .set files are committed, and runs once per variant: any logged run
with a report counts, whatever its EA or .set hash; only a run without a report may be repeated.
"""
import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from mt5r import compile as compmod  # noqa: E402
from mt5r import (curate, deliver, env, evaluate, explog, journal, limits, m1_contract, metrics,  # noqa: E402
                  montecarlo, pipeline, reports, runner, setfile, stats, textio, trades, wfo)

REPO = pathlib.Path(__file__).resolve().parents[1]
RUN = pipeline.RUN
RESULTS = REPO / "results"
DELIV = REPO / "deliverables"
FINAL = RESULTS / "final_selection" / "selection.json"
FOLDS = RESULTS / "wfo" / "folds.json"
PILOT = RESULTS / "pilot" / "pilot_summary.json"
PREREG_REL = "research/preregistration.json"
ORIG_SET, A_SET, B_SET, CAND_SET = ("ob_m1_structure_original.set", "ob_m1_structure_variant_a.set",
                                    "ob_m1_structure_variant_b.set", "ob_m1_structure_candidate.set")
WFO_WINDOW = list(RUN["windows"]["wfo"])
DAYS_PER_MONTH, FILL_TARGET = 30.44, 15          # R28 pilot target; the frozen values live in the prereg
CHART_PERIOD = m1_contract.CHART_PERIOD                          # the EA runs on the M1 chart (KTD1)
VARIANTS = {"A": 0, "B": 1}                                     # StructureVariant (R11)


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
    """The committed pre-registration, refused when the EA source differs from the one it registered (R29)."""
    if not committed(PREREG_REL):
        raise SystemExit("research/preregistration.json is missing or has uncommitted changes; refusing to run")
    P = pipeline.prereg()
    if P.get("ea_source_sha256") != ea_sha():
        raise SystemExit("the EA source differs from the pre-registered ea_source_sha256; refusing to run (R29)")
    return P


def sha(path) -> str:
    return textio.sha256(path)


def sha_source(path) -> str:
    """sha256 of a source file with CRLF read as LF, so a git checkout's line endings do not change it."""
    return hashlib.sha256(pathlib.Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def ea_sha() -> str:
    return sha_source(pipeline.EA_SRC)


def installed_ea_matches(cfg) -> None:
    """The tester compiles and runs the EA sources in the isolated copy (copied by `install`), not the repo files the
    pre-registration hash guards: refuse when an installed source differs from the repo source (code review)."""
    for name in env.EA_SOURCES:
        installed = cfg.mt5_dir / "MQL5" / "Experts" / name
        if not installed.exists() or sha_source(installed) != sha_source(pipeline.EA_SRC.parent / name):
            raise SystemExit(f"{installed} differs from mql5/Experts/{name}; run `install` from the registered "
                             "source before any protocol run (R29)")


def holdout_pending(log: list, ea_sha256: str, set_shas: dict) -> list:
    """August-September sides still allowed to run (R31: exactly once). Any logged run that produced a report
    counts as done, whatever its status and whatever EA or .set hash it ran with; only runs without a report
    (infrastructure failures) may be repeated. ea_sha256 is kept for the caller's log."""
    done = set()
    for e in log:
        who = str(e.get("role", ""))[len("holdout_"):] if str(e.get("role", "")).startswith("holdout_") else None
        if who in set_shas and e.get("has_report"):
            done.add(who)
    return [w for w in set_shas if w not in done]


def tester_deposit(deposit: float) -> int:
    """The deposit MT5 actually starts with: it truncates a fractional Deposit= to whole dollars."""
    return int(deposit)


def refuse_holdout_window(start: str, end: str) -> None:
    """After pre-registration no ad-hoc tester run may touch the August-September window (R31)."""
    P = pipeline.prereg()
    if P and start <= P["holdout"][1] and end >= P["holdout"][0]:
        raise SystemExit(f"{start}-{end} overlaps the frozen holdout {P['holdout'][0]}-{P['holdout'][1]} (R31)")


# ------------------------------------------------------------------ shared helpers
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
    """R25 conformance check of one archived research run: the independent M5/M1 replay (KTD11)."""
    from mt5r import conformance_m1 as cm
    d = runner.RUNS / run_id
    values = run_values(run_id)
    params = {**values, "point": RUN["symbol_spec"]["tick_size"],
              "contract_size": RUN["symbol_spec"]["contract_size"]}
    run = cm.read_run(d, run_id)
    res = cm.full(run["setups"], run["events"], run["pivots"], run["bars_m1"], run["bars_m5"], params,
                  run.get("deals"), run.get("trail"), run.get("sl_moves"),
                  trailing=cm.parse_trailing(values.get("EnableTrailingStop")))
    viol = res["violations"]
    facts = journal.run_facts(d)
    out = {"run_id": run_id, "setups": len(run["setups"]), "bars_m1": len(run["bars_m1"]),
           "bars_m5": len(run["bars_m5"]), "violations": len(viol), "violation_rows": viol[:500],
           "coverage": res["coverage"], "occurrences": res["occurrences"],
           "journal": {k: facts.get(k) for k in ("warmup_bars", "stops_level_pts", "tick", "discarded_days",
                                                 "discarded_minutes", "total_minute_bars", "funnel")}}
    dst = RESULTS / dest / run_id
    dst.mkdir(parents=True, exist_ok=True)
    evaluate.save(out, dst / "conformance.json")
    print(f"{run_id}: {len(run['setups'])} setups, {len(viol)} conformance violations", flush=True)
    return out


def _md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    fmt = lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v)) else (
        f"{v:.2f}" if isinstance(v, float) else str(v))
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    rows += ["| " + " | ".join(fmt(v) for v in r) + " |" for r in df.itertuples(index=False, name=None)]
    return "\n".join(rows)


# ------------------------------------------------------------------ U5: install, smoke, conformance
def cmd_setup(args) -> None:
    """Build or refresh the isolated copy from the allowlisted live files (env.build)."""
    if runner.live_terminal_running():
        raise SystemExit("live MT5 terminal is running; setup only while it is closed (R35)")
    cfg = env.load_config()
    man = env.build(cfg)
    print(json.dumps({"mt5_dir": str(cfg.mt5_dir), "n_files": man["n_files"]}, indent=1))


def cmd_install(args) -> None:
    """Copy both EA sources into the isolated copy and compile them with its MetaEditor."""
    if runner.live_terminal_running():
        raise SystemExit("live MT5 terminal is running; install only while it is closed (R35)")
    cfg = env.load_config()
    env.disable_mcp(cfg)   # build 6231 re-adds an empty [MCP.Custom] on every exit; the check below still decides
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
    """One-month research-build runs of both structure variants on the M1 chart, each checked (U7)."""
    refuse_holdout_window(args.start, args.end)
    cfg = env.load_config()
    for name in args.variant or list(VARIANTS):
        run_id = f"smoke_{name.lower()}"
        res, rep = pipeline.run_single(cfg, run_id, "research", CHART_PERIOD, args.start, args.end,
                                       overrides={"StructureVariant": VARIANTS[name]}, role="smoke",
                                       purpose=f"U7 smoke, variant {name}", log_extra={"ea_sha256": ea_sha()})
        if rep is None:
            raise SystemExit(f"{run_id}: {res.status}, no report")
        curate.curate(run_id, "smoke")
        conformance_report(run_id, "smoke")


def cmd_conformance(args) -> None:
    for run_id in args.run_ids:
        conformance_report(run_id, args.dest)


def cmd_optsmoke(args) -> None:
    """2-pass optimization over StructureVariant, the only optimized input (KTD14): proves the optimizer path."""
    refuse_holdout_window(args.start, args.end)
    cfg = env.load_config()
    run_id = "optsmoke"
    ranges = {"StructureVariant": (0, 1, 1)}
    res, df = pipeline.run_optimization(cfg, run_id, "research", CHART_PERIOD, args.start, args.end, {}, ranges,
                                        role="optsmoke", purpose="U7 optimization smoke")
    if df is None:
        raise SystemExit(f"{run_id}: {res.status}, no optimization XML")
    curate.curate(run_id, "smoke")
    axes = {"StructureVariant": [0, 1]}
    ints = pd.api.types.is_integer_dtype(df["StructureVariant"])
    wfo.merge_grids([df], axes)             # raises on a missing or duplicate combination
    out = {"run_id": run_id, "passes": len(df), "integer_categorical_columns": ints, "merge_grids": "ok"}
    (RESULTS / "smoke" / "optsmoke.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    if len(df) != 2 or not ints:
        raise SystemExit("optimization smoke did not yield 2 clean passes")


# ------------------------------------------------------------------ U6: pilot and charts
def pilot_summary(per_variant: dict, window: list) -> dict:
    """R28: fills per month, funnel and reason counts per structure variant (no profit fields). M5/M1 are fixed;
    a shortfall against 15 fills per month is reported per variant and changes no rule."""
    days = evaluate.window_days(*window)
    runs = {}
    for name, x in per_variant.items():
        dl = x["deals"]
        fills = int((dl["type"].isin([0, 1]) & (dl["entry"] == 0)).sum())
        reasons = x["setups"]["reason"].astype(str).value_counts() if "reason" in x["setups"] else {}
        runs[name] = {"run_id": x["run_id"], "fills": fills, "days": days,
                      "fills_per_month": evaluate.fills_per_month(fills, *window),
                      "funnel": {k: int(v) for k, v in x["funnel"].items()},
                      "reasons": {str(k): int(v) for k, v in dict(reasons).items()}}
    return {"window": list(window), "days_per_month": DAYS_PER_MONTH, "target_fills_per_month": FILL_TARGET,
            "rule": "M5 zone / M1 confirmation fixed; frequency reported per variant, no rule change (R28)",
            "runs": runs, "shortfall": {n: r["fills_per_month"] < FILL_TARGET for n, r in runs.items()}}


def cmd_pilot(args) -> None:
    if pipeline.prereg() is not None:
        raise SystemExit("research/preregistration.json exists; the frequency pilot runs before R29")
    cfg = env.load_config()
    per_variant, conf = {}, {}
    for name, value in VARIANTS.items():
        run_id = f"pilot_{name.lower()}"
        res, rep = pipeline.run_single(cfg, run_id, "research", CHART_PERIOD, *WFO_WINDOW,
                                       overrides={"StructureVariant": value}, role="pilot",
                                       purpose=f"R28 frequency pilot, variant {name}",
                                       log_extra={"ea_sha256": ea_sha()})
        if rep is None:
            raise SystemExit(f"{run_id}: {res.status}, no report")
        curate.curate(run_id, "pilot")
        d = runner.RUNS / run_id
        per_variant[name] = {"run_id": run_id, "deals": reports.read_deals(d / f"rl_deals_{run_id}.csv"),
                             "setups": pd.read_csv(d / f"rl_setups_{run_id}.csv", keep_default_na=False),
                             "funnel": journal.run_facts(d)["funnel"]}
        conf[name] = conformance_report(run_id, "pilot")["violations"]
    out = pilot_summary(per_variant, WFO_WINDOW)
    for n, v in conf.items():
        out["runs"][n]["conformance_violations"] = v
    PILOT.parent.mkdir(parents=True, exist_ok=True)
    PILOT.write_text(json.dumps(out, indent=1))
    print(json.dumps({n: r["fills_per_month"] for n, r in out["runs"].items()}))
    print("Next: `charts`, then STOP for the user's chart review (R27).")


TICKCOV = RESULTS / "pilot" / "tick_coverage.json"
TICK_MONTHS = ["2025.12", "2026.01", "2026.02", "2026.03", "2026.04", "2026.05", "2026.06", "2026.07"]


def tick_coverage(per_month: dict) -> dict:
    """Generated-tick share per month and per R30 fold (3-month train + 1-month OOS, OOS March-July), summed from
    the tester's per-month 'real ticks discarded for X minutes of Y total minute bars' lines."""
    def pct(rows):
        gen = sum(r["discarded_minutes"] for r in rows)
        tot = sum(r["total_minute_bars"] for r in rows)
        return {"generated_minutes": gen, "minute_bars": tot, "generated_pct": round(100 * gen / tot, 2) if tot else None}
    months = {m: {**per_month[m], **pct([per_month[m]])} for m in TICK_MONTHS}
    folds = []
    for k in range(3, len(TICK_MONTHS)):
        train, oos = TICK_MONTHS[k - 3:k], TICK_MONTHS[k]
        folds.append({"fold": k - 2, "train": [train[0], train[-1]], "oos": oos,
                      "train_cov": pct([per_month[m] for m in train]), "oos_cov": pct([per_month[oos]])})
    return {"months": months, "folds": folds, "total": pct(list(per_month.values()))}


def cmd_tickcov(args) -> None:
    """Real-tick coverage per month (one research-build run per calendar month of the WFO window). Only the
    tester's tick-quality journal lines are used; the runs' trades are not read."""
    import calendar
    cfg = env.load_config()
    per_month = {}
    for m in TICK_MONTHS:
        y, mo = map(int, m.split("."))
        start, end = f"{m}.01", f"{m}.{calendar.monthrange(y, mo)[1]:02d}"
        run_id = f"tickcov_{y}{mo:02d}"
        pipeline.run_single(cfg, run_id, "research", CHART_PERIOD, start, end,
                            overrides={"StructureVariant": 0}, role="tick_coverage",
                                       purpose="real-tick coverage per month (journal tick lines only)")
        f = journal.run_facts(runner.RUNS / run_id)
        per_month[m] = {k: f[k] for k in ("discarded_days", "discarded_minutes", "total_minute_bars",
                                          "ticks", "real_ticks_begin")}
        print(m, per_month[m], flush=True)
    out = tick_coverage(per_month)
    pilot = journal.run_facts(runner.RUNS / "pilot_a")
    out["pilot_whole_window"] = {k: pilot[k] for k in ("discarded_days", "discarded_minutes", "total_minute_bars")}
    TICKCOV.write_text(json.dumps(out, indent=1))
    print(json.dumps(out["folds"], indent=1))


def cmd_charts(args) -> None:
    """R26 gate charts from the pilot runs of both variants (or one --run-id)."""
    from mt5r import charts_m1
    run_ids = [args.run_id] if args.run_id else [f"pilot_{n.lower()}" for n in VARIANTS]
    for run_id in run_ids:
        res = charts_m1.render(charts_m1.load_run(runner.RUNS / run_id, run_id),
                               RESULTS / "pilot" / f"charts_{run_id}", seed=stats.SEED)
        print(json.dumps({k: [str(x) for x in v] if isinstance(v, list) else str(v)
                          for k, v in res.items() if k != "examples"}, indent=1))
    print("STOP: send the charts and table to the user; U8 starts only after approval (R27).")


# ------------------------------------------------------------------ U7: freeze rules
PILOT_RUN_IDS = [f"pilot_{n.lower()}" for n in VARIANTS]


def cmd_freeze_rules(args) -> None:
    """R29: write the pre-registration once; it must be committed before `wfo`."""
    if pipeline.PREREG_PATH.exists():
        raise SystemExit("research/preregistration.json exists; the protocol is frozen once (R29)")
    done = {e.get("id") for e in explog.read() if e.get("role") == "pilot" and e.get("status") == "ok"}
    missing = [r for r in PILOT_RUN_IDS if r not in done]
    if missing:
        raise SystemExit(f"pilot runs {missing} are not logged as ok; the pilot precedes the freeze (R28)")
    p = pipeline.build_prereg(PILOT_RUN_IDS, ea_sha(), pipeline.GATE_RECORD)
    pipeline.PREREG_PATH.write_text(json.dumps(p, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {_rel(pipeline.PREREG_PATH)}; commit it before `wfo` (R29)")


# ------------------------------------------------------------------ U9: WFO, freeze, August-September
SERIES = ("procedure", "fixed_a", "fixed_b")
FIXED = {"fixed_a": {"StructureVariant": 0}, "fixed_b": {"StructureVariant": 1}}


def grid_for_window(cfg, P: dict, tag: str, start: str, end: str) -> pd.DataFrame:
    """One complete optimization over the pre-registered grid (StructureVariant 0..1)."""
    run_id = f"{tag}_grid"
    ranges = {k: tuple(v) for k, v in P["grid_ranges"].items()}
    _, df = pipeline.run_optimization(cfg, run_id, "research", P["chart_period"], start, end, {}, ranges,
                                      role="train", purpose=f"{tag} grid")
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
    explog.append({"id": f"{tag}_selection", "purpose": "KTD14 selection", "role": "selection",
                   "status": sel["status"], "notes": json.dumps(info)})
    return info


def oos_record(cfg, run_id: str, start: str, end: str, deposit: float, params: dict, role: str, purpose: str,
               dest: str) -> dict:
    """One chained single run with its summary, two drawdowns, input check and conformance."""
    res, rep = pipeline.run_single(cfg, run_id, "research", CHART_PERIOD, start, end,
                                   deposit=tester_deposit(deposit), overrides=params, role=role, purpose=purpose,
                                   log_extra={"ea_sha256": ea_sha()})
    if rep is None:
        raise SystemExit(f"{run_id}: {res.status}, no report")
    s = reports.summary(rep)
    curate.curate(run_id, dest)
    return {**s, "run_id": run_id, "params": params, "deposit": tester_deposit(deposit),
            "final_balance": round(tester_deposit(deposit) + s["net_profit"], 2),
            "input_mismatches": pipeline.check_inputs_loaded(rep, params),
            "conformance_violations": conformance_report(run_id, dest)["violations"]}


def cmd_wfo(args) -> None:
    """R30: per fold, select the variant on train, then run the procedure, fixed A and fixed B on the OOS month
    with chained deposits; then the final selection on May-July."""
    P = prereg_committed()
    cfg = env.load_config()
    installed_ea_matches(cfg)
    done = json.loads(FOLDS.read_text()) if FOLDS.exists() else []
    deposits = {w: P["deposit"] for w in SERIES}
    for rec in done:
        deposits = rec["next_deposits"]
    for fold in P["folds"][len(done):]:
        k = fold["fold"]
        info = select_on(cfg, P, f"f{k}", *fold["train"])
        rec = {"fold": k, "selection": info, "test": fold["test"], "deposits": dict(deposits), "oos": {}}
        for who in SERIES:
            params = info["params"] if who == "procedure" else FIXED[who]
            rec["oos"][who] = oos_record(cfg, f"f{k}_oos_{who}", *fold["test"], deposits[who], params,
                                         f"oos_{who}", f"fold {k} OOS {who}", "wfo")
        deposits = {w: rec["oos"][w]["final_balance"] for w in SERIES}
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


def tested_values(params: dict) -> dict:
    vals = pipeline.base_values("delivered")
    vals.update(params)
    return vals


def cmd_freeze(args) -> None:
    """Write the original (code defaults), variant A, variant B and candidate .set files before August-September."""
    prereg_committed()                 # the guards (committed prereg, registered EA source)
    if (DELIV / CAND_SET).exists() and committed(_rel(DELIV / CAND_SET)):
        raise SystemExit(f"{CAND_SET} is already frozen and committed (R31)")
    sel = json.loads(FINAL.read_text())
    DELIV.mkdir(exist_ok=True)
    spec = pipeline.specs("delivered")
    name = {0: "A", 1: "B"}[sel["params"]["StructureVariant"]]
    setfile.write_set(DELIV / ORIG_SET, setfile.render_lines(spec),
                      header="M5 OB + M1 structure, original parameters: code defaults of ob_m1_structure.mq5")
    setfile.write_set(DELIV / A_SET, setfile.render_lines(spec, tested_values(FIXED["fixed_a"])),
                      header="Variant A (HH/LL only) as tested: code defaults + run_constants risk inputs")
    setfile.write_set(DELIV / B_SET, setfile.render_lines(spec, tested_values(FIXED["fixed_b"])),
                      header="Variant B (HH+HL / LL+LH) as tested: code defaults + run_constants risk inputs")
    setfile.write_set(DELIV / CAND_SET, setfile.render_lines(spec, tested_values(sel["params"])),
                      header=f"Frozen candidate = variant {name} ({sel['status']}, final train "
                             f"{sel['train'][0]}-{sel['train'][1]})\nNOT VALIDATED: no independent period exists (R33)")
    rec = {"ea_sha256": ea_sha(), "prereg_sha256": sha(pipeline.PREREG_PATH), "selection": sel,
           "candidate_variant": name, "sets": {n: sha(DELIV / n) for n in (ORIG_SET, A_SET, B_SET, CAND_SET)}}
    (FINAL.parent / "freeze.json").write_text(json.dumps(rec, indent=1))
    print(f"frozen candidate = variant {name}; commit deliverables/*.set and results/final_selection before "
          "`holdout`")


HOLDOUT_SETS = {"variant_a": A_SET, "variant_b": B_SET}
AUGSEP = RESULTS / "aug_sep_check" / "aug_sep.json"


def cmd_holdout(args) -> None:
    """R31: August-September once, after the candidate freeze, for variant A and variant B (the candidate is one
    of them). Labelled as a non-independent historical check; never used for any choice."""
    P = prereg_committed()
    for name in (CAND_SET, *HOLDOUT_SETS.values()):
        if not (DELIV / name).exists() or not committed(_rel(DELIV / name)):
            raise SystemExit(f"{name} is not frozen and committed; run `freeze` and commit the candidate first (R31)")
    ea = ea_sha()
    shas = {w: sha(DELIV / n) for w, n in HOLDOUT_SETS.items()}
    todo = holdout_pending(explog.read(), ea, shas)
    if not todo:
        raise SystemExit("a completed August-September run already exists; it runs once (R31)")
    cfg = env.load_config()
    installed_ea_matches(cfg)
    out = json.loads(AUGSEP.read_text(encoding="utf-8")) if AUGSEP.exists() else {}
    start, end = P["holdout"]
    for who in todo:
        vals = setfile.read_set(DELIV / HOLDOUT_SETS[who])
        run_id = f"holdout_{who}"
        res, rep = pipeline.run_single(cfg, run_id, "research", CHART_PERIOD, start, end, overrides=vals,
                                       role=f"holdout_{who}", purpose=f"R31 {P['holdout_label']}",
                                       log_extra={"ea_sha256": ea, "set_sha256": shas[who]})
        if rep is None:
            print(f"{run_id}: {res.status}, no report; may be rerun", flush=True)
            continue
        curate.curate(run_id, "aug_sep_check")
        x = evaluate.load_run(run_id)
        st = evaluate.stitch([x])
        out[who] = {"run_id": run_id, "window": [start, end], "label": P["holdout_label"], "independent": False,
                    "input_mismatches": pipeline.check_inputs_loaded(rep, vals),
                    "conformance_violations": conformance_report(run_id, "aug_sep_check")["violations"],
                    "net_profit": x["summary"]["net_profit"], "fills": len(x["trades"]),
                    "fills_per_month": evaluate.fills_per_month(len(x["trades"]), start, end),
                    "tester_equity_dd_pct": x["summary"]["equity_dd_pct"],
                    "tester_balance_dd_pct": x["summary"]["balance_dd_pct"], **evaluate.drawdowns(st),
                    "first_breach": limits.first_breach(x["days"], initial=x["deposit"])}
    out["candidate_variant"] = json.loads((FINAL.parent / "freeze.json").read_text())["candidate_variant"]
    out["note"] = ("Non-independent historical check (R31): run once after the candidate freeze, reported apart, "
                   "never used for any choice; the WFO months were also seen by earlier research.")
    evaluate.save(out, AUGSEP)
    print(json.dumps({w: {k: out[w][k] for k in ("fills_per_month", "net_profit")} for w in HOLDOUT_SETS if w in out},
                     indent=1, default=str))


# ------------------------------------------------------------------ U10: robustness and acceptance
ROBUST = RESULTS / "robustness"


def _stitch_runs(run_ids: list, root) -> dict:
    return evaluate.stitch([evaluate.load_run(r, root) for r in run_ids])


def _series_view(s: dict, start: str, end: str) -> dict:
    tab = s["table"]
    return {"net": round(float(s["net_profit"]), 2), "fills": int(len(s["trades"])),
            "fills_per_month": evaluate.fills_per_month(len(s["trades"]), start, end),
            "win_rate": round(float((tab["net"] > 0).mean()), 4) if len(tab) else 0.0,
            **evaluate.drawdowns(s)}


def session_sensitivity(tag: str, run_ids: list, groups: dict, window: list, out_root: pathlib.Path = None) -> dict:
    """The pre-registered quote-only-minute sensitivity: one probe run over the given runs, then the fixed trade
    list re-priced per run and per group of chained runs (results/pilot/gate_decisions.md). out_root defaults to
    this research's results/robustness; another study passes its own root."""
    import session_probe as sp
    from mt5r import session_sensitivity as ss
    out_dir = (out_root or ROBUST) / tag
    sp.cmd_run(run_ids, tag, out_dir, window=window)
    cs = sp.cases(run_ids)
    ticks = pd.read_csv(runner.RUNS / tag / f"rl_ticks_{tag}.csv")
    alt = ss.alt_exits(cs, ticks).merge(cs[["case_id", "run_id"]], on="case_id")
    alt.to_csv(out_dir / "alt_exits.csv", index=False)
    runs = {r: cm_read(r) for r in run_ids}
    per_run = {r: ss.evaluate(runs[r]["setups"], runs[r]["deals"], alt[alt.run_id == r]) for r in run_ids}
    per_group = {g: ss.evaluate_series([(runs[r]["setups"], runs[r]["deals"], alt[alt.run_id == r]) for r in ids])
                 for g, ids in groups.items()}
    res = {"method": "re-pricing of a fixed trade list; closed-trade balance drawdown on the corrected timeline",
           "per_run": per_run, "per_group": per_group}
    evaluate.save(res, out_dir / "sensitivity.json")
    return res


def cm_read(run_id: str) -> dict:
    from mt5r import conformance_m1 as cm
    return cm.read_run(runner.RUNS / run_id, run_id)


def cmd_robustness(args) -> None:
    """R32 evidence and the KTD14 acceptance: stability runs, the three series and the fold groups, cost stress,
    MC, DSR and the session sensitivity."""
    P = prereg_committed()
    if not committed(_rel(DELIV / CAND_SET)):
        raise SystemExit("candidate is not frozen and committed; run `freeze` and commit first")
    cfg = env.load_config()
    installed_ea_matches(cfg)
    folds = json.loads(FOLDS.read_text())
    cand = json.loads(FINAL.read_text())["params"]
    cand_series = {0: "fixed_a", 1: "fixed_b"}[cand["StructureVariant"]]
    root = RESULTS / "wfo"
    ids = {w: [f["oos"][w]["run_id"] for f in folds] for w in SERIES}
    series = {w: _stitch_runs(ids[w], root) for w in SERIES}
    win = [P["folds"][0]["test"][0], P["folds"][-1]["test"][1]]
    G, R = P["reporting"]["generated_ticks"]["folds_group_G"], P["reporting"]["generated_ticks"]["folds_group_R"]

    def pick(w, grp):
        return [f["oos"][w]["run_id"] for f in folds if f["fold"] in grp]

    def span(grp):
        return [P["folds"][grp[0] - 1]["test"][0], P["folds"][grp[-1] - 1]["test"][1]]

    def fold_net(w, grp=None):
        return [{"fold": f["fold"], "net": f["oos"][w]["net_profit"]} for f in folds if grp is None or f["fold"] in grp]

    # KTD15 stability: the candidate and one constant moved at a time, March-July; never used for selection
    st = P["stability"]
    rows = []
    for i, pert in enumerate([{}] + st["runs"]):
        run_id = "stability_candidate" if i == 0 else f"stability_{i}"
        params = {**cand, **pert}
        r = oos_record(cfg, run_id, *st["window"], P["deposit"], params,
                       "static_candidate" if i == 0 else "stability", "KTD15 stability (not used for selection)",
                       "robustness")
        x = evaluate.load_run(run_id, ROBUST)
        rows.append({"run_id": run_id, "perturbation": pert, "net": r["net_profit"], "fills": len(x["trades"]),
                     "tester_equity_dd_pct": r["equity_dd_pct"], "tester_balance_dd_pct": r["balance_dd_pct"],
                     "conformance_violations": r["conformance_violations"], "input_mismatches": r["input_mismatches"]})
    share = float(np.mean([r["net"] > 0 for r in rows[1:]]))

    tv = evaluate.trial_sharpe_variance(sorted(root.glob("*_selection/scored_grid.csv")))
    # fixed A is the baseline (base None); stability belongs to the procedure and the candidate's own series
    acc = {w: evaluate.evaluate(series[w], None if w == "fixed_a" else series["fixed_a"], fold_net(w),
                                share if w in ("procedure", cand_series) else None, tv["var_sr"], P)
           for w in SERIES}
    group_r = {w: _stitch_runs(pick(w, R), root) for w in SERIES}
    acc_r = evaluate.evaluate(group_r["procedure"], group_r["fixed_a"], fold_net("procedure", R), share,
                              tv["var_sr"], P, window=span(R))
    groups = {w: {"all": _series_view(series[w], *win),
                  "G": _series_view(_stitch_runs(pick(w, G), root), *span(G)),
                  "R": _series_view(group_r[w], *span(R))} for w in SERIES}
    train_generated = {x["fold"]: x["train_cov"]["generated_pct"]
                       for x in json.loads(TICKCOV.read_text())["folds"]}
    fold_rows = [{"fold": f["fold"], "test": f["test"], "selected": f["selection"]["params"],
                  "status": f["selection"]["status"],
                  "train_generated_pct": train_generated[f["fold"]],
                  **{f"{w}_net": f["oos"][w]["net_profit"] for w in SERIES},
                  **{f"{w}_fills": f["oos"][w]["trades"] for w in SERIES},
                  **{f"{w}_tester_equity_dd_pct": f["oos"][w]["equity_dd_pct"] for w in SERIES},
                  **{f"{w}_tester_balance_dd_pct": f["oos"][w]["balance_dd_pct"] for w in SERIES}} for f in folds]

    S = P["stats"]
    bm = metrics.bar_minutes(P["acceptance"]["event_bar"])
    shuffle = {}
    for w in SERIES:
        ev = metrics.trade_events(series[w]["trades"], bm) if len(series[w]["trades"]) else pd.DataFrame({"ret": []})
        mc = (montecarlo.shuffle_paths(ev["ret"].to_numpy(), n_paths=S["mc_paths"], seed=S["seed"],
                                       initial=series[w]["initial"]) if len(ev) else {})
        shuffle[w] = {k: v for k, v in mc.items() if np.ndim(v) == 0}

    sens = session_sensitivity("session_probe_wfo", [r for w in SERIES for r in ids[w]],
                               {w: ids[w] for w in SERIES}, list(RUN["windows"]["wfo"]))
    evaluate.save({"candidate": cand, "candidate_series": cand_series, "acceptance": acc,
                   "acceptance_group_R_report_only": acc_r, "passed_all": acc["procedure"]["passed_all"],
                   "recommended": False, "fold_rows": fold_rows, "groups": groups,
                   "stability": {"profitable_share": share, "rows": rows, "window": st["window"]},
                   "var_sr": tv["var_sr"], "var_sr_source": tv["source"], "var_sr_passes": tv["passes"],
                   "shuffle_mc": shuffle, "session_sensitivity": sens["per_group"]},
                  RESULTS / "acceptance.json")
    print(json.dumps({"passed_all": acc["procedure"]["passed_all"], "recommended": False}, indent=1))


def cmd_augsep_sensitivity(args) -> None:
    """The pre-registered session sensitivity for the August-September runs (after `holdout`)."""
    P = prereg_committed()
    ids = [f"holdout_{w}" for w in HOLDOUT_SETS if (runner.RUNS / f"holdout_{w}").exists()]
    sens = session_sensitivity("session_probe_augsep", ids, {i: [i] for i in ids}, list(P["holdout"]))
    print(json.dumps(sens["per_group"], indent=1))


# ------------------------------------------------------------------ U10: deliverables
def cmd_deliver(args) -> None:
    """Validate the .set files on the delivered build, then tables and charts."""
    P = prereg_committed()
    acc = json.loads((RESULTS / "acceptance.json").read_text())
    final = json.loads(FINAL.read_text())
    folds = json.loads(FOLDS.read_text())
    cfg = env.load_config()
    installed_ea_matches(cfg)
    ex5 = pipeline.BUILDS["delivered"][0]
    start, end = P["folds"][0]["test"]
    checks = {}
    for name in (ORIG_SET, A_SET, B_SET, CAND_SET):
        run_id = "validate_" + name.replace(".set", "")
        text = pipeline._ini(ex5, CHART_PERIOD, start, end, P["deposit"], run_id, deliver.set_file_lines(DELIV / name))
        res = runner.run(cfg, run_id, text, ex5, meta={"role": "set_validation", "set": name})
        rep = reports.parse_html(res.report) if res.report else None
        mism = pipeline.check_inputs_loaded(rep, setfile.read_set(DELIV / name)) if rep else ["no report"]
        checks[name] = {"mismatches": mism, "period": [start, end], "status": res.status}
        explog.append({"id": run_id, "purpose": "R33 .set validation", "role": "set_validation", "expert": ex5,
                       "status": "ok" if rep and not mism else "mismatch", "has_report": rep is not None})
        curate.curate(run_id, "set_validation")
    evaluate.save(checks, RESULTS / "set_validation.json")

    root = RESULTS / "wfo"
    series = {w: _stitch_runs([f["oos"][w]["run_id"] for f in folds], root) for w in SERIES}
    hold = {w: evaluate.stitch([evaluate.load_run(f"holdout_{w}", RESULTS / "aug_sep_check")])
            for w in HOLDOUT_SETS if (RESULTS / "aug_sep_check" / f"holdout_{w}").exists()}
    labels = {"procedure": "WFO procedure (per-fold variant)", "fixed_a": "Variant A (baseline)",
              "fixed_b": "Variant B"}
    rows = []
    for w, s in series.items():
        a = acc["acceptance"][w]
        rows.append({"series": labels[w] + " - OOS Mar-Jul", "net_after_costs": round(s["net_profit"], 2),
                     "fills": len(s["trades"]), "balance_dd_pct": a["drawdowns"]["balance_dd_pct"],
                     "equity_dd_pct": a["drawdowns"]["equity_dd_pct"],
                     "stressed_net": a["criteria"]["cost_stress"]["value"]["stressed_net"],
                     "criteria_passed": sum(1 for c in a["criteria"].values() if c["pass"]),
                     "criteria_evaluated": sum(1 for c in a["criteria"].values() if c["evaluated"])})
    for w, s in hold.items():
        d = evaluate.drawdowns(s)
        rows.append({"series": f"{w.replace('_', ' ').title()} - Aug-Sep (non-independent)",
                     "net_after_costs": round(s["net_profit"], 2), "fills": len(s["trades"]),
                     "balance_dd_pct": d["balance_dd_pct"], "equity_dd_pct": d["equity_dd_pct"],
                     "stressed_net": None, "criteria_passed": None, "criteria_evaluated": None})
    DELIV.mkdir(exist_ok=True)
    (DELIV / "parameter_table.md").write_text("# Parameter table\n\n" + deliver.parameter_table(P, final["params"]) + "\n")
    (DELIV / "comparison.md").write_text("# Series comparison (net after costs; drawdowns from the daily records "
                                         "and the closed-trade balance)\n\n" + _md(pd.DataFrame(rows)) + "\n")
    cols = [c for c in trades.COLUMNS if c not in ("position_id",)]
    (DELIV / "trades_oos.md").write_text("# OOS trades, WFO procedure\n\n" + _md(series["procedure"]["table"][cols]) + "\n")
    label = f"OOS {P['folds'][0]['test'][0]}-{P['folds'][-1]['test'][1]}"
    fold_bars = [{"fold": f["fold"], "label": f["test"][0][:7], "candidate": f["oos"]["procedure"]["net_profit"],
                  "baseline": f["oos"]["fixed_a"]["net_profit"], "candidate_trades": f["oos"]["procedure"]["trades"]}
                 for f in folds]
    mc = acc["acceptance"]["procedure"]["criteria"]["loss_limits"]["value"]["monte_carlo"]
    same = all(f["selection"]["params"] == FIXED["fixed_a"] for f in folds)   # every fold fell back to A
    curves = ({"Variant A = WFO procedure (A in every fold)": series["fixed_a"], labels["fixed_b"]: series["fixed_b"]}
              if same else {labels[w]: series[w] for w in SERIES})
    made = deliver.charts(curves, fold_bars, mc, DELIV / "charts", label, "oos")
    if hold:
        made += deliver.charts({w.replace("_", " ").title(): s for w, s in hold.items()}, [], {}, DELIV / "charts",
                               f"Aug-Sep {P['holdout'][0]}-{P['holdout'][1]} (non-independent)", "aug_sep")
    shutil.copy2(pipeline.EA_SRC, DELIV / m1_contract.EA_SOURCE)
    if deliver.should_write_recommended(acc) or any(DELIV.glob("*recommended*.set")):
        raise SystemExit("a recommended .set must not exist in this research (R33)")
    print(json.dumps({"set_validation": {k: len(v["mismatches"]) for k, v in checks.items()}, "charts": made},
                     indent=1))


# ------------------------------------------------------------------ entry point
COMMANDS = {"setup": cmd_setup, "install": cmd_install, "smoke": cmd_smoke, "conformance": cmd_conformance, "optsmoke": cmd_optsmoke,
            "pilot": cmd_pilot, "charts": cmd_charts, "freeze-rules": cmd_freeze_rules, "wfo": cmd_wfo,
            "freeze": cmd_freeze, "holdout": cmd_holdout, "robustness": cmd_robustness, "deliver": cmd_deliver,
            "tickcov": cmd_tickcov, "augsep-sensitivity": cmd_augsep_sensitivity}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="build/refresh the isolated MT5 copy from allowlisted live files")
    sub.add_parser("install", help="copy EA sources into the isolated copy and compile both builds")
    s = sub.add_parser("smoke", help="one-month research-build runs of both variants + conformance")
    s.add_argument("--variant", choices=list(VARIANTS), action="append")
    s.add_argument("--start", default="2026.03.01")
    s.add_argument("--end", default="2026.03.31")
    s = sub.add_parser("conformance", help="conformance check of archived research runs")
    s.add_argument("run_ids", nargs="+")
    s.add_argument("--dest", default="smoke")
    s = sub.add_parser("optsmoke", help="2-pass optimization smoke over StructureVariant")
    s.add_argument("--start", default="2026.03.02")
    s.add_argument("--end", default="2026.03.06")
    sub.add_parser("pilot", help="R28 frequency pilot of both variants on M5/M1 (no profit fields)")
    sub.add_parser("augsep-sensitivity", help="session sensitivity of the August-September runs (after holdout)")
    sub.add_parser("tickcov", help="real-tick coverage per month and per fold (one run per month)")
    s = sub.add_parser("charts", help="R26 gate charts from the pilot runs")
    s.add_argument("--run-id")
    sub.add_parser("freeze-rules", help="write research/preregistration.json (R29, KTD14)")
    for name, text in (("wfo", "walk-forward folds and final selection"), ("freeze", "write the .set files"),
                       ("holdout", "run the frozen holdout once"), ("robustness", "R32 robustness and KTD14 acceptance"),
                       ("deliver", "validate .set files, tables and charts")):
        sub.add_parser(name, help=text)
    args = ap.parse_args()
    COMMANDS[args.cmd](args)


if __name__ == "__main__":
    main()
