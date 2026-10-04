"""1R trailing stop - development runs on March 2026: python research/trail_cli.py <install|runs|analyze>
(plan docs/plans/2026-10-05-0007-feat-trailing-stop-1r-plan.md, U5).

March 2026 is already exposed; these runs are a development comparison only: no improvement claim, no
recommended.set, and the trailing rule is never changed from their results. This CLI never touches the frozen
studies (their pre-registrations, guards, results or experiment log): its own run IDs (tr1_), results
(results/trailing_v1), experiment log and deliverables (deliverables/trailing_v1). Every tester call goes through the
isolated runner, which refuses while the live terminal runs.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

import cli  # noqa: E402
from mt5r import (charts_trail, compile as compmod, conformance_m1 as cm, curate, deliver, env, evaluate,  # noqa: E402
                  explog, pipeline, reports, runner, setfile)

REPO = pathlib.Path(__file__).resolve().parents[1]
RESULTS = REPO / "results" / "trailing_v1"
DELIV = REPO / "deliverables" / "trailing_v1"
SET_NAME = "ob_m1_structure_trailing.set"
LOG = RESULTS / "experiment_log.jsonl"
WINDOW = ("2026.03.01", "2026.03.31")
DEPOSIT = 10000.0
PREFIX = "tr1_"
BASE_INPUTS = {"ImpulseWindowBars": 2, "SwingStrengthM1": 3, "StopBufferPoints": 20, "RiskRR": 2.0,
               "RiskPercent": 1.0, "MaxExposures": 3, "WarmupDays": 30}
# research-build runs: (name, variant, trailing); delivered twins of the trail-on runs
RUNS = [("off_a", 0, False), ("off_b", 1, False), ("on_a", 0, True), ("on_b", 1, True)]
TWINS = {"on_a_delivered": "on_a", "on_b_delivered": "on_b"}
BASELINE = {"off_a": REPO / "results/numeric_v1/wfo/nv1_f1_oos_baseline_a",
            "off_b": REPO / "results/numeric_v1/wfo/nv1_f1_oos_baseline_b"}
VARIANT = {"on_a": 0, "on_b": 1, "off_a": 0, "off_b": 1}


def run_id(name: str) -> str:
    if "/" in name or "\\" in name:
        raise ValueError(f"bad run name {name!r}")
    return PREFIX + name


def check_window(start: str, end: str) -> None:
    if (start, end) != WINDOW:
        raise SystemExit(f"{start}-{end}: this development CLI runs March 2026 only {WINDOW}")


def use_study_log() -> None:
    """pipeline.run_single appends to explog.LOG: point it at this study's own log (never results/)."""
    explog.LOG = LOG


def inputs(variant: int, trailing: bool) -> dict:
    return {**BASE_INPUTS, "StructureVariant": variant, "EnableTrailingStop": trailing}


def run_dir(name: str) -> pathlib.Path:
    return RESULTS / run_id(name)


def _deals_signature(rep: dict) -> list:
    """Deal rows of a parsed tester report without the ticket/order/comment columns (the builds' twins compare)."""
    d = rep.get("deals")
    if d is None or len(d) == 0:
        return []
    keep = [c for c in d.columns if str(c).lower() not in ("deal", "order", "comment")]
    return [tuple(str(x) for x in row) for row in d[keep].itertuples(index=False, name=None)]


def compare_deals(a: pd.DataFrame, b: pd.DataFrame) -> list:
    """Field-for-field differences of two rl_deals tables (first 20)."""
    diffs = []
    if list(a.columns) != list(b.columns):
        return [f"columns {list(a.columns)} != {list(b.columns)}"]
    if len(a) != len(b):
        diffs.append(f"{len(a)} != {len(b)} deals")
    for i, (ra, rb) in enumerate(zip(a.astype(str).itertuples(index=False), b.astype(str).itertuples(index=False))):
        if tuple(ra) != tuple(rb):
            diffs.append(f"deal row {i}: {tuple(ra)} != {tuple(rb)}")
            if len(diffs) >= 20:
                break
    return diffs


# ------------------------------------------------------------------ install + compile
def cmd_install(args) -> None:
    use_study_log()
    cfg = env.load_config()
    env.install_sources(cfg)
    out = {}
    for src in env.EA_SOURCES:
        r = compmod.compile_ea(cfg, src)
        (RESULTS / "compile").mkdir(parents=True, exist_ok=True)
        (RESULTS / "compile" / f"{pathlib.Path(src).stem}.log").write_text(r["log"], encoding="utf-8")
        out[src] = {k: r[k] for k in ("errors", "warnings", "ex5_exists")}
        out[src]["source_sha256"] = cli.sha_source(REPO / "mql5" / "Experts" / src)
    evaluate.save(out, RESULTS / "compile" / "compile.json")
    print(json.dumps(out, indent=1))
    if any(v["errors"] != 0 or not v["ex5_exists"] for v in out.values()):
        raise SystemExit("compile failed")


# ------------------------------------------------------------------ runs
def write_set() -> pathlib.Path:
    """The trailing .set: code defaults with EnableTrailingStop=true; not a recommendation (R8)."""
    path = DELIV / SET_NAME
    if path.exists():
        return path
    spec = pipeline.specs("delivered")
    values = {s.name: s.default for s in spec}
    values["EnableTrailingStop"] = True
    setfile.write_set(path, setfile.render_lines(spec, values),
                      header="M5 OB + M1 structure, code defaults plus the 1R trailing stop "
                             "(plan 2026-10-05-0007). Development only - not a recommended set.")
    return path


def research_run(cfg, name: str, variant: int, trailing: bool) -> dict:
    rid = run_id(name)
    dest = run_dir(name)
    if dest.exists():
        return json.loads((dest / "record.json").read_text())
    vals = inputs(variant, trailing)
    res, rep = pipeline.run_single(cfg, rid, "research", cli.CHART_PERIOD, *WINDOW, deposit=DEPOSIT, overrides=vals,
                                   role="trail_dev", purpose=f"1R trailing development run {name}")
    if rep is None:
        raise SystemExit(f"{rid}: no report ({res.status})")
    curate.curate(rid, f"trailing_v1")
    rec = {"run_id": rid, "kind": "research", "status": res.status, "inputs": vals,
           "input_mismatches": pipeline.check_inputs_loaded(rep, vals), **reports.summary(rep),
           "history_quality": rep["header"].get("History Quality"), "build": rep["header"].get("Build")}
    evaluate.save(rec, dest / "record.json")
    return rec


def twin_run(cfg, name: str, set_path: pathlib.Path) -> dict:
    """Delivered build. on_a_delivered runs the trailing .set file itself (it also proves the .set loads)."""
    rid = run_id(name)
    dest = run_dir(name)
    if dest.exists():
        return json.loads((dest / "record.json").read_text())
    ex5 = pipeline.BUILDS["delivered"][0]
    src = TWINS[name]
    if name == "on_a_delivered":
        lines, expected = deliver.set_file_lines(set_path), setfile.read_set(set_path)
    else:
        expected = inputs(VARIANT[src], True)
        vals = {**pipeline.base_values("delivered"), **expected}
        lines = setfile.render_lines(pipeline.specs("delivered"), vals)
    text = pipeline._ini(ex5, cli.CHART_PERIOD, *WINDOW, DEPOSIT, rid, lines)
    res = runner.run(cfg, rid, text, ex5, meta={"role": "trail_twin", "of": src})
    rep = reports.parse_html(res.report) if res.report else None
    if rep is None:
        raise SystemExit(f"{rid}: no report ({res.status})")
    explog.append({"id": rid, "role": "trail_twin", "expert": ex5, "from": WINDOW[0], "to": WINDOW[1],
                   "status": res.status, "has_report": True})
    curate.curate(rid, "trailing_v1")
    rec = {"run_id": rid, "kind": "delivered", "of": run_id(src), "status": res.status,
           "input_mismatches": pipeline.check_inputs_loaded(rep, expected), **reports.summary(rep),
           "history_quality": rep["header"].get("History Quality"), "build": rep["header"].get("Build")}
    evaluate.save(rec, dest / "record.json")
    return rec


def cmd_runs(args) -> None:
    use_study_log()
    check_window(*WINDOW)
    cfg = env.load_config()
    cli.installed_ea_matches(cfg)
    set_path = write_set()
    recs = {}
    for name, variant, trailing in RUNS:
        recs[name] = research_run(cfg, name, variant, trailing)
        print(f"{name}: net {recs[name]['net_profit']} trades {recs[name]['trades']}", flush=True)
        if not trailing:
            ours = pd.read_csv(run_dir(name) / f"rl_deals_{run_id(name)}.csv")
            tag = BASELINE[name].name
            base = pd.read_csv(BASELINE[name] / f"rl_deals_{tag}.csv")
            diffs = compare_deals(ours, base)
            if diffs:
                evaluate.save({"run": run_id(name), "baseline": tag, "diffs": diffs}, RESULTS / f"{name}_mismatch.json")
                raise SystemExit(f"{name}: trades differ from {tag} - stop and diagnose (plan stop condition)")
    for name in TWINS:
        recs[name] = twin_run(cfg, name, set_path)
        print(f"{name}: net {recs[name]['net_profit']} trades {recs[name]['trades']}", flush=True)
    evaluate.save(recs, RESULTS / "runs.json")


# ------------------------------------------------------------------ analysis
def positions(name: str) -> pd.DataFrame:
    """One row per filled position: exit class, R against R0 = |fill - SL0| (the original risk), net."""
    d, rid = run_dir(name), run_id(name)
    st = pd.read_csv(d / f"rl_setups_{rid}.csv", keep_default_na=False, na_values=[""])
    deals = pd.read_csv(d / f"rl_deals_{rid}.csv")
    f = st[st["fill_price"].notna()].copy()
    d2 = deals[deals["position_id"] > 0].copy()
    d2["net"] = d2["profit"] + d2["commission"] + d2["swap"]
    net = d2.groupby("position_id")["net"].sum()
    f["net"] = f["position_id"].map(net).fillna(0.0)
    f["r0"] = (f["fill_price"] - f["sl"]).abs()
    f["r"] = f["net"] / (f["r0"] * f["volume"] * 100)
    return f


def summarize(name: str, rec: dict) -> dict:
    p = positions(name)
    out = {"run": run_id(name), "trades": int(len(p)), "net": round(float(p["net"].sum()), 2),
           "tester_equity_dd_pct": rec.get("equity_dd_pct"), "mean_r_vs_r0": round(float(p["r"].mean()), 4),
           "win_rate": round(float((p["net"] > 0).mean()), 4),
           "exit_kinds": {k: int(v) for k, v in p["exit_kind"].value_counts().items()}}
    trail_csv = run_dir(name) / f"rl_trail_{run_id(name)}.csv"
    if trail_csv.exists():
        t = pd.read_csv(trail_csv, keep_default_na=False, na_values=[""])
        out["trail"] = {"positions": int(len(t)), "activated": int(t["activated_msc"].notna().sum()),
                        "requests": int(t["requests"].sum()), "accepted": int(t["accepted"].sum()),
                        "rejected": int(t["rejected"].sum()), "not_sent": int(t["not_sent"].sum()),
                        "state_roundtrip_ok": int((t["state_roundtrip"] == "ok").sum())}
        out["trail_sl_exits_in_profit"] = int(((p["exit_kind"] == "trail") & (p["net"] > 0)).sum())
    return out


def conformance(name: str, variant: int) -> dict:
    rid = run_id(name)
    run = cm.read_run(run_dir(name), rid)
    params = {**BASE_INPUTS, "StructureVariant": variant, "point": cli.RUN["symbol_spec"]["tick_size"],
              "contract_size": cli.RUN["symbol_spec"]["contract_size"]}
    res = cm.full(run["setups"], run["events"], run["pivots"], run["bars_m1"], run["bars_m5"], params,
                  run.get("deals"), run.get("trail"), run.get("sl_moves"))
    out = {"run_id": rid, "violations": len(res["violations"]), "violation_rows": res["violations"][:200],
           "coverage": res["coverage"], "trail_files": sorted(k for k in ("trail", "sl_moves") if k in run)}
    evaluate.save(out, run_dir(name) / "conformance.json")
    return out


def cmd_analyze(args) -> None:
    use_study_log()
    recs = json.loads((RESULTS / "runs.json").read_text())
    conf = {name: conformance(name, VARIANT[name]) for name, _, _ in RUNS}
    builds = {}
    for twin, src in TWINS.items():
        a = reports.parse_html(next(run_dir(src).glob("*.htm")))
        b = reports.parse_html(next(run_dir(twin).glob("*.htm")))
        sa, sb = _deals_signature(a), _deals_signature(b)
        builds[twin] = {"of": run_id(src), "same_deals": sa == sb, "deals": [len(sa), len(sb)],
                        "input_mismatches": recs[twin]["input_mismatches"]}
    evaluate.save(builds, RESULTS / "builds_match.json")
    match = {name: {"baseline": BASELINE[name].name, "identical": not (RESULTS / f"{name}_mismatch.json").exists(),
                    "trail_files_written": sorted(p.name for p in run_dir(name).glob("rl_trail*")) +
                    sorted(p.name for p in run_dir(name).glob("rl_sl_moves*"))} for name in BASELINE}
    evaluate.save(match, RESULTS / "baseline_match.json")
    summary = {name: summarize(name, recs[name]) for name, _, _ in RUNS}
    evaluate.save(summary, RESULTS / "summary.json")
    charts = {}
    for name in ("on_a", "on_b"):
        run = cm.read_run(run_dir(name), run_id(name))
        charts[name] = [p.relative_to(REPO).as_posix()
                        for p in charts_trail.render(run, RESULTS / "charts", run_id(name))]
    evaluate.save(charts, RESULTS / "charts.json")
    print(json.dumps({"conformance": {k: v["violations"] for k, v in conf.items()}, "builds": builds,
                      "baseline": match, "summary": summary}, indent=1, ensure_ascii=False, default=str))


COMMANDS = {"install": cmd_install, "runs": cmd_runs, "analyze": cmd_analyze}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in COMMANDS:
        sub.add_parser(name)
    args = ap.parse_args()
    COMMANDS[args.cmd](args)


if __name__ == "__main__":
    main()
