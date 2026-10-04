"""Fixed-stop tools refuse a trailed run, and the stop-path chart (plan 2026-10-05-0007, U4)."""
import pandas as pd
import pytest

from mt5r import charts_trail, session_sensitivity, stress, trailing
from test_conformance_trail import GOOD_MOVES, LONG_BARS, bars, moves, setups, trail


def test_a_run_is_trailed_by_a_trail_exit_or_a_non_empty_trail_table():
    assert trailing.is_trailed_run(setups(exit_kind="trail"))
    assert trailing.is_trailed_run(setups(exit_kind="tp"), trail())
    assert not trailing.is_trailed_run(setups(exit_kind="sl"), trail().iloc[0:0])
    assert not trailing.is_trailed_run(setups(exit_kind="tp"))


def test_the_session_sensitivity_refuses_a_trailed_run():
    with pytest.raises(trailing.FixedStopOnly, match="fixed stop"):
        session_sensitivity.evaluate(setups(exit_kind="trail"), pd.DataFrame(), pd.DataFrame())


def test_the_stop_slippage_stress_refuses_a_trailed_run_and_still_charges_fixed_stops():
    with pytest.raises(trailing.FixedStopOnly):
        stress.stop_slippage_cost(pd.DataFrame({"exit_kind": ["trail"], "volume": [0.1]}), 100, 10, 0.01)
    cost = stress.stop_slippage_cost(pd.DataFrame({"exit_kind": ["sl", "tp"], "volume": [0.1, 0.1]}), 100, 10, 0.01)
    assert list(cost) == pytest.approx([1.0, 0.0])


def test_the_session_probe_and_wfo_diagnostics_refuse_a_trailed_run(monkeypatch):
    import session_probe
    import wfo_diagnostics
    run = {"setups": setups(exit_kind="tp"), "trail": trail(), "bars_m1": bars(LONG_BARS)}
    monkeypatch.setattr(session_probe.cm, "read_run", lambda *a, **k: run)
    with pytest.raises(trailing.FixedStopOnly):
        session_probe.cases(["x_a"])
    monkeypatch.setattr(wfo_diagnostics, "_load", lambda folder, rid: (run, None, None, run["setups"]))
    with pytest.raises(trailing.FixedStopOnly):
        wfo_diagnostics.run_positions("f", "r")


def test_the_chart_draws_the_accepted_stop_path(tmp_path):
    st = setups().iloc[0].to_dict()
    tr = trail().iloc[0].to_dict()
    out = tmp_path / "pos.png"
    series = charts_trail.draw(bars(LONG_BARS), st, tr, moves(GOOD_MOVES), out)
    assert out.exists() and out.stat().st_size > 10_000
    assert [v for _, v in series] == [1995.0, 2000.0, 2000.5, 2002.5, 2002.5]


def test_chart_picks_are_deterministic_per_exit_class_and_side():
    t = pd.concat([trail(position_id=1, exit_kind="trail"), trail(position_id=2, exit_kind="tp"),
                   trail(position_id=3, exit_kind="trail"), trail(position_id=4, exit_kind="trail"),
                   trail(position_id=5, dir="S", exit_kind="sl")], ignore_index=True)
    assert charts_trail.select(t, per_class=2) == [1, 3, 2, 5]


def test_render_skips_setups_that_never_filled(tmp_path):
    st = pd.concat([setups(), setups().assign(setup_id=9, position_id=None, fill_price=None)], ignore_index=True)
    run = {"setups": st, "trail": trail(), "sl_moves": moves(GOOD_MOVES), "bars_m1": bars(LONG_BARS)}
    out = charts_trail.render(run, tmp_path, "t")
    assert [p.name for p in out] == ["t_L_trail_pos101.png"]
