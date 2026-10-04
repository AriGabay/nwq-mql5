import io

import pandas as pd
import pytest

from mt5r import reports, trades

SETUPS = """setup_id,dir,entry,sl,tp,volume,fill_time_msc,fill_price,position_id,exit_kind,reason
1,L,2000.00,1995.00,2010.00,0.20,1767261000000,2000.00,11,tp,filled
2,S,2100.00,2104.00,2092.00,0.25,1767347400500,2099.95,12,sl,filled
3,L,2050.00,2046.00,2058.00,0.10,1767433800250,2049.90,13,tp,filled_late
4,L,2060.00,2055.00,2070.00,,,,,,expired_unfilled
"""

DEALS = """time,ticket,position_id,type,entry,volume,price,profit,commission,swap,magic,comment
2026.01.01 00:00:00,1,0,2,0,0.00,0.00,10000.00,0.00,0.00,0,
2026.01.01 10:30:00,2,11,0,0,0.20,2000.00,0.00,-1.00,0.00,770101,OBR
2026.01.01 12:00:00,3,11,1,1,0.20,2010.00,200.00,-1.00,-0.50,770101,tp 2010.00
2026.01.02 10:30:00,4,12,1,0,0.25,2099.95,0.00,0.00,0.00,770101,OBR
2026.01.02 11:00:00,5,12,0,1,0.25,2104.00,-101.25,0.00,0.00,770101,sl 2104.00
2026.01.03 10:30:00,6,13,0,0,0.10,2049.90,0.00,0.00,0.00,770101,OBR
2026.01.03 14:00:00,7,13,1,1,0.10,2058.00,81.00,0.00,-2.00,770101,tp 2058.00
"""


def _deals(tmp_path):
    p = tmp_path / "rl_deals_x.csv"
    p.write_text(DEALS)
    return reports.read_deals(p)


def test_trade_table_winner_loser_and_late_fill(tmp_path):
    setups = pd.read_csv(io.StringIO(SETUPS))
    t = trades.trade_table(setups, _deals(tmp_path)).set_index("setup_id")
    assert list(t.index) == [1, 2, 3]            # the unfilled setup is not a trade
    for col in ["dir", "open_time", "intended_entry", "fill_price", "sl", "tp", "planned_rr", "realized_r",
                "gross_profit", "commission", "swap", "net", "exit_kind", "reason"]:
        assert col in t.columns, col

    w = t.loc[1]                                  # long winner
    assert (w["dir"], w["intended_entry"], w["fill_price"], w["sl"], w["tp"]) == ("L", 2000.0, 2000.0, 1995.0, 2010.0)
    assert w["planned_rr"] == pytest.approx(2.0)
    assert w["gross_profit"] == pytest.approx(200.0) and w["commission"] == pytest.approx(-2.0)
    assert w["swap"] == pytest.approx(-0.5) and w["net"] == pytest.approx(197.5)
    # risk = 5.00 * 0.20 lot * 100 = 100 USD
    assert w["realized_r"] == pytest.approx(1.975)
    assert w["open_time"] == pd.Timestamp("2026-01-01 10:30:00")

    l = t.loc[2]                                  # short loser, filled 0.05 better than intended
    assert (l["dir"], l["fill_price"], l["exit_kind"]) == ("S", 2099.95, "sl")
    assert l["planned_rr"] == pytest.approx(2.0)
    assert l["net"] == pytest.approx(-101.25)
    assert l["realized_r"] == pytest.approx(-101.25 / 100.0)   # 4.00 * 0.25 * 100

    late = t.loc[3]
    assert late["reason"] == "filled_late" and late["net"] == pytest.approx(79.0)
    assert late["planned_rr"] == pytest.approx(2.0)
    assert late["realized_r"] == pytest.approx(79.0 / 40.0)   # 4.00 * 0.10 * 100


def test_trade_table_net_matches_deal_totals(tmp_path):
    setups = pd.read_csv(io.StringIO(SETUPS))
    t = trades.trade_table(setups, _deals(tmp_path))
    assert t["net"].sum() == pytest.approx(197.5 - 101.25 + 79.0)


def test_trade_table_keeps_positions_without_setup_row(tmp_path):
    setups = pd.read_csv(io.StringIO(SETUPS)).iloc[:1]
    t = trades.trade_table(setups, _deals(tmp_path))
    assert len(t) == 3
    assert t["net"].sum() == pytest.approx(197.5 - 101.25 + 79.0)
    orphan = t[t["setup_id"].isna()]
    assert len(orphan) == 2 and orphan["realized_r"].isna().all()


def test_trade_table_empty(tmp_path):
    setups = pd.read_csv(io.StringIO(SETUPS)).iloc[3:]
    deals = _deals(tmp_path).iloc[:1]
    t = trades.trade_table(setups, deals)
    assert t.empty and "net" in t.columns


def test_trade_table_without_setups_file(tmp_path):
    t = trades.trade_table(pd.DataFrame(columns=["position_id"]), _deals(tmp_path))
    assert len(t) == 3 and t["setup_id"].isna().all()
    assert t["net"].sum() == pytest.approx(197.5 - 101.25 + 79.0)
    assert list(t["fill_price"]) == [2000.0, 2099.95, 2049.90]


def test_exit_kind_falls_back_to_exit_deal_comment(tmp_path):
    t = trades.trade_table(pd.DataFrame(columns=["position_id"]), _deals(tmp_path))
    assert list(t["exit_kind"]) == ["tp", "sl", "tp"]


def test_m1_log_request_price_is_the_intended_entry(tmp_path):
    """The M5/M1 EA logs the Market request price as request_price (m1_contract); it is the intended entry."""
    setups = pd.read_csv(io.StringIO(SETUPS)).rename(columns={"entry": "request_price"})
    t = trades.trade_table(setups, _deals(tmp_path)).set_index("setup_id")
    assert t.loc[1, "intended_entry"] == 2000.0 and t.loc[1, "planned_rr"] == pytest.approx(2.0)
