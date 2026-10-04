"""trail_r23 and the stop path in the exit check (plan 2026-10-05-0007, U3): the independent checker replays every
logged stop move through research/mt5r/trailing.py and checks completeness against the M1 Bid bars."""
import pathlib
from types import SimpleNamespace

import pandas as pd
import pytest

from mt5r import conformance_m1 as cm
from mt5r import m1_contract as mc

T0 = 1772400000                      # an M1 bar open (multiple of 60)
TICK = 0.01


def ms(bar, sec=10):
    return (T0 + 60 * bar + sec) * 1000


def bars(rows):
    """rows: (open, high, low, close) per consecutive minute from T0."""
    return pd.DataFrame([{"time": T0 + 60 * i, "open": o, "high": h, "low": lo, "close": c, "tick_volume": 10,
                          "spread": 20, "warmup": 0} for i, (o, h, lo, c) in enumerate(rows)])


LONG_BARS = [(2000.0, 2001.0, 1999.5, 2000.5),      # 0 fill
             (2000.5, 2005.5, 2000.2, 2005.3),      # 1 reaches +1R (2005.00)
             (2005.3, 2007.5, 2004.0, 2006.0),      # 2 best 2007.50
             (2006.0, 2006.0, 2002.4, 2002.6)]      # 3 exit at the trailed stop 2002.50


def setups(exit_kind="trail", exit_price=2002.50, exit_bar=3, fill=2000.0, sl=1995.0, tp=2010.0):
    return pd.DataFrame([{"setup_id": 7, "position_id": 101, "dir": "L", "fill_price": fill, "sl": sl, "tp": tp,
                          "fill_msc": ms(0), "exit_msc": ms(exit_bar, 30), "exit_price": exit_price,
                          "exit_kind": exit_kind, "reason": "filled"}])


def moves(rows):
    cols = mc.SL_MOVE_COLUMNS
    return pd.DataFrame([dict(zip(cols, (101,) + r)) for r in rows], columns=cols)


GOOD_MOVES = [(ms(1, 20), 2005.00, 2005.20, 2005.00, 2000.00, 1995.00, 2000.00, 10009, "accepted"),
              (ms(1, 40), 2005.50, 2005.70, 2005.50, 2000.50, 2000.00, 2000.50, 10009, "accepted"),
              (ms(2, 30), 2007.50, 2007.70, 2007.50, 2002.50, 2000.50, 2002.50, 10009, "accepted")]


def trail(**over):
    row = {"position_id": 101, "setup_id": 7, "dir": "L", "fill_price": 2000.0, "sl0": 1995.0, "r0": 5.0,
           "tp": 2010.0, "activated_msc": ms(1, 20), "activation_bid": 2005.00, "activation_ask": 2005.20,
           "best_price": 2007.50, "final_sl": 2002.50, "requests": 3, "accepted": 3, "rejected": 0, "not_sent": 0,
           "exit_kind": "trail", "state_roundtrip": "ok", "state_final": "ok"}
    row.update(over)
    return pd.DataFrame([row])


def run(st=None, tr=None, mv=None, bar_rows=LONG_BARS):
    B = cm._Bars(bars(bar_rows), 60)
    return cm.check_trails(setups() if st is None else st, trail() if tr is None else tr,
                           moves(GOOD_MOVES) if mv is None else mv, B, TICK)


def details(viol):
    return " | ".join(v["detail"] for v in viol)


def test_a_conforming_long_trail_passes():
    viol, n, unv = run()
    assert viol == [] and n == 1 and unv == {}


def test_a_request_that_is_not_the_model_value_is_flagged():
    bad = list(GOOD_MOVES)
    bad[1] = bad[1][:4] + (2000.40,) + bad[1][5:6] + (2000.40,) + bad[1][7:]
    viol, _, _ = run(mv=moves(bad), tr=trail(final_sl=2002.50))
    assert "!= model 2000.5" in details(viol)


def test_a_retreating_accepted_stop_is_flagged():
    bad = GOOD_MOVES[:2] + [(ms(2, 30), 2007.50, 2007.70, 2007.50, 2000.40, 2000.50, 2000.40, 10009, "accepted")]
    viol, _, _ = run(mv=moves(bad), tr=trail(final_sl=2000.40))
    assert "does not improve" in details(viol)


def test_a_request_before_1r_is_flagged():
    early = [(ms(1, 5), 2004.00, 2004.20, 2004.00, 1999.00, 1995.00, 1999.00, 10009, "accepted")] + GOOD_MOVES
    viol, _, _ = run(mv=moves(early), tr=trail(activated_msc=ms(1, 5), accepted=4))
    assert "request before +1R" in details(viol)


@pytest.mark.parametrize("field,value,text", [("tp", 2011.0, "differ from rl_setups"), ("r0", 4.0, "R0 4.0")])
def test_a_changed_tp_or_r0_is_flagged(field, value, text):
    viol, _, _ = run(tr=trail(**{field: value}))
    assert text in details(viol)


def test_exit_classification_must_follow_the_moves():
    viol, _, _ = run(st=setups(exit_kind="sl"), tr=trail(exit_kind="sl"))
    assert "classified sl although the stop had been moved" in details(viol)
    viol, _, _ = run(mv=moves([]), tr=trail(activated_msc=None, activation_bid=None, activation_ask=None,
                                            accepted=0, final_sl=1995.0, best_price=2000.0),
                     bar_rows=[LONG_BARS[0], (2000.5, 2001.0, 1999.0, 2000.0), (2000.0, 2000.5, 1994.0, 1995.0)],
                     st=setups(exit_kind="trail", exit_bar=2, exit_price=1995.0))
    assert "classified trail although the stop was never moved" in details(viol)


def test_a_missed_long_activation_is_flagged():
    viol, _, _ = run(mv=moves([]), tr=trail(activated_msc=None, activation_bid=None, activation_ask=None,
                                            accepted=0, final_sl=1995.0, best_price=2007.5, exit_kind="sl"),
                     st=setups(exit_kind="sl"))
    assert "reaches +1R on Bid but no activation" in details(viol)


def test_a_stalled_long_trail_is_flagged():
    viol, _, _ = run(mv=moves(GOOD_MOVES[:2]), tr=trail(best_price=2005.50, final_sl=2000.50, accepted=2))
    assert "below the highest Bid 2007.5" in details(viol)


def test_a_short_activation_the_bars_cannot_prove_is_counted_not_flagged():
    st = pd.DataFrame([{"setup_id": 8, "position_id": 202, "dir": "S", "fill_price": 2000.0, "sl": 2005.2,
                        "tp": 1989.6, "fill_msc": ms(0), "exit_msc": ms(3, 30), "exit_price": 1989.6,
                        "exit_kind": "tp", "reason": "filled"}])
    tr = trail(position_id=202, setup_id=8, dir="S", sl0=2005.2, r0=5.2, tp=1989.6, activated_msc=None,
               activation_bid=None, activation_ask=None, best_price=2000.0, final_sl=2005.2, accepted=0,
               exit_kind="tp")
    short_bars = [(2000.0, 2000.5, 1999.5, 2000.0), (2000.0, 2000.0, 1994.7, 1995.0),
                  (1995.0, 1995.5, 1993.0, 1994.0), (1994.0, 1994.0, 1989.0, 1989.5)]
    viol, _, unv = run(st=st, tr=tr, mv=moves([]), bar_rows=short_bars)
    assert viol == [] and unv == {"short_trail_ask": [8]}


def _exit_check(st, mv):
    rp = cm._Replay(st, None, None, bars(LONG_BARS), bars(LONG_BARS), {}, None, trail(), mv)
    rp.B = cm._Bars(bars(LONG_BARS), 60)
    s = SimpleNamespace(row=st.iloc[0].to_dict(), sign=1, sid=7, reason="filled")
    rp._check_exit(s)
    return details(rp.out)


def test_the_exit_check_uses_the_stop_in_force_not_sl0():
    assert _exit_check(setups(), moves(GOOD_MOVES)) == ""
    # without the moves the stop would be SL0 = 1995.00, which bar 3 (low 2002.40) never reaches
    assert "does not reach the stop 1995.0" in _exit_check(setups(), moves([]))


def test_a_trail_exit_whose_bar_never_reaches_the_trailed_stop_is_flagged():
    st = setups(exit_price=2003.10)
    low = list(LONG_BARS)
    low[3] = (2006.0, 2006.0, 2003.0, 2003.2)
    rp = cm._Replay(st, None, None, bars(low), bars(low), {}, None, trail(), moves(GOOD_MOVES))
    rp.B = cm._Bars(bars(low), 60)
    rp._check_exit(SimpleNamespace(row=st.iloc[0].to_dict(), sign=1, sid=7, reason="filled"))
    assert "does not reach the stop 2002.5" in details(rp.out)


def test_stop_at_returns_the_last_move_strictly_before():
    path = [(100, 2000.0), (200, 2001.0)]
    assert cm.stop_at(path, 1995.0, 100) == 1995.0
    assert cm.stop_at(path, 1995.0, 150) == 2000.0
    assert cm.stop_at(path, 1995.0, 999) == 2001.0


def test_a_request_in_a_market_closed_minute_or_an_identical_resend_is_flagged():
    mc_rows = [GOOD_MOVES[0][:6] + (1995.00, 10018, "rejected"),
               (ms(1, 30), 2005.20, 2005.40, 2005.20, 2000.20, 1995.00, 1995.00, 10018, "rejected")]
    viol, _, _ = run(mv=moves(mc_rows), tr=trail(final_sl=1995.0, accepted=0, best_price=2007.5, exit_kind="sl"),
                     st=setups(exit_kind="sl", exit_price=1995.0))
    assert "already answered market closed" in details(viol)
    same = [GOOD_MOVES[0][:6] + (1995.00, 10006, "rejected"), GOOD_MOVES[0][:6] + (1995.00, 10006, "rejected")]
    viol, _, _ = run(mv=moves(same), tr=trail(final_sl=1995.0, accepted=0, best_price=2007.5, exit_kind="sl"),
                     st=setups(exit_kind="sl", exit_price=1995.0))
    assert "0 ms after its rejection" in details(viol)


# ------------------------------------------------------------------ the PR #6 evidence under the plan 0128 rules
V1_ON_B = pathlib.Path(__file__).resolve().parents[2] / "results" / "trailing_v1" / "tr1_on_b"


def test_the_pr6_trailed_run_is_not_recertified_under_the_new_contract():
    """results/trailing_v1/tr1_on_b predates rl_trail.state_final: it fails `fields` and nothing else."""
    import cli
    run = cm.read_run(V1_ON_B, "tr1_on_b")
    params = {**cli.RUN["symbol_spec"], "ImpulseWindowBars": 2, "SwingStrengthM1": 3, "StopBufferPoints": 20,
              "RiskRR": 2.0, "RiskPercent": 1.0, "MaxExposures": 3, "WarmupDays": 30, "StructureVariant": 1,
              "point": cli.RUN["symbol_spec"]["tick_size"]}
    res = cm.full(run["setups"], run["events"], run["pivots"], run["bars_m1"], run["bars_m5"], params,
                  run.get("deals"), run.get("trail"), run.get("sl_moves"), trailing=True)
    assert [(v["rule"], "state_final" in v["detail"]) for v in res["violations"]] == [("fields", True)]


# ------------------------------------------------------------------ completeness (plan 0128, R12-R13)
def two_positions(trail_rows=(101, 102)):
    st = pd.concat([setups(), setups().assign(setup_id=8, position_id=102)], ignore_index=True)
    tr = pd.concat([trail(position_id=p, setup_id=7 if p == 101 else 8) for p in trail_rows], ignore_index=True)
    mv = pd.concat([moves(GOOD_MOVES), moves(GOOD_MOVES).assign(position_id=102)], ignore_index=True)
    return st, tr, mv


def test_every_filled_position_needs_exactly_one_trail_row():                                  # Covers AE5
    st, tr, mv = two_positions()
    viol, n, _ = run(st=st, tr=tr, mv=mv)
    assert viol == [] and n == 2
    st, tr, mv = two_positions(trail_rows=(101,))
    viol, _, _ = run(st=st, tr=tr, mv=mv[mv["position_id"] == 101])
    assert "position 102 is filled but has no rl_trail row" in details(viol)
    st, tr, mv = two_positions(trail_rows=(101, 102, 102))
    assert "2 rl_trail rows for position 102" in details(run(st=st, tr=tr, mv=mv)[0])


def test_a_trail_row_without_a_filled_position_is_flagged():
    viol, _, _ = run(tr=pd.concat([trail(), trail(position_id=555, setup_id=99)], ignore_index=True))
    assert "rl_trail row for position 555 has no filled rl_setups row" in details(viol)


def test_the_trail_files_must_match_the_run_input():
    assert cm.trail_presence(True, None, None)[0]["rule"] == "trail_r23"
    assert "missing" in cm.trail_presence(True, trail(), None)[0]["detail"]
    assert "trail-off run" in cm.trail_presence(False, trail(), moves(GOOD_MOVES))[0]["detail"]
    assert cm.trail_presence(False, None, None) == [] and cm.trail_presence(True, trail(), moves([])) == []
    assert "EnableTrailingStop" in cm.trail_presence("maybe", None, None)[0]["detail"]
    assert cm.parse_trailing("true") is True and cm.parse_trailing(1) is True and cm.parse_trailing("false") is False
    assert cm.parse_trailing(None) is False


def test_a_not_trailed_row_is_accepted_only_without_moves_activation_or_a_moved_stop():
    nt = dict(state_roundtrip="not_trailed:no_risk", state_final="not_trailed", activated_msc=None,
              activation_bid=None, activation_ask=None, accepted=0, requests=0, final_sl=1995.0, best_price=2000.0,
              exit_kind="sl", sl0=1995.0, r0=0.0, tp=0.0)
    st = setups(exit_kind="sl", exit_price=1995.0)
    viol, _, _ = run(st=st, tr=trail(**nt), mv=moves([]))
    assert viol == []
    viol, _, _ = run(st=st, tr=trail(**nt), mv=moves(GOOD_MOVES[:1]))
    assert "not trailed but has 1 stop request" in details(viol)
    viol, _, _ = run(st=st, tr=trail(**{**nt, "state_roundtrip": "ok"}), mv=moves([]))
    assert "state_final not_trailed" in details(viol)


def test_the_stored_state_at_close_must_equal_memory():
    viol, _, _ = run(tr=trail(state_final="mismatch"))
    assert "stored state at close" in details(viol)


def test_a_trail_table_without_the_state_final_column_fails_fields():
    viol, _, _ = run(tr=trail().drop(columns=["state_final"]))
    assert viol[0]["rule"] == "fields" and "state_final" in viol[0]["detail"]


# ------------------------------------------------------------------ the retry policy (plan 0128, R3-R7)
REJ_AT = ms(1, 20)


def rejected_then(after_ms, rc=10006):
    return [(REJ_AT, 2005.00, 2005.20, 2005.00, 2000.00, 1995.00, 1995.00, rc, "rejected"),
            (REJ_AT + after_ms, 2005.00, 2005.20, 2005.00, 2000.00, 1995.00, 2000.00, 10009, "accepted"),
            (ms(2, 30), 2007.50, 2007.70, 2007.50, 2002.50, 2000.00, 2002.50, 10009, "accepted")]


def test_a_request_within_one_second_of_its_rejection_is_flagged():
    viol, _, _ = run(mv=moves(rejected_then(600)), tr=trail(requests=3, accepted=2, rejected=1))
    assert "600 ms after its rejection" in details(viol)
    viol, _, _ = run(mv=moves(rejected_then(1000)), tr=trail(requests=3, accepted=2, rejected=1))
    assert viol == []


def test_the_too_many_requests_backoff_is_ea_wide_and_doubles():
    rows = [(REJ_AT, 2005.00, 2005.20, 2005.00, 2000.00, 1995.00, 1995.00, 10024, "rejected"),
            (REJ_AT + 1000, 2005.00, 2005.20, 2005.00, 2000.00, 1995.00, 1995.00, 10024, "rejected"),
            (REJ_AT + 2500, 2005.00, 2005.20, 2005.00, 2000.00, 1995.00, 2000.00, 10009, "accepted"),
            (ms(2, 30), 2007.50, 2007.70, 2007.50, 2002.50, 2000.00, 2002.50, 10009, "accepted")]
    tr = trail(requests=4, accepted=2, rejected=2)
    assert "during the EA backoff" in details(run(mv=moves(rows), tr=tr)[0])        # needs 2 s after the 2nd
    rows[2] = (REJ_AT + 3000,) + rows[2][1:]
    assert run(mv=moves(rows), tr=tr)[0] == []


def test_two_retries_on_one_tick_are_flagged_and_two_normal_requests_are_not():
    st, tr, mv = two_positions()
    assert run(st=st, tr=tr, mv=mv)[0] == []                                   # normal sends share ticks
    rows = rejected_then(1000)
    mv = pd.concat([moves(rows), moves(rows).assign(position_id=102)], ignore_index=True)
    tr = pd.concat([trail(requests=3, accepted=2, rejected=1),
                    trail(position_id=102, setup_id=8, requests=3, accepted=2, rejected=1)], ignore_index=True)
    viol, _, _ = run(st=st, tr=tr, mv=mv)
    assert "second retry on tick" in details(viol)


def test_a_rejected_request_must_also_improve_the_stop():
    rows = [GOOD_MOVES[0], (ms(1, 40), 2005.50, 2005.70, 2005.50, 2000.00, 2000.00, 2000.00, 10006, "rejected"),
            (ms(2, 30), 2007.50, 2007.70, 2007.50, 2002.50, 2000.00, 2002.50, 10009, "accepted")]
    viol, _, _ = run(mv=moves(rows), tr=trail(accepted=2, rejected=1))
    assert "does not improve" in details(viol)


# ------------------------------------------------------------------ plan 0128 evidence: results/trailing_v2/tr2_on_b
V2_ON_B = pathlib.Path(__file__).resolve().parents[2] / "results" / "trailing_v2" / "tr2_on_b"


def _v2_full(trail_table):
    import cli
    run = cm.read_run(V2_ON_B, "tr2_on_b")
    params = {"ImpulseWindowBars": 2, "SwingStrengthM1": 3, "StopBufferPoints": 20, "RiskRR": 2.0, "RiskPercent": 1.0,
              "MaxExposures": 3, "WarmupDays": 30, "StructureVariant": 1,
              "point": cli.RUN["symbol_spec"]["tick_size"], "contract_size": cli.RUN["symbol_spec"]["contract_size"]}
    t = run["trail"] if trail_table is None else trail_table(run["trail"])
    return run, cm.full(run["setups"], run["events"], run["pivots"], run["bars_m1"], run["bars_m5"], params,
                        run.get("deals"), t, run.get("sl_moves"), trailing=True)


def test_the_stored_trailed_run_passes_with_one_row_per_filled_position():
    run, res = _v2_full(None)
    filled = int(run["setups"]["fill_price"].notna().sum())
    assert res["violations"] == [] and res["coverage"]["rules"]["trail_r23"]["checked"] == filled == 56


def test_deleting_one_trail_row_of_the_stored_run_fails():                                    # Covers R14
    run, res = _v2_full(lambda t: t.iloc[1:])
    pid = int(run["trail"]["position_id"].iloc[0])
    assert [v["detail"] for v in res["violations"]] == [f"position {pid} is filled but has no rl_trail row"]


def test_duplicating_one_trail_row_of_the_stored_run_fails():                                 # Covers R14
    run, res = _v2_full(lambda t: pd.concat([t, t.iloc[[3]]], ignore_index=True))
    pid = int(run["trail"]["position_id"].iloc[3])
    assert any(v["detail"] == f"2 rl_trail rows for position {pid}" for v in res["violations"])
