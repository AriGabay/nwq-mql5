"""R41 lookback as a wall-clock window (AMENDMENT C, 2026-10-02).

The average is the mean tick volume of the signal-timeframe bars that OPEN in [open(m) - 24 h, open(m)), m being the
push bar (the identifying FVG's middle candle): the push bar is excluded, no bar from before the window is pulled in
to make up a quota, and closed-market hours contribute no bars (none exist, none are invented). A loaded history that
starts after the window start is reported as `no_history`, a window without bars as `empty`; neither is a ratio.

Synthetic M15 schedule as at the broker (server time): daily break 00:00-01:00, weekend Sat 00:00 - Mon 01:00.
"""
import numpy as np
import pandas as pd
import pytest

import volume_audit as va
from mt5r import conformance as cf

P, DAY = 900, 86400
L24 = 24 * 3600
MON = int(pd.Timestamp("2026-03-02").timestamp())   # a Monday, 00:00


def schedule(first_day=MON - 4 * DAY, days=8):
    """Bar open times Thu .. Thu on the broker schedule (no bars 00:00-01:00, none Sat/Sun)."""
    out = []
    for d in range(days):
        day = first_day + d * DAY
        if pd.Timestamp(day, unit="s").dayofweek >= 5:
            continue
        out += list(range(day + 3600, day + DAY, P))
    return np.array(out, dtype=np.int64)


def at(t, ts):
    return int(np.searchsorted(t, int(pd.Timestamp(ts).timestamp())))


@pytest.fixture
def bars():
    t = schedule()
    v = np.full(len(t), 100.0)
    return t, v


def test_window_across_the_daily_break_counts_only_existing_bars(bars):
    t, v = bars
    m = at(t, "2026-03-04 12:00")                       # Wednesday
    start = at(t, "2026-03-03 12:00")                   # Tuesday 12:00, first bar of the window (included)
    v[start - 1] = 1e6                                   # Tuesday 11:45: just before the window, must not count
    v[start] = 196.0                                     # the bar opening exactly at the window start counts
    v[m] = 500.0                                         # the push bar itself is excluded from the average
    w = cf.vol_window(t, v, m, L24)
    assert w["status"] == "ok"
    assert w["n"] == 48 + 44                             # Tue 12:00-23:45 and Wed 01:00-11:45; the break adds none
    assert w["average"] == pytest.approx((91 * 100 + 196) / 92)
    assert w["ratio"] == pytest.approx(500 / w["average"])
    assert w["start"] == t[m] - L24


def test_window_across_the_weekend_does_not_reach_back_to_friday(bars):
    t, v = bars
    v[t < MON] = 1e6                                     # every bar up to Friday: outside a Monday-morning window
    m = at(t, "2026-03-02 03:00")
    v[m] = 300.0
    w = cf.vol_window(t, v, m, L24)
    assert w["status"] == "ok" and w["n"] == 8           # Mon 01:00-02:45 only, no quota filled from Friday
    assert w["average"] == pytest.approx(100.0) and w["ratio"] == pytest.approx(3.0)


def test_first_bar_after_the_weekend_has_an_empty_window(bars):
    t, v = bars
    m = at(t, "2026-03-02 01:00")
    w = cf.vol_window(t, v, m, L24)
    assert w["status"] == "empty" and w["n"] == 0 and w["ratio"] is None


def test_history_starting_after_the_window_start_is_a_history_shortage(bars):
    t, v = bars
    m = at(t, "2026-02-26 12:00")                       # Thursday, the first loaded day
    w = cf.vol_window(t, v, m, L24)
    assert w["status"] == "no_history" and w["ratio"] is None
    # a missing bar inside a covered window is not invented: the mean uses the bars that exist
    t2 = np.delete(t, at(t, "2026-03-04 06:00"))
    m2 = at(t2, "2026-03-04 12:00")
    assert cf.vol_window(t2, np.full(len(t2), 100.0), m2, L24)["n"] == 91


def test_zero_reference_volume_is_empty(bars):
    t, v = bars
    m = at(t, "2026-03-04 12:00")
    v[:m] = 0.0
    assert cf.vol_window(t, v, m, L24)["status"] == "empty"


def test_audit_recomputation_agrees_with_the_checker(bars):
    t, v = bars
    v = np.random.default_rng(1).integers(50, 400, len(t)).astype(float)
    b = pd.DataFrame({"time": t, "tick_volume": v})
    r, avg, n, status = va.ratios(b, L24)
    for m in range(len(t)):
        w = cf.vol_window(t, v, m, L24)
        assert status[m] == w["status"] and n[m] == w["n"]
        if w["status"] == "ok":
            assert r[m] == pytest.approx(w["ratio"]) and avg[m] == pytest.approx(w["average"])
        else:
            assert np.isnan(r[m])


def test_checker_recomputes_logged_ratio_over_a_weekend_window(bars):
    t, v = bars
    m = at(t, "2026-03-02 03:00")
    v[m] = 300.0
    df = pd.DataFrame({"time": t, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "tick_volume": v})
    B = cf._Bars(df, P)
    assert B.vol_ratio(m, cf.vol_lookback_seconds(24)) == pytest.approx(3.0)
    d = cf.fvg_volume(df, int(t[m - 1]), P, 24)
    assert d["n"] == 8 and d["ratio"] == pytest.approx(3.0) and d["window_start"] == int(t[m]) - L24
