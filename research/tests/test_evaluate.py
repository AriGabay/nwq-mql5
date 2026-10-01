import math

import pandas as pd
import pytest

from mt5r import evaluate, pipeline

# test windows of 31 + 30 + 31 + 29 = 121 calendar days
FOLDS_121 = [{"fold": 1, "train": ["2025.12.01", "2026.02.28"], "test": ["2026.03.01", "2026.03.31"]},
             {"fold": 2, "train": ["2026.01.01", "2026.03.31"], "test": ["2026.04.01", "2026.04.30"]},
             {"fold": 3, "train": ["2026.02.01", "2026.04.30"], "test": ["2026.05.01", "2026.05.31"]},
             {"fold": 4, "train": ["2026.03.01", "2026.05.31"], "test": ["2026.06.01", "2026.06.29"]}]


def prereg(period="M15", folds=FOLDS_121):
    p = pipeline.build_prereg(period, pilot_runs=2, ea_sha256="x", gate_changes=[])
    p["folds"] = folds
    p["stats"]["mc_paths"] = 500          # speed; thresholds stay as frozen
    p["stats"]["bootstrap_resamples"] = 500
    return p


def stitched(n, start="2026-03-01", days=121, profit=10.0, exit_kind="tp", times=None, volume=0.1):
    dates = pd.date_range(start, periods=days, freq="D")
    opens = times if times is not None else [dates[i * days // n] + pd.Timedelta(hours=10) for i in range(n)]
    opens = pd.to_datetime(pd.Series(opens, dtype="datetime64[ns]"))
    tr = pd.DataFrame({"open_time": opens, "close_time": opens + pd.Timedelta(hours=1), "direction": 1,
                       "volume": volume, "open_price": 2000.0, "close_price": 2000.0, "profit": profit,
                       "commission": 0.0, "swap": 0.0, "balance_at_open": 10000.0, "comment": "OBR"})
    table = pd.DataFrame({"setup_id": range(n), "open_time": opens, "volume": volume, "gross_profit": profit,
                          "commission": 0.0, "swap": 0.0, "net": profit, "exit_kind": exit_kind})
    pnl = tr.groupby(tr["open_time"].dt.normalize())["profit"].sum().reindex(dates, fill_value=0.0)
    close = 10000.0 + pnl.cumsum().to_numpy()
    prev = ([10000.0] + list(close[:-1]))[:days]
    d = pd.DataFrame({"date": dates, "bal_open": prev, "eq_open": prev,
                      "eq_min": [min(a, b) for a, b in zip(prev, close)],
                      "eq_max": [max(a, b) for a, b in zip(prev, close)], "bal_close": close, "eq_close": close,
                      "spread_median": 0.2})
    if days == 0:
        d = d.iloc[0:0]
    return {"days": d, "trades": tr, "table": table, "initial": 10000.0, "net_profit": float(profit * n)}


def run(proc, base=None, folds_net=(1, 1, 1, 1), share=1.0, holdout=None, p=None):
    base = base or stitched(10, profit=-1.0)
    rows = [{"fold": i + 1, "procedure_net": v} for i, v in enumerate(folds_net)]
    return evaluate.evaluate(proc, base, rows, {"profitable_share": share}, 0.001, holdout, p or prereg())


def test_frequency_uses_fills_per_30_44_days():
    ok = run(stitched(60))["criteria"]["oos_frequency"]
    assert ok["value"]["fills"] == 60 and ok["value"]["days"] == 121
    assert ok["value"]["fills_per_month"] == pytest.approx(15.09, abs=0.005)
    assert ok["pass"] is True
    bad = run(stitched(59))["criteria"]["oos_frequency"]
    assert bad["value"]["fills_per_month"] == pytest.approx(14.84, abs=0.005)
    assert bad["pass"] is False


def _leaves(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _leaves(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _leaves(v)
    else:
        yield x


@pytest.mark.parametrize("days", [0, 121])
def test_empty_oos_fails_every_criterion_with_numeric_values(days):
    empty = stitched(0, days=days)
    out = run(empty, folds_net=(0, 0, 0, 0), share=float("nan"), holdout=stitched(0, "2026-08-01", 60))
    crit = out["criteria"]
    assert set(crit) == {"oos_frequency", "oos_net", "bootstrap_ci", "positive_folds", "top_events_removed",
                         "loss_limits", "cost_stress", "neighbors", "dsr", "holdout"}
    for name, c in crit.items():
        assert c["pass"] is False, name
        for leaf in _leaves(c["value"]):
            assert isinstance(leaf, (int, float)), (name, leaf)
            assert math.isfinite(leaf), (name, leaf)
    assert out["passed_all"] is False and out["recommended"] is False


def test_bar_minutes_from_timeframe_merges_m5_bar():
    t = pd.to_datetime(["2026-03-02 10:01", "2026-03-02 10:04", "2026-03-02 10:06"])
    proc = stitched(3, times=t)
    assert evaluate.bar_minutes("M5") == 5 and evaluate.bar_minutes("M15") == 15
    m5 = run(proc, p=prereg("M5"))["criteria"]["top_events_removed"]["value"]
    m15 = run(proc, p=prereg("M15"))["criteria"]["top_events_removed"]["value"]
    assert m5["n_events"] == 2      # 10:01 and 10:04 share the 10:00 M5 bar; 10:06 is the next bar
    assert m15["n_events"] == 1     # all three in the 10:00 M15 bar


def test_net_positive_and_above_baseline():
    c = run(stitched(60, profit=10.0), base=stitched(60, profit=12.0))["criteria"]["oos_net"]
    assert c["value"] == {"net": 600.0, "baseline_net": 720.0} and c["pass"] is False
    assert run(stitched(60, profit=10.0))["criteria"]["oos_net"]["pass"] is True


def test_positive_folds_majority_three_of_five():
    p = prereg(folds=FOLDS_121 + [{"fold": 5, "train": ["x", "y"], "test": ["2026.06.30", "2026.06.30"]}])
    assert run(stitched(60), folds_net=(5, 1, 2, -1, -3), p=p)["criteria"]["positive_folds"]["pass"] is True
    c = run(stitched(60), folds_net=(5, 1, 0, -1, -3), p=p)["criteria"]["positive_folds"]
    assert c["value"] == 2 and c["pass"] is False


def test_top_two_events_removed():
    t = pd.to_datetime(["2026-03-02 10:00", "2026-03-03 10:00", "2026-03-04 10:00"])
    proc = stitched(3, times=t)
    proc["trades"]["profit"] = [100.0, 50.0, -20.0]
    c = run(proc)["criteria"]["top_events_removed"]
    assert c["value"]["net_without_top"] == pytest.approx(-20.0) and c["pass"] is False
    proc["trades"]["profit"] = [100.0, 50.0, 20.0]
    assert run(proc)["criteria"]["top_events_removed"]["pass"] is True


def test_cost_stress_spread_plus_stop_slippage():
    # per 0.1-lot trade: spread 1 x 0.2 x 0.1 x 100 = 2.00; slippage 10 x 0.01 x 100 x 0.1 = 1.00
    c = run(stitched(60, profit=5.0, exit_kind="sl"))["criteria"]["cost_stress"]
    assert c["value"]["stressed_net"] == pytest.approx(60 * (5.0 - 3.0)) and c["pass"] is True
    c = run(stitched(60, profit=2.5, exit_kind="sl"))["criteria"]["cost_stress"]
    assert c["value"]["stressed_net"] == pytest.approx(60 * (2.5 - 3.0)) and c["pass"] is False
    c = run(stitched(60, profit=2.5, exit_kind="tp"))["criteria"]["cost_stress"]
    assert c["value"]["stressed_net"] == pytest.approx(60 * (2.5 - 2.0)) and c["pass"] is True


def test_neighbor_share_threshold():
    assert run(stitched(60), share=0.6)["criteria"]["neighbors"]["pass"] is True
    assert run(stitched(60), share=0.59)["criteria"]["neighbors"]["pass"] is False


def test_holdout_needs_frequency_profit_and_no_breach():
    # 2026.08.01-09.29 = 60 days; 15 fills/month needs 30 fills (29.57)
    good = run(stitched(60), holdout=stitched(30, "2026-08-01", 60))["criteria"]["holdout"]
    assert good["pass"] is True and good["value"]["days"] == 60
    assert run(stitched(60), holdout=stitched(29, "2026-08-01", 60))["criteria"]["holdout"]["pass"] is False
    assert run(stitched(60), holdout=stitched(30, "2026-08-01", 60, profit=-1.0))["criteria"]["holdout"]["pass"] is False
    crash = stitched(30, "2026-08-01", 60)
    crash["days"].loc[5, "eq_min"] = 8000.0
    c = run(stitched(60), holdout=crash)["criteria"]["holdout"]
    assert c["value"]["breach"] == 1 and c["pass"] is False
    assert run(stitched(60), holdout=None)["criteria"]["holdout"]["pass"] is False


def test_never_recommended_even_when_all_pass():
    out = run(stitched(60, profit=10.0), holdout=stitched(30, "2026-08-01", 60))
    assert out["recommended"] is False
    assert "R30" in out["recommended_note"]


def test_trial_sharpe_variance_uses_custom_column_of_passes_with_trades(tmp_path):
    import pandas as pd
    from mt5r import evaluate
    a = tmp_path / "f1.csv"
    b = tmp_path / "f2.csv"
    pd.DataFrame({"custom": [0.1, 0.3, 9.0], "trades": [5, 7, 0]}).to_csv(a, index=False)
    pd.DataFrame({"custom": [0.0, 0.2, 0.4], "trades": [3, 3, 3]}).to_csv(b, index=False)
    # fold 1: var([0.1, 0.3]) = 0.02 (the 0-trade pass is ignored); fold 2: var([0, .2, .4]) = 0.04; mean 0.03
    out = evaluate.trial_sharpe_variance([a, b])
    assert abs(out["var_sr"] - 0.03) < 1e-12
    assert out["passes"] == 5 and out["source"].startswith("optimization Custom")
