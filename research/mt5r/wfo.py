"""Walk-forward selection (KTD12, KTD13) and OOS deposit chaining (R22).

Categorical axes (e.g. ObMode, EntryMode) have no order: smoothing and neighbours only compare passes
with exactly the same categorical values, and step one index along one ordinal axis at a time.
"""
import itertools

import numpy as np
import pandas as pd


def trade_floor(train_months: int, per_month: int = 15) -> int:
    """KTD12 minimum train fills: per_month x train months (45 for a 3-month train window)."""
    return int(per_month) * int(train_months)


def merge_grids(dfs: list, axes: dict) -> pd.DataFrame:
    """Concatenate optimization tables; every axes combination must appear exactly once."""
    names = list(axes)
    df = pd.concat(dfs, ignore_index=True)
    seen = list(zip(*(df[n] for n in names)))
    expected = set(itertools.product(*axes.values()))
    if len(seen) != len(set(seen)):
        raise ValueError("duplicate parameter combination in merged grid")
    unexpected = set(seen) - expected
    if unexpected:
        raise ValueError(f"combinations outside the grid: {sorted(unexpected)[:5]}")
    missing = expected - set(seen)
    if missing:
        raise ValueError(f"{len(missing)} grid combinations missing, e.g. {sorted(missing)[:5]}")
    return df.sort_values(names, kind="mergesort").reset_index(drop=True)


def _axes_from(grid_df, param_cols):
    return {c: sorted(grid_df[c].unique()) for c in param_cols}


def score(grid_df, param_cols, trade_floor, dd_max_pct=10.0, categorical=()) -> pd.DataFrame:
    """Add eligible, raw_score and neighbor-smoothed score.

    raw_score = profit / eq_dd_money (recovery factor) for eligible passes, 0 otherwise.
    A pass with zero drawdown uses profit / 1.0 (drawdown floored at 1 USD).
    smoothed = mean over the pass and its existing ordinal distance-1 neighbors in the same
    categorical cell.
    """
    out = grid_df.copy()
    out["eligible"] = (out["trades"] >= trade_floor) & (out["eq_dd_pct"] <= dd_max_pct)
    dd = out["eq_dd_money"].where(out["eq_dd_money"] > 0, 1.0)
    out["raw_score"] = np.where(out["eligible"], out["profit"] / dd, 0.0)

    axes = _axes_from(out, param_cols)
    pos = {c: {v: i for i, v in enumerate(axes[c])} for c in param_cols}
    keys = [tuple(pos[c][v] for c, v in zip(param_cols, row))
            for row in zip(*(out[c] for c in param_cols))]
    raw = dict(zip(keys, out["raw_score"]))
    smoothed = []
    for key in keys:
        vals = [raw[key]]
        for d in _ordinal_dims(param_cols, categorical):
            for step in (-1, 1):
                nb = key[:d] + (key[d] + step,) + key[d + 1:]
                if nb in raw:
                    vals.append(raw[nb])
        smoothed.append(float(np.mean(vals)))
    out["smoothed"] = smoothed
    return out


def _ordinal_dims(param_cols, categorical):
    unknown = set(categorical) - set(param_cols)
    if unknown:
        raise ValueError(f"categorical axes not in the grid: {sorted(unknown)}")
    return [d for d, c in enumerate(param_cols) if c not in set(categorical)]


def _index_of(axis, value):
    """Position of value on an ordered axis; nearest grid value if off-grid."""
    if value in axis:
        return axis.index(value)
    return int(np.argmin([abs(a - value) for a in axis]))


def select(grid_df, param_cols, defaults: dict, trade_floor, dd_max_pct=10.0, categorical=()) -> dict:
    """Pick the eligible pass with the highest smoothed score (KTD12).

    Ties: smaller distance to the defaults (index steps on ordinal axes, 1 per differing
    categorical axis), then smallest axis indices in parameter order.
    No eligible pass: the defaults with status no_eligible_pass.
    """
    scored = score(grid_df, param_cols, trade_floor, dd_max_pct, categorical)
    eligible = scored[scored["eligible"]]
    if eligible.empty:
        return {"params": dict(defaults), "status": "no_eligible_pass", "scored": scored}

    axes = {c: list(v) for c, v in _axes_from(scored, param_cols).items()}
    home = [_index_of(axes[c], defaults[c]) for c in param_cols]
    best = eligible["smoothed"].max()
    top = eligible[np.isclose(eligible["smoothed"], best, rtol=1e-12, atol=0.0)]

    def rank(combo):
        idx = [axes[c].index(v) for c, v in zip(param_cols, combo)]
        dist = sum((i != h) if c in categorical else abs(i - h) for c, i, h in zip(param_cols, idx, home))
        return (dist, idx)

    chosen = min(top[param_cols].itertuples(index=False, name=None), key=rank)
    params = {c: v.item() if hasattr(v, "item") else v for c, v in zip(param_cols, chosen)}
    return {"params": params, "status": "selected", "scored": scored}


def neighbors(params: dict, axes: dict, categorical=()) -> list:
    """Distance-1 grid neighbors: one ordinal axis moved one step, lower before upper.

    Categorical axes keep the candidate's value (exact match)."""
    out = []
    for name, values in axes.items():
        if name in categorical:
            continue
        i = list(values).index(params[name])
        for j in (i - 1, i + 1):
            if 0 <= j < len(values):
                nb = dict(params)
                nb[name] = values[j]
                out.append(nb)
    return out


def chain_deposits(initial: float, fold_final_balances: list) -> list:
    """Deposit per fold: initial, then previous fold's final balance rounded to cents (KTD7)."""
    if not fold_final_balances:
        return []
    return [float(initial)] + [round(float(b), 2) for b in fold_final_balances[:-1]]
