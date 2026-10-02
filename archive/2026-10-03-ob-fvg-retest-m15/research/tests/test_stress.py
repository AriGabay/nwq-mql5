import pandas as pd
import pytest

from mt5r import stress


def trades():
    return pd.DataFrame({
        "open_time": pd.to_datetime(["2026-03-02 10:00", "2026-03-03 11:00"]),
        "volume": [0.5, 2.0],
        "profit": [100.0, -50.0],
        "commission": [-4.0, -16.0],
        "swap": [0.0, -1.0],
    })


def test_extra_cost_fixed_spread_scales_with_k():
    t = trades()
    one = stress.extra_cost(t, contract_size=20.0, k_spread=1.0, fixed_spread=0.5)
    two = stress.extra_cost(t, contract_size=20.0, k_spread=2.0, fixed_spread=0.5)
    assert list(one) == pytest.approx([0.5 * 0.5 * 20, 0.5 * 2.0 * 20])
    base = stress.apply(t, one * 0)["profit_net_stressed"]
    stressed = stress.apply(t, two)["profit_net_stressed"]
    assert list(base - stressed) == pytest.approx([2 * 0.5 * 0.5 * 20, 2 * 0.5 * 2.0 * 20])


def test_extra_cost_spread_by_day():
    t = trades()
    spreads = pd.Series([0.3, 0.7], index=pd.to_datetime(["2026-03-02", "2026-03-03"]))
    cost = stress.extra_cost(t, contract_size=1.0, k_spread=1.0, spread_by_day=spreads)
    assert list(cost) == pytest.approx([0.3 * 0.5, 0.7 * 2.0])
    with pytest.raises(ValueError):
        stress.extra_cost(t, contract_size=1.0, k_spread=1.0, spread_by_day=spreads.iloc[:1])
    with pytest.raises(ValueError):
        stress.extra_cost(t, contract_size=1.0, k_spread=1.0)


def test_apply_commission_scale():
    t = trades()
    zero = pd.Series(0.0, index=t.index)
    out = stress.apply(t, zero, commission_scale=1.5)
    assert list(out["profit_net_stressed"]) == pytest.approx([100 - 6.0, -50 - 24.0 - 1.0])
    assert "profit_net_stressed" not in t.columns


def test_stop_exit_slippage_10_points_on_three_sl_exits_of_one_lot():
    t = pd.DataFrame({"volume": [1.0, 1.0, 1.0, 1.0, 1.0],
                      "exit_kind": ["sl", "tp", "sl", "sl", "tp"],
                      "profit": [-100.0, 200.0, -100.0, -100.0, 200.0],
                      "commission": [0.0] * 5, "swap": [0.0] * 5})
    cost = stress.stop_slippage_cost(t, contract_size=100.0, points=10, point=0.01)
    assert list(cost) == pytest.approx([10.0, 0.0, 10.0, 10.0, 0.0])
    out = stress.apply(t, cost)
    assert out["profit_net_stressed"].sum() == pytest.approx(100.0 - 30.0)
    # TP exits unchanged
    tp = out["exit_kind"] == "tp"
    assert list(out.loc[tp, "profit_net_stressed"]) == [200.0, 200.0]


def test_stop_slippage_scales_with_volume_and_ignores_open_trades():
    t = pd.DataFrame({"volume": [0.25, 2.0, 0.5], "exit_kind": ["sl", "sl", ""]})
    cost = stress.stop_slippage_cost(t, contract_size=100.0, points=10, point=0.01)
    assert list(cost) == pytest.approx([2.5, 20.0, 0.0])
