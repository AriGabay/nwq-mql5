import pathlib

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
    text = env._read_text(cfg.mt5_dir / "config" / "assistant.ini")
    assert "Enable=1" not in text and "ApiKey=abc" not in text and "[MCP.MetaEditor]\r\nEnable=0" in text
