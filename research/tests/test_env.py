
import pytest

from mt5r import env


def _cfg(tmp_path, login=1234567):
    live = tmp_path / "live" / "prefix"
    mt5 = live / "drive_c" / "Program Files" / "MetaTrader 5"
    return env.Config(live_mt5_dir=mt5, live_prefix=live, isolated_prefix=tmp_path / "iso" / "prefix",
                      wine_dir=tmp_path / "wine", server="Srv-1", symbol="XAUUSD.s", login=login)


def _touch(root, rel, text=None):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        p.write_bytes(b"x")
    else:
        env.write_utf16(p, text)
    return p


def test_plan_copy_takes_only_allowlisted_files(tmp_path):
    cfg = _cfg(tmp_path)
    live = cfg.live_mt5_dir
    for rel in ["terminal64.exe", "MQL5/Include/Trade/Trade.mqh", "Bases/Srv-1/ticks/XAUUSD.s/202603.tkc",
                "Bases/Srv-1/history/EURUSD/2026.hcc", "Bases/Srv-1/trades/1/deals.dat", "config/accounts.dat",
                "config/community.ini", "MQL5/Experts/other_ea.ex5", "Bases/Srv-1/symbols/symbols-1.dat"]:
        _touch(live, rel)
    got = env.plan_copy(cfg)
    assert got == ["Bases/Srv-1/symbols/symbols-1.dat", "Bases/Srv-1/ticks/XAUUSD.s/202603.tkc",
                   "MQL5/Include/Trade/Trade.mqh", "terminal64.exe"]


@pytest.mark.parametrize("rel", ["config/accounts.dat", "config/community.ini", "config/certificates/c.cer",
                                 "config/virtualhosts.dat", "Bases/Srv-1/trades/x.dat"])
def test_allowlist_rejects_credential_bearing_files(tmp_path, rel):
    cfg = _cfg(tmp_path)
    _touch(cfg.mt5_dir, "terminal64.exe")
    _touch(cfg.mt5_dir, rel)
    assert rel in env.check_allowlist(cfg)


def test_allowlist_rejects_password_and_unapproved_login(tmp_path):
    cfg = _cfg(tmp_path)
    _touch(cfg.mt5_dir, "config/common.ini", "[Common]\r\nLogin=1234567\r\nPassword=secret\r\n")
    assert "config/common.ini (credential key)" in env.check_allowlist(cfg)
    _touch(cfg.mt5_dir, "config/common.ini", "[Common]\r\nLogin=7654321\r\n")
    assert "config/common.ini (unapproved login)" in env.check_allowlist(cfg)
    _touch(cfg.mt5_dir, "config/common.ini", env.common_ini(cfg))
    assert env.check_allowlist(cfg) == []


def test_common_ini_has_no_password_and_disables_trading(tmp_path):
    text = env.common_ini(_cfg(tmp_path)).lower()
    assert "password" not in text and "allowlivetrading=0" in text and "enabled=0" in text


def test_isolated_prefix_must_not_overlap_live(tmp_path):
    cfg = _cfg(tmp_path)
    env.assert_isolated(cfg)
    for bad in [cfg.live_prefix, cfg.live_prefix / "sub", cfg.live_prefix.parent]:
        cfg.isolated_prefix = bad
        with pytest.raises(RuntimeError):
            env.assert_isolated(cfg)


def test_disable_mcp_turns_servers_off(tmp_path):
    cfg = _cfg(tmp_path)
    _touch(cfg.mt5_dir, "config/assistant.ini",
           "[MCP.MetaTrader]\r\nEnable=1\r\nEndpoint=http://127.0.0.1:22346/mcp\r\nApiKey=abc\r\n")
    env.disable_mcp(cfg)
    from mt5r import textio
    text = textio.read_text(cfg.mt5_dir / "config" / "assistant.ini")
    assert "Enable=1" not in text and "ApiKey=abc" not in text and "[MCP.MetaEditor]\r\nEnable=0" in text


def test_disable_mcp_fills_a_section_without_enable(tmp_path):
    cfg = _cfg(tmp_path)
    _touch(cfg.mt5_dir, "config/common.ini", env.common_ini(cfg))
    _touch(cfg.mt5_dir, "config/assistant.ini", "[MCP.MetaTrader]\r\nEnable=0\r\n[MCP.Custom]\r\n")
    with pytest.raises(RuntimeError, match=r"MCP.Custom"):
        env.assert_trade_safety(cfg)
    env.disable_mcp(cfg)
    env.assert_trade_safety(cfg)


def _safe_copy(cfg):
    _touch(cfg.mt5_dir, "config/common.ini", env.common_ini(cfg))
    _touch(cfg.mt5_dir, "config/assistant.ini", "[MCP.MetaEditor]\r\nEnable=0\r\n[MCP.MetaTrader]\r\nEnable=0\r\n")


def test_trade_safety_passes_for_generated_config(tmp_path):
    cfg = _cfg(tmp_path)
    _safe_copy(cfg)
    env.assert_trade_safety(cfg)


@pytest.mark.parametrize("common, assistant, msg", [
    ("[Common]\r\n[Experts]\r\nAllowLiveTrading=1\r\nEnabled=0\r\n", None, "AllowLiveTrading=0"),
    ("[Common]\r\n[Experts]\r\nAllowLiveTrading=0\r\nEnabled=1\r\n", None, "Enabled=0"),
    ("[Common]\r\nPassword=x\r\n[Experts]\r\nAllowLiveTrading=0\r\nEnabled=0\r\n", None, "stores a password"),
    ("[Common]\r\n", None, "AllowLiveTrading=0"),
    (None, "[MCP.MetaTrader]\r\nEnable=1\r\n", "Enable=0"),
])
def test_trade_safety_refuses_unsafe_config(tmp_path, common, assistant, msg):
    cfg = _cfg(tmp_path)
    _safe_copy(cfg)
    if common:
        _touch(cfg.mt5_dir, "config/common.ini", common)
    if assistant:
        _touch(cfg.mt5_dir, "config/assistant.ini", assistant)
    with pytest.raises(RuntimeError, match=msg):
        env.assert_trade_safety(cfg)


def test_install_sources_copies_both_builds_into_isolated_copy(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    repo = tmp_path / "repo"
    for name, text in (("ob_m1_structure.mq5", "// strategy"), ("ob_m1_structure_research.mq5", "#define RESEARCH_LOG")):
        p = repo / "mql5" / "Experts" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    monkeypatch.setattr(env, "REPO", repo)
    env.install_sources(cfg)
    dst = cfg.isolated_prefix / "drive_c" / "mt5r" / "MQL5" / "Experts"
    assert sorted(p.name for p in dst.iterdir()) == ["ob_m1_structure.mq5", "ob_m1_structure_research.mq5"]
    assert (dst / "ob_m1_structure_research.mq5").read_text() == "#define RESEARCH_LOG"
    assert not (cfg.live_mt5_dir / "MQL5").exists()


def test_install_sources_refuses_overlapping_prefix(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cfg.isolated_prefix = cfg.live_prefix / "nested"
    monkeypatch.setattr(env, "REPO", tmp_path / "repo")
    with pytest.raises(RuntimeError, match="overlaps"):
        env.install_sources(cfg)


def _native_cfg(tmp_path):
    return env.Config(live_mt5_dir=tmp_path / "Program Files" / "MetaTrader 5", live_prefix=tmp_path / "data",
                      isolated_prefix=tmp_path / "mt5r", wine_dir=None, server="Srv-1", symbol="XAUUSD.s",
                      login=1234567, live_data_dir=tmp_path / "data")


def test_native_copy_takes_binaries_from_install_and_rest_from_data_dir(tmp_path):
    cfg = _native_cfg(tmp_path)
    for rel in ["terminal64.exe", "MetaEditor64.exe", "uninstall.exe", "MQL5/Include/x.mqh"]:
        _touch(cfg.live_mt5_dir, rel)
    for rel in ["bases/Srv-1/ticks/XAUUSD.s/202603.tkc", "bases/Srv-1/trades/1/deals.dat", "config/accounts.dat",
                "config/servers.dat", "MQL5/Include/Trade/Trade.mqh", "terminal64.exe"]:
        _touch(cfg.data_dir, rel)
    src = env.copy_sources(cfg)
    assert list(src) == ["MQL5/Include/Trade/Trade.mqh", "MetaEditor64.exe", "bases/Srv-1/ticks/XAUUSD.s/202603.tkc",
                         "config/servers.dat", "terminal64.exe"]
    assert src["terminal64.exe"].parent == cfg.live_mt5_dir and src["config/servers.dat"].parent.parent == cfg.data_dir


def test_native_paths_and_isolation(tmp_path):
    cfg = _native_cfg(tmp_path)
    assert cfg.native and cfg.mt5_dir == cfg.isolated_prefix and cfg.launcher() == []
    assert cfg.win_path("MQL5/Experts/a.mq5") == str(cfg.mt5_dir) + r"\MQL5\Experts\a.mq5"
    assert "DYLD_FALLBACK_LIBRARY_PATH" not in cfg.env()
    env.assert_isolated(cfg)
    for bad in [cfg.live_mt5_dir / "mt5r", cfg.data_dir / "x", cfg.live_mt5_dir.parent]:
        cfg.isolated_prefix = bad
        with pytest.raises(RuntimeError, match="overlaps"):
            env.assert_isolated(cfg)
