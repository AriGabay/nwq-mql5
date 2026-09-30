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
