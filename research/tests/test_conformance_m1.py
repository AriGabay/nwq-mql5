"""Independent M5/M1 conformance checker (plan 2026-10-03-0013, U4; R5-R19, R22, R25, KTD2-KTD8, KTD11).

All scenarios run with SwingStrengthM1 = 1 so every pivot is a hand-checkable turning point: a pivot high at bar p
has a strictly higher high than bars p-1 and p+1 and is confirmed at the close of bar p+1.

Base fixture (fixtures/m1/rl_*_base.csv, M1 bar i opens at T0 + 60 i, bars 0-4 are warm-up):
  M5 #1 (bars 5-9) is a bearish OB 2004.80-2010.10, identifying FVG #1..#3 (2010.10-2010.90), identified when #3 closes.
  Pivots: H4 L9 H16 L17 H19 (pre-touch; H0 = H19 2017.20, L0 = L17 2012.30), L26 2007.80, H29 2014.20 (a lower high
  that never completes), L31 2010.30, H35 2018.70, L37 2014.10.
  Bar 24 touches the OB high (Bid 2010.10). Bar 33 closes 2014.50 above H29: HH, origin bar 31. Bar 33 is the middle
  candle of FVG 32..34 (2012.70-2014.40), fixed when bar 34 = k+1 closes (AE1). L37 confirms at bar 38 (HL 2014.10).
  Bar 38 is the reaction; the Market buy fills at the first tick of bar 39 (Ask 2016.70), SL 2004.60 (OB anchor
  2004.80 - 20 points), TP 2016.70 + 2 x 12.10 = 2040.90.
Other scenarios reuse bars 0-24 (or 0-k) of the base and replace the tail; each one is described where it is built.
"""
import gzip
import pathlib
import shutil

import pandas as pd
import pytest

from mt5r import conformance_m1 as cf
from mt5r import m1_contract as mc

FX = pathlib.Path(__file__).parent / "fixtures" / "m1"
T0 = 1772442000                     # Monday 2026-03-02 09:00
T7 = 1772839200                     # Friday 2026-03-06 23:20 (AE7)
MONDAY = 1773018000                 # Monday 2026-03-09 01:00 (AE7)
K = 4020.0                          # AE7 price mirror: p -> K - p
PARAMS = dict(StructureVariant=0, ImpulseWindowBars=2, SwingStrengthM1=1, StopBufferPoints=20, RiskRR=2.0,
              point=0.01)
PARAMS_B = {**PARAMS, "StructureVariant": 1}


def t(i):
    return T0 + 60 * i


def rules(violations):
    return {v["rule"] for v in violations}


def run_check(run, params=PARAMS):
    return cf.check(run["setups"], run["events"], run["pivots"], run["bars_m1"], run["bars_m5"], params)


BASE_M1 = pd.read_csv(FX / "rl_bars_m1_base.csv")
BASE_ROWS = [tuple(r) for r in BASE_M1[["open", "high", "low", "close"]].itertuples(index=False)]
PREFIX_PIVOTS = [(1, "H", 4, 2010.2), (2, "L", 9, 2004.8), (3, "H", 16, 2015.2), (4, "L", 17, 2012.3),
                 (5, "H", 19, 2017.2)]
LONG = dict(setup_id=1, dir="L", variant=0, ob_time=t(5), ob_high=2010.1, ob_low=2004.8, idfvg_c1_time=t(5),
            idfvg_c3_time=t(15), idfvg_low=2010.1, idfvg_high=2010.9, identified_in_warmup=0,
            touch_msc=t(24) * 1000 + 40000, touch_bar_time=t(24), breaks=0, returns=0)
TOUCH = dict(setup_id=1, kind="touch", bar_time=t(24), tick_msc=t(24) * 1000 + 40000, price=2010.1)
# Short candidate created by bars 0-24 + a tail that stays below 2010.90: bearish FVG #3..#5, OB = bullish #3.
SHORT_UNTOUCHED = dict(setup_id=2, dir="S", variant=0, ob_time=t(15), ob_high=2017.2, ob_low=2010.9,
                       idfvg_c1_time=t(15), idfvg_c3_time=t(25), idfvg_low=2010.1, idfvg_high=2010.9,
                       identified_in_warmup=0, breaks=0, returns=0, reason="run_end_untouched")


# --- in-test scenario builder ----------------------------------------------------------------------------------
def build(rows, pivots, events, setups, times=None, warm=5):
    """A run dict from M1 rows (open, high, low, close); M5 bars are the aggregate of each complete group of five
    M1 bars or of a group followed by a later M1 bar (a closed M5 bar). pivots: (id, type, peak index, level[,
    replaced_by]) with conf = peak + 1 (N = 1)."""
    times = times or [t(i) for i in range(len(rows))]
    m1 = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    m1.insert(0, "time", times)
    m1["tick_volume"], m1["spread"] = 100, 20
    m1["warmup"] = [1 if i < warm else 0 for i in range(len(rows))]
    m5 = []
    groups = m1.groupby(m1["time"] // 300 * 300, sort=True)
    for start, g in groups:
        if len(g) == 5 or (m1["time"] >= start + 300).any():
            m5.append(dict(time=int(start), open=g["open"].iloc[0], high=g["high"].max(), low=g["low"].min(),
                           close=g["close"].iloc[-1], tick_volume=500, spread=20, warmup=int(g["warmup"].iloc[0])))
    pv = [dict(pivot_id=p[0], type=p[1], peak_time=times[p[2]], conf_time=times[p[2] + 1], level=p[3],
               replaced_by=p[4] if len(p) > 4 else None, outside_bar=0) for p in pivots]
    seq = {}
    ev = []
    for e in events:
        seq[e["setup_id"]] = seq.get(e["setup_id"], 0) + 1
        ev.append({c: e.get(c, seq[e["setup_id"]] if c == "seq" else None) for c in mc.EVENT_COLUMNS})
    st = [{c: s.get(c) for c in mc.SETUP_COLUMNS} for s in setups]
    return dict(setups=pd.DataFrame(st, columns=mc.SETUP_COLUMNS), events=pd.DataFrame(ev, columns=mc.EVENT_COLUMNS),
                pivots=pd.DataFrame(pv, columns=mc.PIVOT_COLUMNS), bars_m1=m1, bars_m5=pd.DataFrame(m5))


def with_events(run, events):
    return {**run, "events": build(BASE_ROWS[:1], [], events, [])["events"]}


def with_setup(run, **changes):
    s = run["setups"].copy()
    for k, v in changes.items():
        s.loc[0, k] = v
    return {**run, "setups": s}


def base_events(run):
    return run["events"].to_dict("records")


@pytest.fixture
def base():
    return cf.read_run(FX, "base")


# --- readers ---------------------------------------------------------------------------------------------------
def test_readers_keep_times_integer_and_load_gzipped_bars(base, tmp_path):
    assert pd.api.types.is_integer_dtype(base["bars_m1"]["time"]) and int(base["bars_m1"]["time"].iloc[1]) == t(1)
    assert int(base["events"].loc[0, "tick_msc"]) == t(24) * 1000 + 40000
    assert pd.isna(base["events"].loc[0, "ref_id"]) and base["setups"].loc[0, "reason"] == "filled"
    assert base["pivots"]["type"].tolist()[:2] == ["H", "L"] and int(base["bars_m1"]["warmup"].sum()) == 5
    for f in FX.glob("rl_*_base.csv"):
        if f.name.startswith("rl_bars_m1"):
            with open(f, "rb") as src, gzip.open(tmp_path / (f.name + ".gz"), "wb") as dst:
                shutil.copyfileobj(src, dst)
        else:
            shutil.copy(f, tmp_path / f.name)
    gz = cf.read_run(tmp_path, "base")
    assert gz["bars_m1"]["time"].tolist() == base["bars_m1"]["time"].tolist()
    assert run_check(gz) == []


def test_valid_base_long_passes(base):
    assert run_check(base) == []
    occ = cf.occurrences(base["setups"], base["events"], base["pivots"], base["bars_m1"], base["bars_m5"], PARAMS)
    assert occ["long"] == 1 and occ["filled"] == 1 and occ["sc_hh"] == 1 and occ["fvg_fixed"] == 1
    assert occ["hl"] == 1 and occ["reaction"] == 1 and occ["lh_voided"] == 1 and occ["sl_anchor_ob"] == 1


# --- AE1 (R12) -------------------------------------------------------------------------------------------------
def test_ae1_fvg_with_middle_candle_after_the_hh_bar_is_flagged(base):
    ev = base_events(base)
    fvg = next(e for e in ev if e["kind"] == "fvg_fixed")
    fvg.update(bar_time=t(35), ref_time=t(33), lo=2014.7, hi=2016.4)   # FVG 33..35: middle candle 34 = k+1
    v = run_check(with_events(base, ev))
    assert "fvg_r12" in rules(v)
    assert any(x["rule"] == "fvg_r12" and "fvg_not_in_move" in x["detail"] for x in v)


# --- AE2 (R6, R7) and KTD6 -------------------------------------------------------------------------------------
AE2_TAIL = [(2010.0, 2010.1, 2006.8, 2007.0),   # 25
            (2007.0, 2007.1, 2004.7, 2004.9),   # 26 wick below the OB low 2004.80, closes inside: no break
            (2004.9, 2005.0, 2003.8, 2004.0),   # 27 closes below: break
            (2004.0, 2004.7, 2003.9, 2004.5),   # 28 still below, no return (same episode)
            (2004.5, 2006.2, 2004.4, 2006.0),   # 29 Bid back at 2004.80: return
            (2006.0, 2007.2, 2005.9, 2007.0)]   # 30


def ae2(events=None, reason="run_end_waiting", tail=AE2_TAIL, **changes):
    events = events if events is not None else [
        TOUCH, dict(setup_id=1, kind="break", bar_time=t(27), price=2004.0),
        dict(setup_id=1, kind="return", bar_time=t(29), tick_msc=t(29) * 1000 + 15000, price=2004.8)]
    return build(BASE_ROWS[:25] + tail, PREFIX_PIVOTS + [(6, "L", 27, 2003.8)], events,
                 [{**LONG, "breaks": 1, "returns": 1, "reason": reason, **changes}, SHORT_UNTOUCHED])


def test_ae2_valid_break_and_bid_range_return_pass():
    assert run_check(ae2()) == []


def test_ae2_wick_below_the_ob_is_not_a_break():
    ev = [TOUCH, dict(setup_id=1, kind="break", bar_time=t(26), price=2004.9),
          dict(setup_id=1, kind="return", bar_time=t(29), tick_msc=t(29) * 1000 + 15000, price=2004.8)]
    assert "break_r6" in rules(run_check(ae2(ev)))


def test_ae2_return_is_the_first_bid_range_reach_not_a_close_inside():
    ev = [TOUCH, dict(setup_id=1, kind="break", bar_time=t(27), price=2004.0),
          dict(setup_id=1, kind="return", bar_time=t(30), tick_msc=t(30) * 1000 + 1000, price=2006.0)]
    assert "return_r7" in rules(run_check(ae2(ev)))


KTD6_TAIL = AE2_TAIL[:4] + [(2004.5, 2005.0, 2004.4, 2004.6)]   # 29: wicks back to 2005.00, closes below again


def test_ktd6_return_and_break_in_one_bar_cancels():
    ev = [TOUCH, dict(setup_id=1, kind="break", bar_time=t(27), price=2004.0),
          dict(setup_id=1, kind="return", bar_time=t(29), tick_msc=t(29) * 1000 + 10000, price=2004.8),
          dict(setup_id=1, kind="cancelled_second_break", bar_time=t(29))]
    ok = ae2(ev, reason="cancelled_second_break", tail=KTD6_TAIL, reason_msc=t(30) * 1000)
    assert run_check(ok) == []
    occ = cf.occurrences(ok["setups"], ok["events"], ok["pivots"], ok["bars_m1"], ok["bars_m5"], PARAMS)
    assert occ["return_and_break_same_bar"] == 1
    kept = ev[:3]
    assert "second_break_r8" in rules(run_check(ae2(kept, tail=KTD6_TAIL)))


# --- AE3, AE4 (R22) --------------------------------------------------------------------------------------------
def test_ae3_lower_high_then_close_below_l1_cancels():
    # After the touch: L1 = L26 2007.80 (below L0 2012.30), H2 = H29 2014.20 (below H0 2017.20), bar 32 closes below L1.
    rows = BASE_ROWS[:31] + [(2012.0, 2012.1, 2009.8, 2010.0), (2010.0, 2010.1, 2007.6, 2007.7)]
    piv = PREFIX_PIVOTS + [(6, "L", 26, 2007.8), (7, "H", 29, 2014.2)]
    cancel = dict(setup_id=1, kind="cancelled_opposing_structure", bar_time=t(32), ref_id=7, ref_time=t(26))
    ok = build(rows, piv, [TOUCH, cancel], [{**LONG, "reason": "cancelled_opposing_structure"}])
    assert run_check(ok) == []
    missed = build(rows, piv, [TOUCH], [{**LONG, "reason": "run_end_waiting"}])
    assert "cancel_r22" in rules(run_check(missed))


def test_ae3_close_above_h2_first_voids_the_lower_high():
    # Bar 31 closes 2015.00 above H2 (void, and the HH of R10); H32 2017.70 then replaces H29 and is above H0, so
    # bar 35 closing below L1 cancels nothing. FVG 30..32 is fixed at bar 32 and lapses at bar 33.
    rows = BASE_ROWS[:30] + [(2014.0, 2014.1, 2012.8, 2013.0), (2013.0, 2015.2, 2012.9, 2015.0),
                             (2015.0, 2017.7, 2014.9, 2017.5), (2017.5, 2017.6, 2013.8, 2014.0),
                             (2014.0, 2014.1, 2010.8, 2011.0), (2011.0, 2011.1, 2007.4, 2007.6)]
    piv = PREFIX_PIVOTS + [(6, "L", 26, 2007.8), (7, "H", 29, 2014.2, 8), (8, "H", 32, 2017.7)]
    ev = [TOUCH, dict(setup_id=1, kind="sc_hh", bar_time=t(31), price=2014.2, ref_id=7, ref_time=t(29)),
          dict(setup_id=1, kind="fvg_fixed", bar_time=t(32), ref_time=t(30), lo=2014.1, hi=2014.9),
          dict(setup_id=1, kind="fvg_lapsed", bar_time=t(33))]
    assert run_check(build(rows, piv, ev, [{**LONG, "reason": "run_end_waiting"}])) == []
    claimed = ev + [dict(setup_id=1, kind="cancelled_opposing_structure", bar_time=t(35), ref_id=7, ref_time=t(26))]
    v = run_check(build(rows, piv, claimed, [{**LONG, "reason": "cancelled_opposing_structure"}]))
    assert "cancel_r22" in rules(v)


def test_ae4_lower_high_from_another_swing_does_not_combine_with_an_old_low():
    # The touch bar closes below L0 (2012.30); later H29 and H33 are lower highs whose own preceding lows (L26, L31)
    # are never closed below. Claiming a cancellation from H29 plus the close below L0 is mixing moves.
    rows = BASE_ROWS[:31] + [(2012.0, 2012.1, 2009.3, 2009.5), (2009.5, 2011.7, 2009.4, 2011.5),
                             (2011.5, 2013.7, 2011.4, 2013.5), (2013.5, 2013.6, 2010.3, 2010.5),
                             (2010.5, 2010.6, 2009.6, 2009.8)]
    piv = PREFIX_PIVOTS + [(6, "L", 26, 2007.8), (7, "H", 29, 2014.2), (8, "L", 31, 2009.3), (9, "H", 33, 2013.7)]
    assert run_check(build(rows, piv, [TOUCH], [{**LONG, "reason": "run_end_waiting"}])) == []
    mixed = [TOUCH, dict(setup_id=1, kind="cancelled_opposing_structure", bar_time=t(31), ref_id=7, ref_time=t(17))]
    v = run_check(build(rows, piv, mixed, [{**LONG, "reason": "cancelled_opposing_structure"}]))
    assert "cancel_r22" in rules(v)


# --- AE5 (R14, R11) --------------------------------------------------------------------------------------------
AE5_ROWS = BASE_ROWS[:36] + [(2018.5, 2018.6, 2014.8, 2015.0),   # 36
                             (2015.0, 2015.8, 2014.3, 2015.6),   # 37 touches the FVG and closes green above it
                             (2015.6, 2015.7, 2014.0, 2014.2),   # 38 pivot low 2014.00 (HL), confirmed at 39
                             (2014.2, 2016.4, 2014.1, 2016.2),   # 39 HL confirmation bar
                             (2016.2, 2016.3, 2014.6, 2014.8),   # 40
                             (2014.8, 2016.5, 2014.3, 2016.3),   # 41 first qualifying reaction after the HL
                             (2016.3, 2017.5, 2016.2, 2017.3)]   # 42 fill at its first tick (Ask 2016.50)
AE5_PIVOTS = PREFIX_PIVOTS + [(6, "L", 26, 2007.8), (7, "H", 29, 2014.2), (8, "L", 31, 2010.3), (9, "H", 35, 2018.7),
                              (10, "L", 38, 2014.0), (11, "H", 39, 2016.4), (12, "L", 41, 2014.3)]
AE5_HEAD = [TOUCH, dict(setup_id=1, kind="sc_hh", bar_time=t(33), price=2014.2, ref_id=7, ref_time=t(31)),
            dict(setup_id=1, kind="fvg_fixed", bar_time=t(34), ref_time=t(32), lo=2012.7, hi=2014.4)]


def ae5_fill(i, ask):
    tp = round(ask + 2 * (ask - 2004.6), 2)
    return [dict(setup_id=1, kind="entry_attempt", tick_msc=t(i) * 1000, price=ask, detail="10009"),
            dict(setup_id=1, kind="fill", tick_msc=t(i) * 1000, price=ask, lo=2004.6, hi=tp)], tp


def test_ae5_variant_b_enters_only_after_the_hl_confirmation():
    fill, tp = ae5_fill(42, 2016.5)
    ev = AE5_HEAD + [dict(setup_id=1, kind="hl", bar_time=t(39), price=2014.0, ref_id=10),
                     dict(setup_id=1, kind="reaction", bar_time=t(41))] + fill
    row = {**LONG, "variant": 1, "reason": "filled", "sl": 2004.6, "sl_anchor": "ob", "sl_anchor_price": 2004.8,
           "buffer_pts": 20, "tp": tp, "fill_msc": t(42) * 1000, "fill_price": 2016.5}
    assert run_check(build(AE5_ROWS, AE5_PIVOTS, ev, [row]), PARAMS_B) == []
    early_fill, early_tp = ae5_fill(38, 2015.8)
    early = AE5_HEAD + [dict(setup_id=1, kind="reaction", bar_time=t(37))] + early_fill
    row_early = {**row, "tp": early_tp, "fill_msc": t(38) * 1000, "fill_price": 2015.8}
    assert "reaction_r14" in rules(run_check(build(AE5_ROWS, AE5_PIVOTS, early, [row_early]), PARAMS_B))


# --- AE6 (R16) -------------------------------------------------------------------------------------------------
# Long A (base OB) is touched at bar 25; long B (OB = bearish M5 #5, 2006.80-2011.10, overlapping A) is identified
# at bar 40 and touched at bar 44. Bar 49 closes above H39 for both (origin L45), FVG 48..50 (2016.20-2017.90),
# HL L52, and bar 53 is a reaction candle for both. B touched later, so B enters at bar 54; A loses.
AE6_ROWS = BASE_ROWS[:20] + [
    (2017.0, 2017.1, 2015.6, 2015.8), (2015.8, 2015.9, 2014.4, 2014.6), (2014.6, 2014.7, 2013.2, 2013.4),
    (2013.4, 2013.5, 2012.0, 2012.2), (2012.2, 2012.3, 2010.8, 2011.0),                                    # 20-24
    (2011.0, 2011.1, 2009.3, 2009.5), (2009.5, 2009.6, 2007.8, 2008.0), (2008.0, 2008.1, 2006.8, 2007.0),
    (2007.0, 2008.2, 2006.9, 2008.0), (2008.0, 2009.2, 2007.9, 2009.0),                                    # 25-29
    (2009.0, 2010.2, 2008.9, 2010.0), (2010.0, 2011.2, 2009.9, 2011.0), (2011.0, 2012.2, 2010.9, 2012.0),
    (2012.0, 2013.2, 2011.9, 2013.0), (2013.0, 2014.2, 2012.9, 2014.0),                                    # 30-34
    (2014.0, 2014.7, 2013.9, 2014.5), (2014.5, 2015.2, 2014.4, 2015.0), (2015.0, 2015.7, 2014.9, 2015.5),
    (2015.5, 2016.2, 2015.4, 2016.0), (2016.0, 2016.7, 2015.9, 2016.5),                                    # 35-39
    (2016.5, 2016.6, 2015.2, 2015.4), (2015.4, 2015.5, 2014.1, 2014.3), (2014.3, 2014.4, 2013.0, 2013.2),
    (2013.2, 2013.3, 2011.9, 2012.1), (2012.1, 2012.2, 2010.8, 2011.0),                                    # 40-44
    (2011.0, 2011.1, 2009.8, 2010.0), (2010.0, 2012.2, 2009.9, 2012.0), (2012.0, 2014.2, 2011.9, 2014.0),
    (2014.0, 2016.2, 2013.9, 2016.0), (2016.0, 2018.2, 2015.9, 2018.0),                                    # 45-49
    (2018.0, 2020.2, 2017.9, 2020.0), (2020.0, 2020.1, 2018.3, 2018.5), (2018.5, 2018.6, 2016.5, 2016.7),
    (2016.7, 2019.7, 2016.6, 2019.5), (2019.5, 2020.2, 2019.4, 2020.0)]                                    # 50-54
AE6_PIVOTS = PREFIX_PIVOTS + [(6, "L", 27, 2006.8), (7, "H", 39, 2016.7), (8, "L", 45, 2009.8), (9, "H", 50, 2020.2),
                              (10, "L", 52, 2016.5)]
B = dict(setup_id=2, dir="L", variant=0, ob_time=t(25), ob_high=2011.1, ob_low=2006.8, idfvg_c1_time=t(25),
         idfvg_c3_time=t(35), idfvg_low=2011.1, idfvg_high=2013.9, identified_in_warmup=0,
         touch_msc=t(44) * 1000 + 20000, touch_bar_time=t(44), breaks=0, returns=0)
A = {**LONG, "touch_msc": t(25) * 1000 + 30000, "touch_bar_time": t(25)}


def ae6_common(sid):
    return [dict(setup_id=sid, kind="sc_hh", bar_time=t(49), price=2016.7, ref_id=7, ref_time=t(45)),
            dict(setup_id=sid, kind="fvg_fixed", bar_time=t(50), ref_time=t(48), lo=2016.2, hi=2017.9),
            dict(setup_id=sid, kind="hl", bar_time=t(53), price=2016.5, ref_id=10),
            dict(setup_id=sid, kind="reaction", bar_time=t(53))]


def ae6_entry(sid, sl):
    tp = round(2019.7 + 2 * (2019.7 - sl), 2)
    return [dict(setup_id=sid, kind="entry_attempt", tick_msc=t(54) * 1000, price=2019.7, detail="10009"),
            dict(setup_id=sid, kind="fill", tick_msc=t(54) * 1000, price=2019.7, lo=sl, hi=tp)], tp


def test_ae6_later_touch_wins_the_reaction_candle():
    touch_a = dict(setup_id=1, kind="touch", bar_time=t(25), tick_msc=t(25) * 1000 + 30000, price=2010.1)
    touch_b = dict(setup_id=2, kind="touch", bar_time=t(44), tick_msc=t(44) * 1000 + 20000, price=2011.1)
    fill_b, tp_b = ae6_entry(2, 2006.6)
    ev = ([touch_a] + ae6_common(1) + [dict(setup_id=1, kind="lost_competition", bar_time=t(53), ref_id=2)]
          + [touch_b] + ae6_common(2) + fill_b)
    filled = dict(reason="filled", sl_anchor="ob", buffer_pts=20, fill_msc=t(54) * 1000, fill_price=2019.7)
    ok = build(AE6_ROWS, AE6_PIVOTS, ev, [{**A, "reason": "run_end_waiting"},
                                         {**B, **filled, "sl": 2006.6, "sl_anchor_price": 2006.8, "tp": tp_b}])
    assert run_check(ok) == []
    occ = cf.occurrences(ok["setups"], ok["events"], ok["pivots"], ok["bars_m1"], ok["bars_m5"], PARAMS)
    assert occ["lost_competition"] == 1 and occ["competition"] == 1
    fill_a, tp_a = ae6_entry(1, 2004.6)
    wrong = ([touch_a] + ae6_common(1) + fill_a
             + [touch_b] + ae6_common(2) + [dict(setup_id=2, kind="lost_competition", bar_time=t(53), ref_id=1)])
    bad = build(AE6_ROWS, AE6_PIVOTS, wrong, [{**A, **filled, "sl": 2004.6, "sl_anchor_price": 2004.8, "tp": tp_a},
                                             {**B, "reason": "run_end_waiting"}])
    assert "competition_r16" in rules(run_check(bad))


# --- AE7 (R15, R18), short ------------------------------------------------------------------------------------
# The base mirrored (p -> 4020 - p) with bars 0-38 on Friday from 23:20; the reaction closes at 23:58 and the next
# bar is Monday 01:00, opening (Bid) at 2015.50 >= OB high 2015.20 + 20 points, so Ask 2015.70 >= SL 2015.60.
def mirror(rows):
    return [(round(K - o, 2), round(K - lo, 2), round(K - hi, 2), round(K - c, 2)) for o, hi, lo, c in rows]


AE7_ROWS = mirror(BASE_ROWS[:39]) + [(2015.5, 2015.6, 2006.8, 2007.0)]
AE7_TIMES = [T7 + 60 * i for i in range(39)] + [MONDAY]
AE7_PIVOTS = [(1, "L", 4, 2009.8), (2, "H", 9, 2015.2), (3, "L", 16, 2004.8), (4, "H", 17, 2007.7),
              (5, "L", 19, 2002.8), (6, "H", 26, 2012.2), (7, "L", 29, 2005.8), (8, "H", 31, 2009.7),
              (9, "L", 35, 2001.3), (10, "H", 37, 2005.9), (11, "L", 38, 2003.3)]
SHORT = dict(setup_id=1, dir="S", variant=0, ob_time=T7 + 300, ob_high=2015.2, ob_low=2009.9, idfvg_c1_time=T7 + 300,
             idfvg_c3_time=T7 + 900, idfvg_low=2009.1, idfvg_high=2009.9, identified_in_warmup=0,
             touch_msc=(T7 + 60 * 24) * 1000 + 40000, touch_bar_time=T7 + 60 * 24, breaks=0, returns=0)


def ae7(tail_events, **row):
    t7 = lambda i: T7 + 60 * i  # noqa: E731
    ev = [dict(setup_id=1, kind="touch", bar_time=t7(24), tick_msc=t7(24) * 1000 + 40000, price=2009.9),
          dict(setup_id=1, kind="sc_hh", bar_time=t7(33), price=2005.8, ref_id=7, ref_time=t7(31)),
          dict(setup_id=1, kind="fvg_fixed", bar_time=t7(34), ref_time=t7(32), lo=2005.6, hi=2007.3),
          dict(setup_id=1, kind="hl", bar_time=t7(38), price=2005.9, ref_id=10),
          dict(setup_id=1, kind="reaction", bar_time=t7(38))] + tail_events
    return build(AE7_ROWS, AE7_PIVOTS, ev, [{**SHORT, **row}], times=AE7_TIMES)


def test_ae7_short_with_stop_crossed_after_the_weekend_is_skipped_and_keeps_waiting():
    skip = dict(setup_id=1, kind="skipped_stop_crossed", tick_msc=MONDAY * 1000, price=2015.7, lo=2015.6)
    ok = ae7([skip], reason="run_end_waiting")
    assert run_check(ok) == []
    occ = cf.occurrences(ok["setups"], ok["events"], ok["pivots"], ok["bars_m1"], ok["bars_m5"], PARAMS)
    assert occ["short"] == 1 and occ["skipped_stop_crossed"] == 1 and occ["run_end_waiting"] == 1
    sent = [dict(setup_id=1, kind="entry_attempt", tick_msc=MONDAY * 1000, price=2015.5, detail="10009"),
            dict(setup_id=1, kind="fill", tick_msc=MONDAY * 1000, price=2015.5, lo=2015.6, hi=2015.3)]
    v = run_check(ae7(sent, reason="filled", sl=2015.6, sl_anchor="ob", sl_anchor_price=2015.2, buffer_pts=20,
                      tp=2015.3, fill_msc=MONDAY * 1000, fill_price=2015.5))
    assert "entry_r15" in rules(v)


# --- pivot causality (R9), tf_sync, touch claims (R5), TP (R19), SL anchor (R18) -------------------------------
def test_event_using_a_pivot_before_its_confirmation_is_flagged(base):
    ev = base_events(base)
    next(e for e in ev if e["kind"] == "hl")["bar_time"] = t(37)    # L37 is confirmed only when bar 38 closes
    assert "pivot_causality_r9" in rules(run_check(with_events(base, ev)))


def test_m5_bar_that_is_not_the_aggregate_of_its_m1_bars_is_flagged(base):
    m5 = base["bars_m5"].copy()
    m5.loc[2, "high"] = 2012.0
    v = run_check({**base, "bars_m5": m5})
    assert "tf_sync" in rules(v)


@pytest.mark.parametrize("bar", [23, 25])   # 23: low 2011.20 never reaches 2010.10; 25: bar 24 reached it first
def test_touch_claim_not_on_the_first_reaching_bar_is_flagged(base, bar):
    ev = base_events(base)
    ev[0].update(bar_time=t(bar), tick_msc=t(bar) * 1000 + 30000)
    assert "touch_r5" in rules(run_check(with_events(with_setup(base, touch_msc=t(bar) * 1000 + 30000,
                                                                touch_bar_time=t(bar)), ev)))


def test_tp_not_2r_from_the_fill_is_flagged(base):
    ev = base_events(base)
    ev[-1]["hi"] = 2050.0
    assert "tp_r19" in rules(run_check(with_events(with_setup(base, tp=2050.0), ev)))


def test_sl_anchor_not_the_r18_minimum_is_flagged(base):
    ev = base_events(base)
    ev[-1]["lo"] = 2013.9
    bad = with_events(with_setup(base, sl=2013.9, sl_anchor="hl", sl_anchor_price=2014.1, tp=2022.3), ev)
    ev[-1]["hi"] = 2022.3
    bad = with_events(bad, ev)
    assert "sl_r18" in rules(run_check(bad))


def test_short_fill_sl_uses_the_spread_logged_on_the_entry_attempt():
    """R18: a short stop adds the spread at entry. The entry_attempt event carries Bid (lo) and Ask (hi) of that
    tick, so the checker uses the exact spread rather than the bar's spread column."""
    t7 = lambda i: T7 + 60 * i  # noqa: E731
    rows = AE7_ROWS[:-1] + [(2010.0, 2010.2, 2009.5, 2009.8)]     # Monday opens below the stop: no skip
    ev = [dict(setup_id=1, kind="touch", bar_time=t7(24), tick_msc=t7(24) * 1000 + 40000, price=2009.9),
          dict(setup_id=1, kind="sc_hh", bar_time=t7(33), price=2005.8, ref_id=7, ref_time=t7(31)),
          dict(setup_id=1, kind="fvg_fixed", bar_time=t7(34), ref_time=t7(32), lo=2005.6, hi=2007.3),
          dict(setup_id=1, kind="hl", bar_time=t7(38), price=2005.9, ref_id=10),
          dict(setup_id=1, kind="reaction", bar_time=t7(38))]

    def run(ask):
        sent = [dict(setup_id=1, kind="entry_attempt", tick_msc=MONDAY * 1000, price=2010.0, lo=2010.0, hi=ask,
                     detail="10009"),
                dict(setup_id=1, kind="fill", tick_msc=MONDAY * 1000, price=2010.0, lo=2015.7, hi=1998.6)]
        setup = {**SHORT, "reason": "filled", "sl": 2015.7, "sl_anchor": "ob", "sl_anchor_price": 2015.2,
                 "buffer_pts": 20, "tp": 1998.6, "fill_msc": MONDAY * 1000, "fill_price": 2010.0}
        return run_check(build(rows, AE7_PIVOTS, ev + sent, [setup], times=AE7_TIMES))

    assert run(2010.3) == []                        # SL 2015.7 = anchor 2015.2 + 0.20 buffer + 0.30 spread
    assert "sl_r18" in rules(run(2010.2))           # logged spread 0.20 does not explain SL 2015.7
