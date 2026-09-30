
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
