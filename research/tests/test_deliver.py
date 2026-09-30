import pandas as pd

from mt5r import deliver, pipeline


def test_recommended_is_never_written_in_this_research():
    # R30: no independent period exists, so even a full pass does not produce a recommended .set
    assert not deliver.should_write_recommended({"recommended": True, "passed_all": True})
    assert not deliver.should_write_recommended({"recommended": True, "acceptance": {"_passed_all": True}})
    assert not deliver.should_write_recommended({})


def test_parameter_table_lists_grid_defaults_and_candidate():
    p = pipeline.build_prereg("M5", pilot_runs=2, ea_sha256="x", gate_changes=[])
    cand = {"ObMode": 1, "EntryMode": 0, "ObMaxAgeBars": 144, "FvgWindowBars": 12, "OrderExpiryBars": 6}
    t = deliver.parameter_table(p, cand)
    assert "| `ObMode` | 0 | 1 | 0, 1 (categorical) |" in t
    assert "| `EntryMode` | 0 | 0 | 0, 1, 2, 3 (categorical) | unchanged" in t
    assert "| `ObMaxAgeBars` | 96 | 144 | 48, 96, 144 |" in t
    assert "| `OrderExpiryBars` | 12 | 6 | 6, 12, 18 |" in t
    assert "| `SignalTF` | M15 | M5 |" in t


def test_set_file_lines_round_trip(tmp_path):
    from mt5r import setfile
    p = tmp_path / "x.set"
    setfile.write_set(p, ["A=1||1||0||1||N", "B=x"], header="h")
    assert deliver.set_file_lines(p) == ["A=1||1||0||1||N", "B=x"]


def _curve(start, vals):
    d = pd.date_range(start, periods=len(vals), freq="D")
    return {"days": pd.DataFrame({"date": d, "eq_close": vals})}


def test_charts_use_passed_paths_and_titles(tmp_path):
    curves = {"Candidate": _curve("2026-03-01", [10000, 10100, 10050]),
              "Baseline": _curve("2026-03-01", [10000, 9950, 9900])}
    folds = [{"fold": 1, "label": "2026-03", "candidate": 120.0, "baseline": -30.0, "candidate_trades": 18}]
    mc = {"breach_prob": 0.04, "final_return_p5": -0.05, "final_return_p50": 0.1, "final_return_p95": 0.3}
    made = deliver.charts(curves, folds, mc, tmp_path / "c", window_label="OOS Mar-Jul 2026", stem="oos")
    assert sorted(made) == ["oos_by_fold.png", "oos_equity.png", "oos_mc_return_percentiles.png"]
    for f in made:
        assert (tmp_path / "c" / f).stat().st_size > 0
