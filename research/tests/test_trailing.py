"""1R trailing stop reference model (plan 2026-10-05-0007, U1): the user's rule as executable spec for the EA and
the independent checker."""
import pytest

from mt5r import trailing as tr

TICK, POINT = 0.01, 0.01
DONE = tr.RETCODE_DONE


def _long(fill=2000.00, sl0=1995.00, tp=None):
    r0 = abs(fill - sl0)
    return tr.TrailState(direction=1, fill=fill, sl0=sl0, tp=tp if tp is not None else fill + 2 * r0, sl=sl0)


def _short(fill=2000.00, sl0=2005.20, tp=None):
    r0 = abs(fill - sl0)
    return tr.TrailState(direction=-1, fill=fill, sl0=sl0, tp=tp if tp is not None else fill - 2 * r0, sl=sl0)


def _step(st, bid, ask, bar=0, stops=0, freeze=0):
    return tr.decide(st, bid, ask, msc=1, bar_time=bar, stops_points=stops, freeze_points=freeze, point=POINT,
                     tick=TICK)


def _accept(st, d, bar=0):
    return tr.on_result(st, d.requested, DONE, sl_read=d.requested, tp_read=st.tp, tick=TICK, bar_time=bar)


# ------------------------------------------------------------------ activation and the requested stop (R1-R4)
def test_long_no_activation_before_1r_then_activation_exactly_at_1r_requests_entry():          # Covers AE1
    st = _long()
    assert st.r0 == pytest.approx(5.00)
    assert _step(st, 2004.99, 2005.19).kind == "none" and not st.active
    d = _step(st, 2005.00, 2005.20)
    assert st.active and d.kind == "send" and d.requested == pytest.approx(2000.00)


def test_long_advances_continuously_and_never_retreats():                                      # Covers AE1
    st = _long()
    _accept(st, _step(st, 2005.00, 2005.20))
    d = _step(st, 2007.50, 2007.70)
    assert d.requested == pytest.approx(2002.50)                    # 1.5R -> stop at +0.5R
    _accept(st, d)
    assert _step(st, 2003.00, 2003.20).kind == "none"               # pullback: no request, stop stays
    assert st.sl == pytest.approx(2002.50) and st.active and st.best == pytest.approx(2007.50)
    d = _step(st, 2007.51, 2007.71)
    assert d.requested == pytest.approx(2002.51)                    # one more tick of best -> one more tick of stop


def test_long_moves_one_tick_per_new_best_tick_between_1r_and_2r():
    st = _long()
    reqs = []
    for k in range(0, 501):                                          # Bid 2005.00 .. 2010.00 in 0.01 steps
        bid = round(2005.00 + k * TICK, 2)
        d = _step(st, bid, bid + 0.2)
        if d.kind == "send":
            _accept(st, d)
            reqs.append(d.requested)
    assert len(reqs) == 501
    assert reqs[0] == pytest.approx(2000.00) and reqs[-1] == pytest.approx(2005.00)
    assert all(b - a == pytest.approx(TICK) for a, b in zip(reqs, reqs[1:]))


def test_short_uses_ask_and_no_extra_spread():                                                 # Covers AE2
    st = _short()
    assert st.r0 == pytest.approx(5.20)
    assert _step(st, 1994.61, 1994.81).kind == "none"               # Ask 1994.81 > E - R0
    d = _step(st, 1994.60, 1994.80)
    assert st.active and d.requested == pytest.approx(2000.00)
    _accept(st, d)
    d = _step(st, 1992.80, 1993.00)
    assert d.requested == pytest.approx(1998.20)                    # lowest Ask + R0, no spread term


def test_short_never_retreats_and_stays_active_on_pullback():
    st = _short()
    _accept(st, _step(st, 1994.60, 1994.80))
    _accept(st, _step(st, 1992.80, 1993.00))
    assert _step(st, 1999.00, 1999.20).kind == "none"
    assert st.sl == pytest.approx(1998.20) and st.active


def test_r0_and_tp_never_change_and_the_tp_sent_is_the_position_tp():
    st = _long()
    r0, tp = st.r0, st.tp
    for bid in (2005.00, 2006.00, 2007.00, 2008.00):
        d = _step(st, bid, bid + 0.2)
        assert d.tp == pytest.approx(tp)
        _accept(st, d)
    assert st.r0 == r0 and st.tp == tp and st.sl0 == 1995.00


# ------------------------------------------------------------------ rounding
def test_off_grid_levels_round_conservatively():
    assert tr.requested_sl(1, 2007.505, 5.00, TICK) == pytest.approx(2002.50)   # long: down
    assert tr.requested_sl(-1, 1993.005, 5.20, TICK) == pytest.approx(1998.21)  # short: up
    assert tr.requested_sl(1, 2005.00 + 1e-11, 5.00, TICK) == pytest.approx(2000.00)


# ------------------------------------------------------------------ rejection, broker limits, retry (R10-R12)
def test_done_but_unchanged_stop_is_a_rejection_and_the_same_value_is_not_resent():            # Covers AE3
    st = _long()
    d = _step(st, 2005.00, 2005.20, bar=100)
    out = tr.on_result(st, d.requested, DONE, sl_read=1995.00, tp_read=st.tp, tick=TICK, bar_time=100)
    assert out == "rejected" and st.sl == 1995.00                 # the active stop is kept
    assert _step(st, 2005.00, 2005.20, bar=100).kind == "none"    # same value, same bar: no flood
    assert _step(st, 2005.00, 2005.20, bar=160).kind == "send"    # new M1 bar: retry
    out = tr.on_result(st, 2000.00, DONE, sl_read=2000.00, tp_read=st.tp, tick=TICK, bar_time=160)
    assert out == "accepted" and st.sl == 2000.00


def test_a_failed_retcode_is_a_rejection_and_an_improved_value_is_sent_in_the_same_bar():
    st = _long()
    d = _step(st, 2005.00, 2005.20, bar=100)
    assert tr.on_result(st, d.requested, 10018, sl_read=1995.00, tp_read=st.tp, tick=TICK, bar_time=100) == "rejected"
    d = _step(st, 2005.01, 2005.21, bar=100)
    assert d.kind == "send" and d.requested == pytest.approx(2000.01)


def test_a_changed_tp_on_read_back_is_a_rejection():
    st = _long()
    d = _step(st, 2005.00, 2005.20)
    assert tr.on_result(st, d.requested, DONE, sl_read=d.requested, tp_read=st.tp + 1, tick=TICK, bar_time=0) \
        == "rejected"


def test_stops_level_blocks_a_too_close_stop_and_it_is_logged_once_per_value_and_bar():
    st = _long()
    _accept(st, _step(st, 2005.00, 2005.20))
    _accept(st, _step(st, 2008.00, 2008.20))                       # stop 2003.00
    d = _step(st, 2008.10, 2008.30, bar=200, stops=600)             # req 2003.10, Bid - req = 5.00 < 6.00
    assert d.kind == "not_sent" and d.reason == "stops_level" and st.sl == pytest.approx(2003.00)
    assert _step(st, 2008.10, 2008.30, bar=200, stops=600).kind == "none"


def test_freeze_level_blocks_a_modification_near_the_current_stop_or_tp():
    st = _long()
    _accept(st, _step(st, 2005.00, 2005.20))
    d = _step(st, 2009.95, 2010.15, freeze=10)                      # TP 2010.00 is 0.05 away < 0.10
    assert d.kind == "not_sent" and d.reason == "freeze_level"


def test_concurrent_positions_trail_independently():
    a, b = _long(), _short()
    da = _step(a, 2005.00, 2005.20)
    db = _step(b, 2005.00, 2005.20)
    assert da.kind == "send" and db.kind == "none" and not b.active
    _accept(a, da)
    assert a.sl == pytest.approx(2000.00) and b.sl == pytest.approx(2005.20)
