"""1R trailing stop reference model (plans 2026-10-05-0007 and 2026-10-05-0128): the user's rule, the retry policy
and the persistence as executable spec for the EA and the independent checker."""
import pytest

from mt5r import trailing as tr

TICK, POINT = 0.01, 0.01
DONE = tr.RETCODE_DONE
REJ = 10006                     # TRADE_RETCODE_REJECT: any rejection that is neither market closed nor too many


def _long(fill=2000.00, sl0=1995.00, tp=None, ticket=1):
    r0 = abs(fill - sl0)
    return tr.TrailState(direction=1, fill=fill, sl0=sl0, tp=tp if tp is not None else fill + 2 * r0, sl=sl0,
                         ticket=ticket)


def _short(fill=2000.00, sl0=2005.20, tp=None, ticket=2):
    r0 = abs(fill - sl0)
    return tr.TrailState(direction=-1, fill=fill, sl0=sl0, tp=tp if tp is not None else fill - 2 * r0, sl=sl0,
                         ticket=ticket)


def _step(st, bid, ask, msc=1, bar=0, stops=0, freeze=0, ctx=None):
    return tr.decide(st, ctx if ctx is not None else tr.EaContext(), bid, ask, msc=msc, bar_time=bar,
                     stops_points=stops, freeze_points=freeze, point=POINT, tick=TICK)


def _answer(st, d, retcode=DONE, msc=1, bar=0, ctx=None, sl_read=None):
    ok = retcode == DONE
    return tr.on_result(st, ctx if ctx is not None else tr.EaContext(), d.requested, retcode,
                        sl_read=(d.requested if ok else st.sl) if sl_read is None else sl_read, tp_read=st.tp,
                        tick=TICK, bar_time=bar, msc=msc)


# ------------------------------------------------------------------ activation and the requested stop (R1-R4, plan 0007)
def test_long_no_activation_before_1r_then_activation_exactly_at_1r_requests_entry():
    st = _long()
    assert st.r0 == pytest.approx(5.00)
    assert _step(st, 2004.99, 2005.19).kind == "none" and not st.active
    d = _step(st, 2005.00, 2005.20)
    assert st.active and d.kind == "send" and d.requested == pytest.approx(2000.00)


def test_long_advances_continuously_and_never_retreats():
    st = _long()
    _answer(st, _step(st, 2005.00, 2005.20))
    d = _step(st, 2007.50, 2007.70)
    assert d.requested == pytest.approx(2002.50)                    # 1.5R -> stop at +0.5R
    _answer(st, d)
    assert _step(st, 2003.00, 2003.20).kind == "none"               # pullback: no request, stop stays
    assert st.sl == pytest.approx(2002.50) and st.active and st.best == pytest.approx(2007.50)
    d = _step(st, 2007.51, 2007.71)
    assert d.requested == pytest.approx(2002.51)


def test_long_moves_one_tick_per_new_best_tick_between_1r_and_2r():
    st = _long()
    reqs = []
    for k in range(0, 501):                                          # Bid 2005.00 .. 2010.00 in 0.01 steps
        bid = round(2005.00 + k * TICK, 2)
        d = _step(st, bid, bid + 0.2, msc=1 + k)
        if d.kind == "send":
            _answer(st, d, msc=1 + k)
            reqs.append(d.requested)
    assert len(reqs) == 501
    assert reqs[0] == pytest.approx(2000.00) and reqs[-1] == pytest.approx(2005.00)
    assert all(b - a == pytest.approx(TICK) for a, b in zip(reqs, reqs[1:]))


def test_short_uses_ask_and_no_extra_spread():
    st = _short()
    assert st.r0 == pytest.approx(5.20)
    assert _step(st, 1994.61, 1994.81).kind == "none"
    d = _step(st, 1994.60, 1994.80)
    assert st.active and d.requested == pytest.approx(2000.00)
    _answer(st, d)
    d = _step(st, 1992.80, 1993.00)
    assert d.requested == pytest.approx(1998.20)


def test_short_never_retreats_and_stays_active_on_pullback():
    st = _short()
    _answer(st, _step(st, 1994.60, 1994.80))
    _answer(st, _step(st, 1992.80, 1993.00))
    assert _step(st, 1999.00, 1999.20).kind == "none"
    assert st.sl == pytest.approx(1998.20) and st.active


def test_r0_and_tp_never_change_and_the_tp_sent_is_the_position_tp():
    st = _long()
    r0, tp = st.r0, st.tp
    for k, bid in enumerate((2005.00, 2006.00, 2007.00, 2008.00)):
        d = _step(st, bid, bid + 0.2, msc=1 + k)
        assert d.tp == pytest.approx(tp)
        _answer(st, d, msc=1 + k)
    assert st.r0 == r0 and st.tp == tp and st.sl0 == 1995.00


def test_off_grid_levels_round_conservatively():
    assert tr.requested_sl(1, 2007.505, 5.00, TICK) == pytest.approx(2002.50)
    assert tr.requested_sl(-1, 1993.005, 5.20, TICK) == pytest.approx(1998.21)
    assert tr.requested_sl(1, 2005.00 + 1e-11, 5.00, TICK) == pytest.approx(2000.00)


def test_done_but_unchanged_stop_is_a_rejection_and_the_active_stop_is_kept():
    st, ctx = _long(), tr.EaContext()
    d = _step(st, 2005.00, 2005.20, msc=1000, ctx=ctx)
    assert _answer(st, d, DONE, msc=1000, ctx=ctx, sl_read=1995.00) == "rejected" and st.sl == 1995.00


def test_a_changed_tp_on_read_back_is_a_rejection():
    st = _long()
    d = _step(st, 2005.00, 2005.20)
    assert tr.on_result(st, tr.EaContext(), d.requested, DONE, sl_read=d.requested, tp_read=st.tp + 1, tick=TICK,
                        bar_time=0, msc=1) == "rejected"


# ------------------------------------------------------------------ the retry policy (plan 0128, R2-R7)
def test_after_a_rejection_nothing_is_sent_for_one_second_even_when_the_stop_improves():     # Covers AE1
    st, ctx = _long(), tr.EaContext()
    d = _step(st, 2005.00, 2005.20, msc=10_000_200, ctx=ctx)
    assert _answer(st, d, REJ, msc=10_000_200, ctx=ctx) == "rejected"
    for k, msc in enumerate(range(10_000_300, 10_001_200, 100)):           # best improves on every tick
        bid = 2005.10 + 0.10 * k
        assert _step(st, bid, bid + 0.2, msc=msc, ctx=ctx).kind == "none"
    assert st.best == pytest.approx(2005.90)                                # tracking never paused
    d = _step(st, 2006.00, 2006.20, msc=10_001_200, ctx=ctx)
    assert d.kind == "send" and d.requested == pytest.approx(2001.00) and d.retry   # from the current best


def test_short_waits_one_second_after_a_rejection_on_ask():
    st, ctx = _short(), tr.EaContext()
    d = _step(st, 1994.60, 1994.80, msc=5_000, ctx=ctx)
    _answer(st, d, REJ, msc=5_000, ctx=ctx)
    assert _step(st, 1994.00, 1994.20, msc=5_999, ctx=ctx).kind == "none"
    d = _step(st, 1993.90, 1994.10, msc=6_000, ctx=ctx)
    assert d.kind == "send" and d.requested == pytest.approx(1999.30)


def test_repeated_rejections_send_at_most_once_per_second():
    st, ctx = _long(), tr.EaContext()
    sends = []
    for msc in range(0, 5_000, 100):                                         # a tick every 100 ms, price rising
        bid = round(2005.00 + msc / 100 * TICK, 2)
        d = _step(st, bid, bid + 0.2, msc=msc, ctx=ctx)
        if d.kind == "send":
            sends.append(msc)
            _answer(st, d, REJ, msc=msc, ctx=ctx)
    assert sends == [0, 1000, 2000, 3000, 4000]


def test_a_success_after_the_wait_restores_per_tick_requests():
    st, ctx = _long(), tr.EaContext()
    _answer(st, _step(st, 2005.00, 2005.20, msc=0, ctx=ctx), REJ, msc=0, ctx=ctx)
    d = _step(st, 2005.10, 2005.30, msc=1000, ctx=ctx)
    assert _answer(st, d, DONE, msc=1000, ctx=ctx) == "accepted" and st.rejected_msc is None
    d = _step(st, 2005.20, 2005.40, msc=1001, ctx=ctx)
    assert d.kind == "send" and not d.retry


def test_too_many_requests_backs_off_1_2_4_8_16_then_30_seconds_ea_wide():                 # Covers AE2
    st, ctx = _long(), tr.EaContext()
    msc, gaps = 0, []
    d = _step(st, 2005.00, 2005.20, msc=msc, ctx=ctx)
    for _ in range(7):
        _answer(st, d, tr.RETCODE_TOO_MANY_REQUESTS, msc=msc, ctx=ctx)
        start = msc
        while True:
            msc += 100
            d = _step(st, 2005.00, 2005.20, msc=msc, ctx=ctx)
            if d.kind == "send":
                break
        gaps.append(msc - start)
    assert gaps == [1000, 2000, 4000, 8000, 16000, 30000, 30000]


def test_backoff_holds_every_position_and_a_success_clears_the_streak():
    a, b, ctx = _long(ticket=1), _long(ticket=2), tr.EaContext()
    _answer(a, _step(a, 2005.00, 2005.20, msc=0, ctx=ctx), tr.RETCODE_TOO_MANY_REQUESTS, msc=0, ctx=ctx)
    assert _step(b, 2005.00, 2005.20, msc=500, ctx=ctx).kind == "none"      # b never failed, still held
    da = _step(a, 2005.00, 2005.20, msc=1000, ctx=ctx)
    db = _step(b, 2005.00, 2005.20, msc=1000, ctx=ctx)
    assert da.kind == "send" and db.kind == "none"                          # one retry on this tick
    assert _answer(a, da, DONE, msc=1000, ctx=ctx) == "accepted" and ctx.streak == 0
    da = _step(a, 2005.10, 2005.30, msc=1001, ctx=ctx)
    db = _step(b, 2005.10, 2005.30, msc=1001, ctx=ctx)
    assert da.kind == "send" and db.kind == "send"                          # normal behaviour again


def test_only_too_many_requests_advances_the_streak():
    st, ctx = _long(), tr.EaContext()
    _answer(st, _step(st, 2005.00, 2005.20, msc=0, ctx=ctx), tr.RETCODE_TOO_MANY_REQUESTS, msc=0, ctx=ctx)
    _answer(st, _step(st, 2005.00, 2005.20, msc=1000, ctx=ctx), REJ, msc=1000, ctx=ctx)
    assert ctx.streak == 1
    _answer(st, _step(st, 2005.00, 2005.20, msc=2000, ctx=ctx), tr.RETCODE_TOO_MANY_REQUESTS, msc=2000, ctx=ctx)
    assert ctx.streak == 2 and ctx.backoff_until_msc == 4000


def test_two_pending_retries_on_one_tick_go_one_per_tick():
    a, b, ctx = _long(ticket=1), _long(ticket=2), tr.EaContext()
    _answer(a, _step(a, 2005.00, 2005.20, msc=0, ctx=ctx), REJ, msc=0, ctx=ctx)
    _answer(b, _step(b, 2005.00, 2005.20, msc=0, ctx=ctx), REJ, msc=0, ctx=ctx)
    assert _step(a, 2005.00, 2005.20, msc=1000, ctx=ctx).kind == "send"
    assert _step(b, 2005.00, 2005.20, msc=1000, ctx=ctx).kind == "none"
    assert _step(b, 2005.00, 2005.20, msc=1001, ctx=ctx).kind == "send"


def test_normal_requests_of_several_positions_share_a_tick():
    a, b, ctx = _long(ticket=1), _long(ticket=2), tr.EaContext()
    assert _step(a, 2005.00, 2005.20, msc=0, ctx=ctx).kind == "send"
    assert _step(b, 2005.00, 2005.20, msc=0, ctx=ctx).kind == "send"


def test_market_closed_holds_until_the_next_bar_and_the_wait_still_applies():               # Covers AE3
    st, ctx = _long(), tr.EaContext()
    bar0, bar1 = 60_000, 120_000                                             # M1 bars in ms
    d = _step(st, 2005.00, 2005.20, msc=119_800, bar=bar0, ctx=ctx)
    assert _answer(st, d, tr.RETCODE_MARKET_CLOSED, msc=119_800, bar=bar0, ctx=ctx) == "rejected"
    assert _step(st, 2005.50, 2005.70, msc=119_900, bar=bar0, ctx=ctx).kind == "none"
    assert _step(st, 2005.50, 2005.70, msc=120_000, bar=bar1, ctx=ctx).kind == "none"   # new bar, wait not over
    d = _step(st, 2005.50, 2005.70, msc=120_800, bar=bar1, ctx=ctx)
    assert d.kind == "send" and d.requested == pytest.approx(2000.50)


def test_stops_level_is_logged_once_per_value_and_bar_and_starts_no_wait():
    st, ctx = _long(), tr.EaContext()
    _answer(st, _step(st, 2005.00, 2005.20, msc=0, ctx=ctx), msc=0, ctx=ctx)
    _answer(st, _step(st, 2008.00, 2008.20, msc=1, ctx=ctx), msc=1, ctx=ctx)  # stop 2003.00
    d = _step(st, 2008.10, 2008.30, msc=2, bar=200, stops=600, ctx=ctx)    # req 2003.10, 5.00 < 6.00
    assert d.kind == "not_sent" and d.reason == "stops_level" and st.sl == pytest.approx(2003.00)
    assert _step(st, 2008.10, 2008.30, msc=3, bar=200, stops=600, ctx=ctx).kind == "none"
    assert st.rejected_msc is None
    d = _step(st, 2008.20, 2008.40, msc=4, bar=200, stops=0, ctx=ctx)       # level lifted: sent at once
    assert d.kind == "send" and not d.retry


def test_freeze_level_blocks_a_modification_near_the_current_stop_or_tp():
    st = _long()
    _answer(st, _step(st, 2005.00, 2005.20))
    d = _step(st, 2009.95, 2010.15, msc=2, freeze=10)
    assert d.kind == "not_sent" and d.reason == "freeze_level"


def test_concurrent_positions_trail_independently():
    a, b, ctx = _long(), _short(), tr.EaContext()
    da = _step(a, 2005.00, 2005.20, ctx=ctx)
    db = _step(b, 2005.00, 2005.20, ctx=ctx)
    assert da.kind == "send" and db.kind == "none" and not b.active
    _answer(a, da, ctx=ctx)
    assert a.sl == pytest.approx(2000.00) and b.sl == pytest.approx(2005.20)


# ------------------------------------------------------------------ persistence (plan 0128, R9-R11)
def test_a_new_best_survives_a_rejected_update_save_and_restore():                          # Covers AE4
    st, ctx = _long(ticket=7), tr.EaContext()
    _answer(st, _step(st, 2007.50, 2007.70, msc=0, ctx=ctx), msc=0, ctx=ctx)
    d = _step(st, 2008.20, 2008.40, msc=100, ctx=ctx)
    assert _answer(st, d, REJ, msc=100, ctx=ctx) == "rejected"
    assert ctx.store.dirty
    back = tr.restore(ctx.store.mem, 7)
    assert back.best == pytest.approx(2008.20) and back.active and back.r0 == pytest.approx(5.00)
    assert back.sl0 == 1995.00 and back.activated_msc == 0


def test_a_best_seen_while_held_is_stored():
    st, ctx = _long(ticket=3), tr.EaContext()
    _answer(st, _step(st, 2005.00, 2005.20, msc=0, ctx=ctx), REJ, msc=0, ctx=ctx)
    assert _step(st, 2006.40, 2006.60, msc=300, ctx=ctx).kind == "none"
    assert tr.restore(ctx.store.mem, 3).best == pytest.approx(2006.40)


def test_the_flush_runs_at_registration_activation_every_ten_seconds_when_dirty_and_at_deinit():
    st, ctx = _long(ticket=4), tr.EaContext()
    tr.register(st, ctx, msc=0)
    assert not ctx.store.dirty and tr.restore(ctx.store.disk, 4).fill == 2000.00
    _step(st, 2005.00, 2005.20, msc=500, ctx=ctx)                          # activation flushes at once
    assert not ctx.store.dirty and tr.restore(ctx.store.disk, 4).active
    _step(st, 2005.10, 2005.30, msc=600, ctx=ctx)                          # new best: stored, not yet flushed
    assert ctx.store.dirty and tr.restore(ctx.store.disk, 4).best == pytest.approx(2005.00)
    assert not tr.flush_due(ctx, 10_499) and tr.flush_due(ctx, 10_500)
    tr.flush(ctx, 10_500)
    assert tr.restore(ctx.store.disk, 4).best == pytest.approx(2005.10)
    _step(st, 2005.20, 2005.40, msc=11_000, ctx=ctx)
    tr.deinit(ctx, [st], msc=11_001)
    assert not ctx.store.dirty and tr.restore(ctx.store.disk, 4).best == pytest.approx(2005.20)
