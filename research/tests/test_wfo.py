import itertools

import pandas as pd
import pytest

from mt5r import wfo


def make_grid(axes, cells):
    """cells: {(a, b): (profit, trades, eq_dd_money, eq_dd_pct)}; others ineligible."""
    names = list(axes)
    rows = []
    for combo in itertools.product(*axes.values()):
        profit, trades, dd_money, dd_pct = cells.get(combo, (500.0, 2, 100.0, 5.0))
        row = dict(zip(names, combo))
        row.update(profit=profit, trades=trades, eq_dd_money=dd_money, eq_dd_pct=dd_pct)
        rows.append(row)
    return pd.DataFrame(rows)


@pytest.mark.parametrize("baseline,expected", [(40, 15), (12, 6), (4, 5), (15, 15), (11, 5)])
def test_trade_floor(baseline, expected):
    assert wfo.trade_floor(baseline) == expected


def test_merge_grids_ok_and_sorted():
    axes = {"A": [1, 2, 3, 4], "B": [10, 20, 30]}
    full = make_grid(axes, {})
    parts = [full.iloc[i::4] for i in range(4)]
    merged = wfo.merge_grids(parts[::-1], axes)
    assert len(merged) == 12
    assert list(zip(merged.A, merged.B)) == list(itertools.product(*axes.values()))


def test_merge_grids_576():
    axes = {"P1": [1, 2, 3, 4], "P2": [1, 2, 3], "P3": [1, 2, 3, 4], "P4": [1.0, 1.5, 2.0], "P5": [1, 2, 3, 4]}
    full = make_grid(axes, {})
    parts = [full.iloc[i * 144:(i + 1) * 144] for i in range(4)]
    assert len(wfo.merge_grids(parts, axes)) == 576


def test_merge_grids_duplicate_or_missing_raises():
    axes = {"A": [1, 2, 3, 4], "B": [10, 20, 30]}
    full = make_grid(axes, {})
    with pytest.raises(ValueError):
        wfo.merge_grids([full, full.iloc[:1]], axes)
    with pytest.raises(ValueError):
        wfo.merge_grids([full.iloc[1:]], axes)
    extra = full.copy()
    extra.loc[0, "A"] = 99
    with pytest.raises(ValueError):
        wfo.merge_grids([extra], axes)


def test_ae1_cluster_beats_isolated_peak():
    axes = {"A": [1, 2, 3, 4], "B": [10, 20, 30]}
    cells = {
        (1, 10): (1000.0, 20, 100.0, 5.0),  # isolated, raw 10, neighbors ineligible
        (4, 20): (800.0, 20, 100.0, 5.0),   # cluster, raw 8
        (4, 30): (800.0, 20, 100.0, 5.0),
        (3, 30): (800.0, 20, 100.0, 5.0),
    }
    grid = make_grid(axes, cells)
    res = wfo.select(grid, ["A", "B"], {"A": 1, "B": 10}, trade_floor=15)
    assert res["status"] == "selected"
    assert res["params"] == {"A": 4, "B": 30}
    assert all(type(v) is int for v in res["params"].values())
    scored = res["scored"].set_index(["A", "B"])
    assert scored.loc[(1, 10), "raw_score"] == pytest.approx(10.0)
    assert scored.loc[(1, 10), "smoothed"] == pytest.approx(10.0 / 3)
    assert scored.loc[(4, 30), "smoothed"] == pytest.approx(8.0)
    assert scored.loc[(4, 20), "smoothed"] == pytest.approx(16.0 / 4)
    assert scored["eligible"].sum() == 4
    assert (scored.loc[~scored["eligible"], "raw_score"] == 0).all()


def test_dd_filter_and_zero_drawdown():
    axes = {"A": [1, 2]}
    cells = {(1,): (500.0, 20, 0.0, 0.0), (2,): (9000.0, 20, 100.0, 10.01)}
    scored = wfo.score(make_grid(axes, cells), ["A"], trade_floor=15)
    assert list(scored["eligible"]) == [True, False]
    assert scored["raw_score"].iloc[0] == pytest.approx(500.0)  # profit / 1.0
    assert scored["raw_score"].iloc[1] == 0


def test_ae2_no_eligible_pass_returns_defaults():
    axes = {"A": [1, 2, 3, 4], "B": [10, 20, 30]}
    grid = make_grid(axes, {})
    res = wfo.select(grid, ["A", "B"], {"A": 2, "B": 20}, trade_floor=15)
    assert res["status"] == "no_eligible_pass"
    assert res["params"] == {"A": 2, "B": 20}


def test_tie_breaks_toward_defaults():
    axes = {"A": [1, 2, 3, 4, 5], "B": [1]}
    cells = {(1, 1): (800.0, 20, 100.0, 5.0), (5, 1): (800.0, 20, 100.0, 5.0)}
    grid = make_grid(axes, cells)
    assert wfo.select(grid, ["A", "B"], {"A": 4, "B": 1}, 15)["params"] == {"A": 5, "B": 1}
    assert wfo.select(grid, ["A", "B"], {"A": 2, "B": 1}, 15)["params"] == {"A": 1, "B": 1}
    # equidistant: deterministic lexicographic tie-break (lowest index first)
    assert wfo.select(grid, ["A", "B"], {"A": 3, "B": 1}, 15)["params"] == {"A": 1, "B": 1}


def test_neighbors_interior_and_edges():
    axes = {"A": [1, 2, 3, 4], "B": [10, 20, 30]}
    assert wfo.neighbors({"A": 2, "B": 20}, axes) == [
        {"A": 1, "B": 20}, {"A": 3, "B": 20}, {"A": 2, "B": 10}, {"A": 2, "B": 30}]
    assert wfo.neighbors({"A": 1, "B": 10}, axes) == [{"A": 2, "B": 10}, {"A": 1, "B": 20}]
    assert wfo.neighbors({"A": 4, "B": 30}, axes) == [{"A": 3, "B": 30}, {"A": 4, "B": 20}]


def test_chain_deposits():
    assert wfo.chain_deposits(10000.0, [10123.456, 9987.001, 10500.0]) == [10000.0, 10123.46, 9987.0]
    assert wfo.chain_deposits(10000.0, []) == []
