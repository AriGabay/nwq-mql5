"""Monte Carlo paths: trade-event shuffle and daily block bootstrap (R21, KTD8)."""
import numpy as np

from . import metrics
from .stats import SEED, stationary_bootstrap_indices


def _pct(a, qs):
    return {q: float(np.percentile(a, q)) for q in qs}


def shuffle_paths(event_returns, n_paths=10000, seed=SEED, initial=10000.0) -> dict:
    """Permute per-event returns and compound; final equity is order-invariant."""
    r = np.asarray(event_returns, float)
    rng = np.random.default_rng(seed)
    perm = rng.permuted(np.tile(r, (n_paths, 1)), axis=1)
    eq = initial * np.cumprod(1 + perm, axis=1)
    eq = np.hstack([np.full((n_paths, 1), float(initial)), eq])
    peak = np.maximum.accumulate(eq, axis=1)
    max_dd = ((peak - eq) / peak).max(axis=1) * 100

    streak = np.zeros(n_paths, dtype=int)
    longest = np.zeros(n_paths, dtype=int)
    for t in range(perm.shape[1]):
        streak = np.where(perm[:, t] < 0, streak + 1, 0)
        longest = np.maximum(longest, streak)

    out = {"final_equity": eq[:, -1], "max_dd_pct": max_dd, "max_losing_streak": longest,
           "seed": seed, "n_paths": n_paths}
    for q, v in _pct(max_dd, (50, 95, 99)).items():
        out[f"max_dd_pct_p{q}"] = v
    for q, v in _pct(longest, (50, 95, 99)).items():
        out[f"max_losing_streak_p{q}"] = v
    return out


def block_bootstrap_paths(days_df, horizon=252, n_paths=10000, mean_block=5, seed=SEED,
                          initial=10000.0, daily_loss_pct=5.0, total_floor_frac=0.90) -> dict:
    """Stationary-bootstrap daily records into paths that stop at the first R24 breach.

    Day record: r_close = close-to-close equity return (metrics.daily_returns) and
    r_min = eq_min / bal_open - 1. The simulated balance is the previous day's
    closing equity (positions treated as flat overnight). A breached path ends
    with equity at that day's minimum.
    """
    d = days_df.sort_values("date")
    r_close = metrics.daily_returns(d).to_numpy(float)
    r_min = (d["eq_min"] / d["bal_open"] - 1).to_numpy(float)
    idx = stationary_bootstrap_indices(len(d), mean_block, np.random.default_rng(seed),
                                       size=n_paths, length=horizon)

    bal = np.full(n_paths, float(initial))
    peak = bal.copy()
    max_dd = np.zeros(n_paths)
    alive = np.ones(n_paths, dtype=bool)
    breach_day = np.full(n_paths, -1)
    daily_hit = np.zeros(n_paths, dtype=bool)
    total_hit = np.zeros(n_paths, dtype=bool)
    daily_loss = initial * daily_loss_pct / 100.0
    total_floor = initial * total_floor_frac

    for t in range(horizon):
        j = idx[:, t]
        eq_min = bal * (1 + r_min[j])
        daily = alive & (eq_min < bal - daily_loss)
        total = alive & (eq_min < total_floor)
        breach = daily | total
        max_dd = np.where(alive, np.maximum(max_dd, (peak - eq_min) / peak), max_dd)
        breach_day[breach] = t
        daily_hit |= daily
        total_hit |= total
        bal = np.where(breach, eq_min, bal)
        alive &= ~breach
        bal = np.where(alive, bal * (1 + r_close[j]), bal)
        peak = np.where(alive, np.maximum(peak, bal), peak)

    final_ret = bal / initial - 1
    max_dd_pct = max_dd * 100
    out = {
        "breach_prob": float((breach_day >= 0).mean()),
        "daily_breach_prob": float(daily_hit.mean()),
        "total_breach_prob": float(total_hit.mean()),
        "breach_day": breach_day,
        "seed": seed, "horizon": horizon, "n_paths": n_paths, "mean_block": mean_block,
    }
    for q, v in _pct(final_ret, (1, 5, 50, 95, 99)).items():
        out[f"final_return_p{q}"] = v
    for q, v in _pct(max_dd_pct, (50, 95, 99)).items():
        out[f"max_dd_pct_p{q}"] = v
    return out
