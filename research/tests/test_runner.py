import pathlib
import re

from mt5r import runner

SRC = (pathlib.Path(runner.__file__)).read_text()


def test_no_name_based_kill_or_prefix_reset():
    code = "\n".join(l for l in SRC.splitlines() if not l.strip().startswith(("#", '"""')))
    assert not re.search(r"\b(pkill|killall|wineboot)\b", code.split('"""', 2)[-1])


def test_every_wineserver_call_uses_isolated_env():
    calls = [l for l in SRC.splitlines() if "wineserver" in l and "subprocess" in l]
    assert calls and all("cfg.env()" in l for l in calls)


def test_shutdown_refuses_overlapping_prefix(tmp_path, monkeypatch):
    from mt5r import env
    live = tmp_path / "live"
    cfg = env.Config(live_mt5_dir=live, live_prefix=live, isolated_prefix=live / "inner", wine_dir=tmp_path,
                     server="S", symbol="X")
    called = []
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: called.append(a))
    import pytest
    with pytest.raises(RuntimeError):
        runner.shutdown_prefix(cfg)
    assert called == []


def test_isolated_process_filter(monkeypatch):
    class R:
        returncode = 0
        stdout = ("1 wine64-preloader C:\\Program Files\\MetaTrader 5\\terminal64.exe\n"
                  "2 wine64-preloader C:\\mt5r\\terminal64.exe /portable\n")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: R())
    assert runner.isolated_processes() == ["2 wine64-preloader C:\\mt5r\\terminal64.exe /portable"]


def test_live_terminal_detection(monkeypatch):
    class R:
        returncode = 0
        stdout = "1 wine64-preloader C:\\Program Files\\MetaTrader 5\\terminal64.exe \n"
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: R())
    assert runner.live_terminal_running()


def test_run_refuses_while_live_terminal_runs(monkeypatch, tmp_path):
    import pytest
    from mt5r import env
    monkeypatch.setattr(runner, "live_terminal_running", lambda: True)
    monkeypatch.setattr(runner, "_free_gb", lambda p: 100.0)
    cfg = env.Config(live_mt5_dir=tmp_path, live_prefix=tmp_path / "l", isolated_prefix=tmp_path / "i",
                     wine_dir=tmp_path, server="S", symbol="X")
    with pytest.raises(RuntimeError, match="live MT5 terminal is running"):
        runner.run(cfg, "x", "", "e.ex5")


def test_run_refuses_unsafe_isolated_config(monkeypatch, tmp_path):
    import pytest
    from mt5r import env
    monkeypatch.setattr(runner, "live_terminal_running", lambda: False)
    monkeypatch.setattr(runner, "isolated_processes", lambda cfg=None: [])
    monkeypatch.setattr(runner, "_free_gb", lambda p: 100.0)
    launched = []
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: launched.append(a))
    cfg = env.Config(live_mt5_dir=tmp_path, live_prefix=tmp_path / "l", isolated_prefix=tmp_path / "i",
                     wine_dir=tmp_path, server="S", symbol="X")
    env.write_utf16(cfg.mt5_dir / "config" / "common.ini", "[Experts]\r\nAllowLiveTrading=1\r\nEnabled=1\r\n")
    with pytest.raises(RuntimeError, match="not trade-safe"):
        runner.run(cfg, "x", "", "e.ex5")
    assert launched == []


def test_native_shutdown_ends_only_isolated_pids(tmp_path, monkeypatch):
    import pytest
    from mt5r import env
    cfg = env.Config(live_mt5_dir=tmp_path / "live", live_prefix=tmp_path / "data", isolated_prefix=tmp_path / "mt5r",
                     wine_dir=None, server="S", symbol="X")
    procs = [r"11 C:\Program Files\MetaTrader 5\terminal64.exe x", f"22 {cfg.mt5_dir}" + r"\terminal64.exe /portable"]
    killed = []

    def fake_run(cmd, **k):
        if cmd[0] == "taskkill":
            killed.append(cmd[2])
            procs[:] = [p for p in procs if not p.startswith(cmd[2] + " ")]
        return type("R", (), {"returncode": 0, "stdout": "\n".join(procs)})()
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.isolated_processes(cfg) == [procs[1]]
    runner.shutdown_prefix(cfg)
    assert killed == ["22"] and runner.isolated_processes(cfg) == []
    assert "pkill" not in SRC and "/IM" not in SRC


def test_unreadable_mt5_process_blocks_but_is_never_killed(tmp_path, monkeypatch):
    import pytest
    from mt5r import env
    cfg = env.Config(live_mt5_dir=tmp_path / "live", live_prefix=tmp_path / "data", isolated_prefix=tmp_path / "mt5r",
                     wine_dir=None, server="S", symbol="X")
    lines = ["3728 " + runner.UNREADABLE + "terminal64.exe",
             "8208 C:/Edge/msedgewebview2.exe --user-data-dir=" + str(cfg.mt5_dir) + "/temp"]
    killed = []
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)

    def fake_run(cmd, **k):
        if cmd[0] == "taskkill":
            killed.append(cmd[2])
        return type("R", (), {"returncode": 0, "stdout": "\n".join(lines)})()
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.live_terminal_running()
    assert runner.isolated_processes(cfg) == [lines[0]]
    with pytest.raises(RuntimeError, match="still alive"):
        runner.shutdown_prefix(cfg)
    assert killed == []


def test_process_list_failure_fails_closed(monkeypatch):
    """If the process list cannot be read, the live-terminal check must not report 'closed'."""
    import pytest
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "", "stderr": "denied"})())
    with pytest.raises(RuntimeError, match="process list"):
        runner.live_terminal_running()
