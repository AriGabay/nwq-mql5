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


def test_trade_floor_is_per_month_times_train_months():
    # KTD12: eligible when train fills >= 15 x train months
    assert wfo.trade_floor(3) == 45
    assert wfo.trade_floor(1) == 15
    assert wfo.trade_floor(3, per_month=10) == 30


def test_trade_floor_boundary_44_ineligible_45_eligible():
    axes = {"A": [1, 2]}
    cells = {(1,): (500.0, 44, 100.0, 5.0), (2,): (500.0, 45, 100.0, 5.0)}
    scored = wfo.score(make_grid(axes, cells), ["A"], trade_floor=wfo.trade_floor(3))
    assert list(scored["eligible"]) == [False, True]


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


CAT_AXES = {"EntryMode": [0, 1, 2, 3], "ObMaxAgeBars": [48, 96, 144]}


def test_categorical_smoothing_never_crosses_entry_mode():
    cells = {(1, 96): (1000.0, 50, 100.0, 5.0),   # raw 10, same-cell neighbours ineligible
             (0, 96): (800.0, 50, 100.0, 5.0),    # raw 8, adjacent EntryMode values
             (2, 96): (800.0, 50, 100.0, 5.0)}
    scored = wfo.score(make_grid(CAT_AXES, cells), ["EntryMode", "ObMaxAgeBars"], trade_floor=45,
                       categorical=["EntryMode"]).set_index(["EntryMode", "ObMaxAgeBars"])
    # mean of itself and (1,48), (1,144) only; (0,96) and (2,96) are other categories
    assert scored.loc[(1, 96), "smoothed"] == pytest.approx(10.0 / 3)
    assert scored.loc[(0, 96), "smoothed"] == pytest.approx(8.0 / 3)
    assert scored.loc[(1, 48), "smoothed"] == pytest.approx(10.0 / 2)
    assert scored.loc[(3, 96), "smoothed"] == pytest.approx(0.0)


def test_categorical_neighbors_differ_by_one_ordinal_step():
    assert wfo.neighbors({"EntryMode": 1, "ObMaxAgeBars": 96}, CAT_AXES, categorical=["EntryMode"]) == [
        {"EntryMode": 1, "ObMaxAgeBars": 48}, {"EntryMode": 1, "ObMaxAgeBars": 144}]
    axes = {"ObMode": [0, 1], "EntryMode": [0, 1, 2, 3], "ObMaxAgeBars": [48, 96, 144],
            "FvgWindowBars": [6, 12, 18], "OrderExpiryBars": [6, 12, 18]}
    nbs = wfo.neighbors({"ObMode": 1, "EntryMode": 2, "ObMaxAgeBars": 48, "FvgWindowBars": 12,
                         "OrderExpiryBars": 18}, axes, categorical=["ObMode", "EntryMode"])
    assert nbs == [
        {"ObMode": 1, "EntryMode": 2, "ObMaxAgeBars": 96, "FvgWindowBars": 12, "OrderExpiryBars": 18},
        {"ObMode": 1, "EntryMode": 2, "ObMaxAgeBars": 48, "FvgWindowBars": 6, "OrderExpiryBars": 18},
        {"ObMode": 1, "EntryMode": 2, "ObMaxAgeBars": 48, "FvgWindowBars": 18, "OrderExpiryBars": 18},
        {"ObMode": 1, "EntryMode": 2, "ObMaxAgeBars": 48, "FvgWindowBars": 12, "OrderExpiryBars": 12}]


def test_categorical_tie_break_counts_category_change_as_one():
    cells = {(3, 96): (900.0, 50, 100.0, 5.0),    # smoothed 9/3 = 3
             (1, 144): (600.0, 50, 100.0, 5.0)}   # smoothed 6/2 = 3
    grid = make_grid(CAT_AXES, cells)
    res = wfo.select(grid, ["EntryMode", "ObMaxAgeBars"], {"EntryMode": 0, "ObMaxAgeBars": 96}, 45,
                     categorical=["EntryMode"])
    # (3,96): category differs (1) + 0 steps = 1; (1,144): 1 + 1 step = 2
    assert res["params"] == {"EntryMode": 3, "ObMaxAgeBars": 96}


def test_categorical_no_eligible_pass_returns_defaults():
    res = wfo.select(make_grid(CAT_AXES, {}), ["EntryMode", "ObMaxAgeBars"],
                     {"EntryMode": 0, "ObMaxAgeBars": 96}, 45, categorical=["EntryMode"])
    assert res["status"] == "no_eligible_pass"
    assert res["params"] == {"EntryMode": 0, "ObMaxAgeBars": 96}


def _variants(profit_a, profit_b, trades=(60, 60), dd=(5.0, 5.0)):
    return pd.DataFrame({"StructureVariant": [0, 1], "profit": [profit_a, profit_b], "trades": list(trades),
                         "eq_dd_pct": list(dd), "eq_dd_money": [100.0, 100.0]})


def test_two_variant_selection_ties_and_fallback_go_to_a():
    """U9: equal scores pick A; no eligible pass falls back to A; otherwise the higher recovery factor wins."""
    cols, d = ["StructureVariant"], {"StructureVariant": 0}
    tie = wfo.select(_variants(200.0, 200.0), cols, d, 45, 10.0, categorical=cols)
    assert tie["params"] == {"StructureVariant": 0} and tie["status"] == "selected"
    none = wfo.select(_variants(200.0, 300.0, trades=(44, 30)), cols, d, 45, 10.0, categorical=cols)
    assert none["params"] == {"StructureVariant": 0} and none["status"] == "no_eligible_pass"
    b = wfo.select(_variants(200.0, 300.0), cols, d, 45, 10.0, categorical=cols)
    assert b["params"] == {"StructureVariant": 1}
    dd = wfo.select(_variants(200.0, 300.0, dd=(5.0, 10.5)), cols, d, 45, 10.0, categorical=cols)
    assert dd["params"] == {"StructureVariant": 0}                 # B over the 10% equity DD limit
