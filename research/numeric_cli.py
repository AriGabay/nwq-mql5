"""numeric_v1 research CLI: python research/numeric_cli.py <step>  (plan docs/plans/2026-10-04-1851-feat-ob-m1-numeric-grid-research-plan.md)

Order: optsmoke-nv1 -> behaviour-nv1 (pre-freeze, train-only window) -> freeze-rules-nv1 (commit + push)
-> wfo-nv1 -> freeze-nv1 (commit + push) -> robustness-nv1 -> deliver-nv1.

This CLI never reads the previous research's results for a decision and never writes its files: its own
pre-registration (research/preregistration_numeric_v1.json), results (results/numeric_v1), deliverables
(deliverables/numeric_v1), experiment log and run IDs (prefix nv1_). Every tester call goes through the isolated
runner, which refuses while the live terminal runs. August-September 2026 is never run (KTD12).
"""
import argparse
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import cli  # noqa: E402
from mt5r import (curate, env, evaluate, explog, gridrun, metrics, montecarlo, numeric_v1 as nv, pipeline,  # noqa: E402
                  reports, runner, setfile)

REPO = nv.REPO
WFO = nv.RESULTS / "wfo"
FINAL_DIR = nv.RESULTS / "final_selection"
ROBUST = nv.RESULTS / "robustness"
SMOKE = nv.RESULTS / "smoke"
FOLDS_JSON = WFO / "folds.json"
SERIES = ("procedure", "baseline_a", "baseline_b")
SETS = {"original": "ob_m1_structure_nv1_original.set", "baseline_a": "ob_m1_structure_nv1_baseline_a.set",
        "baseline_b": "ob_m1_structure_nv1_baseline_b.set", "candidate": "ob_m1_structure_nv1_candidate.set",
        "fallback": "ob_m1_structure_nv1_fallback.set"}
BRANCH = "feat/new-test-robust-optimization"


def _rel(p) -> str:
    return pathlib.Path(p).resolve().relative_to(REPO).as_posix()


def _dest(path: pathlib.Path) -> str:
    """curate/conformance destination relative to results/."""
    return pathlib.Path(path).resolve().relative_to(REPO / "results").as_posix()


def save(obj, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    evaluate.save(obj, path)


# ------------------------------------------------------------------ guards (U2 step 3)
def start(pre_freeze: bool) -> tuple:
    """Common entry: own experiment log, config, installed-EA check; then the stage guard. Returns (cfg, P)."""
    explog.LOG = nv.LOG
    if pre_freeze:
        if nv.PREREG_PATH.exists():
            raise SystemExit(f"{nv.PREREG_REL} exists: pre-freeze commands run only before the freeze (U2)")
        old = json.loads((REPO / nv.OLD_PREREG_REL).read_text(encoding="utf-8"))
        if old.get("ea_source_sha256") != cli.ea_sha():
            raise SystemExit("the EA source differs from the registered ea_source_sha256; numeric_v1 runs the "
                             "unchanged EA (R3)")
        P = None
    else:
        P = prereg_committed()
    cfg = env.load_config()
    cli.installed_ea_matches(cfg)
    return cfg, P


def prereg_committed() -> dict:
    """R16/U6: the numeric_v1 pre-registration is committed, unchanged, pushed, and registers this EA source."""
    if not cli.committed(nv.PREREG_REL):
        raise SystemExit(f"{nv.PREREG_REL} is missing or has uncommitted changes; refusing to run")
    subprocess.run(["git", "fetch", "-q", "origin", BRANCH], cwd=REPO, capture_output=True, text=True)
    pushed = subprocess.run(["git", "diff", "--quiet", f"origin/{BRANCH}", "--", nv.PREREG_REL], cwd=REPO)
    present = subprocess.run(["git", "cat-file", "-e", f"origin/{BRANCH}:{nv.PREREG_REL}"], cwd=REPO)
    if pushed.returncode != 0 or present.returncode != 0:
        raise SystemExit(f"{nv.PREREG_REL} is not pushed to origin/{BRANCH}; push it before any research run (R16)")
    P = nv.load_prereg()
    if P.get("ea_source_sha256") != cli.ea_sha():
        raise SystemExit("the EA source differs from the registered ea_source_sha256; refusing to run (R3)")
    return P


def oos_run(cfg, name: str, start: str, end: str, deposit: float, params: dict, role: str, purpose: str,
            dest: pathlib.Path) -> dict:
    """One research-build single run with its record (inputs loaded, conformance, drawdowns), reused on resume."""
    rid = nv.check_run_id(nv.run_id(name))
    nv.check_window(start, end)
    rec_path = dest / rid / "record.json"
    if rec_path.exists():
        return json.loads(rec_path.read_text())
    values = {**nv.FIXED_INPUTS, **params}
    rec = cli.oos_record(cfg, rid, start, end, deposit, values, role, purpose, _dest(dest))
    rec["params"] = {k: int(params[k]) for k in nv.AXES}
    save(rec, rec_path)
    return rec


# ------------------------------------------------------------------ U3: one train window
def run_window(cfg, tag: str, start: str, end: str, out_dir: pathlib.Path, purpose: str) -> dict:
    """The window's split optimizations, their verification, the merged 18-row grid and the selection record.
    A verified record is reused on resume and its optimization never re-run (a re-run would come from the cache)."""
    nv.check_window(start, end)
    rec_path = out_dir / "window.json"
    if rec_path.exists():
        rec = json.loads(rec_path.read_text())
        if rec.get("status") == "verified":
            return rec
    tables, runs = [], []
    for r in nv.split_runs():
        rid = nv.check_run_id(nv.run_id(f"{tag}_grid_{r['part']}"))
        vals = {**nv.FIXED_INPUTS, **r["fixed"]}
        res, _ = pipeline.run_optimization(cfg, rid, "research", cli.CHART_PERIOD, start, end, vals, r["ranges"],
                                           role="train", purpose=f"{purpose} ({r['part']})")
        v = gridrun.verify_optimization(runner.RUNS / rid, rid, r["expected"], nv.GRID, r["fixed"])
        v.pop("tuples")
        v["status_runner"] = res.status
        runs.append(v)
        if v["status"] != "ok":
            save({"tag": tag, "train": [start, end], "status": "failed", "runs": runs}, rec_path)
            raise SystemExit(f"{rid}: pass verification failed: {v['problems'][:5]} (KTD4)")
        curate.curate(rid, _dest(out_dir))
        tables.append(gridrun.run_table(runner.RUNS / rid, rid, nv.GRID, r["fixed"]))
    grid = gridrun.merge(tables, nv.GRID)
    sel = nv.select(grid)
    sel["scored"].to_csv(out_dir / "scored_grid.csv", index=False)
    rec = {"tag": tag, "train": [start, end], "status": "verified", "runs": runs,
           "expected": nv.n_combos(), "completed": int(sum(x["completed"] for x in runs)),
           "failed": int(sum(x["failed"] for x in runs)), "cached": int(sum(x["cached"] for x in runs)),
           "seconds": round(sum(x["seconds"] or 0 for x in runs), 1), "trade_floor": nv.trade_floor(),
           "selection_status": sel["status"], "label": sel["label"], "params": sel["params"],
           "reason": sel["reason"]}
    save(rec, rec_path)
    save({k: rec[k] for k in ("tag", "train", "trade_floor", "params", "reason", "label")}
         | {"status": sel["status"], "eligible_passes": sel["reason"]["eligible_passes"]}, out_dir / "selection.json")
    explog.append({"id": f"{nv.PREFIX}{tag}_selection", "purpose": "train-only selection (KTD5)", "role": "selection",
                   "status": sel["status"], "notes": json.dumps({"params": sel["params"], "label": sel["label"]})})
    return rec


# ------------------------------------------------------------------ U5: pre-freeze smoke
def cmd_optsmoke(args) -> None:
    """18-pass split optimization on the train-only check window (KTD3, KTD4, KTD11)."""
    cfg, _ = start(pre_freeze=True)
    rec = run_window(cfg, "smoke", *nv.SMOKE_WINDOW, SMOKE / "optsmoke", "U5 optimization smoke")
    out = {k: rec[k] for k in ("train", "expected", "completed", "failed", "cached", "seconds", "runs")}
    out["note"] = "train-only check window; its metrics are not used by the selection rule"
    save(out, SMOKE / "optsmoke.json")
    print(json.dumps({k: out[k] for k in ("expected", "completed", "failed", "cached", "seconds")}, indent=1))


def behaviour_summary(run_id: str, dest: str, params: dict) -> dict:
    """KTD11 evidence of one behaviour run: conformance coverage of the pivot and stop rules, the pivot count, and
    the logged long stop-to-anchor distances (points), which must all equal the run's buffer."""
    conf = cli.conformance_report(run_id, dest)
    run = cli.cm_read(run_id)
    rules = conf["coverage"]["rules"]
    st = run["setups"]
    filled = st[pd.to_numeric(st["fill_price"], errors="coerce").notna()]
    longs = filled[filled["dir"].astype(str) == "L"]
    dist = ((pd.to_numeric(longs["sl_anchor_price"]) - pd.to_numeric(longs["sl"])) / 0.01).round(1)
    return {"run_id": run_id, "params": params, "violations": conf["violations"],
            "pivot_causality_r9": rules.get("pivot_causality_r9"), "sl_r18": rules.get("sl_r18"),
            "pivots": int(len(run["pivots"])), "fills": int(len(filled)), "long_fills": int(len(longs)),
            "long_stop_to_anchor_points": sorted(set(dist.tolist())),
            "logged_buffer_pts": sorted(set(pd.to_numeric(filled["buffer_pts"], errors="coerce").dropna().tolist()))}


def cmd_behaviour(args) -> None:
    """Five research-build runs on the train-only window: N and the buffer must change behaviour (R9, KTD11)."""
    cfg, _ = start(pre_freeze=True)
    dest = SMOKE / "behaviour"
    rows, pivot_sets = [], {}
    for p in nv.BEHAVIOUR_RUNS:
        name = "behaviour_" + "_".join(str(v) for v in nv.tuple_of(p))
        rec = oos_run(cfg, name, *nv.SMOKE_WINDOW, nv.DEPOSIT, p, "behaviour", "U5 behaviour check (KTD11)", dest)
        rid = rec["run_id"]
        s = behaviour_summary(rid, _dest(dest), p)
        s["input_mismatches"] = rec["input_mismatches"]
        piv = cli.cm_read(rid)["pivots"]
        pivot_sets[nv.tuple_of(p)] = set(map(tuple, piv[["type", "peak_time"]].astype(str).to_numpy()))
        rows.append(s)
    n_sets = {n: pivot_sets[(0, n, 20)] for n in (2, 3, 4)}
    out = {"window": nv.SMOKE_WINDOW, "runs": rows,
           "pivot_sets_differ_across_N": len({frozenset(v) for v in n_sets.values()}) == 3,
           "pivot_counts_by_N": {n: len(v) for n, v in n_sets.items()},
           "all_zero_violations": all(r["violations"] == 0 for r in rows),
           "rules_checked_in_every_run": all((r["pivot_causality_r9"] or {}).get("checked", 0) > 0
                                             and (r["sl_r18"] or {}).get("checked", 0) > 0 for r in rows),
           "stop_distance_equals_buffer": all(r["long_stop_to_anchor_points"] == [float(r["params"]["StopBufferPoints"])]
                                              for r in rows if r["long_fills"]),
           "all_inputs_loaded": all(not r["input_mismatches"] for r in rows),
           "note": "train-only check window; its metrics are not used by the selection rule"}
    save(out, SMOKE / "behaviour.json")
    print(json.dumps({k: out[k] for k in ("pivot_sets_differ_across_N", "pivot_counts_by_N", "all_zero_violations",
                                          "rules_checked_in_every_run", "stop_distance_equals_buffer",
                                          "all_inputs_loaded")}, indent=1))


# ------------------------------------------------------------------ U6: freeze
def cmd_freeze_rules(args) -> None:
    if nv.PREREG_PATH.exists():
        raise SystemExit(f"{nv.PREREG_REL} exists; the protocol is frozen once (R16)")
    explog.LOG = nv.LOG
    old = json.loads((REPO / nv.OLD_PREREG_REL).read_text(encoding="utf-8"))
    files = [SMOKE / "optsmoke.json", SMOKE / "behaviour.json"]
    missing = [_rel(f) for f in files if not f.exists()]
    if missing:
        raise SystemExit(f"pre-freeze evidence missing: {missing}; run optsmoke-nv1 and behaviour-nv1 first (U5)")
    evidence = {_rel(f): cli.sha(f) for f in files}
    p = nv.build_prereg(cli.ea_sha(), old, evidence)
    nv.PREREG_PATH.write_text(json.dumps(p, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {nv.PREREG_REL}; commit and push it before `wfo-nv1` (R16)")


# ------------------------------------------------------------------ U7: walk-forward
def cmd_wfo(args) -> None:
    cfg, P = start(pre_freeze=False)
    done = json.loads(FOLDS_JSON.read_text()) if FOLDS_JSON.exists() else []
    deposits = {w: float(P["deposit"]) for w in SERIES}
    for rec in done:
        deposits = rec["next_deposits"]
    for fold in P["folds"][len(done):]:
        k = fold["fold"]
        win = run_window(cfg, f"f{k}", *fold["train"], WFO / f"f{k}_selection", f"fold {k} train grid")
        params = {"procedure": win["params"], **{b: nv.BASELINES[b] for b in ("baseline_a", "baseline_b")}}
        rec = {"fold": k, "train": fold["train"], "test": fold["test"], "window": win, "deposits": dict(deposits),
               "oos": {}}
        for w in SERIES:
            role = ("oos_procedure_fallback" if w == "procedure" and win["label"] == "fallback" else f"oos_{w}")
            rec["oos"][w] = oos_run(cfg, f"f{k}_oos_{w}", *fold["test"], deposits[w], params[w], role,
                                    f"fold {k} OOS {w} ({nv.series_name(params[w])})", WFO)
        deposits = {w: rec["oos"][w]["final_balance"] for w in SERIES}
        rec["next_deposits"] = deposits
        done.append(rec)
        save(done, FOLDS_JSON)
        print(f"fold {k}: {win['selection_status']} {win['params']} "
              f"{win['completed']}/{win['expected']} passes, {win['seconds']}s", flush=True)
    final = run_window(cfg, "final", *P["final_train"], FINAL_DIR, "final selection (May-July)")
    print(f"final: {final['selection_status']} {final['params']}", flush=True)
    window_table()


def window_table() -> pd.DataFrame:
    """R28: per train window, expected/completed/failed/cached, seconds, eligible passes and the selection."""
    rows = []
    folds = json.loads(FOLDS_JSON.read_text()) if FOLDS_JSON.exists() else []
    recs = [(f"fold {f['fold']}", f["window"]) for f in folds]
    if (FINAL_DIR / "window.json").exists():
        recs.append(("final", json.loads((FINAL_DIR / "window.json").read_text())))
    for name, w in recs:
        rows.append({"window": name, "train": f"{w['train'][0]}-{w['train'][1]}", "expected": w["expected"],
                     "completed": w["completed"], "failed": w["failed"], "cached": w["cached"],
                     "seconds": w["seconds"], "eligible": w["reason"]["eligible_passes"],
                     "status": w["selection_status"], "label": w["label"],
                     "params": "/".join(str(w["params"][a]) for a in nv.AXES), "reason": w["reason"]["note"]})
    df = pd.DataFrame(rows)
    if len(df):
        df.to_csv(WFO / "windows.csv", index=False)
    return df


# ------------------------------------------------------------------ U8: freeze the .set files
def tested_values(params: dict) -> dict:
    vals = pipeline.base_values("delivered")
    vals.update(nv.FIXED_INPUTS)
    vals.update({k: int(params[k]) for k in nv.AXES})
    return vals


def cmd_freeze(args) -> None:
    prereg_committed()
    explog.LOG = nv.LOG
    final = json.loads((FINAL_DIR / "window.json").read_text())
    kind = "candidate" if final["selection_status"] == "selected" else "fallback"
    names = [SETS["original"], SETS["baseline_a"], SETS["baseline_b"], SETS[kind]]
    for n in names:
        if cli.committed(_rel(nv.DELIV / n)):
            raise SystemExit(f"{n} is already frozen and committed (U8)")
    nv.DELIV.mkdir(parents=True, exist_ok=True)
    spec = pipeline.specs("delivered")
    setfile.write_set(nv.DELIV / SETS["original"], setfile.render_lines(spec),
                      header="M5 OB + M1 structure, original parameters: code defaults of ob_m1_structure.mq5")
    for b in ("baseline_a", "baseline_b"):
        setfile.write_set(nv.DELIV / SETS[b], setfile.render_lines(spec, tested_values(nv.BASELINES[b])),
                          header=f"numeric_v1 fixed {b} {nv.tuple_of(nv.BASELINES[b])} as tested")
    t = nv.tuple_of(final["params"])
    head = (f"numeric_v1 candidate {t}: final train selection {final['train'][0]}-{final['train'][1]}\n"
            "NOT VALIDATED: no independent period exists (R24)" if kind == "candidate" else
            f"numeric_v1 FALLBACK {t}: no train pass met the thresholds (no_eligible_pass); this is the default "
            "carried forward, not a selected or improved candidate (KTD6)")
    setfile.write_set(nv.DELIV / SETS[kind], setfile.render_lines(spec, tested_values(final["params"])), header=head)
    rec = {"ea_sha256": cli.ea_sha(), "prereg_sha256": cli.sha(nv.PREREG_PATH), "final": final, "kind": kind,
           "sets": {n: cli.sha(nv.DELIV / n) for n in names}}
    save(rec, FINAL_DIR / "freeze.json")
    print(f"frozen {kind} {t}; commit and push deliverables/numeric_v1 and results/numeric_v1/final_selection")


# ------------------------------------------------------------------ U9: robustness and acceptance
def stitch(run_ids: list, root: pathlib.Path) -> dict:
    return evaluate.stitch([evaluate.load_run(r, root) for r in run_ids])


def series_view(s: dict, start: str, end: str, tester_dd: list) -> dict:
    tab = s["table"]
    d = evaluate.drawdowns(s)
    return {"net": round(float(s["net_profit"]), 2), "fills": int(len(s["trades"])),
            "fills_per_month": evaluate.fills_per_month(len(s["trades"]), start, end),
            "win_rate": round(float((tab["net"] > 0).mean()), 4) if len(tab) else 0.0,
            "dd_tester_equity_max_pct": max(x[0] for x in tester_dd), "dd_tester_balance_max_pct": max(x[1] for x in tester_dd),
            "dd_daily_records_pct": d["equity_dd_pct"], "dd_closed_trades_pct": d["balance_dd_pct"]}


def cmd_robustness(args) -> None:
    cfg, P = start(pre_freeze=False)
    freeze = json.loads((FINAL_DIR / "freeze.json").read_text())
    kind = freeze["kind"]
    if not cli.committed(_rel(nv.DELIV / SETS[kind])):
        raise SystemExit("the final .set is not frozen and committed; run freeze-nv1, commit and push first (U8)")
    folds = json.loads(FOLDS_JSON.read_text())
    cand = {k: int(v) for k, v in freeze["final"]["params"].items()}
    win = [P["folds"][0]["test"][0], P["folds"][-1]["test"][1]]

    # KTD8: the static candidate or fallback and its grid neighbours, March-July; never read by selection
    rows = []
    for i, p in enumerate([cand] + nv.neighbours(cand)):
        name = "static_" + nv.series_name(p) if i == 0 else f"neighbour_{i}"
        r = oos_run(cfg, name, *nv.STABILITY_WINDOW, nv.DEPOSIT, p, "static" if i == 0 else "neighbour",
                    "KTD8 neighbour stability (never used for selection)", ROBUST)
        rows.append({"run_id": r["run_id"], "params": nv.tuple_of(p), "net": r["net_profit"], "fills": r["trades"],
                     "tester_equity_dd_pct": r["equity_dd_pct"], "tester_balance_dd_pct": r["balance_dd_pct"],
                     "conformance_violations": r["conformance_violations"], "input_mismatches": r["input_mismatches"]})
    share = float(np.mean([r["net"] > 0 for r in rows[1:]])) if len(rows) > 1 else float("nan")

    ids = {w: [f["oos"][w]["run_id"] for f in folds] for w in SERIES}
    series = {w: stitch(ids[w], WFO) for w in SERIES}
    tdd = {w: [(f["oos"][w]["equity_dd_pct"], f["oos"][w]["balance_dd_pct"]) for f in folds] for w in SERIES}

    def fold_net(w, grp=None):
        return [{"fold": f["fold"], "net": f["oos"][w]["net_profit"]} for f in folds if grp is None or f["fold"] in grp]

    def pick(w, grp):
        return [f["oos"][w]["run_id"] for f in folds if f["fold"] in grp]

    def span(grp):
        return [P["folds"][grp[0] - 1]["test"][0], P["folds"][grp[-1] - 1]["test"][1]]

    grids = sorted(WFO.glob("f*_selection/scored_grid.csv")) + [FINAL_DIR / "scored_grid.csv"]
    tv = evaluate.trial_sharpe_variance(grids)
    bases = {b: series[b] for b in ("baseline_a", "baseline_b")}
    acc = {"procedure": evaluate.evaluate(series["procedure"], None, fold_net("procedure"), share, tv["var_sr"], P,
                                          bases=bases),
           "baseline_a": evaluate.evaluate(series["baseline_a"], None, fold_net("baseline_a"), None, tv["var_sr"], P),
           "baseline_b": evaluate.evaluate(series["baseline_b"], None, fold_net("baseline_b"), None, tv["var_sr"], P,
                                           bases={"baseline_a": series["baseline_a"]})}
    G, R = nv.GROUPS["G"], nv.GROUPS["R"]
    group_r = {w: stitch(pick(w, R), WFO) for w in SERIES}
    acc_r = evaluate.evaluate(group_r["procedure"], None, fold_net("procedure", R), share, tv["var_sr"], P,
                              window=span(R), bases={b: group_r[b] for b in ("baseline_a", "baseline_b")})
    groups = {w: {"all": series_view(series[w], *win, tdd[w]),
                  "G": series_view(stitch(pick(w, G), WFO), *span(G), [t for f, t in zip(folds, tdd[w]) if f["fold"] in G]),
                  "R": series_view(group_r[w], *span(R), [t for f, t in zip(folds, tdd[w]) if f["fold"] in R])}
              for w in SERIES}
    S = P["stats"]
    bm = metrics.bar_minutes(P["acceptance"]["event_bar"])
    shuffle = {}
    for w in SERIES:
        ev = metrics.trade_events(series[w]["trades"], bm) if len(series[w]["trades"]) else pd.DataFrame({"ret": []})
        mc = (montecarlo.shuffle_paths(ev["ret"].to_numpy(), n_paths=S["mc_paths"], seed=S["seed"],
                                       initial=series[w]["initial"]) if len(ev) else {})
        shuffle[w] = {k: v for k, v in mc.items() if np.ndim(v) == 0}
    tag = nv.check_run_id(nv.run_id("session_probe_wfo"))
    sens = cli.session_sensitivity(tag, [r for w in SERIES for r in ids[w]], {w: ids[w] for w in SERIES}, win,
                                   out_root=ROBUST)
    fold_rows = [{"fold": f["fold"], "test": f["test"], "label": f["window"]["label"],
                  "selected": f["window"]["params"], "status": f["window"]["selection_status"],
                  **{f"{w}_params": "/".join(str(f["oos"][w]["params"][a]) for a in nv.AXES) for w in SERIES},
                  **{f"{w}_net": f["oos"][w]["net_profit"] for w in SERIES},
                  **{f"{w}_fills": f["oos"][w]["trades"] for w in SERIES},
                  **{f"{w}_tester_equity_dd_pct": f["oos"][w]["equity_dd_pct"] for w in SERIES},
                  **{f"{w}_tester_balance_dd_pct": f["oos"][w]["balance_dd_pct"] for w in SERIES},
                  **{f"{w}_conformance_violations": f["oos"][w]["conformance_violations"] for w in SERIES},
                  **{f"{w}_input_mismatches": len(f["oos"][w]["input_mismatches"]) for w in SERIES}} for f in folds]
    out = {"study": nv.STUDY, "final": {"params": cand, "kind": kind, "series": nv.series_name(cand)},
           "acceptance": acc, "acceptance_group_R_report_only": acc_r,
           "passed_all": acc["procedure"]["passed_all"], "recommended": False, "fold_rows": fold_rows,
           "groups": groups, "stability": {"profitable_share": share, "rows": rows, "window": nv.STABILITY_WINDOW},
           "var_sr": tv["var_sr"], "var_sr_source": tv["source"], "var_sr_passes": tv["passes"],
           "var_sr_grids": tv["grids"], "shuffle_mc": shuffle, "session_sensitivity": sens["per_group"]}
    save(out, nv.RESULTS / "acceptance.json")
    print(json.dumps({"passed_all": out["passed_all"], "recommended": False}, indent=1))


# ------------------------------------------------------------------ U10: delivery validation and report
def _deals_signature(rep: dict) -> list:
    d = rep.get("deals")
    if d is None or len(d) == 0:
        return []
    d = d.copy()
    keep = [c for c in d.columns if str(c).lower() not in ("deal", "order", "comment")]
    return [tuple(str(x) for x in row) for row in d[keep].itertuples(index=False, name=None)]


def cmd_deliver(args) -> None:
    cfg, P = start(pre_freeze=False)
    freeze = json.loads((FINAL_DIR / "freeze.json").read_text())
    kind = freeze["kind"]
    folds = json.loads(FOLDS_JSON.read_text())
    cand = {k: int(v) for k, v in freeze["final"]["params"].items()}
    f1 = folds[0]
    twins = {nv.tuple_of(f1["oos"][w]["params"]): f1["oos"][w]["run_id"] for w in SERIES}
    ex5 = pipeline.BUILDS["delivered"][0]
    s, e = nv.VALIDATION_WINDOW
    nv.check_window(s, e)
    checks = {}
    for key, params in (("original", nv.DEFAULTS), ("baseline_a", nv.BASELINES["baseline_a"]),
                        ("baseline_b", nv.BASELINES["baseline_b"]), (kind, cand)):
        name = SETS[key]
        rid = nv.check_run_id(nv.run_id("validate_" + key))
        text = pipeline._ini(ex5, cli.CHART_PERIOD, s, e, nv.DEPOSIT, rid,
                             cli.deliver.set_file_lines(nv.DELIV / name))
        res = runner.run(cfg, rid, text, ex5, meta={"role": "set_validation", "set": name})
        rep = reports.parse_html(res.report) if res.report else None
        mism = pipeline.check_inputs_loaded(rep, setfile.read_set(nv.DELIV / name)) if rep else ["no report"]
        t = nv.tuple_of(params)
        twin = twins.get(t)
        if twin is None:
            twin = oos_run(cfg, "twin_" + key, s, e, nv.DEPOSIT, params, "twin", "KTD13 research-build twin",
                           nv.RESULTS / "set_validation")["run_id"]
            twin_dir = nv.RESULTS / "set_validation"
        else:
            twin_dir = WFO
        twin_rep = reports.parse_html(next((twin_dir / twin).glob(f"{twin}.htm")))
        same = rep is not None and _deals_signature(rep) == _deals_signature(twin_rep)
        checks[name] = {"run_id": rid, "params": t, "mismatches": mism, "status": res.status,
                        "research_twin": twin, "same_deals_as_research_build": same,
                        "delivered_net": reports.summary(rep)["net_profit"] if rep else None,
                        "research_net": reports.summary(twin_rep)["net_profit"]}
        explog.append({"id": rid, "purpose": "KTD13 .set validation", "role": "set_validation", "expert": ex5,
                       "status": "ok" if rep and not mism and same else "mismatch", "has_report": rep is not None})
        curate.curate(rid, _dest(nv.RESULTS / "set_validation"))
    save(checks, nv.RESULTS / "set_validation.json")
    if any(nv.DELIV.glob("*recommended*")):
        raise SystemExit("a recommended .set must not exist (R24)")
    print(json.dumps({k: {"mismatches": len(v["mismatches"]), "same_deals": v["same_deals_as_research_build"]}
                      for k, v in checks.items()}, indent=1))


COMMANDS = {"optsmoke-nv1": cmd_optsmoke, "behaviour-nv1": cmd_behaviour, "freeze-rules-nv1": cmd_freeze_rules,
            "wfo-nv1": cmd_wfo, "freeze-nv1": cmd_freeze, "robustness-nv1": cmd_robustness,
            "deliver-nv1": cmd_deliver}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in COMMANDS:
        sub.add_parser(name)
    args = ap.parse_args()
    COMMANDS[args.cmd](args)


if __name__ == "__main__":
    main()
