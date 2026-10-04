"""1R trailing stop - development runs on March 2026: python research/trail_cli.py <install|runs|analyze>
(plans docs/plans/2026-10-05-0007-feat-trailing-stop-1r-plan.md U5 and
docs/plans/2026-10-05-0128-fix-trailing-retry-persistence-plan.md U4, KTD8).

March 2026 is already exposed; these runs are a development comparison only: no improvement claim, no
recommended.set, and the trailing rule is never changed from their results. This CLI never touches the frozen
studies (their pre-registrations, guards, results or experiment log) nor results/trailing_v1: its own run IDs (tr2_),
results (results/trailing_v2), experiment log and deliverables (deliverables/trailing_v2). Every tester call goes
through the isolated runner, which refuses while the live terminal runs.

Provenance (KTD8): each record.json holds the EA source hash (source_sha256: {file name: sha256 with CRLF read as LF}
for both .mq5 files), the EX5 hash from the run manifest, the full input set and the window. A folder is reused only
when all four match the current ones; otherwise it is refused by field name and left untouched. Each `runs` call is
an attempt n, reserved in attempts/a<n>.started.json before any tester call and closed in attempts/a<n>.json (neither
is ever overwritten); attempt n >= 2 uses run IDs ending _a<n>.
"""
import argparse
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

import cli  # noqa: E402
from mt5r import (charts_trail, compile as compmod, conformance_m1 as cm, curate, deliver, env, evaluate,  # noqa: E402
                  explog, pipeline, reports, runner, setfile, textio, trades)

REPO = pathlib.Path(__file__).resolve().parents[1]
STUDY = "trailing_v2"
RESULTS = REPO / "results" / STUDY
DELIV = REPO / "deliverables" / STUDY
SET_NAME = "ob_m1_structure_trailing.set"
SET_HEADER = ("M5 OB + M1 structure, code defaults plus the 1R trailing stop "
              "(plan 2026-10-05-0007). Development only - not a recommended set.")
LOG = RESULTS / "experiment_log.jsonl"
WINDOW = ("2026.03.01", "2026.03.31")
DEPOSIT = 10000.0
PREFIX = "tr2_"
BASE_INPUTS = {"ImpulseWindowBars": 2, "SwingStrengthM1": 3, "StopBufferPoints": 20, "RiskRR": 2.0,
               "RiskPercent": 1.0, "MaxExposures": 3, "WarmupDays": 30}
# research-build runs: (name, variant, trailing); delivered twins of the trail-on runs
RUNS = [("off_a", 0, False), ("off_b", 1, False), ("on_a", 0, True), ("on_b", 1, True)]
TWINS = {"on_a_delivered": "on_a", "on_b_delivered": "on_b"}
BASELINE = {"off_a": REPO / "results/numeric_v1/wfo/nv1_f1_oos_baseline_a",
            "off_b": REPO / "results/numeric_v1/wfo/nv1_f1_oos_baseline_b"}
VARIANT = {name: variant for name, variant, _ in RUNS}
TRAILING = {name: trailing for name, _, trailing in RUNS}
PROVENANCE = ("source_sha256", "ex5_sha256", "inputs", "window")


def run_id(name: str, attempt: int = 1) -> str:
    """tr2_<name> for attempt 1, tr2_<name>_a<n> for attempt n >= 2 (KTD8)."""
    if "/" in name or "\\" in name:
        raise ValueError(f"bad run name {name!r}")
    return PREFIX + name + (f"_a{attempt}" if attempt >= 2 else "")


def check_window(start: str, end: str) -> None:
    if (start, end) != WINDOW:
        raise SystemExit(f"{start}-{end}: this development CLI runs March 2026 only {WINDOW}")


def use_study_log() -> None:
    """pipeline.run_single appends to explog.LOG: point it at this study's own log (never results/)."""
    explog.LOG = LOG


def inputs(variant: int, trailing: bool) -> dict:
    return {**BASE_INPUTS, "StructureVariant": variant, "EnableTrailingStop": trailing}


def run_dir(name: str, attempt: int = 1) -> pathlib.Path:
    return RESULTS / run_id(name, attempt)


# ------------------------------------------------------------------ provenance (KTD8)
def source_sha() -> dict:
    """{file name: sha256 with CRLF read as LF} of both EA sources: the research file is only a define plus an
    include, so the main source must be part of the hash."""
    return {n: cli.sha_source(pipeline.EA_SRC.parent / n) for n in env.EA_SOURCES}


def ex5_name(name: str) -> str:
    return pipeline.BUILDS["delivered" if name in TWINS else "research"][0]


def installed_ex5_sha(cfg, ex5: str) -> str:
    """sha256 of the EX5 the isolated tester runs (the runner's manifest hashes the same file)."""
    path = cfg.mt5_dir / "MQL5" / "Experts" / ex5
    if not path.exists():
        raise SystemExit(f"{path} is missing; run `install` first")
    return textio.sha256(path)


def set_lines() -> list:
    return setfile.render_lines(pipeline.specs("delivered"), {"EnableTrailingStop": True})


def full_inputs(name: str) -> dict:
    """The input set the run receives beyond the EA defaults (those are covered by the source hash). ResearchRunTag
    is left out: it is the run ID, i.e. the folder itself."""
    if name == "on_a_delivered":   # runs the .set file: its values as the tester reads them (setfile.read_set)
        return {l.split("=", 1)[0].strip(): l.split("=", 1)[1].split("||")[0] for l in set_lines()}
    if name in TWINS:
        return {**pipeline.base_values("delivered"), **inputs(VARIANT[TWINS[name]], True)}
    return {**pipeline.base_values("research"), **inputs(VARIANT[name], TRAILING[name])}


def current_provenance(cfg, name: str) -> dict:
    return {"source_sha256": source_sha(), "ex5_sha256": installed_ex5_sha(cfg, ex5_name(name)),
            "inputs": full_inputs(name), "window": list(WINDOW)}


def _norm(x):
    return json.loads(json.dumps(evaluate.jsonable(x), default=str))


def read_record(dest: pathlib.Path) -> dict:
    p = dest / "record.json"
    return json.loads(p.read_text()) if p.exists() else {}


def provenance_diff(rec: dict, cur: dict) -> list:
    """The provenance fields that are missing from the record or differ from the current ones."""
    return [f for f in PROVENANCE if rec.get(f) is None or _norm(rec[f]) != _norm(cur[f])]


def _rel(p: pathlib.Path) -> str:
    try:
        return p.relative_to(REPO).as_posix()
    except ValueError:
        return p.as_posix()


def reuse_or_refuse(rid: str, cur: dict):
    """The record of an existing curated folder whose provenance matches `cur`; None when nothing exists yet.
    Refuses (SystemExit, folder untouched) a folder with any field missing or different, and a raw runs/<id>
    without a curated folder, which the runner would delete."""
    dest = RESULTS / rid
    if dest.exists():
        rec = read_record(dest)
        diff = provenance_diff(rec, cur)
        if diff:
            raise SystemExit(f"{rid}: {_rel(dest)} provenance missing or different: {', '.join(diff)}; refused, the "
                             "folder is left untouched (KTD8)")
        return rec
    if (runner.RUNS / rid).exists():
        raise SystemExit(f"{rid}: runs/{rid} exists without a curated folder; refused, the runner would delete it "
                         "(KTD8)")
    return None


def _create(path: pathlib.Path, obj) -> None:
    """Write a JSON record that must never be overwritten (attempt evidence, KTD8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:
        f.write(json.dumps(evaluate.jsonable(obj), indent=1, default=str))


def attempts_started() -> int:
    return len(list((RESULTS / "attempts").glob("a*.started.json")))


def latest_match(name: str, cur: dict, upto: int):
    """The run ID of the latest attempt <= upto whose folder matches `cur`, or None."""
    for k in range(upto, 0, -1):
        rid = run_id(name, k)
        if (RESULTS / rid).exists() and not provenance_diff(read_record(RESULTS / rid), cur):
            return rid
    return None


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
    if runner.live_terminal_running():
        raise SystemExit("live MT5 terminal is running; install only while it is closed (R35)")
    use_study_log()
    cfg = env.load_config()
    env.disable_mcp(cfg)   # the same safety steps as cli.cmd_install
    env.assert_trade_safety(cfg)
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
    """The trailing .set: code defaults with EnableTrailingStop=true; not a recommendation (R8). An existing file
    is reused only when it is byte-identical to a fresh render; a differing one is refused and left untouched."""
    path = DELIV / SET_NAME
    with tempfile.TemporaryDirectory() as tmp:
        fresh = pathlib.Path(tmp) / SET_NAME
        setfile.write_set(fresh, set_lines(), header=SET_HEADER)
        data = fresh.read_bytes()
    if path.exists():
        if path.read_bytes() != data:
            raise SystemExit(f"{_rel(path)} differs from a fresh render of the trailing .set; refused, the file is "
                             "left untouched (KTD8)")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _manifest_ex5_sha(dest: pathlib.Path):
    p = dest / "manifest.json"
    return json.loads(p.read_text()).get("ex5_sha256") if p.exists() else None


def research_run(cfg, name: str, rid: str, cur: dict = None) -> dict:
    """One research-build run under `rid`, or the record of a matching existing folder (reuse_or_refuse)."""
    cur = cur or current_provenance(cfg, name)
    rec = reuse_or_refuse(rid, cur)
    if rec is not None:
        return rec
    dest = RESULTS / rid
    vals = inputs(VARIANT[name], TRAILING[name])
    res, rep = pipeline.run_single(cfg, rid, "research", cli.CHART_PERIOD, *WINDOW, deposit=DEPOSIT, overrides=vals,
                                   role="trail_dev", purpose=f"1R trailing development run {name}")
    if rep is None:
        raise SystemExit(f"{rid}: no report ({res.status})")
    curate.curate(rid, STUDY)
    rec = {"run_id": rid, "kind": "research", "status": res.status, "source_sha256": cur["source_sha256"],
           "ex5_sha256": _manifest_ex5_sha(dest), "inputs": cur["inputs"], "window": list(WINDOW),
           "input_mismatches": pipeline.check_inputs_loaded(rep, vals), **reports.summary(rep),
           "history_quality": rep["header"].get("History Quality"), "build": rep["header"].get("Build")}
    evaluate.save(rec, dest / "record.json")
    return rec


def twin_run(cfg, name: str, rid: str, set_path: pathlib.Path, of: str, cur: dict = None) -> dict:
    """Delivered build of the research run `of`. on_a_delivered runs the trailing .set file itself (it also proves
    the .set loads)."""
    cur = cur or current_provenance(cfg, name)
    rec = reuse_or_refuse(rid, cur)
    if rec is not None:
        return rec
    dest = RESULTS / rid
    ex5 = ex5_name(name)
    src = TWINS[name]
    if name == "on_a_delivered":
        lines, expected = deliver.set_file_lines(set_path), setfile.read_set(set_path)
    else:
        expected = inputs(VARIANT[src], True)
        vals = {**pipeline.base_values("delivered"), **expected}
        lines = setfile.render_lines(pipeline.specs("delivered"), vals)
    text = pipeline._ini(ex5, cli.CHART_PERIOD, *WINDOW, DEPOSIT, rid, lines)
    res = runner.run(cfg, rid, text, ex5, meta={"role": "trail_twin", "of": of})
    rep = reports.parse_html(res.report) if res.report else None
    if rep is None:
        raise SystemExit(f"{rid}: no report ({res.status})")
    explog.append({"id": rid, "role": "trail_twin", "expert": ex5, "from": WINDOW[0], "to": WINDOW[1],
                   "status": res.status, "has_report": True})
    curate.curate(rid, STUDY)
    rec = {"run_id": rid, "kind": "delivered", "of": of, "status": res.status, "source_sha256": cur["source_sha256"],
           "ex5_sha256": _manifest_ex5_sha(dest), "inputs": cur["inputs"], "window": list(WINDOW),
           "input_mismatches": pipeline.check_inputs_loaded(rep, expected), **reports.summary(rep),
           "history_quality": rep["header"].get("History Quality"), "build": rep["header"].get("Build")}
    evaluate.save(rec, dest / "record.json")
    return rec


def plan_attempt(n: int, cur: dict) -> tuple:
    """Per run name: the latest earlier attempt's matching folder (reused), else this attempt's own run ID, which
    must not exist yet in runs/ or results/. Returns ({name: run ID}, refusals); no side effects."""
    plan, refusals = {}, []
    for name in cur:
        rid = latest_match(name, cur[name], n - 1)
        if rid is None:
            rid = run_id(name, n)
            if (RESULTS / rid).exists():
                diff = provenance_diff(read_record(RESULTS / rid), cur[name])
                why = "differs in " + ", ".join(diff) if diff else "not from an earlier attempt"
                refusals.append(f"{rid}: {_rel(RESULTS / rid)} already exists ({why}); refused, the folder is left "
                                "untouched")
            elif (runner.RUNS / rid).exists():
                refusals.append(f"{rid}: runs/{rid} exists without a curated folder; refused, the runner would "
                                "delete it")
        plan[name] = rid
    return plan, refusals


def cmd_runs(args) -> None:
    """One attempt n (KTD8): reserved before any tester call, closed with the run IDs it used, never overwritten."""
    use_study_log()
    check_window(*WINDOW)
    cfg = env.load_config()
    cli.installed_ea_matches(cfg)
    set_path = write_set()
    names = [name for name, _, _ in RUNS] + list(TWINS)
    cur = {name: current_provenance(cfg, name) for name in names}
    att = RESULTS / "attempts"
    n = attempts_started() + 1
    plan, refusals = plan_attempt(n, cur)
    base = {"attempt": n, "source_sha256": source_sha(), "window": list(WINDOW)}
    _create(att / f"a{n}.started.json",
            {**base, "planned": plan, "started": pd.Timestamp.now().isoformat(timespec="seconds")})
    if refusals:
        _create(att / f"a{n}.json", {**base, "status": "refused", "refusals": refusals, "run_ids": {}})
        raise SystemExit("; ".join(refusals) + " (KTD8)")
    used, reused = {}, []
    try:
        for name, _, trailing in RUNS:
            rid = plan[name]
            if (RESULTS / rid).exists():
                reused.append(name)
            rec = research_run(cfg, name, rid, cur[name])
            used[name] = rid
            print(f"{name} ({rid}): net {rec['net_profit']} trades {rec['trades']}", flush=True)
            if not trailing:
                ours = pd.read_csv(RESULTS / rid / f"rl_deals_{rid}.csv")
                tag = BASELINE[name].name
                base_deals = pd.read_csv(BASELINE[name] / f"rl_deals_{tag}.csv")
                diffs = compare_deals(ours, base_deals)
                if diffs:
                    evaluate.save({"run": rid, "baseline": tag, "diffs": diffs}, RESULTS / f"{rid}_mismatch.json")
                    raise SystemExit(f"{name}: trades differ from {tag} - stop and diagnose (plan stop condition)")
        for name, src in TWINS.items():
            rid = plan[name]
            if (RESULTS / rid).exists():
                reused.append(name)
            rec = twin_run(cfg, name, rid, set_path, used[src], cur[name])
            used[name] = rid
            print(f"{name} ({rid}): net {rec['net_profit']} trades {rec['trades']}", flush=True)
    except BaseException as e:
        _create(att / f"a{n}.json", {**base, "status": "failed", "error": f"{type(e).__name__}: {e}",
                                     "run_ids": used, "reused": reused})
        raise
    _create(att / f"a{n}.json", {**base, "status": "ok", "run_ids": used, "reused": reused})


# ------------------------------------------------------------------ analysis
def positions(rid: str) -> pd.DataFrame:
    """One row per filled position: exit class, R against R0 = |fill - SL0| (the original risk), net."""
    d = RESULTS / rid
    st = pd.read_csv(d / f"rl_setups_{rid}.csv", keep_default_na=False, na_values=[""])
    deals = pd.read_csv(d / f"rl_deals_{rid}.csv")
    f = st[st["fill_price"].notna()].copy()
    d2 = deals[deals["position_id"] > 0].copy()
    d2["net"] = d2["profit"] + d2["commission"] + d2["swap"]
    net = d2.groupby("position_id")["net"].sum()
    f["net"] = f["position_id"].map(net).fillna(0.0)
    f["r0"] = (f["fill_price"] - f["sl"]).abs()
    f["r"] = f["net"] / (f["r0"] * f["volume"] * trades.CONTRACT_SIZE)
    return f


def summarize(rid: str, rec: dict) -> dict:
    p = positions(rid)
    out = {"run": rid, "trades": int(len(p)), "net": round(float(p["net"].sum()), 2),
           "tester_equity_dd_pct": rec.get("equity_dd_pct"), "mean_r_vs_r0": round(float(p["r"].mean()), 4),
           "win_rate": round(float((p["net"] > 0).mean()), 4),
           "exit_kinds": {k: int(v) for k, v in p["exit_kind"].value_counts().items()}}
    trail_csv = RESULTS / rid / f"rl_trail_{rid}.csv"
    if trail_csv.exists():
        t = pd.read_csv(trail_csv, keep_default_na=False, na_values=[""])
        out["trail"] = {"positions": int(len(t)), "activated": int(t["activated_msc"].notna().sum()),
                        "requests": int(t["requests"].sum()), "accepted": int(t["accepted"].sum()),
                        "rejected": int(t["rejected"].sum()), "not_sent": int(t["not_sent"].sum()),
                        "state_roundtrip_ok": int((t["state_roundtrip"] == "ok").sum())}
        out["trail_sl_exits_in_profit"] = int(((p["exit_kind"] == "trail") & (p["net"] > 0)).sum())
    return out


def conformance(rid: str, variant: int) -> dict:
    run = cm.read_run(RESULTS / rid, rid)
    params = {**BASE_INPUTS, "StructureVariant": variant, "point": cli.RUN["symbol_spec"]["tick_size"],
              "contract_size": cli.RUN["symbol_spec"]["contract_size"]}
    trailing = cm.parse_trailing(json.loads((RESULTS / rid / "record.json").read_text())["inputs"]
                                 .get("EnableTrailingStop"))                       # the run's own input (R13)
    res = cm.full(run["setups"], run["events"], run["pivots"], run["bars_m1"], run["bars_m5"], params,
                  run.get("deals"), run.get("trail"), run.get("sl_moves"), trailing=trailing)
    out = {"run_id": rid, "trailing": trailing, "violations": len(res["violations"]), "violation_rows": res["violations"][:200],
           "coverage": res["coverage"], "trail_files": sorted(k for k in ("trail", "sl_moves") if k in run)}
    evaluate.save(out, RESULTS / rid / "conformance.json")
    return out


def select_runs(cfg) -> dict:
    """Per run name, the latest attempt whose folder matches the current EA source, EX5, inputs and window (KTD8);
    earlier attempts stay untouched. A name without a matching attempt is refused."""
    n = attempts_started()
    sel = {name: latest_match(name, current_provenance(cfg, name), n) for name in [r[0] for r in RUNS] + list(TWINS)}
    missing = [name for name, rid in sel.items() if rid is None]
    if missing:
        raise SystemExit(f"no attempt matches the current EA source, EX5, inputs and window for {missing}; "
                         "run `runs` first (KTD8)")
    return sel


def cmd_analyze(args) -> None:
    use_study_log()
    sel = select_runs(env.load_config())
    recs = {name: read_record(RESULTS / rid) for name, rid in sel.items()}
    conf = {name: conformance(sel[name], VARIANT[name]) for name, _, _ in RUNS}
    builds = {}
    for twin, src in TWINS.items():
        a = reports.parse_html(next((RESULTS / sel[src]).glob("*.htm")))
        b = reports.parse_html(next((RESULTS / sel[twin]).glob("*.htm")))
        sa, sb = _deals_signature(a), _deals_signature(b)
        builds[twin] = {"run": sel[twin], "of": sel[src], "same_deals": sa == sb, "deals": [len(sa), len(sb)],
                        "input_mismatches": recs[twin]["input_mismatches"]}
    evaluate.save(builds, RESULTS / "builds_match.json")
    match = {name: {"run": sel[name], "baseline": BASELINE[name].name,
                    "identical": not (RESULTS / f"{sel[name]}_mismatch.json").exists(),
                    "trail_files_written": sorted(p.name for p in (RESULTS / sel[name]).glob("rl_trail*")) +
                    sorted(p.name for p in (RESULTS / sel[name]).glob("rl_sl_moves*"))} for name in BASELINE}
    evaluate.save(match, RESULTS / "baseline_match.json")
    summary = {name: summarize(sel[name], recs[name]) for name, _, _ in RUNS}
    evaluate.save(summary, RESULTS / "summary.json")
    charts = {}
    for name in ("on_a", "on_b"):
        run = cm.read_run(RESULTS / sel[name], sel[name])
        charts[name] = [_rel(p) for p in charts_trail.render(run, RESULTS / "charts", sel[name])]
    evaluate.save(charts, RESULTS / "charts.json")
    print(json.dumps({"selected": sel, "conformance": {k: v["violations"] for k, v in conf.items()},
                      "builds": builds, "baseline": match, "summary": summary},
                     indent=1, ensure_ascii=False, default=str))


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
