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
        stdout = ("1 wine64-preloader C:\\Program Files\\MetaTrader 5\\terminal64.exe\n"
                  "2 wine64-preloader C:\\mt5r\\terminal64.exe /portable\n")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: R())
    assert runner.isolated_processes() == ["2 wine64-preloader C:\\mt5r\\terminal64.exe /portable"]
