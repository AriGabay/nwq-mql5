"""trail_cli without the tester: window, run IDs, own log, provenance-checked reuse, attempts, the deal comparison
(plans 2026-10-05-0007 U5 and 2026-10-05-0128 U4, KTD8). No test calls MT5: the tester, the curation and the report
parsing are replaced by fakes that write a run folder."""
import hashlib
import json
import pathlib
import shutil
import types

import pandas as pd
import pytest

import trail_cli as tc
from mt5r import explog

DEALS_CSV = "time,price\nt1,1.0\n"
EX5_RESEARCH = tc.pipeline.BUILDS["research"][0]
EX5_DELIVERED = tc.pipeline.BUILDS["delivered"][0]
ALL_NAMES = [n for n, _, _ in tc.RUNS] + list(tc.TWINS)


def snapshot(d: pathlib.Path) -> dict:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


@pytest.fixture
def study(monkeypatch, tmp_path):
    """The study under tmp_path, a fake isolated install and a fake tester that records each call."""
    results = tmp_path / "results" / "trailing_v2"
    monkeypatch.setattr(tc, "RESULTS", results)
    monkeypatch.setattr(tc, "DELIV", tmp_path / "deliverables" / "trailing_v2")
    monkeypatch.setattr(tc, "LOG", results / "experiment_log.jsonl")
    monkeypatch.setattr(explog, "LOG", tmp_path / "old_log.jsonl")
    monkeypatch.setattr(tc.runner, "RUNS", tmp_path / "runs")
    experts = tmp_path / "mt5" / "MQL5" / "Experts"
    experts.mkdir(parents=True)
    for ex5 in (EX5_RESEARCH, EX5_DELIVERED):
        (experts / ex5).write_bytes(b"ex5 build 1 " + ex5.encode())
    cfg = types.SimpleNamespace(mt5_dir=tmp_path / "mt5")
    monkeypatch.setattr(tc.env, "load_config", lambda: cfg)
    monkeypatch.setattr(tc.cli, "installed_ea_matches", lambda c: None)
    base = {}
    for name in ("off_a", "off_b"):
        d = tmp_path / "baseline" / f"b_{name}"
        d.mkdir(parents=True)
        (d / f"rl_deals_b_{name}.csv").write_text(DEALS_CSV)
        base[name] = d
    monkeypatch.setattr(tc, "BASELINE", base)
    calls = []

    def fake_run(rid, ex5):
        calls.append(rid)
        d = tc.runner.RUNS / rid
        if d.exists():   # the real runner would rmtree it: a test must never get here
            raise AssertionError(f"tester would delete {d}")
        d.mkdir(parents=True)
        sha = hashlib.sha256((experts / ex5).read_bytes()).hexdigest()
        (d / "manifest.json").write_text(json.dumps({"run_id": rid, "ex5": ex5, "ex5_sha256": sha}))
        (d / f"rl_deals_{rid}.csv").write_text(DEALS_CSV)
        return types.SimpleNamespace(status="ok", report=d / "r.htm")

    def run_single(cfg, rid, kind, *a, **k):
        return fake_run(rid, tc.pipeline.BUILDS[kind][0]), {"header": {}}

    monkeypatch.setattr(tc.pipeline, "run_single", run_single)
    monkeypatch.setattr(tc.runner, "run", lambda cfg, rid, text, ex5, **k: fake_run(rid, ex5))
    monkeypatch.setattr(tc.pipeline, "_ini", lambda *a, **k: "ini")
    monkeypatch.setattr(tc.reports, "parse_html", lambda p: {"header": {}})
    monkeypatch.setattr(tc.reports, "summary", lambda rep: {"net_profit": 1.0, "trades": 1, "equity_dd_pct": 0.0})
    monkeypatch.setattr(tc.pipeline, "check_inputs_loaded", lambda rep, exp: [])
    monkeypatch.setattr(tc.curate, "curate",
                        lambda rid, dest: shutil.copytree(tc.runner.RUNS / rid, tc.RESULTS / rid))
    return types.SimpleNamespace(cfg=cfg, calls=calls, results=results, experts=experts, tmp=tmp_path)


def make_folder(study, name: str, rid: str, **override) -> pathlib.Path:
    """A curated folder whose record carries the current provenance, with `override` applied (None drops a field)."""
    rec = {"run_id": rid, "net_profit": 1.0, "trades": 1, "input_mismatches": [],
           **tc.current_provenance(study.cfg, name)}
    for k, v in override.items():
        if v is None:
            rec.pop(k)
        else:
            rec[k] = v
    d = study.results / rid
    d.mkdir(parents=True)
    tc.evaluate.save(rec, d / "record.json")
    (d / f"rl_deals_{rid}.csv").write_text(DEALS_CSV)
    return d


# ------------------------------------------------------------------ study identity
def test_only_march_2026_is_accepted():
    tc.check_window("2026.03.01", "2026.03.31")
    with pytest.raises(SystemExit):
        tc.check_window("2026.08.01", "2026.08.31")


def test_run_ids_carry_tr2_and_the_attempt_suffix_from_attempt_two():
    assert tc.run_id("on_a") == "tr2_on_a"
    assert tc.run_id("on_a", 1) == "tr2_on_a"
    assert tc.run_id("on_a", 2) == "tr2_on_a_a2"
    with pytest.raises(ValueError):
        tc.run_id("../x")


def test_the_study_lives_in_trailing_v2_with_its_own_experiment_log(monkeypatch):
    assert tc.STUDY == "trailing_v2" and tc.PREFIX == "tr2_"
    assert tc.RESULTS == tc.REPO / "results" / "trailing_v2"
    assert tc.LOG == tc.REPO / "results" / "trailing_v2" / "experiment_log.jsonl"
    assert tc.DELIV == tc.REPO / "deliverables" / "trailing_v2"
    monkeypatch.setattr(explog, "LOG", pathlib.Path("old_research_log.jsonl"))
    tc.use_study_log()
    assert explog.LOG == tc.REPO / "results" / "trailing_v2" / "experiment_log.jsonl"


def test_the_source_hash_covers_both_ea_files_with_crlf_read_as_lf():
    src = tc.source_sha()
    experts = tc.REPO / "mql5" / "Experts"
    assert src == {"ob_m1_structure.mq5": tc.cli.sha_source(experts / "ob_m1_structure.mq5"),
                   "ob_m1_structure_research.mq5": tc.cli.sha_source(experts / "ob_m1_structure_research.mq5")}


# ------------------------------------------------------------------ provenance-checked reuse (R15)
def test_a_matching_record_is_reused_without_a_tester_call(study):
    make_folder(study, "on_a", "tr2_on_a", net_profit=7.0)
    make_folder(study, "on_a_delivered", "tr2_on_a_delivered", net_profit=8.0)
    set_path = tc.write_set()
    assert tc.research_run(study.cfg, "on_a", "tr2_on_a")["net_profit"] == 7.0
    assert tc.twin_run(study.cfg, "on_a_delivered", "tr2_on_a_delivered", set_path, "tr2_on_a")["net_profit"] == 8.0
    assert study.calls == []


@pytest.mark.parametrize("field,value", [
    ("source_sha256", {"ob_m1_structure.mq5": "0" * 64, "ob_m1_structure_research.mq5": "0" * 64}),
    ("ex5_sha256", "f" * 64),
    ("inputs", {**tc.BASE_INPUTS, "StructureVariant": 0, "EnableTrailingStop": False}),
    ("window", ["2026.03.01", "2026.03.30"]),
])
def test_each_differing_provenance_field_is_refused_by_name_and_the_folder_is_untouched(study, field, value):
    d = make_folder(study, "on_a", "tr2_on_a", **{field: value})
    before = snapshot(d)
    with pytest.raises(SystemExit, match=field) as e:
        tc.research_run(study.cfg, "on_a", "tr2_on_a")
    others = {"source_sha256", "ex5_sha256", "inputs", "window"} - {field}
    assert not any(o in str(e.value) for o in others)
    assert "tr2_on_a" in str(e.value)
    assert snapshot(d) == before and study.calls == []


def test_a_legacy_record_without_hashes_is_refused(study):
    legacy = {"run_id": "tr2_on_a", "kind": "research", "inputs": tc.inputs(0, True), "net_profit": 1.0}
    d = study.results / "tr2_on_a"
    d.mkdir(parents=True)
    tc.evaluate.save(legacy, d / "record.json")
    before = snapshot(d)
    with pytest.raises(SystemExit) as e:
        tc.research_run(study.cfg, "on_a", "tr2_on_a")
    assert all(f in str(e.value) for f in ("source_sha256", "ex5_sha256", "window"))   # its inputs still match
    assert snapshot(d) == before and study.calls == []


def test_a_folder_without_a_record_is_refused(study):
    d = study.results / "tr2_on_a"
    d.mkdir(parents=True)
    with pytest.raises(SystemExit, match="source_sha256"):
        tc.research_run(study.cfg, "on_a", "tr2_on_a")
    assert study.calls == []


def test_a_raw_run_folder_without_a_curated_folder_is_refused_before_any_tester_call(study):
    raw = tc.runner.RUNS / "tr2_on_b"
    raw.mkdir(parents=True)
    (raw / "manifest.json").write_text("{}")
    with pytest.raises(SystemExit, match="runs/tr2_on_b"):
        tc.cmd_runs(None)
    assert study.calls == [] and snapshot(raw) == {"manifest.json": b"{}"}
    with pytest.raises(SystemExit, match="without a curated folder"):
        tc.research_run(study.cfg, "on_b", "tr2_on_b")
    assert study.calls == []


# ------------------------------------------------------------------ attempts (KTD8)
def test_a_fresh_attempt_runs_everything_and_records_the_four_provenance_fields(study):
    tc.cmd_runs(None)
    assert study.calls == [tc.run_id(n) for n in ALL_NAMES]
    started = json.loads((study.results / "attempts" / "a1.started.json").read_text())
    closed = json.loads((study.results / "attempts" / "a1.json").read_text())
    assert started["attempt"] == 1 and closed["status"] == "ok"
    assert closed["run_ids"] == {n: tc.run_id(n) for n in ALL_NAMES}
    for name in ALL_NAMES:
        rec = json.loads((study.results / tc.run_id(name) / "record.json").read_text())
        cur = tc.evaluate.jsonable(tc.current_provenance(study.cfg, name))
        assert {k: rec[k] for k in tc.PROVENANCE} == cur
    rec = json.loads((study.results / "tr2_on_a" / "record.json").read_text())
    assert rec["ex5_sha256"] == hashlib.sha256((study.experts / EX5_RESEARCH).read_bytes()).hexdigest()
    assert rec["window"] == list(tc.WINDOW) and rec["inputs"]["EnableTrailingStop"] is True
    assert set(rec["source_sha256"]) == {"ob_m1_structure.mq5", "ob_m1_structure_research.mq5"}


def test_a_second_attempt_reuses_matching_folders_and_a_new_ex5_forces_new_ids(study):
    tc.cmd_runs(None)
    first = snapshot(study.results / "tr2_on_a")
    study.calls.clear()
    tc.cmd_runs(None)   # attempt 2: everything matches, nothing runs
    assert study.calls == []
    a2 = json.loads((study.results / "attempts" / "a2.json").read_text())
    assert a2["run_ids"] == {n: tc.run_id(n) for n in ALL_NAMES}
    for ex5 in (EX5_RESEARCH, EX5_DELIVERED):   # install recompiled: new EX5 bytes
        (study.experts / ex5).write_bytes(b"ex5 build 2 " + ex5.encode())
    tc.cmd_runs(None)   # attempt 3: every earlier folder fails the EX5 check
    assert study.calls == [tc.run_id(n, 3) for n in ALL_NAMES]
    assert snapshot(study.results / "tr2_on_a") == first


def test_after_a_refusal_the_next_attempt_uses_a2_ids_and_leaves_the_refused_folder_unchanged(study):
    d = make_folder(study, "on_a", "tr2_on_a", ex5_sha256="f" * 64)
    before = snapshot(d)
    with pytest.raises(SystemExit, match="ex5_sha256"):
        tc.cmd_runs(None)
    assert study.calls == []
    a1 = json.loads((study.results / "attempts" / "a1.json").read_text())
    assert a1["status"] == "refused" and (study.results / "attempts" / "a1.started.json").exists()
    tc.cmd_runs(None)
    assert study.calls == [tc.run_id(n, 2) for n in ALL_NAMES]
    assert "tr2_on_a_a2" in study.calls
    assert snapshot(d) == before
    a2 = json.loads((study.results / "attempts" / "a2.json").read_text())
    assert a2["status"] == "ok" and a2["run_ids"]["on_a"] == "tr2_on_a_a2"


def test_an_existing_attempt_reservation_is_never_overwritten(study):
    att = study.results / "attempts"
    att.mkdir(parents=True)
    (att / "a1.started.json").write_text('{"attempt": 1, "note": "crashed before closing"}')
    tc.cmd_runs(None)
    assert (att / "a1.started.json").read_text() == '{"attempt": 1, "note": "crashed before closing"}'
    assert not (att / "a1.json").exists()
    assert (att / "a2.started.json").exists() and (att / "a2.json").exists()
    assert study.calls == [tc.run_id(n, 2) for n in ALL_NAMES]
    with pytest.raises(FileExistsError):
        tc._create(att / "a2.json", {"overwrite": True})


def test_a_taken_attempt_id_is_refused(study):
    att = study.results / "attempts"
    att.mkdir(parents=True)
    (att / "a1.started.json").write_text("{}")
    raw = tc.runner.RUNS / "tr2_off_b_a2"
    raw.mkdir(parents=True)
    with pytest.raises(SystemExit, match="tr2_off_b_a2"):
        tc.cmd_runs(None)
    assert study.calls == [] and raw.exists()


def test_a_baseline_mismatch_stops_and_closes_the_attempt(study):
    (study.tmp / "baseline" / "b_off_a" / "rl_deals_b_off_a.csv").write_text("time,price\nt1,2.0\n")
    with pytest.raises(SystemExit, match="differ from b_off_a"):
        tc.cmd_runs(None)
    assert study.calls == ["tr2_off_a"]
    assert (study.results / "tr2_off_a_mismatch.json").exists()
    a1 = json.loads((study.results / "attempts" / "a1.json").read_text())
    assert a1["status"] == "failed" and a1["run_ids"] == {"off_a": "tr2_off_a"}


# ------------------------------------------------------------------ analyze picks the latest matching attempt
def test_analyze_selects_the_latest_attempt_whose_provenance_matches(study):
    att = study.results / "attempts"
    att.mkdir(parents=True)
    for n in (1, 2, 3):
        (att / f"a{n}.started.json").write_text("{}")
    for name in ALL_NAMES:
        make_folder(study, name, tc.run_id(name, 1))
    make_folder(study, "on_a", tc.run_id("on_a", 2))
    make_folder(study, "on_a", tc.run_id("on_a", 3), window=["2026.03.01", "2026.03.30"])
    sel = tc.select_runs(study.cfg)
    assert sel["on_a"] == "tr2_on_a_a2"
    assert all(sel[n] == tc.run_id(n) for n in ALL_NAMES if n != "on_a")


def test_analyze_refuses_a_run_name_without_a_matching_attempt(study):
    att = study.results / "attempts"
    att.mkdir(parents=True)
    (att / "a1.started.json").write_text("{}")
    for name in ALL_NAMES:
        make_folder(study, name, tc.run_id(name, 1), **({"ex5_sha256": "f" * 64} if name == "off_b" else {}))
    with pytest.raises(SystemExit, match="off_b"):
        tc.select_runs(study.cfg)


def test_cmd_analyze_reads_the_selected_folders(study, monkeypatch):
    att = study.results / "attempts"
    att.mkdir(parents=True)
    for n in (1, 2):
        (att / f"a{n}.started.json").write_text("{}")
    for name in ALL_NAMES:
        make_folder(study, name, tc.run_id(name, 1))
        (study.results / tc.run_id(name, 1) / "r.htm").write_text("")
    make_folder(study, "on_b", tc.run_id("on_b", 2))
    (study.results / "tr2_on_b_a2" / "r.htm").write_text("")
    seen = []
    monkeypatch.setattr(tc, "conformance", lambda rid, variant: seen.append(rid) or {"violations": 0})
    monkeypatch.setattr(tc, "summarize", lambda rid, rec: {"run": rid})
    monkeypatch.setattr(tc.cm, "read_run", lambda d, rid: {"dir": d})
    monkeypatch.setattr(tc.charts_trail, "render", lambda run, out, rid: [])
    tc.cmd_analyze(None)
    assert seen == ["tr2_off_a", "tr2_off_b", "tr2_on_a", "tr2_on_b_a2"]
    builds = json.loads((study.results / "builds_match.json").read_text())
    assert builds["on_b_delivered"]["of"] == "tr2_on_b_a2"
    summary = json.loads((study.results / "summary.json").read_text())
    assert summary["on_b"]["run"] == "tr2_on_b_a2"


# ------------------------------------------------------------------ the .set file
def test_the_trailing_set_enables_only_the_trail_and_is_not_a_recommendation(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, "DELIV", tmp_path)
    p = tc.write_set()
    vals = tc.setfile.read_set(p)
    assert vals["EnableTrailingStop"] in ("true", "1")
    defaults = {s.name: s.default for s in tc.pipeline.specs("delivered")}
    assert all(str(vals[k]) == str(v) for k, v in defaults.items() if k != "EnableTrailingStop")
    assert not any("recommended" in f.name for f in tmp_path.iterdir())


def test_an_identical_set_is_reused_and_a_differing_set_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, "DELIV", tmp_path)
    p = tc.write_set()
    first = p.read_bytes()
    assert tc.write_set() == p and p.read_bytes() == first
    changed = first.replace("EnableTrailingStop=true".encode("utf-16-le"), "EnableTrailingStop=false".encode("utf-16-le"))
    assert changed != first
    p.write_bytes(changed)
    with pytest.raises(SystemExit, match="differs"):
        tc.write_set()
    assert p.read_bytes() == changed


# ------------------------------------------------------------------ unchanged helpers
def test_the_deal_comparison_flags_a_single_changed_field():
    a = pd.DataFrame({"time": ["t1", "t2"], "price": [1.0, 2.0]})
    assert tc.compare_deals(a, a.copy()) == []
    b = a.copy()
    b.loc[1, "price"] = 2.01
    diffs = tc.compare_deals(a, b)
    assert len(diffs) == 1 and "deal row 1" in diffs[0]


def test_inputs_set_the_variant_and_the_trailing_switch_only():
    assert tc.inputs(1, True) == {**tc.BASE_INPUTS, "StructureVariant": 1, "EnableTrailingStop": True}


def test_install_refuses_while_the_live_terminal_runs(monkeypatch):
    called = []
    monkeypatch.setattr(tc.runner, "live_terminal_running", lambda: True)
    monkeypatch.setattr(tc.env, "load_config", lambda: called.append("config"))
    with pytest.raises(SystemExit, match="live MT5 terminal is running"):
        tc.cmd_install(None)
    assert called == []
