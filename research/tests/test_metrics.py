import math

import numpy as np
import pandas as pd
import pytest

from mt5r import metrics


def trades():
    t = pd.to_datetime
    return pd.DataFrame({
        "open_time": t(["2026-03-02 10:00", "2026-03-02 10:05", "2026-03-02 10:14",
                        "2026-03-02 10:15", "2026-03-02 10:03"]),
        "close_time": t(["2026-03-02 12:00", "2026-03-02 12:01", "2026-03-02 12:02",
                         "2026-03-02 13:00", "2026-03-02 11:00"]),
        "direction": [1, 1, 1, 1, -1],
        "volume": [0.1] * 5,
        "open_price": [1.0] * 5,
        "close_price": [1.0] * 5,
        "profit": [60.0, 50.0, 40.0, -300.0, 80.0],
        "commission": [-2.0] * 5,
        "swap": [0.0, 0.0, 0.0, 0.0, -1.0],
        "balance_at_open": [10000.0, 10000.0, 10000.0, 10000.0, 10000.0],
    })


def test_trade_events_groups_same_bar_same_direction():
    ev = metrics.trade_events(trades())
    assert len(ev) == 3
    first = ev[(ev.direction == 1) & (ev.open_time == pd.Timestamp("2026-03-02 10:00"))].iloc[0]
    assert first.n_positions == 3
    assert first.profit_net == pytest.approx(150.0 - 6.0)
    assert first.ret == pytest.approx(144.0 / 10000.0)
    nxt = ev[ev.open_time == pd.Timestamp("2026-03-02 10:15")].iloc[0]
    assert nxt.n_positions == 1 and nxt.profit_net == pytest.approx(-302.0)
    short = ev[ev.direction == -1].iloc[0]
    assert short.profit_net == pytest.approx(77.0)


def test_summary_balance_drawdown_and_event_stats():
    s = metrics.summary(trades())
    assert s["net_profit"] == pytest.approx(144.0 - 302.0 + 77.0)
    assert s["n_trades"] == 5 and s["n_events"] == 3
    assert s["win_rate"] == pytest.approx(2 / 3)
    assert s["expectancy_per_event"] == pytest.approx((144.0 - 302.0 + 77.0) / 3)
    assert s["profit_factor"] == pytest.approx((144.0 + 77.0) / 302.0)
    # close order: 77 (11:00), 58, 48, 38 -> peak 10221, then -302 -> 9919
    assert s["max_balance_dd_money"] == pytest.approx(302.0)
    assert s["max_balance_dd_pct"] == pytest.approx(302.0 / 10221.0 * 100)


def test_summary_edge_cases():
    only_wins = trades().iloc[[0]]
    assert math.isinf(metrics.summary(only_wins)["profit_factor"])
    empty = trades().iloc[:0]
    s = metrics.summary(empty)
    assert s["n_events"] == 0 and math.isnan(s["profit_factor"])
    assert s["max_balance_dd_money"] == 0.0


def days():
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-03-02", "2026-03-03"]),
        "bal_open": [10000.0, 10100.0],
        "eq_open": [10000.0, 10100.0],
        "eq_min": [9950.0, 9900.0],
        "eq_max": [10200.0, 10150.0],
        "bal_close": [10100.0, 10000.0],
        "eq_close": [10100.0, 10000.0],
        "spread_median": [0.3, 0.3],
    })


def test_summary_equity_drawdown_from_days():
    s = metrics.summary(trades(), days_df=days())
    assert s["max_equity_dd_money"] == pytest.approx(300.0)
    assert s["max_equity_dd_pct"] == pytest.approx(300.0 / 10200.0 * 100)
    assert s["recovery_factor"] == pytest.approx(s["net_profit"] / 300.0)


def test_daily_returns():
    r = metrics.daily_returns(days())
    assert list(r) == pytest.approx([0.01, 10000.0 / 10100.0 - 1])


def test_concentration():
    ev = pd.DataFrame({"profit_net": [100.0, 50.0, -30.0, 20.0]})
    c = metrics.concentration(ev)
    assert c["largest_event_share"] == pytest.approx(100.0 / 140.0)
    assert c["net_without_top2"] == pytest.approx(-10.0)
