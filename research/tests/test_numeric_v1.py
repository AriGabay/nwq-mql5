"""numeric_v1 study: grid, split ranges, selection, neighbours, series identity, guards, pre-registration and the
pass verification against tester output (plan 2026-10-04-1851, U2-U4)."""
import json
import pathlib

import pandas as pd
import pytest

from mt5r import explog, gridrun, numeric_v1 as nv, pipeline, reports, wfo

REPO = pathlib.Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ grid and split ranges (R5, R7, KTD3)
def test_grid_has_18_tuples_and_no_buffer_30():
    tuples = [nv.tuple_of(c) for c in nv.combos()]
    assert len(tuples) == len(set(tuples)) == 18
    assert all(t[2] in (10, 20, 40) for t in tuples)


def test_defaults_and_baselines_are_in_the_grid_and_defaults_equal_baseline_a():
    tuples = {nv.tuple_of(c) for c in nv.combos()}
    assert nv.tuple_of(nv.DEFAULTS) in tuples
    assert all(nv.tuple_of(b) in tuples for b in nv.BASELINES.values())
    assert nv.tuple_of(nv.DEFAULTS) == nv.tuple_of(nv.BASELINES["baseline_a"]) == (0, 3, 20)
    assert nv.tuple_of(nv.BASELINES["baseline_b"]) == (1, 3, 20)


def test_split_runs_cover_the_buffer_axis_with_lo_10_to_20_and_hi_fixed_40():
    runs = {r["part"]: r for r in nv.split_runs()}
    assert set(runs) == {"lo", "hi"}
    assert runs["lo"]["ranges"]["StopBufferPoints"] == (10, 10, 20)
    assert runs["lo"]["expected"] == 12 and runs["hi"]["expected"] == 6
    assert runs["hi"]["fixed"] == {"StopBufferPoints": 40}
    assert runs["hi"]["ranges"] == {"StructureVariant": (0, 1, 1), "SwingStrengthM1": (2, 1, 4)}
    # expanding the ranges gives each grid tuple exactly once, and never 30
    seen = []
    for r in runs.values():
        axes = {k: list(range(a, c + 1, b)) for k, (a, b, c) in r["ranges"].items()}
        axes.update({k: [v] for k, v in r["fixed"].items()})
        seen += [nv.tuple_of(c) for c in nv.combos({k: axes[k] for k in nv.AXES})]
    assert sorted(seen) == sorted(nv.tuple_of(c) for c in nv.combos())


# ------------------------------------------------------------------ series identity and neighbours (KTD7, KTD8)
def test_candidate_with_variant_0_is_not_baseline_a():                                 # Covers AE3
    assert nv.series_name({"StructureVariant": 0, "SwingStrengthM1": 2, "StopBufferPoints": 40}) == "candidate_0_2_40"
    assert nv.series_name({"StructureVariant": 0, "SwingStrengthM1": 3, "StopBufferPoints": 20}) == "baseline_a"
    assert nv.series_name({"StructureVariant": 1, "SwingStrengthM1": 3, "StopBufferPoints": 20}) == "baseline_b"


def test_neighbours_at_the_grid_corner_are_two_and_never_the_candidate():               # Covers AE4
    got = {nv.tuple_of(n) for n in nv.neighbours({"StructureVariant": 1, "SwingStrengthM1": 2, "StopBufferPoints": 10})}
    assert got == {(1, 3, 10), (1, 2, 20)}


def test_neighbours_of_the_defaults_are_four():                                         # Covers AE5
    got = {nv.tuple_of(n) for n in nv.neighbours(nv.DEFAULTS)}
    assert got == {(0, 2, 20), (0, 4, 20), (0, 3, 10), (0, 3, 40)}
    assert (0, 3, 20) not in got


def test_buffer_40_neighbour_steps_to_20_not_30():
    got = {nv.tuple_of(n) for n in nv.neighbours({"StructureVariant": 0, "SwingStrengthM1": 3, "StopBufferPoints": 40})}
    assert got == {(0, 2, 40), (0, 4, 40), (0, 3, 20)}


# ------------------------------------------------------------------ selection (KTD5, KTD6)
def grid_df(eligible=None, scores=None):
    """18 passes; `eligible` maps tuples to (profit, eq_dd_pct); all others fail the DD limit."""
    eligible = eligible or {}
    rows = []
    for c in nv.combos():
        t = nv.tuple_of(c)
        profit, dd = eligible.get(t, (-100.0, 30.0))
        rows.append({**c, "profit": profit, "trades": 200, "eq_dd_pct": dd, "recovery_factor": profit / 500.0,
                     "custom": 0.0, "sharpe": 0.0, "eq_dd_money": 500.0})
    return pd.DataFrame(rows)


def test_single_eligible_pass_is_selected_with_its_score_as_reason():
    sel = nv.select(grid_df({(1, 4, 40): (900.0, 8.0)}))
    assert sel["status"] == "selected" and sel["label"] == "selected"
    assert nv.tuple_of(sel["params"]) == (1, 4, 40)
    assert sel["reason"]["eligible_passes"] == 1
    # smoothed with its existing neighbours (1,3,40) and (1,4,20), which score 0
    assert sel["reason"]["smoothed_score"] == pytest.approx(900.0 / 500.0 / 3)


def test_tie_breaks_on_distance_to_defaults_then_lowest_indices():
    df = grid_df({(0, 2, 10): (600.0, 5.0), (0, 4, 40): (600.0, 5.0)})
    # both are grid corners with two neighbours each, so their smoothed scores are equal
    sel = nv.select(df)
    # (0,2,10) is 2 index steps from (0,3,20); (0,4,40) is also 2: equal distance -> lowest indices -> (0,2,10)
    assert nv.tuple_of(sel["params"]) == (0, 2, 10)
    assert sel["reason"]["tied_at_top"] == 2


def test_no_eligible_pass_is_a_labelled_fallback_with_threshold_counts():                # Covers AE2
    sel = nv.select(grid_df())
    assert sel["status"] == "no_eligible_pass" and sel["label"] == "fallback"
    assert nv.tuple_of(sel["params"]) == (0, 3, 20)
    assert sel["reason"]["eligible_passes"] == 0 and sel["reason"]["above_dd_limit"] == 18
    assert "fallback" in sel["reason"]["note"]


def test_smoothing_never_mixes_variants():
    sc = wfo.score(grid_df({(0, 2, 10): (500.0, 5.0)}), nv.AXES, 45, 10.0, categorical=nv.CATEGORICAL)
    row = sc[(sc.StructureVariant == 1) & (sc.SwingStrengthM1 == 2) & (sc.StopBufferPoints == 10)]
    assert float(row["smoothed"].iloc[0]) == 0.0


# ------------------------------------------------------------------ guards (KTD2, KTD12)
def test_window_guard_refuses_august_overlap_in_dot_format():
    with pytest.raises(SystemExit):
        nv.check_window("2026.07.20", "2026.08.05")
    nv.check_window("2026.07.01", "2026.07.31")
    nv.check_window(*nv.SMOKE_WINDOW)


def test_run_ids_and_probe_tags_carry_the_prefix():
    assert nv.run_id("f3_grid_lo") == "nv1_f3_grid_lo"
    assert nv.run_id("session_probe_wfo") == "nv1_session_probe_wfo"
    assert nv.run_id("nv1_session_probe_wfo") == "nv1_session_probe_wfo"
    with pytest.raises(ValueError):
        nv.run_id("../wfo")


def test_explog_writes_to_the_given_or_current_module_log(tmp_path, monkeypatch):
    study, old = tmp_path / "study.jsonl", tmp_path / "old.jsonl"
    monkeypatch.setattr(explog, "LOG", old)
    explog.append({"id": "a"})
    explog.append({"id": "b"}, study)
    assert [e["id"] for e in explog.read(old)] == ["a"]
    assert [e["id"] for e in explog.read(study)] == ["b"]
    monkeypatch.setattr(explog, "LOG", study)
    explog.append({"id": "c"})
    assert [e["id"] for e in explog.read(study)] == ["b", "c"]


def test_old_cli_defaults_still_point_at_the_old_results():
    import inspect
    import cli
    assert cli.ROBUST == REPO / "results" / "robustness"
    assert inspect.signature(cli.session_sensitivity).parameters["out_root"].default is None
    assert explog.LOG == REPO / "results" / "experiment_log.jsonl"


# ------------------------------------------------------------------ pre-registration (R16, KTD9)
def test_prereg_records_grid_windows_budget_and_dsr_arithmetic():
    prev = json.loads((REPO / "research" / "preregistration.json").read_text(encoding="utf-8"))
    p = nv.build_prereg("abc", prev, {"optsmoke": "x"})
    assert p["grid"] == nv.GRID and p["grid_combinations"] == 18
    assert p["fixed_inputs"] == {"ImpulseWindowBars": 2, "RiskRR": 2.0, "RiskPercent": 1.0, "MaxExposures": 3,
                                 "WarmupDays": 30}
    assert len(p["folds"]) == 5 and p["final_train"] == ["2026.05.01", "2026.07.31"]
    assert p["excluded_window"] == ["2026.08.01", "2026.09.29"]
    assert p["run_budget"]["train_passes"] == 108 and p["run_budget"]["train_optimization_runs"] == 12
    s = p["stats"]
    assert s["dsr_trials"] == 126 and sum(s["dsr_trials_components"].values()) == 126
    assert s["dsr_trials_sensitivity"] == 18
    assert p["acceptance"]["min_stability_profitable_share"] == prev["acceptance"]["min_stability_profitable_share"]
    assert "baseline_a AND" in p["acceptance"]["net_vs_baseline"]
    assert p["ea_source_sha256"] == "abc" and p["pre_freeze_evidence"] == {"optsmoke": "x"}


def test_prereg_builder_does_not_touch_the_old_protocol():
    before = (REPO / "research" / "preregistration.json").read_bytes()
    nv.build_prereg("abc", json.loads(before.decode("utf-8")), {})
    assert (REPO / "research" / "preregistration.json").read_bytes() == before
    assert nv.PREREG_PATH != pipeline.PREREG_PATH


# ------------------------------------------------------------------ pass verification (KTD4, AE1)
LOG_HEAD = "﻿DN\t0\t15:19:16.854\tTester\tLocal network farm switched off\n"


def _write_log(d: pathlib.Path, sections: list) -> None:
    (d / "logs").mkdir(parents=True, exist_ok=True)
    text = LOG_HEAD
    for total, new in sections:
        text += f"OH\t0\t15:19:19.205\tTester\t{gridrun.START_MARK}\n"
        text += f"RJ\t0\t15:19:46.333\tTester\toptimization finished, total passes {total}\n"
        if new is not None:
            text += f"LS\t0\t15:19:46.354\tTester\t{new} new records saved to cache file 'x.opt'\n"
    (d / "logs" / "Tester__logs__20261005.log").write_bytes(text.encode("utf-16-le"))


def _frame_line(p: int, sv: int, n: int, b: int) -> str:
    inputs = (f"StructureVariant={sv};ImpulseWindowBars=2;SwingStrengthM1={n};StopBufferPoints={b};RiskRR=2;"
              f"RiskPercent=1;MaxExposures=3;WarmupDays=30;MagicNumber=770201;TradeComment=OBM1;ResearchRunTag=x")
    return f"P,{p},0.01,0.00,0.00,1.00,100,{inputs}\n"


def _xml(d: pathlib.Path, run_id: str, rows: list, with_buffer: bool) -> None:
    head = ["Pass", "Result", "Profit", "Expected Payoff", "Profit Factor", "Recovery Factor", "Sharpe Ratio",
            "Custom", "Equity DD %", "Trades", "StructureVariant", "SwingStrengthM1"] + (
        ["StopBufferPoints"] if with_buffer else [])
    cell = lambda v: f"<Cell><Data>{v}</Data></Cell>"
    body = "".join("<Row>" + "".join(cell(v) for v in r) + "</Row>" for r in [head] + rows)
    (d / f"{run_id}.xml").write_text('<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:'
                                     f'spreadsheet"><Worksheet><Table>{body}</Table></Worksheet></Workbook>')


def make_run(tmp_path, part, total=None, new="same", drop=0, bad=None, xml_mismatch=False):
    r = {x["part"]: x for x in nv.split_runs()}[part]
    run_id = f"nv1_t_grid_{part}"
    d = tmp_path / run_id
    d.mkdir()
    sv_n_b = []
    for sv in (0, 1):
        for n in (2, 3, 4):
            for b in ((10, 20) if part == "lo" else (40,)):
                sv_n_b.append((sv, n, b))
    sv_n_b = sv_n_b[:len(sv_n_b) - drop]
    frames = "kind,pass,a,b,c,d,e,f,g,h\n"
    rows = []
    for p, (sv, n, b) in enumerate(sv_n_b):
        frames += _frame_line(p, sv, n, bad if (bad and p == 0) else b)
        xn = n + 1 if (xml_mismatch and p == 0) else n
        rows.append([p, 0.1, 10 * p, 1, 1.1, 0.5, 0.2, 0.01, 5.0, 100, sv, xn] + ([b] if part == "lo" else []))
    (d / f"rl_frames_{run_id}.csv").write_text(frames)
    _xml(d, run_id, rows, with_buffer=(part == "lo"))
    n_done = len(sv_n_b)
    _write_log(d, [(total if total is not None else n_done, n_done if new == "same" else new)])
    (d / "manifest.json").write_text(json.dumps({"seconds": 30.0, "exit_code": 0}))
    return d, run_id, r


def test_lo_and_hi_runs_verify_and_merge_into_18_unique_tuples(tmp_path):              # Covers AE1
    tables = []
    for part in ("lo", "hi"):
        d, rid, r = make_run(tmp_path, part)
        rec = gridrun.verify_optimization(d, rid, r["expected"], nv.GRID, r["fixed"])
        assert rec["status"] == "ok", rec["problems"]
        assert (rec["expected"], rec["completed"], rec["failed"], rec["cached"]) == (r["expected"],) * 2 + (0, 0)
        tables.append(gridrun.run_table(d, rid, nv.GRID, r["fixed"]))
    merged = gridrun.merge(tables, nv.GRID)
    assert len(merged) == 18 and 30 not in set(merged["StopBufferPoints"])
    hi = merged[merged["run_id"].str.endswith("_hi")]
    assert set(hi["StopBufferPoints"]) == {40}


def test_a_missing_frame_fails_with_failed_count(tmp_path):
    d, rid, r = make_run(tmp_path, "lo", drop=1)
    rec = gridrun.verify_optimization(d, rid, r["expected"], nv.GRID, r["fixed"])
    assert rec["status"] == "failed" and rec["completed"] == 11 and rec["failed"] == 1


def test_cached_results_without_new_records_fail(tmp_path):
    d, rid, r = make_run(tmp_path, "lo", new=None)
    rec = gridrun.verify_optimization(d, rid, r["expected"], nv.GRID, r["fixed"])
    assert rec["status"] == "failed" and rec["cached"] == 12
    assert any("cached or partial" in p for p in rec["problems"])


def test_a_pass_with_buffer_30_fails(tmp_path):
    d, rid, r = make_run(tmp_path, "lo", bad=30)
    rec = gridrun.verify_optimization(d, rid, r["expected"], nv.GRID, r["fixed"])
    assert rec["status"] == "failed" and any("StopBufferPoints=30" in p for p in rec["problems"])


def test_xml_and_frames_disagreeing_on_an_input_fail(tmp_path):
    d, rid, r = make_run(tmp_path, "lo", xml_mismatch=True)
    rec = gridrun.verify_optimization(d, rid, r["expected"], nv.GRID, r["fixed"])
    assert rec["status"] == "failed" and any("but frames" in p for p in rec["problems"])


def test_a_duplicated_tuple_across_runs_fails_the_merge(tmp_path):
    d, rid, r = make_run(tmp_path, "lo")
    t = gridrun.run_table(d, rid, nv.GRID, r["fixed"])
    with pytest.raises(ValueError):
        gridrun.merge([t, t], nv.GRID)


def test_the_check_reads_only_this_runs_section_of_the_daily_log(tmp_path):
    d, rid, r = make_run(tmp_path, "hi")
    _write_log(d, [(12, 12), (6, 6)])          # the lo run earlier the same day, then this hi run
    rec = gridrun.verify_optimization(d, rid, r["expected"], nv.GRID, r["fixed"])
    assert rec["status"] == "ok" and rec["log_total_passes"] == 6 and rec["new_cache_records"] == 6


def test_real_previous_grid_run_is_read_by_the_same_parsers():
    """The f1 grid of the previous research (2 passes, StructureVariant only) verifies with its own grid."""
    d = REPO / "runs" / "f1_grid"
    if not d.exists():
        pytest.skip("raw run folder not present (runs/ is not in git)")
    grid = {"StructureVariant": [0, 1], "SwingStrengthM1": [3], "StopBufferPoints": [20]}
    rec = gridrun.verify_optimization(d, "f1_grid", 2, grid, {"SwingStrengthM1": 3, "StopBufferPoints": 20})
    assert rec["status"] == "ok", rec["problems"]
    assert reports.read_frames(d / "rl_frames_f1_grid.csv")[0].shape[0] == 2


# ------------------------------------------------------------------ result provenance (plan 2026-10-04-2133, U2, KTD8)
NEW = {"ob_m1_structure_research.XAUUSD.s.M1.20251201.20260301.40.AB.opt": [100, 1]}


def test_fresh_computation_with_a_new_cache_file_is_computed_and_verifies(tmp_path):
    d, rid, r = make_run(tmp_path, "lo")
    rec = gridrun.verify_optimization(d, rid, r["expected"], nv.GRID, r["fixed"], cache=({}, NEW))
    assert (rec["status"], rec["provenance"], rec["cache_change"]) == ("ok", "computed", "new")


def test_results_served_from_the_cache_are_reused_and_fail(tmp_path):              # Covers AE2
    d, rid, r = make_run(tmp_path, "lo", new=None)
    rec = gridrun.verify_optimization(d, rid, r["expected"], nv.GRID, r["fixed"], cache=(NEW, dict(NEW)))
    assert (rec["status"], rec["provenance"], rec["cache_change"]) == ("failed", "reused", "unchanged")


def test_complete_counts_with_an_unchanged_cache_are_partial_and_fail(tmp_path):
    d, rid, r = make_run(tmp_path, "lo")
    rec = gridrun.verify_optimization(d, rid, r["expected"], nv.GRID, r["fixed"], cache=(NEW, dict(NEW)))
    assert (rec["status"], rec["provenance"]) == ("failed", "partial")
    assert any("provenance partial" in p for p in rec["problems"])


def test_a_grown_cache_file_counts_as_a_cache_change():
    name = next(iter(NEW))
    assert gridrun.cache_change(NEW, {name: [180, 2]}) == "modified"
    assert gridrun.cache_change(NEW, dict(NEW)) == "unchanged"
    assert gridrun.cache_change({}, NEW) == "new"


def test_without_snapshots_verification_is_unchanged_and_the_cache_not_checked(tmp_path):
    d, rid, r = make_run(tmp_path, "lo")
    rec = gridrun.verify_optimization(d, rid, r["expected"], nv.GRID, r["fixed"])
    assert (rec["status"], rec["provenance"], rec["cache_change"]) == ("ok", "computed", "not_checked")


def test_the_snapshot_takes_only_this_windows_research_cache_files(tmp_path):
    cache = tmp_path / "Tester" / "cache"
    cache.mkdir(parents=True)
    names = ["ob_m1_structure_research.XAUUSD.s.M1.20251201.20260301.40.AB.opt",     # this window: end + 1 day
             "ob_m1_structure_research.XAUUSD.s.M1.20251201.20260228.40.CD.opt",     # end date itself: not ours
             "ob_m1_structure.XAUUSD.s.M1.20251201.20260301.40.EF.opt",              # delivered build
             "ob_fvg_retest_research.XAUUSD.s.M15.20251201.20260301.40.GH.opt",      # another EA
             "ob_m1_structure_research.XAUUSD.s.M1.20251201_20260301.4.IJ.tst"]      # a single test, not an .opt
    for n in names:
        (cache / n).write_bytes(b"x")
    snap = gridrun.cache_snapshot(tmp_path, "2025.12.01", "2026.02.28")
    assert list(snap) == [names[0]]
