"""numeric_cli: stage guards, the OOS loop with chained deposits and fallback labels, resume without re-optimizing,
and the frozen .set naming (plan 2026-10-04-1851, U2, U7, U8)."""
import json
import subprocess

import pytest

import numeric_cli as nc
from mt5r import explog, numeric_v1 as nv, setfile


@pytest.fixture(autouse=True)
def _keep_module_log(monkeypatch, tmp_path):
    """start() points explog.LOG at the study log; keep that inside each test."""
    monkeypatch.setattr(explog, "LOG", tmp_path / "experiment_log.jsonl")
    monkeypatch.setattr(nv, "LOG", tmp_path / "study_log.jsonl")


def test_pre_freeze_commands_refuse_once_the_prereg_exists(tmp_path, monkeypatch):
    f = tmp_path / "prereg.json"
    f.write_text("{}")
    monkeypatch.setattr(nv, "PREREG_PATH", f)
    with pytest.raises(SystemExit, match="pre-freeze"):
        nc.start(pre_freeze=True)


def test_post_freeze_commands_refuse_an_uncommitted_prereg(monkeypatch):
    monkeypatch.setattr(nc.cli, "committed", lambda path: False)
    with pytest.raises(SystemExit, match="uncommitted"):
        nc.prereg_committed()


def test_post_freeze_commands_refuse_a_prereg_registering_another_ea(monkeypatch):
    monkeypatch.setattr(nc.cli, "committed", lambda path: True)
    monkeypatch.setattr(nc, "prereg_published", lambda path: "origin/main")
    monkeypatch.setattr(nv, "load_prereg", lambda: {"ea_source_sha256": "other"})
    with pytest.raises(SystemExit, match="EA source"):
        nc.prereg_committed()


def _prereg():
    return {"deposit": 10000, "folds": nv.FOLDS[:2], "final_train": nv.FINAL_TRAIN}


def _window(tag, label="selected", params=(1, 2, 40)):
    p = dict(zip(nv.AXES, params))
    return {"tag": tag, "train": ["a", "b"], "status": "verified", "expected": 18, "completed": 18, "failed": 0,
            "cached": 0, "seconds": 30.0, "selection_status": "selected" if label == "selected" else "no_eligible_pass",
            "label": label, "params": p, "reason": {"eligible_passes": 1 if label == "selected" else 0, "note": "x"}}


def test_oos_loop_passes_all_three_axes_and_chains_each_series_deposit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(nc, "start", lambda pre_freeze: (None, _prereg()))
    monkeypatch.setattr(nc, "FOLDS_JSON", tmp_path / "folds.json")
    monkeypatch.setattr(nc, "WFO", tmp_path)
    monkeypatch.setattr(nc, "FINAL_DIR", tmp_path / "final")
    wins = {"f1": _window("f1", "fallback", (0, 3, 20)), "f2": _window("f2"), "final": _window("final")}
    monkeypatch.setattr(nc, "run_window", lambda cfg, tag, s, e, out, purpose: wins[tag])

    def fake_oos(cfg, name, s, e, deposit, params, role, purpose, dest):
        calls.append((name, deposit, nv.tuple_of(params), role))
        bal = deposit + {"procedure": 100.0, "baseline_a": -50.0, "baseline_b": 10.0}[name.split("_oos_")[1]]
        return {"run_id": "nv1_" + name, "final_balance": bal, "params": {k: int(params[k]) for k in nv.AXES},
                "net_profit": bal - deposit, "trades": 1, "equity_dd_pct": 1.0, "balance_dd_pct": 1.0}

    monkeypatch.setattr(nc, "oos_run", fake_oos)
    monkeypatch.setattr(nc, "window_table", lambda: None)
    nc.cmd_wfo(None)
    by = {c[0]: c for c in calls}
    assert by["f1_oos_procedure"][2] == (0, 3, 20) and by["f1_oos_procedure"][3] == "oos_procedure_fallback"  # AE2
    assert by["f2_oos_procedure"][2] == (1, 2, 40) and by["f2_oos_procedure"][3] == "oos_procedure"
    assert by["f2_oos_baseline_b"][2] == (1, 3, 20)
    assert by["f2_oos_procedure"][1] == 10100.0 and by["f2_oos_baseline_a"][1] == 9950.0
    assert by["f2_oos_baseline_b"][1] == 10010.0
    saved = json.loads((tmp_path / "folds.json").read_text())
    assert saved[0]["window"]["label"] == "fallback"


def test_resume_reuses_a_verified_window_and_starts_no_optimization(tmp_path, monkeypatch):
    out = tmp_path / "f3_selection"
    out.mkdir()
    (out / "window.json").write_text(json.dumps(_window("f3")))
    monkeypatch.setattr(nc.pipeline, "run_optimization",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("optimization must not re-run")))
    rec = nc.run_window(None, "f3", "2026.02.01", "2026.04.30", out, "fold 3")
    assert rec["params"] == dict(zip(nv.AXES, (1, 2, 40)))


def test_resume_skips_a_completed_oos_run(tmp_path, monkeypatch):
    d = tmp_path / "nv1_f3_oos_procedure"
    d.mkdir()
    (d / "record.json").write_text(json.dumps({"run_id": "nv1_f3_oos_procedure", "net_profit": 5.0}))
    monkeypatch.setattr(nc.cli, "oos_record", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-run")))
    rec = nc.oos_run(None, "f3_oos_procedure", "2026.05.01", "2026.05.31", 10000, nv.DEFAULTS, "r", "p", tmp_path)
    assert rec["net_profit"] == 5.0


def test_oos_run_refuses_the_excluded_window(tmp_path):
    with pytest.raises(SystemExit):
        nc.oos_run(None, "x", "2026.08.01", "2026.08.31", 10000, nv.DEFAULTS, "r", "p", tmp_path)


@pytest.mark.parametrize("status,kind", [("selected", "candidate"), ("no_eligible_pass", "fallback")])
def test_freeze_writes_candidate_only_when_selected(tmp_path, monkeypatch, status, kind):
    monkeypatch.setattr(nc, "prereg_committed", lambda: {})
    monkeypatch.setattr(nc.cli, "committed", lambda path: False)
    monkeypatch.setattr(nc.cli, "ea_sha", lambda: "ea")
    monkeypatch.setattr(nc.cli, "sha", lambda path: "h")
    monkeypatch.setattr(nc, "_rel", lambda p: str(p))
    monkeypatch.setattr(nv, "DELIV", tmp_path / "deliv")
    monkeypatch.setattr(nc, "FINAL_DIR", tmp_path / "final")
    (tmp_path / "final").mkdir()
    params = (0, 2, 40) if status == "selected" else (0, 3, 20)
    w = _window("final", "selected" if status == "selected" else "fallback", params)
    (tmp_path / "final" / "window.json").write_text(json.dumps(w))
    nc.cmd_freeze(None)
    files = sorted(p.name for p in (tmp_path / "deliv").glob("*.set"))
    assert nc.SETS[kind] in files
    other = nc.SETS["fallback" if kind == "candidate" else "candidate"]
    assert other not in files
    vals = setfile.read_set(tmp_path / "deliv" / nc.SETS[kind])
    assert (int(vals["SwingStrengthM1"]), int(vals["StopBufferPoints"])) == params[1:]
    head = (tmp_path / "deliv" / nc.SETS[kind]).read_bytes().decode("utf-16")
    assert ("FALLBACK" in head) == (kind == "fallback")


def _acc(passed: bool) -> dict:
    crit = {k: {"pass": passed, "evaluated": True, "value": 0, "threshold": "t"} for k in
            ("oos_frequency", "oos_net", "bootstrap_ci", "positive_folds", "top_events_removed", "loss_limits",
             "cost_stress", "stability", "dsr")}
    crit["oos_net"]["value"] = {"net": 1.0, "baselines": {"baseline_a": 0.0, "baseline_b": 0.0},
                                "margins": {"baseline_a": 1.0, "baseline_b": 1.0}}
    crit["dsr"]["value"] = {"psr_value": 0.5, "trials": 126, "sensitivity": {"trials": 18, "psr_value": 0.6}}
    a = {"criteria": crit, "passed_all": passed, "drawdowns": {"balance_dd_pct": 1.0, "equity_dd_pct": 1.0}}
    view = {"net": 1.0, "fills": 1, "fills_per_month": 1.0, "win_rate": 0.5, "dd_tester_equity_max_pct": 1.0,
            "dd_tester_balance_max_pct": 1.0, "dd_daily_records_pct": 1.0, "dd_closed_trades_pct": 1.0}
    return {"final": {"params": nv.DEFAULTS, "kind": "fallback", "series": "baseline_a"},
            "acceptance": {"procedure": a, "baseline_a": a, "baseline_b": a},
            "acceptance_group_R_report_only": a, "passed_all": passed, "fold_rows": [],
            "groups": {w: {"all": view, "G": view, "R": view} for w in nc.SERIES},
            "stability": {"profitable_share": 0.5, "rows": [], "window": nv.STABILITY_WINDOW},
            "var_sr": 0.001, "shuffle_mc": {}, "session_sensitivity": {}}


@pytest.mark.parametrize("passed", [False, True])
def test_report_verdict_states_no_improvement_unless_every_criterion_passes(passed):
    text = nc.build_report(_acc(passed), [], [], {})
    assert ("לא נמצא שיפור במסגרת הגריד שנבדק" in text) == (not passed)
    assert "recommended" not in text.lower() or "אין recommended.set" in text


def test_report_states_the_net_condition_each_series_faced():
    """B was compared with A only and A with no baseline; the criteria section must say so (plan 2026-10-04-2133,
    U1, R12), while every pass/fail cell stays the accepted value."""
    acc = _acc(False)
    base = acc["acceptance"]["procedure"]
    acc["acceptance"] = {
        "procedure": base,
        "baseline_a": {**base, "criteria": {**base["criteria"], "oos_net": {
            "pass": False, "evaluated": True, "value": {"net": -1.0, "baseline_net": -1.0}, "threshold": "t"}}},
        "baseline_b": {**base, "criteria": {**base["criteria"], "oos_net": {
            "pass": True, "evaluated": True, "value": {"net": 2.0, "baselines": {"baseline_a": -1.0},
                                                       "margins": {"baseline_a": 3.0}}, "threshold": "t"}}}}
    text = nc.build_report(acc, [], [], {})
    lines = {ln.split(":**")[0]: ln for ln in text.splitlines() if ln.startswith("  - **")}
    assert "baseline A (0,3,20)" in lines["  - **הליך הבחירה"] and "baseline B (1,3,20)" in lines["  - **הליך הבחירה"]
    assert "baseline A (0,3,20)" in lines["  - **baseline B (1,3,20)"]
    assert "baseline B" not in lines["  - **baseline B (1,3,20)"].split(":**")[1]
    assert "אין baseline" in lines["  - **baseline A (0,3,20)"]
    row = next(ln for ln in text.splitlines() if ln.startswith("| נטו > 0"))
    assert row.split("|")[2:5] == [" נכשל ", " נכשל ", " עובר "]


# ------------------------------------------------------------------ publication guard on real git repositories
PRE = "research/prereg.json"


def _git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A clone of a bare origin whose main holds one commit; prereg_published runs inside the clone."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "checkout", "-q", "-b", "main")
    (work / "README").write_text("x")
    _git(work, "add", "README")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "origin", "main")
    monkeypatch.setattr(nc, "REPO", work)
    return work


def _freeze(work, text="{}", msg="freeze"):
    (work / "research").mkdir(exist_ok=True)
    (work / PRE).write_text(text)
    _git(work, "add", PRE)
    _git(work, "commit", "-q", "-m", msg)


def test_publication_guard_accepts_the_freeze_pushed_on_the_working_branch(repo):
    _git(repo, "checkout", "-q", "-b", "feat/a")
    _freeze(repo)
    _git(repo, "push", "-q", "origin", "feat/a")
    assert nc.prereg_published(PRE) == "origin/feat/a"


def test_publication_guard_accepts_a_new_branch_once_the_freeze_is_merged_to_main(repo):
    _git(repo, "checkout", "-q", "-b", "feat/a")
    _freeze(repo)
    _git(repo, "push", "-q", "origin", "feat/a")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feat/a")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "-b", "feat/b")           # new branch, never pushed
    assert nc.prereg_published(PRE) == "origin/main"
    _git(repo, "checkout", "-q", "--detach")                # detached HEAD: main only
    assert nc.prereg_published(PRE) == "origin/main"


def test_publication_guard_refuses_a_freeze_not_pushed_anywhere(repo):
    _git(repo, "checkout", "-q", "-b", "feat/a")
    _freeze(repo)
    with pytest.raises(SystemExit, match="not pushed"):
        nc.prereg_published(PRE)


def test_publication_guard_refuses_a_protocol_changed_after_its_freeze_commit(repo):
    _git(repo, "checkout", "-q", "-b", "feat/a")
    _freeze(repo, "{}")
    _freeze(repo, '{"changed": 1}')
    _git(repo, "push", "-q", "origin", "feat/a")
    with pytest.raises(SystemExit, match="exactly once"):
        nc.prereg_published(PRE)


def test_publication_guard_refuses_the_same_content_without_the_freeze_commit(repo):
    """main gets an identical file from another commit: the content matches, but the freeze itself is unpublished."""
    _git(repo, "checkout", "-q", "-b", "other")
    _freeze(repo, "{}", "copy")
    _git(repo, "push", "-q", "origin", "other:main")
    _git(repo, "checkout", "-q", "-b", "feat/a", "HEAD~1")
    _freeze(repo, "{}")                                      # the real freeze commit, different from main's
    _git(repo, "branch", "-q", "-D", "other")
    with pytest.raises(SystemExit, match="not pushed"):
        nc.prereg_published(PRE)


def test_publication_guard_refuses_an_uncommitted_protocol(repo):
    with pytest.raises(SystemExit, match="exactly once"):
        nc.prereg_published(PRE)
