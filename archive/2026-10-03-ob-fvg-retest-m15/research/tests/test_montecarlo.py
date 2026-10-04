import numpy as np
import pandas as pd
import pytest

from mt5r import montecarlo, stats


def test_shuffle_final_equity_invariant_dd_varies():
    r = np.array([0.02, -0.01, -0.015, 0.03, -0.02, 0.01, -0.005, 0.025])
    out = montecarlo.shuffle_paths(r, n_paths=500)
    assert np.allclose(out["final_equity"], 10000.0 * np.prod(1 + r))
    assert out["max_dd_pct"].std() > 0
    assert out["max_losing_streak"].max() <= 4 and out["max_losing_streak"].min() >= 1
    assert out["max_dd_pct_p50"] <= out["max_dd_pct_p95"] <= out["max_dd_pct_p99"]
    again = montecarlo.shuffle_paths(r, n_paths=500)
    assert np.array_equal(out["max_dd_pct"], again["max_dd_pct"])


def test_shuffle_losing_streak_all_losses():
    out = montecarlo.shuffle_paths(np.array([-0.01] * 5), n_paths=10)
    assert (out["max_losing_streak"] == 5).all()
    assert out["max_dd_pct"][0] == pytest.approx((1 - 0.99 ** 5) * 100)


def days_from(eq_close, eq_min):
    """Flat balance = equity at open each day (positions closed daily)."""
    eq_close = np.asarray(eq_close, float)
    eq_open = np.concatenate([[10000.0], eq_close[:-1]])
    return pd.DataFrame({
        "date": pd.date_range("2026-03-02", periods=len(eq_close), freq="D"),
        "bal_open": eq_open, "eq_open": eq_open, "eq_min": eq_min,
        "eq_max": np.maximum(eq_open, eq_close), "bal_close": eq_close,
        "eq_close": eq_close, "spread_median": 0.3,
    })


def test_zero_risk_no_breach():
    d = days_from([10000.0] * 20, [10000.0] * 20)
    out = montecarlo.block_bootstrap_paths(d, horizon=50, n_paths=200)
    assert out["breach_prob"] == 0.0
    assert out["final_return_p1"] == 0.0 and out["final_return_p99"] == 0.0
    assert out["max_dd_pct_p99"] == 0.0
    assert out["seed"] == 20260930 and out["horizon"] == 50


def test_catastrophic_day_stops_path_once():
    closes = [10000.0] * 10
    mins = [10000.0] * 10
    mins[4] = 9000.0  # -10% intraday -> daily breach (floor bal-500)
    d = days_from(closes, mins)
    out = montecarlo.block_bootstrap_paths(d, horizon=30, n_paths=300, mean_block=5, seed=7)
    idx = stats.stationary_bootstrap_indices(10, 5, np.random.default_rng(7), size=300, length=30)
    hit = idx == 4
    expected_day = np.where(hit.any(axis=1), hit.argmax(axis=1), -1)
    assert np.array_equal(out["breach_day"], expected_day)
    assert out["breach_prob"] == pytest.approx(hit.any(axis=1).mean())
    assert out["daily_breach_prob"] == out["breach_prob"]
    assert out["total_breach_prob"] == 0.0  # 9000 is not below 9000


def test_total_breach_counted():
    d = days_from([10000.0], [8999.0])
    out = montecarlo.block_bootstrap_paths(d, horizon=5, n_paths=20)
    assert out["breach_prob"] == 1.0
    assert out["daily_breach_prob"] == 1.0 and out["total_breach_prob"] == 1.0
    assert (out["breach_day"] == 0).all()


def test_block_bootstrap_reproducible():
    rng = np.random.default_rng(11)
    closes = 10000.0 * np.cumprod(1 + rng.normal(0.001, 0.01, 60))
    mins = np.minimum(closes, np.concatenate([[10000.0], closes[:-1]])) * 0.995
    d = days_from(closes, mins)
    a = montecarlo.block_bootstrap_paths(d, horizon=100, n_paths=300)
    b = montecarlo.block_bootstrap_paths(d, horizon=100, n_paths=300)
    assert a["final_return_p50"] == b["final_return_p50"]
    assert np.array_equal(a["breach_day"], b["breach_day"])
