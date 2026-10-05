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
    ini = dc.startup_ini("Some-Server")
    assert "Server=Some-Server" in ini and "Login=0" in ini
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

    def fetch(u, budget, tries=4):
        budget.request()                                # one HTTP request, charged like the real fetch
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


def test_retries_inside_fetch_are_charged_and_cannot_pass_the_budget(monkeypatch, tmp_path):
    import urllib.error
    monkeypatch.setattr(dc, "RAW", tmp_path / "raw")
    monkeypatch.setattr(dc.time, "sleep", lambda s: None)
    sent = []

    def urlopen(req, timeout=30):
        sent.append(req.full_url)
        raise urllib.error.URLError("connection reset")
    monkeypatch.setattr(dc.urllib.request, "urlopen", urlopen)
    dc._write_json(dc.budget_path(), {"max_requests": 7, "max_wait_seconds": 10_000, "requests": 5, "wait_seconds": 0})
    budget = dc.Budget()
    with pytest.raises(dc.BudgetSpent, match="request budget spent"):
        dc.fetch("https://x/2024/00/02/10h_ticks.bi5", budget, tries=4)
    assert len(sent) == 2                                  # only the 2 requests left, not the 4 retries
    b = dc.load_budget()
    assert b["requests"] == 7 and b["wait_seconds"] == 5    # the pause between them was charged too


def test_a_wait_that_would_pass_the_budget_is_not_slept(monkeypatch, tmp_path):
    monkeypatch.setattr(dc, "RAW", tmp_path / "raw")
    slept = []
    monkeypatch.setattr(dc.time, "sleep", lambda s: slept.append(s))
    dc._write_json(dc.budget_path(), {"max_requests": 99, "max_wait_seconds": 1000, "requests": 0, "wait_seconds": 950})
    with pytest.raises(dc.BudgetSpent, match="wait budget would be exceeded"):
        dc.Budget().wait(900)
    assert slept == [] and dc.load_budget()["wait_seconds"] == 950


def test_the_existing_budget_is_kept_and_marked_as_possibly_undercounted(monkeypatch, tmp_path):
    monkeypatch.setattr(dc, "RAW", tmp_path / "raw")
    dc._write_json(dc.budget_path(), {"max_requests": 2500, "max_wait_seconds": 43200, "requests": 41, "wait_seconds": 900})
    b = dc.Budget().b
    assert b["requests"] == 41 and b["wait_seconds"] == 900 and b["counting"]["count_before"] == 41
    assert "lower than the requests actually sent" in b["counting"]["note"]
    assert dc.load_budget()["counting"] == b["counting"]


def test_the_sample_import_passes_only_its_file_and_still_starts_offline():
    ini = dc.startup_ini("Some-Server", "duka_import_sample.set")
    assert "ScriptParameters=duka_import_sample.set" in ini
    assert ini.index("ScriptParameters") < ini.index("ShutdownTerminal=1")
    assert "Login=0" in ini and "ProxyAddress=127.0.0.1:9" in ini and "AllowLiveTrading=0" in ini
    assert "ScriptParameters" not in dc.startup_ini("Some-Server")


def test_the_sample_is_one_hour_inside_the_approved_january_and_only_for_the_import_checks():
    assert dc.SAMPLE_HOUR.strftime("%Y-%m") in dc.MONTHS and dc.SAMPLE_HOUR.tzinfo is not None
    assert set(dc.SAMPLE_OK) == {"verify-import", "tester-dump", "verify-tester"}
    assert dc.bin_name(dc.SAMPLE) not in {dc.bin_name(m) for m in dc.MONTHS}
    import argparse
    with pytest.raises(SystemExit, match="only"):
        dc.cmd_tester_dump(argparse.Namespace(symbol=dc.BYBIT, month=dc.SAMPLE))


def test_raw_data_folders_are_ignored_by_git():
    ignore = (dc.REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/data/" in ignore and "runs/" in ignore


def test_only_this_runs_journal_lines_are_checked_and_account_numbers_are_masked():
    tab, eol = chr(9), chr(13) + chr(10)
    old = tab.join(["", "0", "00:31:34", "Network", "'1234567': authorized on X-Live through Y"]) + eol
    new = tab.join(["", "0", "19:46:18", "Terminal", "launched"]) + eol
    data = b"\xff\xfe" + old.encode("utf-16-le")
    start = len(data)
    data += new.encode("utf-16-le")
    assert dc.run_part(data, start) == new
    assert dc.connection_lines(dc.run_part(data, start)) == []
    hits = dc.connection_lines(dc.run_part(data, 0))
    assert len(hits) == 1 and "1234567" not in hits[0] and "<number>" in hits[0]


def test_the_manual_import_file_keeps_feed_milliseconds_pairs_order_and_exact_prices():
    t0 = int(dc.SAMPLE_HOUR.timestamp() * 1000)
    ticks = pd.DataFrame({"time_msc": [t0 + 128, t0 + 128, t0 + 999, t0 + 3_599_210],
                          "raw_bid": [2076965, 2076965, 2076000, 2075925], "raw_ask": [2077255, 2077255, 2076005, 2076265]})
    lines = dc.mt5_tick_lines(ticks)
    assert lines[0] == "\t".join(["<DATE>", "<TIME>", "<BID>", "<ASK>", "<LAST>", "<VOLUME>"])
    assert lines[1].split("\t") == ["2024.01.02", "10:00:00.128", "2076.965", "2077.255", "0.000", "0"]
    assert lines[3].split("\t")[1:4] == ["10:00:00.999", "2076.000", "2076.005"]      # .999 stays in its second
    assert len(lines) == 5                                                           # the identical pair is kept
    back = dc.read_mt5_tick_lines("\r\n".join(lines))
    assert back["time_msc"].tolist() == ticks["time_msc"].tolist()
    src = ticks.assign(bid=ticks["raw_bid"] / 1000, ask=ticks["raw_ask"] / 1000)
    assert dc.compare_ticks(src, back, "x")["identical_in_order"]


def test_the_manual_session_config_is_offline_with_trading_off_and_starts_nothing():
    ini = dc.gui_ini("Some-Server")
    assert "Login=0" in ini and "ProxyEnable=1" in ini and "ProxyAddress=127.0.0.1:9" in ini
    assert "AllowLiveTrading=0" in ini and "Enabled=0" in ini
    assert "[StartUp]" not in ini and "Script=" not in ini and "Password" not in ini
