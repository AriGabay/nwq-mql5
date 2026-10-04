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
    (tmp_path / cli.CAND_SET).write_text("StructureVariant=1")
    (tmp_path / cli.A_SET).write_text("StructureVariant=0")
    (tmp_path / cli.B_SET).write_text("StructureVariant=1")
    monkeypatch.setattr(cli, "ea_sha", lambda: "EA1")
    monkeypatch.setattr(pipeline, "prereg", lambda: {"ea_source_sha256": "EA1", "holdout": ["2026.08.01", "2026.09.29"],
                                                     "holdout_label": "x"})
    monkeypatch.setattr(env, "load_config", _boom)
    return {"variant_a": cli.sha(tmp_path / cli.A_SET), "variant_b": cli.sha(tmp_path / cli.B_SET)}


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
    log = [_entry("variant_a", "ok", True, set_sha=frozen["variant_a"]),
           _entry("variant_b", "ok", True, set_sha=frozen["variant_b"])]
    monkeypatch.setattr(explog, "read", lambda: log)
    with pytest.raises(SystemExit, match="already"):
        cli.cmd_holdout(argparse.Namespace())


def test_holdout_allows_rerun_after_infra_failure_without_report(monkeypatch, frozen):
    monkeypatch.setattr(cli, "committed", lambda path: True)
    log = [_entry("variant_a", "infra_failure", False, set_sha=frozen["variant_a"]),
           _entry("variant_b", "failed", False, set_sha=frozen["variant_b"])]
    monkeypatch.setattr(explog, "read", lambda: log)
    with pytest.raises(Boom):                      # got past every guard to env.load_config
        cli.cmd_holdout(argparse.Namespace())


def test_holdout_pending_logic():
    """R31: August-September runs exactly once per variant. Any run that produced a report counts, whatever its
    status and whatever the EA or .set hash (an edited EA or a re-committed .set must not buy a second look)."""
    shas = {"variant_a": "A", "variant_b": "B"}
    done_a = _entry("variant_a", "ok", True, set_sha="A")
    assert cli.holdout_pending([], "EA1", shas) == ["variant_a", "variant_b"]
    assert cli.holdout_pending([done_a], "EA1", shas) == ["variant_b"]
    assert cli.holdout_pending([_entry("variant_a", "ok", True, ea="EA0", set_sha="A")], "EA1", shas) == ["variant_b"]
    assert cli.holdout_pending([_entry("variant_a", "ok", True, set_sha="A0")], "EA1", shas) == ["variant_b"]
    assert cli.holdout_pending([_entry("variant_a", "timeout", True, set_sha="A")], "EA1", shas) == ["variant_b"]
    # a run without a report is not a completed run
    assert cli.holdout_pending([_entry("variant_a", "ok", False, set_sha="A")], "EA1", shas) == [
        "variant_a", "variant_b"]


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


def test_freeze_rules_needs_both_pilot_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "PREREG_PATH", tmp_path / "preregistration.json")
    monkeypatch.setattr(explog, "read", lambda: [{"id": "pilot_a", "role": "pilot", "status": "ok"}])
    with pytest.raises(SystemExit, match="pilot_b"):
        cli.cmd_freeze_rules(argparse.Namespace())
    assert not (tmp_path / "preregistration.json").exists()
    monkeypatch.setattr(explog, "read", lambda: [{"id": r, "role": "pilot", "status": "ok"} for r in ("pilot_a", "pilot_b")])
    monkeypatch.setattr(cli, "ea_sha", lambda: "EA1")
    cli.cmd_freeze_rules(argparse.Namespace())
    p = json.loads((tmp_path / "preregistration.json").read_text(encoding="utf-8"))
    assert p["grid"] == {"StructureVariant": [0, 1]} and p["stats"]["dsr_trials"] == 12
    assert p["holdout_label"] == "בדיקה היסטורית לא עצמאית" and p["ea_source_sha256"] == "EA1"


def test_freeze_rules_refuses_when_prereg_exists(tmp_path, monkeypatch):
    p = tmp_path / "preregistration.json"
    p.write_text("{}")
    monkeypatch.setattr(pipeline, "PREREG_PATH", p)
    with pytest.raises(SystemExit, match="exists"):
        cli.cmd_freeze_rules(argparse.Namespace())
    assert p.read_text() == "{}"


# ------------------------------------------------------------------ pilot summary
def _deals(n_fills):
    rows = [{"type": 2, "entry": 0, "profit": 10000.0}]
    for i in range(n_fills):
        rows += [{"type": 0, "entry": 0, "profit": 0.0}, {"type": 1, "entry": 1, "profit": 12.5}]
    return pd.DataFrame(rows)


def _pilot(a, b):
    setups = pd.DataFrame({"reason": ["filled", "filled", "cancelled_second_break"]})
    return cli.pilot_summary({
        "A": {"run_id": "pilot_a", "deals": _deals(a), "setups": setups, "funnel": {"filled": a}},
        "B": {"run_id": "pilot_b", "deals": _deals(b), "setups": setups, "funnel": {"filled": b}}},
        ["2025.12.01", "2026.07.31"])


def _keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _keys(v)


def test_pilot_summary_reports_fills_per_variant_without_profit_fields():
    """R28: M5/M1 are fixed (no timeframe choice); each variant's fills per month is reported, and a shortfall
    against 15 per month is flagged per variant, never acted on."""
    s = _pilot(120, 119)            # 243 days: 120 fills = 15.03 / month
    assert s["runs"]["A"]["fills"] == 120 and s["runs"]["A"]["days"] == 243
    assert s["runs"]["A"]["fills_per_month"] == pytest.approx(15.03, abs=0.005)
    assert s["runs"]["B"]["reasons"] == {"filled": 2, "cancelled_second_break": 1}
    assert s["shortfall"] == {"A": False, "B": True}
    assert "chosen_period" not in s
    banned = ("profit", "net", "balance", "equity", "commission", "swap", "pnl", "drawdown")
    assert not [k for k in _keys(s) if any(b in k.lower() for b in banned)]
    assert "12.5" not in json.dumps(s)


# ------------------------------------------------------------------ help
def test_help_lists_subcommands():
    out = subprocess.run([sys.executable, str(CLI), "--help"], capture_output=True, text=True, check=True).stdout
    for name in ["install", "smoke", "optsmoke", "pilot", "charts", "conformance", "freeze-rules", "wfo", "freeze",
                 "holdout", "augsep-sensitivity", "robustness", "deliver", "tickcov"]:
        assert name in out, name


def test_chained_deposit_is_what_the_tester_uses():
    """MT5 truncates a fractional Deposit (9873.96 ran as 9873.00): chain on the truncated value (code review)."""
    assert cli.tester_deposit(9873.96) == 9873 and cli.tester_deposit(10000.0) == 10000
