"""numeric_v1 diagnosis: train-window tables, baseline trade diagnosis and the lower-risk accounting estimate
(plan 2026-10-04-2133, U4-U6). Every input is stored evidence; nothing here runs the tester."""
import pandas as pd
import pytest

from mt5r import diagnosis_nv1 as dg, numeric_v1 as nv

FLOOR, DD_MAX = 45, 10.0


# ------------------------------------------------------------------ U4: train windows (R1-R3)
@pytest.mark.parametrize("trades,dd,expected", [
    (40, 12.3, "מתחת לרצפת העסקאות (40 < 45); מעל מגבלת ה־DD (12.30% > 10.00%)"),     # Covers AE1
    (50, 12.3, "מעל מגבלת ה־DD (12.30% > 10.00%)"),
    (40, 9.0, "מתחת לרצפת העסקאות (40 < 45)"),
    (50, 9.0, "כשיר"),
    (45, 10.0, "כשיר"),                                       # the selection rule: trades >= floor, DD <= limit
])
def test_rejection_reason_names_each_failed_condition_with_its_shortfall(trades, dd, expected):
    assert dg.rejection(trades, dd, FLOOR, DD_MAX) == expected


def _grid(window, start, nets, dds=None, trades=100):
    rows = []
    for i, c in enumerate(nv.combos()):
        rows.append({**{a: c[a] for a in nv.AXES}, "profit": nets[i] if isinstance(nets, list) else nets,
                     "trades": trades, "eq_dd_pct": (dds or {}).get(i, 20.0), "recovery_factor": 0.5,
                     "custom": 0.1, "sharpe": 1.0, "window": window, "train_start": start})
    return pd.DataFrame(rows)


def test_train_table_marks_eligibility_like_the_selection_rule():
    t = dg.train_table(_grid("w1", "2026.01.01", 1.0, dds={0: 9.0}), FLOOR, DD_MAX)
    assert len(t) == 18 and int(t["eligible"].sum()) == 1
    assert t.loc[t["eligible"], "reason"].tolist() == ["כשיר"]
    assert {"daily_sharpe", "reason", "eligible"} <= set(t.columns)


def test_combo_summary_counts_profitable_windows_and_never_sums_train_net():
    nets1 = [10.0] * 18
    nets2 = [-5.0] + [10.0] * 17
    nets3 = [-5.0] * 18
    t = dg.train_table(pd.concat([_grid("w1", "2026.01.01", nets1), _grid("w2", "2026.02.01", nets2),
                                  _grid("w3", "2026.03.01", nets3)]), FLOOR, DD_MAX)
    s = dg.combo_summary(t).set_index("combo")
    first = "/".join(str(v) for v in nv.tuple_of(nv.combos()[0]))
    assert s.loc[first, "windows"] == 3 and s.loc[first, "profitable_windows"] == 1
    assert s.loc[first, "net_median"] == -5.0 and s.loc[first, "net_min"] == -5.0 and s.loc[first, "net_max"] == 10.0
    assert not any("sum" in c or "total" in c or "cumulative" in c for c in s.columns)


def test_neighbour_variation_uses_only_the_grid_neighbours_of_the_same_window():
    nets = [0.0] * 18
    idx = {nv.tuple_of(c): i for i, c in enumerate(nv.combos())}
    nets[idx[(0, 3, 20)]] = 100.0                              # one combination far from its neighbours
    t = dg.train_table(_grid("w1", "2026.01.01", nets), FLOOR, DD_MAX)
    v = dg.neighbour_variation(t).set_index("combo")
    n = len(nv.neighbours(dict(zip(nv.AXES, (0, 3, 20)))))
    assert v.loc["0/3/20", "neighbours"] == n
    assert v.loc["0/3/20", "mean_abs_diff_net"] == 100.0
    assert v.loc["1/3/20", "mean_abs_diff_net"] == 0.0         # another variant is not a neighbour (categorical)


def test_rank_correlation_between_consecutive_windows():
    nets = list(range(18))
    t = dg.train_table(pd.concat([_grid("w1", "2026.01.01", nets), _grid("w2", "2026.02.01", nets),
                                  _grid("w3", "2026.03.01", nets[::-1])]), FLOOR, DD_MAX)
    rc = dg.rank_correlation(t)
    assert rc["pair"].tolist() == ["w1 -> w2", "w2 -> w3"]
    assert rc["spearman"].round(6).tolist() == [1.0, -1.0]


# ------------------------------------------------------------------ U5: baseline trades (R4-R8)
def _trades():
    """Three positions of one fold: A 01:00:30-03:00, B opens inside A (01:30-02:00), C after both (05:00-06:00);
    A and B on one structure from two OBs, C alone."""
    return pd.DataFrame({
        "position_id": [1, 2, 3], "dir": ["L", "S", "L"], "net": [40.0, -20.0, -20.0], "realized_r": [2.0, -1.0, -1.0],
        "swap": [-1.0, 0.0, 0.0], "commission": [0.0, 0.0, 0.0], "volume": [0.03, 0.01, 0.02],
        "open_time": pd.to_datetime(["2026-03-02 01:00:30", "2026-03-02 01:30:00", "2026-03-02 05:00:00"]),
        "close_time": pd.to_datetime(["2026-03-02 03:00:00", "2026-03-02 02:00:00", "2026-03-02 06:00:00"]),
        "ref_pivot_id": [7, 7, 9], "ob_time": [100, 200, 300], "fold": [1, 1, 1], "month": ["2026-03"] * 3,
        "deposit": [10000.0] * 3})


def test_trade_stats_report_expectancy_in_r_win_rate_and_average_win_and_loss():
    s = dg.trade_stats(_trades())
    assert s["trades"] == 3 and s["net"] == 0.0
    assert s["expectancy_r"] == pytest.approx(0.0) and s["win_rate"] == pytest.approx(1 / 3)
    assert (s["avg_win"], s["avg_loss"], s["avg_win_r"], s["avg_loss_r"]) == (40.0, -20.0, 2.0, -1.0)


def test_concurrency_counts_other_open_positions_and_clusters_chain_overlaps():
    t = dg.add_concurrency(_trades())
    assert t["concurrent"].tolist() == [0, 1, 0]
    assert t.groupby("cluster")["position_id"].apply(list).tolist() == [[1, 2], [3]]


def test_shared_structure_needs_different_obs_on_the_same_pivot():
    t = _trades()
    assert dg.add_structure(t)["shared_structure"].tolist() == [True, True, False]
    same_ob = t.assign(ob_time=[100, 100, 300])
    assert dg.add_structure(same_ob)["shared_structure"].tolist() == [False, False, False]


def test_the_0100_minute_counts_fills_from_0100_to_before_0101():
    t = _trades()
    t.loc[1, "open_time"] = pd.Timestamp("2026-03-02 01:01:00")
    assert dg.minute_0100(t)["fills"] == 1


def test_outliers_report_net_without_the_largest_wins_and_losses():
    o = dg.outliers(_trades(), k=1)
    assert (o["net"], o["without_top_wins"], o["without_top_losses"], o["without_both"]) == (0.0, -40.0, 20.0, -20.0)


def test_balance_before_each_trade_counts_only_trades_closed_earlier():
    t = dg.add_balance(_trades())
    assert t["balance_before"].tolist() == [10000.0, 10000.0, 10020.0]


# ------------------------------------------------------------------ U6: lower-risk estimate (R9)
def test_scaling_by_one_reproduces_the_closed_trade_path():
    t = dg.add_balance(_trades())
    est = dg.risk_estimate(t, factors=(1.0, 0.5))
    one = est[est["k"] == 1.0].iloc[0]
    assert one["net"] == pytest.approx(0.0)
    assert one["closed_dd_pct"] == pytest.approx(20 / 10000 * 100)      # B closes first: 10000 -> 9980
    assert "אומדן" in one["label"]


def test_half_risk_compounds_half_of_each_trade_return():
    t = pd.DataFrame({"net": [100.0, -101.0], "volume": [0.10, 0.10],
                      "open_time": pd.to_datetime(["2026-03-02 01:00", "2026-03-02 02:00"]),
                      "close_time": pd.to_datetime(["2026-03-02 01:30", "2026-03-02 02:30"]),
                      "fold": [1, 1], "deposit": [10000.0, 10000.0]})
    half = dg.risk_estimate(dg.add_balance(t), factors=(0.5,)).iloc[0]
    assert half["net"] == pytest.approx(10000 * 1.005 * (1 - 0.005) - 10000)


def test_lot_rounding_flags_volumes_the_arithmetic_cannot_represent():
    r = dg.lot_rounding(pd.Series([0.03, 0.01, 0.20]), 0.5)
    assert r["below_min_lot"] == 1                    # 0.01 * 0.5 = 0.005 floors to 0
    assert r["changed_over_10pct"] == 1               # 0.03 * 0.5 = 0.015 floors to 0.01 (-33%)
    assert r["trades"] == 3
