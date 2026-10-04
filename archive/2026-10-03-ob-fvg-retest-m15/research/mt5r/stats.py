"""Bootstrap and Sharpe-based statistics (KTD8, KTD9)."""
import math

import numpy as np
from scipy.stats import kurtosis, norm, skew

EULER_GAMMA = 0.5772156649
SEED = 20260930


def stationary_bootstrap_indices(n, mean_block, rng, size=None, length=None) -> np.ndarray:
    """Politis-Romano stationary bootstrap indices into a series of length n.

    Each step starts a new block at a random index with probability 1/mean_block,
    otherwise continues to the next index (wrapping around). Returns shape
    (length,) or (size, length); length defaults to n.
    """
    length = n if length is None else length
    rows = 1 if size is None else size
    p = 1.0 / mean_block
    new_block = rng.random((rows, length)) < p
    new_block[:, 0] = True
    starts = rng.integers(0, n, (rows, length))
    idx = np.empty((rows, length), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for t in range(1, length):
        idx[:, t] = np.where(new_block[:, t], starts[:, t], (idx[:, t - 1] + 1) % n)
    return idx[0] if size is None else idx


def bootstrap_mean_ci(x, n_boot=10000, mean_block=5, alpha=0.10, seed=SEED):
    """Percentile CI of the mean under the stationary bootstrap."""
    x = np.asarray(x, float)
    idx = stationary_bootstrap_indices(len(x), mean_block, np.random.default_rng(seed), size=n_boot)
    means = x[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def paired_prob_le_zero(a, b, n_boot=10000, mean_block=5, seed=SEED) -> float:
    """Share of paired resamples (same day indices) where mean(a - b) <= 0."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.shape != b.shape:
        raise ValueError("a and b must be aligned series of equal length")
    idx = stationary_bootstrap_indices(len(a), mean_block, np.random.default_rng(seed), size=n_boot)
    return float(((a - b)[idx].mean(axis=1) <= 0).mean())


def sharpe(x) -> float:
    """Non-annualized Sharpe: mean / sample std; nan if undefined."""
    x = np.asarray(x, float)
    if len(x) < 2:
        return math.nan
    sd = x.std(ddof=1)
    return math.nan if sd == 0 else float(x.mean() / sd)


def psr(sr_hat, n, skew, kurt, sr_benchmark=0.0) -> float:
    """Probabilistic Sharpe ratio (Bailey & Lopez de Prado); kurt is non-excess."""
    denom = math.sqrt(1 - skew * sr_hat + (kurt - 1) / 4 * sr_hat ** 2)
    return float(norm.cdf((sr_hat - sr_benchmark) * math.sqrt(n - 1) / denom))


def expected_max_sharpe(n_trials, var_sr) -> float:
    """Expected maximum Sharpe of n_trials unskilled trials with Sharpe variance var_sr."""
    if n_trials <= 1:
        return 0.0
    z1 = norm.ppf(1 - 1 / n_trials)
    z2 = norm.ppf(1 - 1 / (n_trials * math.e))
    return float(math.sqrt(var_sr) * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2))


def dsr(returns, n_trials, var_sr) -> dict:
    """Deflated Sharpe ratio: PSR against the expected max Sharpe of n_trials."""
    x = np.asarray(returns, float)
    sr = sharpe(x)
    sr0 = expected_max_sharpe(n_trials, var_sr)
    g3, g4 = float(skew(x)), float(kurtosis(x, fisher=False))
    value = psr(sr, len(x), g3, g4, sr0) if not math.isnan(sr) else math.nan
    return {"sr": sr, "psr_value": value, "sr0": sr0, "n": int(len(x)), "skew": g3, "kurt": g4}
