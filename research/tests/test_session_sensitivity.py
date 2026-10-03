"""Pre-registered quote-only-minute sensitivity (gate review 2026-10-03): primary results untouched, affected
positions re-priced at the first trigger tick of the quote-only minute on the trigger side."""
import pandas as pd

from mt5r import session_sensitivity as ss

GAP = 1_777_856_400            # a 01:00 session-open bar


def ticks():
    return pd.DataFrame([(GAP * 1000 + 500, 4632.80, 4633.10), (GAP * 1000 + 2063, 4637.77, 4638.07),
                         (GAP * 1000 + 61000, 4633.00, 4633.30)], columns=["tick_msc", "bid", "ask"])


def test_trigger_uses_bid_for_buys_and_ask_for_sells():
    assert ss.trigger(1, 100.0, 110.0, 99.99, 100.30) == "sl"      # buy SL on Bid
    assert ss.trigger(1, 100.0, 110.0, 100.01, 99.0) is None
    assert ss.trigger(-1, 110.0, 100.0, 109.80, 110.00) == "sl"     # sell SL on Ask, Bid still below
    assert ss.trigger(-1, 110.0, 100.0, 99.80, 100.10) is None      # sell TP needs Ask <= TP
    assert ss.trigger(-1, 110.0, 100.0, 99.70, 100.00) == "tp"


def test_alt_exit_is_the_first_trigger_tick_inside_the_quote_only_minute():
    cases = pd.DataFrame([dict(case_id=1, setup_id=7, dir="S", sl=4635.36, tp=4566.87, gap_bar=GAP)])
    a = ss.alt_exits(cases, ticks()).iloc[0]
    assert a.alt_msc == GAP * 1000 + 2063 and a.alt_price == 4638.07 and a.alt_kind == "sl"
    late = pd.DataFrame([dict(case_id=2, setup_id=8, dir="S", sl=4640.0, tp=4566.87, gap_bar=GAP)])
    assert pd.isna(ss.alt_exits(late, ticks()).iloc[0].alt_msc)     # no trigger in the minute: not affected


def test_evaluate_keeps_primary_and_reprices_only_affected_positions():
    setups = pd.DataFrame([
        dict(setup_id=7, reason="filled", position_id=2, dir="S", volume=0.10, exit_msc=GAP * 1000 + 43_000_000,
             exit_price=4566.85, exit_kind="tp"),
        dict(setup_id=9, reason="filled", position_id=3, dir="L", volume=0.10, exit_msc=GAP * 1000 + 50_000_000,
             exit_price=4600.0, exit_kind="sl")])
    deals = pd.DataFrame([dict(type=2, position_id=0, profit=10000.0, commission=0, swap=0),
                          dict(type=1, position_id=2, profit=0.0, commission=0, swap=0),
                          dict(type=0, position_id=2, profit=685.1, commission=0, swap=0),
                          dict(type=0, position_id=3, profit=0.0, commission=0, swap=0),
                          dict(type=1, position_id=3, profit=-100.0, commission=0, swap=0)])
    alt = pd.DataFrame([dict(case_id=1, setup_id=7, alt_msc=GAP * 1000 + 2063, alt_price=4638.07, alt_kind="sl")])
    r = ss.evaluate(setups, deals, alt)
    assert r["affected"] == 1 and r["outcome_flips"] == 1 and r["net_primary"] == 585.1
    # short: (4638.07 - 4566.85) x -1 x 0.10 x 100 = -712.20 on top of the primary 685.10
    assert r["net_delta"] == -712.2 and r["net_sensitivity"] == round(585.1 - 712.2, 2)
    assert r["max_dd_closed_pct_sensitivity"] > r["max_dd_closed_pct_primary"]
