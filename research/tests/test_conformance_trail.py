"""trail_r23 and the stop path in the exit check (plan 2026-10-05-0007, U3): the independent checker replays every
logged stop move through research/mt5r/trailing.py and checks completeness against the M1 Bid bars."""
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
           "exit_kind": "trail", "state_roundtrip": "ok"}
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
    assert "re-sent in the same minute" in details(viol)
