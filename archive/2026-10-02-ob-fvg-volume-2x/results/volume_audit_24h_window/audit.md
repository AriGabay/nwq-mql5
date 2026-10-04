# R41 volume audit, AMENDMENT C (2026-10-02): no optimization, no P&L

Data: pilot runs `pilot_m15` and `pilot_m5` with the corrected EA (Model=4 real ticks, 2025.12.01-2026.07.31,
EA sha256 f375835e...). Source comparison: `audit_m1ohlc_m15/m5` (Model=1, bar tick volume from the broker's M1
history; bar volumes do not depend on the EA rule, so these runs from 2026-10-01 are reused).
Full numbers: `audit.json`. Code: `research/volume_audit.py`. The previous audit (fixed count of 96/288 bars) is
archived in `results/v3_volume_96_bars/volume_audit/`.

## 1. Formula and window (corrected)
`ratio = v[m] / mean(v[j..m-1])`, where m is the identifying FVG's middle candle (the push bar) and j..m-1 are
the signal-timeframe bars that **open** in the wall-clock window `[open(m) - 24 h, open(m))`.
- The push bar is not in the average.
- No bar from before the window is used to make up a count, and closed hours add no zero bars: MT5 has no bars
  for them, and none are invented.
- A history that starts after the window start is counted as `idfvg_volume_no_history`. A window with no bars is
  `idfvg_volume_empty_window`. Neither one qualifies, and both are kept separate from `idfvg_rejected_volume`.
- The ratio is evaluated when candle 3 closes, so every bar used is closed: there is no future data.
- Bars in a 24 h window: normally 92 on M15 and 276 on M5 (24 h minus the 00:00-01:00 daily break). After a
  weekend or holiday the window holds only the first bars of the new session: as few as 1 bar (M15) or 1-2 bars (M5).

## 2. Verification of windows across the daily break and the weekend
- **Unit tests** (`research/tests/test_volume_window.py`, 7 tests) build the broker schedule synthetically. They cover:
  - a window across the daily break (92 bars; the bar just before the window and the push bar are excluded, and
    the bar opening exactly at the window start is included);
  - a Monday 03:00 window (8 bars; Friday's bars are not used);
  - the first bar of the week (empty window);
  - a history shortage, and a missing bar inside the window (the mean uses the 91 bars that exist);
  - zero volume.
  
  A further test pins the audit's vectorised recomputation to the checker's per-bar one.
- **The EA's own source code** is pinned by `test_ea_static.py::test_volume_average_uses_a_wall_clock_window`.
- **On real data**, ratios logged by the EA (identifying and confirmation FVG) were compared with this independent
  recomputation:

| | compared | across the daily break | across a weekend | across a holiday close | max abs diff |
|---|---|---|---|---|---|
| M15 | 20 | 10 | 6 | 4 | 0.00005 |
| M5 | 32 | 21 | 7 | 5 | 0.00005 |

  The difference is the EA's 4-decimal rounding. The conformance checker, which also recomputes the time
  window, found 0 violations in both pilot runs and in both smoke runs.

## 3. History gaps (listed separately)
Gaps between consecutive bars, other than the daily break (132) and the weekends (37):

| TF | gap | length | reading |
|---|---|---|---|
| M15, M5 | 2026-01-19 21:30 to 01-20 01:00 | 3.5 h | early close, US holiday (MLK Day) |
| M15, M5 | 2026-02-16 21:30 to 02-17 01:00 | 3.5 h | early close, US holiday (Presidents' Day) |
| M15, M5 | 2026-05-25 21:30 to 05-26 01:00 | 3.5 h | early close, US holiday (Memorial Day) |
| M5 only | 2026-01-02 03:20 to 03:25 | 5 min | one missing M5 bar (the M15 bar covering it exists) |

The holiday closes are market closures and add no bars, as required. The single missing M5 bar is the only
gap that looks like missing history. `idfvg_volume_no_history` = 0 on both timeframes, because the EA's
3,000-bar warm-up always covers the 24 h window. In `rl_bars`, only the very first logged bar cannot be
recomputed, because warm-up bars are not logged.

## 4. Ratio distributions (time window)
| | median | p90 | p95 | p99 | max | share >= 2x |
|---|---|---|---|---|---|---|
| M15 all bars | 0.990 | 1.389 | 1.521 | 1.757 | 3.061 | 0.33% |
| M15 identifying-FVG candidates | 1.037 | 1.442 | 1.569 | 1.843 | 3.061 | 0.66% |
| M5 all bars | 0.992 | 1.397 | 1.532 | 1.788 | 9.710 | 0.32% |
| M5 identifying-FVG candidates | 1.015 | 1.441 | 1.573 | 1.832 | 2.609 | 0.34% |

- **Short windows after a closure drive the extremes.** The M5 maximum, 9.71, is the bar of 2025-12-26 01:05,
  after the Christmas closure, with a 1-bar window.
- **Bars at 2x or more that came from a window with fewer than half the normal bars:** 12 of 151 on M5, 6 of 52 on M15.
- **Activated setups:** 2 of the 21 recomputable M5 setups qualified through such a short window (minimum 24 bars).
  On M15 there were none (minimum 57 bars).

Ten worked examples per timeframe (5 pass, 5 fail), plus three across a weekend, are in `audit.json` under
`examples`. Each gives the push bar, its volume, the window start, the first and last bar in the window, the
bar count, the average and the ratio.

## 5. Coverage and source (unchanged from 2026-10-01)
- **Real ticks:** 24 whole days, which is 33,707 of 235,857 minute bars (14.3%), were generated, in Dec 2025 and Jan 2026.
- **Bar tick volume under Model=4 is identical to the broker's M1-history bars (Model=1)** for 100% of bars on both
  timeframes. So generated ticks do not change bar volumes.
- **Volume is not constant:** the coefficient of variation is 0.34 on M15 and 0.35 on M5, and the hour-of-day
  median ranges 2.7x.

## 6. The "tick ceiling": a suspicion, not a fact
How the figure is computed: `rate = bar tick_volume / bar length in seconds`. This is an average over the bar,
not a count of ticks in any single second.
- **M15:** maximum 13,135 ticks / 900 s = 14.59 per s (2026-06-17 21:30).
- **M5:** maximum 4,422 ticks / 300 s = 14.74 per s (2026-06-17 21:05).

What the data shows:
- **The top bars come from a few evenings.** Of the ten highest bars, 7 on M15 and 8 on M5 fall on the evening of
  2026-06-17; the rest are on 2026-07-29 and 2026-07-02.
- **There is no pile-up at one value.** Exactly one bar sits at the maximum. Only 4 M15 bars (15 M5 bars) lie
  within 5% of it.
- **The maximum rate is almost the same on M5 and M15.** This fits a ceiling, but it also fits one evening that was
  busy for its whole length.
- **Volume grows much less than range** (M5, top five range ventiles): the median range grows 2.8x (7.9 to 21.8),
  while the median volume grows only 19% (1,978 to 2,362). This points to a feed that rises slowly with activity,
  not to a proven cap.

Conclusion: a hard limit near 15 ticks per second is **suspected, not proven**. Bar volumes alone cannot prove it.
Proof would need real tick timestamps, such as the per-second tick counts in the peak minutes of 2026-06-17,
read with `CopyTicksRange`. That check has not been run. The statement in the 2026-10-01 audit that "volume
saturates at about 15 ticks per second" is withdrawn as a fact and kept as a hypothesis.

## 7. Funnel: fixed bar count (v3) vs time window (now)
| Stage | M15 v3 | M15 now | M5 v3 | M5 now |
|---|---|---|---|---|
| identifying FVGs rejected by volume | 2,107 | 2,091 | 6,845 | 6,826 |
| not judged: empty window | - | 14 | - | 24 |
| not judged: no history | - | 0 | - | 0 |
| activated | 20 | 25 | 29 | 28 |
| touched | 15 | 18 | 25 | 20 |
| confirmed | 6 | 6 | 14 | 11 |
| placed | 6 | 6 | 14 | 11 |
| filled | 5 | 5 | 10 | 7 |
| fills per month | 0.63 | 0.63 | 1.25 | 0.88 |

## Conclusion
The formula and the corrected window are verified: there is no calculation error. The window is a real 24-hour
window across the break, weekends and holidays, the push bar is excluded, and no future data is used. The logged
ratios match an independent recomputation to rounding.

With the time window, the 2x condition remains rare. It holds on 0.33% of all M15 bars and 0.66% of identifying
candidates.

Both timeframes stay far below the 15 fills-per-month threshold. Whether the cause is a capped data feed is
unproven (section 6). The research stays blocked. No multiplier, threshold or trading rule was changed.
