"""trail_cli without the tester: window, run IDs, own log, no reuse, the deal comparison (plan 2026-10-05-0007, U5)."""
import json

import pandas as pd
import pytest

import trail_cli as tc
from mt5r import explog


def test_only_march_2026_is_accepted():
    tc.check_window("2026.03.01", "2026.03.31")
    with pytest.raises(SystemExit):
        tc.check_window("2026.08.01", "2026.08.31")


def test_run_ids_carry_the_study_prefix_and_one_path_component():
    assert tc.run_id("on_a") == "tr1_on_a"
    with pytest.raises(ValueError):
        tc.run_id("../x")


def test_runs_log_to_the_studys_own_experiment_log(monkeypatch, tmp_path):
    monkeypatch.setattr(explog, "LOG", tmp_path / "old_research_log.jsonl")
    monkeypatch.setattr(tc, "LOG", tmp_path / "trailing_v1" / "experiment_log.jsonl")
    tc.use_study_log()
    assert explog.LOG == tmp_path / "trailing_v1" / "experiment_log.jsonl"


def test_an_existing_run_folder_is_reused_as_a_record_and_never_re_run(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, "RESULTS", tmp_path)
    d = tmp_path / "tr1_on_a"
    d.mkdir()
    (d / "record.json").write_text(json.dumps({"run_id": "tr1_on_a", "net_profit": 1.0}))
    monkeypatch.setattr(tc.pipeline, "run_single", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-run")))
    assert tc.research_run(None, "on_a", 0, True)["net_profit"] == 1.0


def test_the_deal_comparison_flags_a_single_changed_field():
    a = pd.DataFrame({"time": ["t1", "t2"], "price": [1.0, 2.0]})
    assert tc.compare_deals(a, a.copy()) == []
    b = a.copy()
    b.loc[1, "price"] = 2.01
    diffs = tc.compare_deals(a, b)
    assert len(diffs) == 1 and "deal row 1" in diffs[0]


def test_inputs_set_the_variant_and_the_trailing_switch_only():
    assert tc.inputs(1, True) == {**tc.BASE_INPUTS, "StructureVariant": 1, "EnableTrailingStop": True}


def test_the_trailing_set_enables_only_the_trail_and_is_not_a_recommendation(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, "DELIV", tmp_path)
    p = tc.write_set()
    vals = tc.setfile.read_set(p)
    assert vals["EnableTrailingStop"] in ("true", "1")
    defaults = {s.name: s.default for s in tc.pipeline.specs("delivered")}
    assert all(str(vals[k]) == str(v) for k, v in defaults.items() if k != "EnableTrailingStop")
    assert not any("recommended" in f.name for f in tmp_path.iterdir())


def test_install_refuses_while_the_live_terminal_runs(monkeypatch):
    called = []
    monkeypatch.setattr(tc.runner, "live_terminal_running", lambda: True)
    monkeypatch.setattr(tc.env, "load_config", lambda: called.append("config"))
    with pytest.raises(SystemExit, match="live MT5 terminal is running"):
        tc.cmd_install(None)
    assert called == []
