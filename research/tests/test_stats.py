import math

import numpy as np
import pytest
from scipy.stats import norm

from mt5r import stats


def test_stationary_bootstrap_indices_shape_range_and_blocks():
    rng = np.random.default_rng(1)
    idx = stats.stationary_bootstrap_indices(50, 5, rng)
    assert idx.shape == (50,) and idx.min() >= 0 and idx.max() < 50
    idx = stats.stationary_bootstrap_indices(200, 5, np.random.default_rng(2), size=2000)
    continues = (idx[:, 1:] == (idx[:, :-1] + 1) % 200).mean()
    assert continues == pytest.approx(0.8 + 0.2 / 200, abs=0.01)  # 1 - 1/mean_block


def test_bootstrap_ci_reproducible_and_positive():
    x = 0.01 + np.random.default_rng(0).normal(0, 0.001, 100)
    a = stats.bootstrap_mean_ci(x, n_boot=2000)
    b = stats.bootstrap_mean_ci(x, n_boot=2000)
    assert a == b
    lo, hi = a
    assert 0 < lo < x.mean() < hi


def test_paired_prob():
    rng = np.random.default_rng(3)
    b = rng.normal(0, 0.01, 120)
    a = b + 0.005 + rng.normal(0, 0.001, 120)
    assert stats.paired_prob_le_zero(a, b, n_boot=2000) < 0.01
    assert stats.paired_prob_le_zero(b, a, n_boot=2000) > 0.99


def test_sharpe():
    assert stats.sharpe([1.0, 2.0, 3.0]) == pytest.approx(2.0 / 1.0)
    assert math.isnan(stats.sharpe([1.0]))
    assert math.isnan(stats.sharpe([2.0, 2.0]))


def test_psr_closed_form():
    sr, n = 0.1, 100
    expected = norm.cdf(sr * math.sqrt(99) / math.sqrt(1 + 0.5 * sr ** 2))
    assert stats.psr(sr, n, 0.0, 3.0) == pytest.approx(expected, rel=1e-12)


def test_expected_max_sharpe():
    assert stats.expected_max_sharpe(1, 0.5) == 0.0
    vals = [stats.expected_max_sharpe(n, 0.01) for n in (2, 10, 100, 2880)]
    assert all(b > a for a, b in zip(vals, vals[1:]))


def test_dsr_published_example():
    # Bailey & Lopez de Prado (2014): annual SR 2.5, 5 years daily (T=1250),
    # skew -3, kurt 10, N=100 trials, annual Var[SR]=0.5 -> SR0 ~0.1132, DSR ~0.9004.
    sr0 = stats.expected_max_sharpe(100, 0.5 / 250)
    assert sr0 == pytest.approx(0.1132, abs=5e-4)
    val = stats.psr(2.5 / math.sqrt(250), 1250, -3.0, 10.0, sr_benchmark=sr0)
    assert val == pytest.approx(0.9004, abs=2e-3)


def test_dsr_n1_equals_psr():
    x = np.random.default_rng(5).normal(0.001, 0.01, 80)
    d = stats.dsr(x, n_trials=1, var_sr=0.01)
    assert d["sr0"] == 0.0 and d["n"] == 80
    from scipy.stats import kurtosis, skew
    assert d["psr_value"] == pytest.approx(stats.psr(stats.sharpe(x), 80, skew(x), kurtosis(x, fisher=False)))
    assert stats.dsr(x, n_trials=2880, var_sr=0.01)["psr_value"] < d["psr_value"]
