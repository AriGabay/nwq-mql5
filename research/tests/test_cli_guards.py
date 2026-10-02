import argparse
import json
import pathlib
import subprocess
import sys

import pandas as pd
import pytest

import cli
from mt5r import env, explog, pipeline, runner

CLI = pathlib.Path(cli.__file__)


class Boom(Exception):
    """Raised by a stub to prove a command got past its guards."""


def _boom(*a, **k):
    raise Boom()


def _git(returncode=0, stdout=""):
    return lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout=stdout, stderr="")


# ------------------------------------------------------------------ commit checks
def test_committed_true_only_for_clean_tracked_file(monkeypatch):
    monkeypatch.setattr(cli.subprocess, "run", _git(0, ""))
    assert cli.committed("research/run_constants.json")
    monkeypatch.setattr(cli.subprocess, "run", _git(0, " M research/preregistration.json\n"))
    assert not cli.committed("research/run_constants.json")


def test_committed_fails_closed_when_git_errors(monkeypatch):
    monkeypatch.setattr(cli.subprocess, "run", _git(128, ""))
    assert not cli.committed("research/run_constants.json")


def test_committed_false_for_missing_file(monkeypatch):
    monkeypatch.setattr(cli.subprocess, "run", _git(0, ""))
    assert not cli.committed("research/does_not_exist.json")


@pytest.mark.parametrize("cmd", ["cmd_wfo", "cmd_freeze", "cmd_holdout", "cmd_robustness"])
def test_protocol_steps_refuse_on_uncommitted_prereg(monkeypatch, cmd):
    monkeypatch.setattr(cli, "committed", lambda path: False)
    monkeypatch.setattr(env, "load_config", _boom)
    with pytest.raises(SystemExit, match="preregistration"):
        getattr(cli, cmd)(argparse.Namespace())


# ------------------------------------------------------------------ holdout guard
@pytest.fixture
def frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DELIV", tmp_path)
    (tmp_path / cli.CAND_SET).write_text("ObMode=1")
    (tmp_path / cli.BASE_SET).write_text("ObMode=0")
    monkeypatch.setattr(cli, "ea_sha", lambda: "EA1")
    monkeypatch.setattr(pipeline, "prereg", lambda: {"ea_source_sha256": "EA1", "holdout": ["2026.08.01", "2026.09.29"]})
    monkeypatch.setattr(env, "load_config", _boom)
    shas = {"candidate": cli.sha(tmp_path / cli.CAND_SET), "baseline": cli.sha(tmp_path / cli.BASE_SET)}
    return shas


def _entry(who, status, has_report, ea="EA1", set_sha="S"):
    return {"id": f"holdout_{who}", "role": f"holdout_{who}", "status": status, "has_report": has_report,
            "ea_sha256": ea, "set_sha256": set_sha}


def test_holdout_refuses_without_committed_candidate_freeze(monkeypatch, frozen):
    monkeypatch.setattr(cli, "committed", lambda path: path.endswith("preregistration.json"))
    monkeypatch.setattr(explog, "read", lambda: [])
    with pytest.raises(SystemExit, match="candidate"):
        cli.cmd_holdout(argparse.Namespace())


def test_holdout_refuses_second_completed_run_with_same_hashes(monkeypatch, frozen):
    monkeypatch.setattr(cli, "committed", lambda path: True)
    log = [_entry("candidate", "ok", True, set_sha=frozen["candidate"]),
           _entry("baseline", "ok", True, set_sha=frozen["baseline"])]
    monkeypatch.setattr(explog, "read", lambda: log)
    with pytest.raises(SystemExit, match="already"):
        cli.cmd_holdout(argparse.Namespace())


def test_holdout_allows_rerun_after_infra_failure_without_report(monkeypatch, frozen):
    monkeypatch.setattr(cli, "committed", lambda path: True)
    log = [_entry("candidate", "infra_failure", False, set_sha=frozen["candidate"]),
           _entry("baseline", "failed", False, set_sha=frozen["baseline"])]
    monkeypatch.setattr(explog, "read", lambda: log)
    with pytest.raises(Boom):                      # got past every guard to env.load_config
        cli.cmd_holdout(argparse.Namespace())


def test_holdout_pending_logic():
    """R24: the holdout runs exactly once per side. Any run that produced a report counts, whatever its status
    and whatever the EA or .set hash (an edited EA or a re-committed .set must not buy a second look)."""
    shas = {"candidate": "C", "baseline": "B"}
    done_c = _entry("candidate", "ok", True, set_sha="C")
    assert cli.holdout_pending([], "EA1", shas) == ["candidate", "baseline"]
    assert cli.holdout_pending([done_c], "EA1", shas) == ["baseline"]
    assert cli.holdout_pending([_entry("candidate", "ok", True, ea="EA0", set_sha="C")], "EA1", shas) == ["baseline"]
    assert cli.holdout_pending([_entry("candidate", "ok", True, set_sha="C0")], "EA1", shas) == ["baseline"]
    assert cli.holdout_pending([_entry("candidate", "timeout", True, set_sha="C")], "EA1", shas) == ["baseline"]
    # a run without a report is not a completed run
    assert cli.holdout_pending([_entry("candidate", "ok", False, set_sha="C")], "EA1", shas) == [
        "candidate", "baseline"]


def test_protocol_steps_refuse_when_the_ea_differs_from_the_preregistered_source(monkeypatch):
    monkeypatch.setattr(cli, "committed", lambda path: True)
    monkeypatch.setattr(pipeline, "prereg", lambda: {"ea_source_sha256": "REGISTERED"})
    monkeypatch.setattr(cli, "ea_sha", lambda: "EDITED")
    monkeypatch.setattr(env, "load_config", _boom)
    for cmd in ("cmd_wfo", "cmd_freeze", "cmd_holdout", "cmd_robustness", "cmd_deliver"):
        with pytest.raises(SystemExit, match="EA source"):
            getattr(cli, cmd)(argparse.Namespace())


def test_ea_hash_ignores_line_endings(tmp_path, monkeypatch):
    lf, crlf = tmp_path / "lf.mq5", tmp_path / "crlf.mq5"
    lf.write_bytes(b"int a;\nint b;\n")
    crlf.write_bytes(b"int a;\r\nint b;\r\n")
    assert cli.sha_source(lf) == cli.sha_source(crlf) == cli.sha(lf)


@pytest.mark.parametrize("cmd", ["cmd_smoke", "cmd_optsmoke"])
def test_tester_steps_refuse_the_holdout_window_after_preregistration(monkeypatch, cmd):
    monkeypatch.setattr(pipeline, "prereg", lambda: {"holdout": ["2026.08.01", "2026.09.29"]})
    monkeypatch.setattr(env, "load_config", _boom)
    with pytest.raises(SystemExit, match="holdout"):
        getattr(cli, cmd)(argparse.Namespace(period="M15", start="2026.07.20", end="2026.08.05"))
    with pytest.raises(Boom):    # a window before the holdout gets past the guard
        getattr(cli, cmd)(argparse.Namespace(period="M15", start="2026.03.01", end="2026.03.31"))


# ------------------------------------------------------------------ install / freeze-rules
def test_install_refuses_while_live_terminal_runs(monkeypatch):
    monkeypatch.setattr(runner, "live_terminal_running", lambda: True)
    monkeypatch.setattr(env, "load_config", _boom)
    with pytest.raises(SystemExit, match="live"):
        cli.cmd_install(argparse.Namespace())


def test_install_turns_mcp_off_before_the_trade_safety_check(monkeypatch):
    """Build 6231 re-adds an empty [MCP.Custom] on exit; install disables it first, as runner.run does."""
    calls = []
    monkeypatch.setattr(runner, "live_terminal_running", lambda: False)
    monkeypatch.setattr(env, "load_config", lambda: "cfg")
    monkeypatch.setattr(env, "disable_mcp", lambda cfg: calls.append("disable_mcp"))

    def safety(cfg):
        calls.append("assert_trade_safety")
        raise RuntimeError("stop here")
    monkeypatch.setattr(env, "assert_trade_safety", safety)
    with pytest.raises(RuntimeError, match="stop here"):
        cli.cmd_install(argparse.Namespace())
    assert calls == ["disable_mcp", "assert_trade_safety"]


def test_freeze_rules_refuses_period_other_than_pilot_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "PREREG_PATH", tmp_path / "preregistration.json")
    pilot = tmp_path / "pilot_summary.json"
    pilot.write_text(json.dumps({"chosen_period": "M15"}))
    monkeypatch.setattr(cli, "PILOT", pilot)
    with pytest.raises(SystemExit, match="M15"):
        cli.cmd_freeze_rules(argparse.Namespace(period="M5", gate_change=[]))
    assert not (tmp_path / "preregistration.json").exists()


def test_freeze_rules_refuses_when_prereg_exists(tmp_path, monkeypatch):
    p = tmp_path / "preregistration.json"
    p.write_text("{}")
    monkeypatch.setattr(pipeline, "PREREG_PATH", p)
    with pytest.raises(SystemExit, match="exists"):
        cli.cmd_freeze_rules(argparse.Namespace(period="M15", gate_change=[]))
    assert p.read_text() == "{}"


def test_freeze_rules_writes_ktd12_protocol(tmp_path, monkeypatch):
    p = tmp_path / "preregistration.json"
    monkeypatch.setattr(pipeline, "PREREG_PATH", p)
    monkeypatch.setattr(cli, "ea_sha", lambda: "abc123")
    monkeypatch.setattr(cli, "PILOT", tmp_path / "no_pilot_summary.json")
    monkeypatch.setattr(explog, "read", lambda: [{"role": "pilot", "status": "ok"}, {"role": "pilot", "status": "ok"},
                                                 {"role": "smoke", "status": "ok"}])
    cli.cmd_freeze_rules(argparse.Namespace(period="M5", gate_change=["none: approved as drawn"]))
    d = json.loads(p.read_text())
    assert d["period"] == "M5" and d["signal_tf_value"] == 5 and d["ea_source_sha256"] == "abc123"
    assert d["grid"] == {"ObMode": [0, 1], "EntryMode": [0, 1, 2, 3], "ObMaxAgeBars": [48, 96, 144],
                         "FvgWindowBars": [6, 12, 18], "OrderExpiryBars": [6, 12, 18]}
    assert d["grid_ranges"]["ObMaxAgeBars"] == [48, 48, 144]
    assert d["categorical"] == ["ObMode", "EntryMode"]
    assert [f["test"] for f in d["folds"]] == [["2026.03.01", "2026.03.31"], ["2026.04.01", "2026.04.30"],
                                               ["2026.05.01", "2026.05.31"], ["2026.06.01", "2026.06.30"],
                                               ["2026.07.01", "2026.07.31"]]
    assert d["final_train"] == ["2026.05.01", "2026.07.31"] and d["holdout"] == ["2026.08.01", "2026.09.29"]
    assert d["stats"]["dsr_trials"] == 216 * 5 + 2 and d["stats"]["seed"] == 20260930
    a = d["acceptance"]
    assert (a["min_fills_per_month"], a["days_per_month"], a["bootstrap_alpha"], a["min_positive_folds"]) == (
        15, 30.44, 0.05, 3)
    assert (a["remove_top_events"], a["mc_breach_prob_max"], a["spread_stress_k"], a["stop_slippage_points"]) == (
        2, 0.10, 1.0, 10)
    assert (a["min_neighbor_profitable_share"], a["dsr_min"], a["dsr_min_days"]) == (0.60, 0.90, 60)
    assert d["selection"]["trade_floor_per_month"] == 15 and d["selection"]["max_equity_dd_pct"] == 10.0
    assert d["gate_rule_changes"] == ["none: approved as drawn"]


# ------------------------------------------------------------------ pilot summary
def _deals(n_fills):
    rows = [{"type": 2, "entry": 0, "profit": 10000.0}]
    for i in range(n_fills):
        rows += [{"type": 0, "entry": 0, "profit": 0.0}, {"type": 1, "entry": 1, "profit": 12.5}]
    return pd.DataFrame(rows)


def _pilot(m5, m15):
    setups = pd.DataFrame({"reason": ["filled", "filled", "expired_untouched"]})
    return cli.pilot_summary({
        "M5": {"run_id": "pilot_m5", "deals": _deals(m5), "setups": setups, "funnel": {"filled": m5}},
        "M15": {"run_id": "pilot_m15", "deals": _deals(m15), "setups": setups, "funnel": {"filled": m15}}},
        ["2025.12.01", "2026.07.31"])


def _keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _keys(v)


def test_pilot_summary_has_no_profit_fields_and_picks_longer_tf():
    s = _pilot(300, 120)            # 243 days: M15 120 fills = 15.03 / month
    assert s["runs"]["M15"]["fills"] == 120 and s["runs"]["M15"]["days"] == 243
    assert s["runs"]["M15"]["fills_per_month"] == pytest.approx(15.03, abs=0.005)
    assert s["runs"]["M5"]["reasons"] == {"filled": 2, "expired_untouched": 1}
    assert s["chosen_period"] == "M15" and s["shortfall"] is False
    banned = ("profit", "net", "balance", "equity", "commission", "swap", "pnl", "drawdown")
    assert not [k for k in _keys(s) if any(b in k.lower() for b in banned)]
    assert "12.5" not in json.dumps(s)


def test_pilot_timeframe_rule_falls_back_to_m5():
    assert _pilot(300, 119)["chosen_period"] == "M5"
    both_short = _pilot(100, 50)
    assert both_short["chosen_period"] == "M5" and both_short["shortfall"] is True


# ------------------------------------------------------------------ help
def test_help_lists_subcommands():
    out = subprocess.run([sys.executable, str(CLI), "--help"], capture_output=True, text=True, check=True).stdout
    for name in ["install", "smoke", "optsmoke", "pilot", "charts", "conformance", "freeze-rules", "wfo", "freeze",
                 "holdout", "robustness", "deliver"]:
        assert name in out, name
