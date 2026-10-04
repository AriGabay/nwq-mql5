import math

import pandas as pd
import pytest

from mt5r import evaluate, pipeline

# test windows of 31 + 30 + 31 + 29 = 121 calendar days
FOLDS_121 = [{"fold": 1, "train": ["2025.12.01", "2026.02.28"], "test": ["2026.03.01", "2026.03.31"]},
             {"fold": 2, "train": ["2026.01.01", "2026.03.31"], "test": ["2026.04.01", "2026.04.30"]},
             {"fold": 3, "train": ["2026.02.01", "2026.04.30"], "test": ["2026.05.01", "2026.05.31"]},
             {"fold": 4, "train": ["2026.03.01", "2026.05.31"], "test": ["2026.06.01", "2026.06.29"]}]
CRITERIA = {"oos_frequency", "oos_net", "bootstrap_ci", "positive_folds", "top_events_removed", "loss_limits",
            "cost_stress", "stability", "dsr"}


def prereg(folds=FOLDS_121):
    p = pipeline.build_prereg(["pilot_a", "pilot_b"], ea_sha256="x", gate={})
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
                       "commission": 0.0, "swap": 0.0, "balance_at_open": 10000.0, "comment": "OBM1"})
    table = pd.DataFrame({"setup_id": range(n), "open_time": opens, "close_time": opens + pd.Timedelta(hours=1),
                          "volume": volume, "gross_profit": profit, "commission": 0.0, "swap": 0.0, "net": profit,
                          "exit_kind": exit_kind})
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


def run(series, base="default", folds_net=(1, 1, 1, 1), share=1.0, p=None):
    base = stitched(10, profit=-1.0) if base == "default" else base
    rows = [{"fold": i + 1, "net": v} for i, v in enumerate(folds_net)]
    return evaluate.evaluate(series, base, rows, share, 0.001, p or prereg())


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
    out = run(stitched(0, days=days), folds_net=(0, 0, 0, 0), share=float("nan"))
    crit = out["criteria"]
    assert set(crit) == CRITERIA          # August-September is not a criterion (R31)
    for name, c in crit.items():
        assert c["pass"] is False, name
        for leaf in _leaves(c["value"]):
            assert isinstance(leaf, (int, float)), (name, leaf)
            assert math.isfinite(leaf), (name, leaf)
    assert out["passed_all"] is False and out["recommended"] is False


def test_trade_events_merge_positions_of_one_m1_bar():
    t = pd.to_datetime(["2026-03-02 10:01:10", "2026-03-02 10:01:50", "2026-03-02 10:02:05"])
    v = run(stitched(3, times=t))["criteria"]["top_events_removed"]["value"]
    assert v["bar_minutes"] == 1 and v["n_events"] == 2


def test_net_positive_and_above_the_variant_a_baseline():
    c = run(stitched(60, profit=10.0), base=stitched(60, profit=12.0))["criteria"]["oos_net"]
    assert c["value"] == {"net": 600.0, "baseline_net": 720.0} and c["pass"] is False
    assert run(stitched(60, profit=10.0))["criteria"]["oos_net"]["pass"] is True
    own = run(stitched(60, profit=10.0), base=None)["criteria"]["oos_net"]       # fixed A is the baseline
    assert own["pass"] is True and "baseline" in own["threshold"]


def test_positive_folds_need_a_strict_majority():
    p = prereg(folds=FOLDS_121 + [{"fold": 5, "train": ["x", "y"], "test": ["2026.06.30", "2026.06.30"]}])
    assert run(stitched(60), folds_net=(5, 1, 2, -1, -3), p=p)["criteria"]["positive_folds"]["pass"] is True
    c = run(stitched(60), folds_net=(5, 1, 0, -1, -3), p=p)["criteria"]["positive_folds"]
    assert c["value"] == 2 and c["pass"] is False
    assert run(stitched(60), folds_net=(5, -1, 2))["criteria"]["positive_folds"]["pass"] is True   # 2 of 3
    assert run(stitched(60), folds_net=(5, -1, -2))["criteria"]["positive_folds"]["pass"] is False


def test_top_two_events_removed():
    t = pd.to_datetime(["2026-03-02 10:00", "2026-03-03 10:00", "2026-03-04 10:00"])
    proc = stitched(3, times=t)
    proc["trades"]["profit"] = [100.0, 50.0, -20.0]
    c = run(proc)["criteria"]["top_events_removed"]
    assert c["value"]["net_without_top"] == pytest.approx(-20.0) and c["pass"] is False
    proc["trades"]["profit"] = [100.0, 50.0, 20.0]
    assert run(proc)["criteria"]["top_events_removed"]["pass"] is True


def test_cost_stress_spread_stop_and_entry_slippage():
    # per 0.1-lot trade: spread 1 x 0.2 x 0.1 x 100 = 2.00; stop slippage 10 x 0.01 x 100 x 0.1 = 1.00;
    # entry slippage 10 x 0.01 x 100 x 0.1 = 1.00 on every trade
    c = run(stitched(60, profit=5.0, exit_kind="sl"))["criteria"]["cost_stress"]
    assert c["value"]["stressed_net"] == pytest.approx(60 * (5.0 - 4.0)) and c["pass"] is True
    assert c["value"]["entry_slippage_cost"] == pytest.approx(60.0)
    c = run(stitched(60, profit=3.5, exit_kind="sl"))["criteria"]["cost_stress"]
    assert c["value"]["stressed_net"] == pytest.approx(60 * (3.5 - 4.0)) and c["pass"] is False
    c = run(stitched(60, profit=3.5, exit_kind="tp"))["criteria"]["cost_stress"]
    assert c["value"]["stressed_net"] == pytest.approx(60 * (3.5 - 3.0)) and c["pass"] is True


def test_stability_share_threshold_and_not_run():
    assert run(stitched(60), share=0.75)["criteria"]["stability"]["pass"] is True
    assert run(stitched(60), share=0.5)["criteria"]["stability"]["pass"] is False
    out = run(stitched(60), share=None)
    assert out["criteria"]["stability"]["evaluated"] is False and out["criteria"]["stability"]["pass"] is None
    assert out["evaluated_all"] is False                         # left out of passed_all, said so


def test_never_recommended_even_when_all_pass():
    out = run(stitched(60, profit=10.0))
    assert out["recommended"] is False
    assert "R33" in out["recommended_note"]


def test_drawdowns_report_closed_balance_and_daily_equity_apart():
    s = stitched(3, times=pd.to_datetime(["2026-03-02 10:00", "2026-03-03 10:00", "2026-03-04 10:00"]))
    s["table"]["net"] = [100.0, -300.0, 50.0]
    s["days"].loc[2, "eq_min"] = 9500.0                          # an intraday low the closed trades never show
    d = evaluate.drawdowns(s)
    assert d["balance_dd_pct"] == pytest.approx(300 / 10100 * 100, abs=1e-3)
    assert d["equity_dd_pct"] > d["balance_dd_pct"]


def test_trial_sharpe_variance_uses_custom_column_of_passes_with_trades(tmp_path):
    a = tmp_path / "f1.csv"
    b = tmp_path / "f2.csv"
    pd.DataFrame({"custom": [0.1, 0.3, 9.0], "trades": [5, 7, 0]}).to_csv(a, index=False)
    pd.DataFrame({"custom": [0.0, 0.2, 0.4], "trades": [3, 3, 3]}).to_csv(b, index=False)
    # fold 1: var([0.1, 0.3]) = 0.02 (the 0-trade pass is ignored); fold 2: var([0, .2, .4]) = 0.04; mean 0.03
    out = evaluate.trial_sharpe_variance([a, b])
    assert abs(out["var_sr"] - 0.03) < 1e-12
    assert out["passes"] == 5 and out["source"].startswith("optimization Custom")


def test_named_baselines_require_beating_every_baseline():                               # numeric_v1 AE6
    p = prereg()
    rows = [{"fold": i + 1, "net": 1} for i in range(4)]
    s = stitched(50, profit=10.0)                                   # net 500
    bases = {"baseline_a": stitched(10, profit=30.0), "baseline_b": stitched(10, profit=80.0)}   # 300, 800
    out = evaluate.evaluate(s, None, rows, 1.0, 0.001, p, bases=bases)["criteria"]["oos_net"]
    assert out["pass"] is False
    assert out["value"]["baselines"] == {"baseline_a": 300.0, "baseline_b": 800.0}
    assert out["value"]["margins"] == {"baseline_a": 200.0, "baseline_b": -300.0}
    bases["baseline_b"] = stitched(10, profit=40.0)                 # 400
    assert evaluate.evaluate(s, None, rows, 1.0, 0.001, p, bases=bases)["criteria"]["oos_net"]["pass"] is True


def test_single_baseline_output_is_unchanged_without_named_baselines():
    out = run(stitched(60))["criteria"]["oos_net"]
    assert set(out["value"]) == {"net", "baseline_net"} and out["threshold"] == "> 0 and > baseline net"


def test_dsr_reports_the_sensitivity_trial_count_when_registered():
    p = prereg()
    p["stats"]["dsr_trials"], p["stats"]["dsr_trials_sensitivity"] = 126, 18
    rows = [{"fold": i + 1, "net": 1} for i in range(4)]
    d = evaluate.evaluate(stitched(80, profit=10.0), None, rows, 1.0, 0.001, p)["criteria"]["dsr"]["value"]
    assert d["trials"] == 126 and d["sensitivity"]["trials"] == 18
    assert d["sensitivity"]["psr_value"] >= d["psr_value"]


def test_named_baselines_fail_when_net_only_equals_a_baseline():
    """numeric_v1's real outcome: the fallback procedure equals baseline A, which is not an improvement."""
    rows = [{"fold": i + 1, "net": 1} for i in range(4)]
    s = stitched(50, profit=10.0)
    bases = {"baseline_a": stitched(50, profit=10.0), "baseline_b": stitched(10, profit=-5.0)}
    out = evaluate.evaluate(s, None, rows, 1.0, 0.001, prereg(), bases=bases)["criteria"]["oos_net"]
    assert out["pass"] is False and out["value"]["margins"]["baseline_a"] == 0.0
