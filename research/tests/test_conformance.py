"""Rule-conformance checker for the OB-FVG retest EA (plan U5).

Fixture bars (fixtures/ob_fvg/rl_bars.csv, M15, bar i opens at T0 + 900*i):
  long  0-17: OB bar 2 (1995.00-2000.20), idFVG 3..5 (1998.00-1999.00), activation 5, touch 7,
              an FVG 6..8 whose candle 1 precedes the touch (AE1), confirmation FVG 9..11 (2003.50-2003.80),
              entry 2003.80 / SL 1994.90 / TP 2021.60, placed in bar 12, filled in bar 13, TP in bar 16.
  short 18-35: price mirror (4000 - p) of the long up to the fill bar 31, then an SL path (loss).
tick_volume: 100/110/120 repeating, with spikes on the four FVG middle candles (long idFVG 4, long cFVG 10, short
idFVG 22, short cFVG 28). PARAMS use VolumeLookbackHours=1, i.e. a 4-bar look-back on M15, so every fixture FVG
ratio is recomputable from rl_bars (AMENDMENT A1); the logged ratios in the setup CSVs match those recomputations.
AMENDMENT B: only the identifying FVG must pass the volume filter; the confirmation FVG's ratio is informational.
"""
import pathlib

import pandas as pd
import pytest

from mt5r import conformance as cf

FX = pathlib.Path(__file__).parent / "fixtures" / "ob_fvg"
T0, P = 1767225600, 900
PARAMS = dict(ObMode=0, EntryMode=0, ImpulseWindowBars=2, BosWindowBars=6, SwingStrength=3, ObMaxAgeBars=96,
              FvgWindowBars=12, OrderExpiryBars=12, StopBufferPoints=10, RiskRR=2.0, point=0.01,
              VolumeMultiplier=2.0, VolumeLookbackHours=1)


def t(i):
    return T0 + P * i


def rules(violations):
    return {v["rule"] for v in violations}


@pytest.fixture
def setups():
    return cf.read_setups(FX / "rl_setups.csv")


@pytest.fixture
def bars():
    return cf.read_bars(FX / "rl_bars.csv")


def long_only(setups, **changes):
    row = setups[setups.setup_id == 1].reset_index(drop=True).copy()
    for k, v in changes.items():
        row.loc[0, k] = v
    return row


def test_readers_keep_times_integer_and_blanks_missing(setups, bars):
    assert bars["time"].tolist()[:2] == [t(0), t(1)] and pd.api.types.is_integer_dtype(bars["time"])
    assert int(setups.loc[0, "place_time_msc"]) == t(12) * 1000 + 200
    assert pd.isna(setups.loc[0, "bos_pivot_time"]) and pd.isna(setups.loc[0, "bos_level"])
    assert setups.loc[1, "dir"] == "S" and setups.loc[0, "reason"] == "filled"


def test_valid_long_and_short_pass(setups, bars):
    assert cf.check(setups, bars, P, PARAMS) == []


def test_ae1_confirmation_candle1_before_touch_is_flagged(setups, bars):
    # FVG 6..8 is a real bullish FVG (bar 8 low 2001.30 > bar 6 high 2001.00) but its candle 1 precedes the touch.
    bad = long_only(setups, cfvg_c1_time=t(6), cfvg_c3_time=t(8), cfvg_low=2001.00, cfvg_high=2001.30,
                    entry=2001.30, tp=2001.30 + 2 * (2001.30 - 1994.90), place_time_msc=t(9) * 1000 + 100)
    v = cf.check(bad, bars, P, PARAMS)
    assert "cfvg_c1_before_touch" in rules(v)
    assert all(x["setup_id"] == 1 for x in v)


def test_fill_before_placement_is_flagged(setups, bars):
    row = long_only(setups)
    row.loc[0, "fill_time_msc"] = int(row.loc[0, "place_time_msc"]) - 1
    assert "fill_before_place" in rules(cf.check(row, bars, P, PARAMS))


def test_tp_not_2r_is_flagged(setups, bars):
    bad = long_only(setups, tp=2003.80 + 3 * (2003.80 - 1994.90))
    assert rules(cf.check(bad, bars, P, PARAMS)) == {"tp"}


def test_placement_at_c3_close_is_inclusive(setups, bars):
    boundary = (t(11) + P) * 1000
    assert cf.check(long_only(setups, place_time_msc=boundary), bars, P, PARAMS) == []
    early = cf.check(long_only(setups, place_time_msc=boundary - 1), bars, P, PARAMS)
    assert rules(early) == {"place_before_c3_close"}


def test_kept_filled_late_passes_but_the_same_fill_as_filled_is_flagged(setups, bars):
    # With OrderExpiryBars=1 the pending window is bar 12; its cancellation executes on the first tick after
    # bar 12 closes. A fill on that tick is kept and labelled filled_late (R38).
    params = {**PARAMS, "OrderExpiryBars": 1}
    fill = t(13) * 1000 + 50
    late = long_only(setups, fill_time_msc=fill, reason_time_msc=fill, reason="filled_late")
    assert cf.check(late, bars, P, params) == []
    assert "fill_after_cancel" in rules(cf.check(long_only(setups, fill_time_msc=fill, reason_time_msc=fill),
                                                 bars, P, params))
    # A "late" fill a whole bar after the cancellation tick is not a kept fill.
    later = long_only(setups, fill_time_msc=t(14) * 1000 + 50, reason="filled_late")
    assert "fill_after_cancel" in rules(cf.check(later, bars, P, params))


def test_ae6_fill_then_close_beyond_ob_same_bar_stays_filled(setups, bars):
    b = bars.copy()
    b.loc[13, ["low", "close"]] = [1994.95, 1994.99]  # filled at 2003.80 mid-bar, then closes below OB low 1995.00
    assert cf.check(long_only(setups), b, P, PARAMS) == []
    occ = cf.occurrences(long_only(setups), b, P)
    assert occ["fill_then_close_beyond_same_bar"] == 1


def test_close_beyond_before_touch_invalidates(setups, bars):
    b = bars.copy()
    b.loc[6, ["low", "close"]] = [1994.00, 1994.50]  # bar 6 closes below the OB low before the recorded touch
    assert "close_beyond_before_stage" in rules(cf.check(long_only(setups), b, P, PARAMS))


def test_ob_must_be_the_most_recent_opposite_candle(setups, bars):
    bad = long_only(setups, ob_time=t(1), ob_high=2001.00, ob_low=1999.50)  # bar 1 is bullish
    assert "ob_candle" in rules(cf.check(bad, bars, P, PARAMS))


def test_entry_mode_mismatch_is_flagged(setups, bars):
    assert "entry" in rules(cf.check(long_only(setups), bars, P, {**PARAMS, "EntryMode": 2}))


def test_reason_inconsistent_with_bars(setups, bars):
    later = ["touch_time", "cfvg_c1_time", "cfvg_c3_time", "cfvg_low", "cfvg_high", "entry", "sl", "tp", "volume",
             "place_time_msc", "order_ticket", "fill_time_msc", "fill_price", "position_id", "exit_time_msc",
             "exit_price", "exit_kind"]
    row = long_only(setups, reason="expired_untouched")
    row.loc[0, later] = float("nan")
    assert "reason_inconsistent" in rules(cf.check(row, bars, P, PARAMS))  # bar 7 touches the OB


def test_confirmation_must_be_first_qualifying_fvg(setups, bars):
    b = bars.copy()
    b.loc[10, "low"] = 2003.10  # FVG 8..10 now qualifies (2003.10 > bar 8 high 2003.00): earlier than 9..11
    b.loc[9, "tick_volume"] = 1000  # a high-volume middle candle changes nothing (AMENDMENT B)
    assert "cfvg_not_first" in rules(cf.check(long_only(setups), b, P, PARAMS))


# --- volume filter on the identifying FVG only (AMENDMENT A1, R41; AMENDMENT B) ----------------------------------
def test_readers_accept_tick_volume_and_the_amendment_columns(setups, bars):
    assert bars["tick_volume"].tolist()[:5] == [100, 110, 120, 100, 330]
    assert setups.loc[0, "idfvg_vol_ratio"] == pytest.approx(3.0698)
    assert setups.loc[1, "cfvg_vol_ratio"] == pytest.approx(3.3488)
    assert pd.isna(setups.loc[0, "market_closed_first_msc"]) and int(setups.loc[0, "place_attempts"]) == 1


def test_old_files_without_volume_columns_are_still_readable(tmp_path):
    old_bars = tmp_path / "rl_bars.csv"
    lines = (FX / "rl_bars.csv").read_text().splitlines()
    old_bars.write_text("\n".join(",".join(x.split(",")[:5]) for x in lines) + "\n")
    old_setups = tmp_path / "rl_setups.csv"
    lines = (FX / "rl_setups.csv").read_text().splitlines()
    old_setups.write_text("\n".join(",".join(x.split(",")[:37]) for x in lines) + "\n")
    b, s = cf.read_bars(old_bars), cf.read_setups(old_setups)
    assert "tick_volume" in b and b["tick_volume"].isna().all()
    assert len(s) == 2 and s.loc[0, "reason"] == "filled"
    assert cf.check(s, b, P, PARAMS) == []  # pre-amendment runs: no volume evidence, no volume violations


def test_confirmation_fvg_with_a_low_ratio_passes(setups, bars):
    b = bars.copy()
    b.loc[10, "tick_volume"] = 150  # cFVG 9..11 middle candle: 150 / 107.5 = 1.40 < 2.0, informational only
    row = long_only(setups, cfvg_vol_ratio=round(150 / 107.5, 4))
    assert cf.check(row, b, P, PARAMS) == []


def test_low_volume_confirmation_fvg_makes_cancelled_no_fvg_inconsistent(setups, bars):
    b = bars.copy()
    b.loc[10, "tick_volume"] = 50  # still confirms: the EA must not have cancelled for lack of an FVG
    later = ["cfvg_c1_time", "cfvg_c3_time", "cfvg_low", "cfvg_high", "entry", "sl", "tp", "volume",
             "stops_level_pts", "place_time_msc", "order_ticket", "fill_time_msc", "fill_price", "position_id",
             "exit_time_msc", "exit_price", "exit_kind", "cfvg_vol_ratio"]
    row = long_only(setups, reason="cancelled_no_fvg", reason_time_msc=(t(7) + 12 * P + P) * 1000,
                    place_attempts=0)
    row.loc[0, later] = float("nan")
    assert "reason_inconsistent" in rules(cf.check(row, b, P, PARAMS))


def test_identifying_fvg_failing_volume_is_flagged(setups, bars):
    b = bars.copy()
    b.loc[4, "tick_volume"] = 200  # 200 / 107.5 = 1.86 < 2.0
    row = long_only(setups, idfvg_vol_ratio=round(200 / 107.5, 4))
    assert rules(cf.check(row, b, P, PARAMS)) == {"idfvg_volume"}


def test_without_volume_multiplier_the_identifying_ratio_is_informational(setups, bars):
    """AMENDMENT D: the EA has no VolumeMultiplier input; its runs carry none, so no volume threshold applies."""
    b = bars.copy()
    b.loc[4, "tick_volume"] = 200  # 1.86x: a violation under R41, nothing now
    params = {k: v for k, v in PARAMS.items() if k != "VolumeMultiplier"}
    row = long_only(setups, idfvg_vol_ratio=round(200 / 107.5, 4))
    assert cf.check(row, b, P, params) == []
    assert "vol_ratio_mismatch" in rules(cf.check(long_only(setups, idfvg_vol_ratio=1.5), b, P, params))
    assert "VolumeMultiplier" not in cf.DEFAULT_PARAMS


def test_logged_ratio_below_multiplier_is_flagged_without_enough_history(setups, bars):
    # Default 24 h look-back (96 bars) cannot be recomputed from 36 logged bars; the logged ratio still has to pass.
    params = {**PARAMS, "VolumeLookbackHours": 24}
    assert cf.check(long_only(setups), bars, P, params) == []
    assert rules(cf.check(long_only(setups, idfvg_vol_ratio=1.5), bars, P, params)) == {"idfvg_volume"}
    assert cf.check(long_only(setups, cfvg_vol_ratio=1.5), bars, P, params) == []  # informational (AMENDMENT B)


def test_earlier_low_volume_fvg_breaks_first_fvg_rule(setups, bars):
    b = bars.copy()
    b.loc[10, "low"] = 2003.10  # FVG 8..10 forms by price; its middle candle 9 has 100 / 112.5 = 0.89 (AMENDMENT B)
    assert "cfvg_not_first" in rules(cf.check(long_only(setups), b, P, PARAMS))


def test_logged_ratio_mismatch_is_flagged(setups, bars):
    assert rules(cf.check(long_only(setups, cfvg_vol_ratio=2.9), bars, P, PARAMS)) == {"vol_ratio_mismatch"}
    assert rules(cf.check(long_only(setups, idfvg_vol_ratio=3.2), bars, P, PARAMS)) == {"vol_ratio_mismatch"}
    assert cf.check(long_only(setups, cfvg_vol_ratio=3.1629), bars, P, PARAMS) == []  # 4-decimal rounding


# --- market closed at placement (AMENDMENT A2, R42) -------------------------------------------------------------
def test_market_closed_retry_placement_after_the_refusal_passes(setups, bars):
    refusal = t(12) * 1000 + 200  # first tick after confirmation c3 (bar 11) closes
    ok = long_only(setups, market_closed_first_msc=refusal, place_time_msc=t(13) * 1000 + 100, place_attempts=3)
    assert cf.check(ok, bars, P, PARAMS) == []
    occ = cf.occurrences(ok, bars, P, PARAMS)
    assert occ["market_closed_retry_placed"] == 1
    early = long_only(setups, market_closed_first_msc=refusal, place_time_msc=refusal - 100, place_attempts=3)
    assert "place_before_market_closed" in rules(cf.check(early, bars, P, PARAMS))
    # A refusal cannot precede the close of confirmation candle 3.
    pre = long_only(setups, market_closed_first_msc=t(11) * 1000 + 5, place_attempts=3)
    assert "market_closed_before_c3_close" in rules(cf.check(pre, bars, P, PARAMS))


def test_late_placement_without_a_market_closed_refusal_is_flagged(setups, bars):
    late = long_only(setups, place_time_msc=t(13) * 1000 + 100)
    assert "place_not_first_bar" in rules(cf.check(late, bars, P, PARAMS))


def test_market_closed_retry_after_the_order_window_is_flagged(setups, bars):
    params = {**PARAMS, "OrderExpiryBars": 1}  # only bar 12 may still place the order
    late = long_only(setups, market_closed_first_msc=t(12) * 1000 + 200, place_time_msc=t(13) * 1000 + 100,
                     place_attempts=3)
    assert "place_after_retry_window" in rules(cf.check(late, bars, P, params))


def _skipped_market_closed(setups, **changes):
    gone = {k: float("nan") for k in ("place_time_msc", "order_ticket", "fill_time_msc", "fill_price", "position_id",
                                       "exit_time_msc", "exit_price", "exit_kind")}
    return long_only(setups, **{**gone, "reason": "skipped_market_closed", "market_closed_first_msc": t(12) * 1000 + 200,
                                "place_attempts": 120, "reason_time_msc": t(14) * 1000 + 300, **changes})


def test_skipped_market_closed_rows(setups, bars):
    assert cf.check(_skipped_market_closed(setups), bars, P, PARAMS) == []
    with_ticket = _skipped_market_closed(setups, order_ticket=5001)
    assert "reason_fields" in rules(cf.check(with_ticket, bars, P, PARAMS))
    no_refusal = _skipped_market_closed(setups, market_closed_first_msc=float("nan"))
    assert "reason_fields" in rules(cf.check(no_refusal, bars, P, PARAMS))
    occ = cf.occurrences(_skipped_market_closed(setups), bars, P, PARAMS)
    assert occ["skipped_market_closed"] == 1 and occ["market_closed_retry_placed"] == 0


def test_skipped_broker_reject_is_a_known_skip_reason(setups, bars):
    row = _skipped_market_closed(setups, reason="skipped_broker_reject", market_closed_first_msc=float("nan"),
                                 place_attempts=1, reason_time_msc=t(12) * 1000 + 200)
    assert cf.check(row, bars, P, PARAMS) == []
    assert {"skipped_market_closed", "skipped_broker_reject"} <= cf.SKIP_REASONS


# --- FVG+BOS mode (R6, KTD3, AE7) ------------------------------------------------------------------------------
BOS_BARS = [
    (2000.0, 2001.0, 1999.0, 2000.5),  # 0
    (2000.5, 2002.0, 2000.0, 2001.5),  # 1
    (2001.5, 2005.0, 2001.0, 2002.0),  # 2 swing-high peak 2005.0
    (2002.0, 2003.0, 2000.0, 2000.5),  # 3
    (2000.5, 2002.0, 1998.0, 1998.5),  # 4 pivot confirmation bar at strength 2
    (1998.5, 1999.0, 1995.0, 1995.5),  # 5 OB (bearish)
    (1995.5, 1998.0, 1995.2, 1997.8),  # 6 idFVG c1
    (1997.8, 2004.0, 1997.5, 2003.8),  # 7
    (2003.8, 2006.0, 1999.0, 2005.5),  # 8 idFVG c3 and break close 2005.5 > 2005.0 -> activation
    (2005.5, 2006.0, 2001.0, 2002.0),  # 9
    (2002.0, 2002.5, 1998.8, 2001.5),  # 10 touch
    (2001.5, 2004.0, 2001.2, 2003.8),  # 11
    (2003.8, 2006.0, 2003.0, 2005.8),  # 12 cFVG 10..12 = 2002.50-2003.00
    (2005.8, 2006.5, 2005.0, 2006.0),  # 13
]


def bos_frames(**changes):
    bars = pd.DataFrame([(t(i),) + b for i, b in enumerate(BOS_BARS)], columns=["time", "open", "high", "low", "close"])
    row = {c: pd.NA for c in cf.SETUP_COLUMNS}
    row.update(setup_id=7, dir="L", ob_mode=1, entry_mode=0, ob_time=t(5), ob_high=1999.0, ob_low=1995.0,
               idfvg_c1_time=t(6), idfvg_c3_time=t(8), idfvg_low=1998.0, idfvg_high=1999.0,
               bos_pivot_time=t(2), bos_pivot_conf_time=t(4), bos_level=2005.0, bos_break_time=t(8),
               activation_time=t(8), touch_time=t(10), cfvg_c1_time=t(10), cfvg_c3_time=t(12),
               cfvg_low=2002.5, cfvg_high=2003.0, entry=2003.0, sl=1994.9, tp=2003.0 + 2 * 8.1, stops_level_pts=20,
               reason="skipped_cap", reason_time_msc=t(13) * 1000 + 10, retest_seen_no_fill=0)
    row.update(changes)
    return pd.DataFrame([row]), bars


def test_bos_valid_setup_passes():
    setups, bars = bos_frames()
    assert cf.check(setups, bars, P, {**PARAMS, "ObMode": 1, "SwingStrength": 2}) == []
    occ = cf.occurrences(setups, bars, P)
    assert occ["bos_mode"] == 1 and occ["cfvg_c1_is_touch_bar"] == 1 and occ["cap_skip"] == 1


def test_ae7_bos_pivot_confirmed_after_break_is_flagged():
    # Same peak (bar 2, before the OB candle) but at strength 7 its last confirmation bar (9) closes after the break (8).
    setups, bars = bos_frames(bos_pivot_conf_time=t(9))
    v = cf.check(setups, bars, P, {**PARAMS, "ObMode": 1, "SwingStrength": 7})
    assert "bos_pivot_confirmed_after_break" in rules(v)


def test_bos_pivot_after_ob_candle_is_flagged():
    setups, bars = bos_frames(bos_pivot_time=t(5), bos_pivot_conf_time=t(7), bos_level=1999.0)
    assert "bos_pivot_not_before_ob" in rules(cf.check(setups, bars, P, {**PARAMS, "ObMode": 1, "SwingStrength": 2}))


def test_occurrences_on_fixture(setups, bars):
    occ = cf.occurrences(setups, bars, P)
    for key in ("touch_bar_close_beyond", "cfvg_c1_is_touch_bar", "idfvg_c1_is_ob", "cap_skip", "too_close_skip",
                "margin_skip", "volume_skip", "duplicate_skip", "filled_late", "invalidated_pending",
                "fill_then_close_beyond_same_bar", "long", "short", "bos_mode", "market_closed_retry_placed",
                "skipped_market_closed", "idfvg_volume_checked", "cfvg_volume_checked"):
        assert key in occ
    assert occ["long"] == 1 and occ["short"] == 1 and occ["bos_mode"] == 0
    assert occ["idfvg_c1_is_ob"] == 0 and occ["cfvg_c1_is_touch_bar"] == 0 and occ["filled_late"] == 0
    # default 24 h look-back: no fixture FVG has 96 logged bars before it; a 1 h look-back recomputes all four
    assert occ["idfvg_volume_checked"] == 0 and occ["cfvg_volume_checked"] == 0
    occ = cf.occurrences(setups, bars, P, PARAMS)
    assert occ["idfvg_volume_checked"] == 2 and occ["cfvg_volume_checked"] == 2
    assert occ["market_closed_retry_placed"] == 0 and occ["skipped_market_closed"] == 0


def test_expired_unfilled_and_retest_flag_follow_the_bars(setups, bars):
    no_fill = {k: float("nan") for k in ("fill_time_msc", "fill_price", "position_id", "exit_time_msc", "exit_price")}
    # Order window of 1 bar (bar 12, low 2004.00) never reaches the 2003.80 limit: a clean expiry.
    row = long_only(setups, reason="expired_unfilled", retest_seen_no_fill=0, exit_kind=float("nan"), **no_fill)
    assert cf.check(row, bars, P, {**PARAMS, "OrderExpiryBars": 1}) == []
    # With 2 bars, bar 13 (low 2003.50) reaches the limit, so an unfilled order must carry retest_seen_no_fill=1.
    assert rules(cf.check(row, bars, P, {**PARAMS, "OrderExpiryBars": 2})) == {"retest_flag"}
    row.loc[0, "retest_seen_no_fill"] = 1
    assert cf.check(row, bars, P, {**PARAMS, "OrderExpiryBars": 2}) == []
    # The order window is still open at the end of the logged bars: expired_unfilled is inconsistent there.
    assert "reason_inconsistent" in rules(cf.check(row, bars, P, {**PARAMS, "OrderExpiryBars": 40}))


def test_run_end_pending_before_confirmation_passes(setups, bars):
    # The run ends at bar 9 while the long OB is touched (bar 7) but no confirmation FVG has completed yet:
    # run_end_pending covers any pre-fill stage, so no confirmation or order fields are required.
    cut = bars[bars.time <= t(9)].reset_index(drop=True)
    later = {k: float("nan") for k in ("cfvg_c1_time", "cfvg_c3_time", "cfvg_low", "cfvg_high", "entry", "sl", "tp",
                                       "volume", "place_time_msc", "order_ticket", "fill_time_msc", "fill_price",
                                       "position_id", "exit_time_msc", "exit_price", "exit_kind")}
    row = long_only(setups, reason="run_end_pending", retest_seen_no_fill=0, **later)
    assert cf.check(row, cut, P, PARAMS) == []
    # A run_end_pending row that already carries an order still needs its placement fields.
    row2 = long_only(setups, reason="run_end_pending", place_time_msc=float("nan"),
                     **{k: float("nan") for k in ("fill_time_msc", "fill_price", "position_id", "exit_time_msc",
                                                  "exit_price", "exit_kind")})
    assert "reason_fields" in rules(cf.check(row2, bars[bars.time <= t(13)].reset_index(drop=True), P, PARAMS))
