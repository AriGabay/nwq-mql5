"""duka_cli without network or MT5: scope, offline import config, no trading calls, tick files, comparisons."""
import datetime as dt
import json

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
    return slept, types.SimpleNamespace(month="2024-01", pause=0.0, wait=900, max_hours=1.0, max_failures=2)


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


def test_a_dropped_connection_pauses_like_a_rate_limit_then_retries_the_same_hour(monkeypatch, tmp_path):
    seen = []

    def answers(u, n):
        if "/02/10h_" in u and u not in seen:          # the first attempt at this hour is dropped
            seen.append(u)
            raise dc.NetworkError(u)
        return 200, b""
    slept, args = _fake_feed(monkeypatch, tmp_path, answers)
    dc.cmd_download(args)
    st = dc.load_status("2024-01")
    assert st["hours"]["2024-01-02T10:00:00+00:00"]["status"] == "empty_response"
    assert st["rate_limits"][0]["kind"] == "network" and 900 in slept


def test_repeated_network_failures_stop_the_run_and_leave_the_month_partial(monkeypatch, tmp_path):
    def answers(u, n):
        if "/02/10h_" in u:
            raise dc.NetworkError(u)
        if "/01/10h_" in u:
            return 404, b""
        return 200, b""
    _, args = _fake_feed(monkeypatch, tmp_path, answers)
    with pytest.raises(SystemExit, match="partial"):
        dc.cmd_download(args)
    st = dc.load_status("2024-01")["hours"]
    assert st["2024-01-02T10:00:00+00:00"]["status"] == "unresolved"
    assert st["2024-01-01T10:00:00+00:00"]["status"] == "http_404"
    dc.cmd_manifest(args)
    man = json.loads((dc.RESULTS / "manifest_2024-01.json").read_text())
    assert man["download_resolved"] is False and man["coverage_confirmed"] is False
    with pytest.raises(SystemExit, match="partial"):
        dc.require_resolved("2024-01", False)
    dc.require_resolved("2024-01", True)


# ------------------------------------------------------------------ coverage: what counts as a closed hour
UTC = dt.timezone.utc
H = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_the_closed_schedule_is_new_york_time_and_follows_daylight_saving():
    assert dc.schedule_closed(H("2024-01-06T12:00"))                  # Saturday
    assert not dc.schedule_closed(H("2024-01-05T21:00"))              # Friday 16:00 New York: still open
    assert dc.schedule_closed(H("2024-01-05T22:00"))                  # Friday 17:00 New York: closed
    assert dc.schedule_closed(H("2024-01-07T22:00"))                  # Sunday 17:00 New York
    assert not dc.schedule_closed(H("2024-01-07T23:00"))              # Sunday 18:00 New York: open
    assert dc.schedule_closed(H("2024-01-03T22:00"))                  # Wednesday daily break (EST)
    assert dc.schedule_closed(H("2026-03-11T21:00")) and not dc.schedule_closed(H("2026-03-11T22:00"))   # EDT
    assert not dc.schedule_closed(H("2024-01-01T12:00"))              # a holiday is never assumed closed


def row(hour, status):
    return {"hour": H(hour).isoformat(), "file": None if status in ("http_404", "not_downloaded") else "f",
            "status": status}


def test_only_answered_hours_inside_the_schedule_count_as_closed():
    rows = dc.classify_hours([row("2024-01-06T12:00", "empty_response"),     # Saturday: closed
                              row("2024-01-03T14:00", "http_404"),           # a trading hour answered 404
                              row("2024-01-03T15:00", "empty_response"),     # a trading hour answered empty
                              row("2024-01-05T15:00", "empty_response"),     # a gap that starts before the close
                              row("2024-01-01T12:00", "empty_response"),     # New Year: no verified basis
                              row("2024-01-06T13:00", "not_downloaded"),     # never fetched, even on a Saturday
                              row("2024-01-03T16:00", "file_with_ticks")])
    assert [r["coverage"] for r in rows] == ["closed_verified", "unverified", "unverified", "unverified",
                                            "unverified", "pending", "ticks"]


def tick_blob():
    import lzma
    import struct
    return lzma.compress(struct.pack(">IIIff", 1000, 2050500, 2050200, 1.0, 1.0), format=lzma.FORMAT_ALONE)


def _feed_by_hour(closed_ok=True, trading_404=None, trading_empty=None):
    """Ticks in every trading hour, empty answers in schedule-closed hours, and the given exceptions."""
    def answers(u, n):
        parts = u.split("/")
        h = dt.datetime(int(parts[-4]), int(parts[-3]) + 1, int(parts[-2]), int(parts[-1][:2]), tzinfo=UTC)
        if h.isoformat() == trading_404:
            return 404, b""
        if h.isoformat() == trading_empty:
            return 200, b""
        return (200, b"") if dc.schedule_closed(h) else (200, tick_blob())
    return answers


@pytest.mark.parametrize("case", ["clean", "404_in_trading_hour", "empty_trading_hour"])
def test_full_coverage_needs_every_open_hour_with_ticks(monkeypatch, tmp_path, case):
    feed = {"clean": _feed_by_hour(),
            "404_in_trading_hour": _feed_by_hour(trading_404="2024-01-10T14:00:00+00:00"),
            "empty_trading_hour": _feed_by_hour(trading_empty="2024-01-10T14:00:00+00:00")}[case]
    _, args = _fake_feed(monkeypatch, tmp_path, feed)
    dc.cmd_download(args)
    dc.cmd_manifest(args)
    man = json.loads((dc.RESULTS / "manifest_2024-01.json").read_text())
    assert man["download_resolved"] is True
    assert man["coverage_confirmed"] is (case == "clean")
    if case != "clean":
        assert man["unverified_hours"] == ["2024-01-10T14:00:00+00:00"]


def test_the_check_reads_the_current_files_not_a_stale_manifest(monkeypatch, tmp_path):
    _, args = _fake_feed(monkeypatch, tmp_path, _feed_by_hour())
    dc.cmd_download(args)
    dc.cmd_manifest(args)
    dc.require_resolved("2024-01", False)
    dc.raw_path(H("2024-01-10T14:00")).write_bytes(b"")             # a file changes after the manifest
    with pytest.raises(SystemExit, match="does not describe the current files"):
        dc.require_resolved("2024-01", False)


def test_the_budget_is_shared_between_runs_and_stops_with_a_partial_summary(monkeypatch, tmp_path, capsys):
    _, args = _fake_feed(monkeypatch, tmp_path, lambda u, n: (200, b""))
    dc._write_json(dc.budget_path(), {"max_requests": 10, "max_wait_seconds": 100, "requests": 0, "wait_seconds": 0})
    with pytest.raises(SystemExit, match="partial - 734 hours missing"):
        dc.cmd_download(args)
    assert dc.load_budget()["requests"] == 10
    with pytest.raises(SystemExit, match="request budget spent"):
        dc.cmd_download(args)                                           # the next run starts with nothing left
    assert dc.load_status("2024-01")["runs"][-1]["hours_missing"] == 734


def test_a_parallel_download_is_refused(monkeypatch, tmp_path):
    import os
    _, args = _fake_feed(monkeypatch, tmp_path, lambda u, n: (200, b""))
    lock = dc.RAW / dc.SYMBOL / "download.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": os.getpid()}))
    with pytest.raises(SystemExit, match="another download is running"):
        dc.cmd_download(args)
