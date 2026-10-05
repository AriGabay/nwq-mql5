"""duka_cli without network or MT5: scope, offline import config, no trading calls, tick files, comparisons."""
import pandas as pd
import pytest

import duka_cli as dc


def test_only_the_two_approved_months():
    assert dc.check_month("2024-01") == (2024, 1) and dc.check_month("2026-03") == (2026, 3)
    with pytest.raises(SystemExit, match="only"):
        dc.check_month("2025-06")


def test_the_import_start_is_offline_with_trading_off_and_shuts_down():
    ini = dc.startup_ini()
    assert "Server=nwq-offline-no-server" in ini and "Login=0" in ini
    assert "ProxyEnable=1" in ini and "ProxyAddress=127.0.0.1:9" in ini
    assert "AllowLiveTrading=0" in ini and "Enabled=0" in ini and "AllowDllImport=0" in ini
    assert "Script=duka_import" in ini and "ShutdownTerminal=1" in ini
    assert "Password" not in ini


def test_the_import_script_has_no_trading_calls_and_a_script_with_one_is_refused(tmp_path):
    dc.assert_no_trading_calls(dc.SRC_DIR / dc.IMPORT_SCRIPT)
    src = (dc.SRC_DIR / dc.IMPORT_SCRIPT).read_text(encoding="utf-8")
    assert "TERMINAL_CONNECTED" in src and "TERMINAL_TRADE_ALLOWED" in src and "MQL_TESTER" in src
    bad = tmp_path / "x.mq5"
    bad.write_text('#include <Trade/Trade.mqh>\nvoid OnStart(){ CTrade t; }', encoding="utf-8")
    with pytest.raises(SystemExit, match="trading calls"):
        dc.assert_no_trading_calls(bad)


def test_the_dump_ea_never_trades_and_runs_only_in_the_tester():
    src = (dc.SRC_DIR / dc.DUMP_EA).read_text(encoding="utf-8")
    assert not any(c in src for c in dc.TRADE_CALLS) and "MQL_TESTER" in src


def test_tick_files_round_trip(tmp_path):
    ticks = pd.DataFrame({"time_msc": [1, 1, 5], "bid": [2000.123, 2000.124, 2000.0], "ask": [2000.4, 2000.5, 2000.3]})
    p = tmp_path / "t.bin"
    dc.write_ticks_bin(p, ticks, 0, 9)
    frm, to, back = dc.read_ticks_bin(p)
    assert (frm, to) == (0, 9) and back.equals(ticks)


def test_compare_ticks_reports_generated_and_dropped_ticks_separately():
    src = pd.DataFrame({"time_msc": [1, 1, 2, 3], "bid": [1.0, 1.0, 1.001, 1.002], "ask": [1.1, 1.1, 1.1, 1.1]})
    same = dc.compare_ticks(src, src.copy(), "x")
    assert same["identical_in_order"] and same["extra_in_target"] == 0 and same["missing_in_target"] == 0
    got = pd.DataFrame({"time_msc": [1, 2, 3, 4], "bid": [1.0, 1.001, 1.002, 1.003], "ask": [1.1, 1.1, 1.1, 1.1]})
    res = dc.compare_ticks(src, got, "x")
    assert res["missing_in_target"] == 1 and res["extra_in_target"] == 1 and not res["identical_in_order"]
    assert res["first_extra"][0]["bid"] == 1.003


# ------------------------------------------------------------------ download statuses (no network)
def _fake_feed(monkeypatch, tmp_path, answers):
    """answers: callable(url, call_no) -> (status, body) or raises; sleeps are recorded, not slept."""
    import types
    monkeypatch.setattr(dc, "RAW", tmp_path / "raw")
    monkeypatch.setattr(dc, "RESULTS", tmp_path / "results")
    slept, calls = [], {"n": 0}
    monkeypatch.setattr(dc.time, "sleep", lambda s: slept.append(s))

    def fetch(u, tries=4):
        calls["n"] += 1
        return answers(u, calls["n"])
    monkeypatch.setattr(dc, "fetch", fetch)
    return slept, types.SimpleNamespace(month="2024-01", pause=0.0, wait=900, max_hours=1.0)


def test_a_429_waits_retry_after_and_retries_the_same_hour(monkeypatch, tmp_path):
    def answers(u, n):
        if n == 3:
            raise dc.RateLimited(u, 37)
        return 200, b""
    slept, args = _fake_feed(monkeypatch, tmp_path, answers)
    dc.cmd_download(args)
    st = dc.load_status("2024-01")
    assert 37 in slept and st["rate_limits"][0]["retry_after"] == 37
    assert len(st["hours"]) == 744 and all(v["status"] == "empty_response" for v in st["hours"].values())


def test_without_retry_after_the_default_wait_is_used(monkeypatch, tmp_path):
    def answers(u, n):
        if n == 1:
            raise dc.RateLimited(u, None)
        return 200, b""
    slept, args = _fake_feed(monkeypatch, tmp_path, answers)
    dc.cmd_download(args)
    assert 900 in slept and dc.load_status("2024-01")["rate_limits"][0]["waited_s"] == 900


def test_a_network_failure_leaves_the_hour_unresolved_and_the_month_partial(monkeypatch, tmp_path):
    def answers(u, n):
        if "/02/10h_" in u:
            raise dc.NetworkError(u)
        if "/03/10h_" in u:
            return 404, b""
        return 200, b""
    _, args = _fake_feed(monkeypatch, tmp_path, answers)
    dc.cmd_download(args)
    st = dc.load_status("2024-01")["hours"]
    assert st["2024-01-02T10:00:00+00:00"]["status"] == "unresolved"
    assert st["2024-01-03T10:00:00+00:00"]["status"] == "http_404"
    dc.cmd_manifest(args)
    man = __import__("json").loads((dc.RESULTS / "manifest_2024-01.json").read_text())
    assert man["complete"] is False and man["hours_pending"] == 1
    with pytest.raises(SystemExit, match="partial"):
        dc.require_complete("2024-01", False)
    dc.require_complete("2024-01", True)


def test_an_empty_hour_counts_as_closed_only_inside_an_observed_pause():
    import datetime as dt
    t = lambda s: int(dt.datetime.fromisoformat(s + "+00:00").timestamp() * 1000)
    ticks = pd.DataFrame({"time_msc": [t("2024-01-05T21:59:00"), t("2024-01-07T23:00:30"),
                                       t("2024-01-08T09:59:00"), t("2024-01-08T10:59:59")]})
    files = [{"hour": "2024-01-06T12:00:00+00:00"}, {"hour": "2024-01-08T10:00:00+00:00"}]
    assert dc.closed_hours(files, ticks) == {"2024-01-06T12:00:00+00:00"}
