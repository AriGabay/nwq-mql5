# R41 volume audit (2026-10-01): no optimization, no P&L

Data: pilot runs `pilot_m15` and `pilot_m5` (Model=4 real ticks, 2025.12.01-2026.07.31, EA sha256 61c6fa21...).
Comparison runs `audit_m1ohlc_m15/m5` use Model=1, where bar tick volume comes from the broker's M1 history.
Full numbers: `audit.json`. Code: `research/volume_audit.py` (independent recomputation from `rl_bars`).

## 1. Formula and window
EA (`VolumeRatio`): ratio = v[m] x N / (v[m-N] + ... + v[m-1]), where m is the identifying FVG's middle
candle and N = 24h x 60 / period minutes (96 on M15, 288 on M5).
- The bars are the signal timeframe's own closed bars (`CopyRates(SignalTF)`).
- The push candle m is not in the average.
- The ratio is evaluated when candle 3 (m+1) closes, so every bar used is closed: there is no future data.
- The 96/288 bars span a median of 25 wall-clock hours (the daily 1-hour break), and up to 101 hours across weekends.
- Logged EA ratio vs independent recomputation: max difference 0.00005 (4-decimal rounding). Compared over
  10 of 20 activated setups on M15 and 19 of 29 on M5; the rest have windows in warm-up bars that are not in `rl_bars`.

## 3. Ratio distributions
| | median | p90 | p95 | p99 | max | share >= 2x |
|---|---|---|---|---|---|---|
| M15 all bars | 0.987 | 1.377 | 1.504 | 1.735 | 2.564 | 0.27% |
| M15 identifying-FVG candidates | 1.031 | 1.437 | 1.555 | 1.818 | 2.564 | 0.48% |
| M5 all bars | 0.989 | 1.388 | 1.517 | 1.762 | 2.678 | 0.28% |
| M5 identifying-FVG candidates | 1.011 | 1.431 | 1.556 | 1.813 | 2.585 | 0.31% |

## 4. Coverage and source
- Real ticks: 24 whole days and 33,707 of 235,857 minute bars (14.3%) were generated, in Dec 2025 and Jan 2026.
- Bar tick volume under Model=4 is identical to the broker's M1-history bars (Model=1) for 100% of bars, on both
  timeframes. The volumes therefore come from the broker's history, not from tick replay, and generated ticks
  do not affect them.
- Volume is not constant: coefficient of variation 0.34, and the hour-of-day median ranges from 2,670 to 7,169 on M15
  (peak to trough 2.7x).
- It saturates. The maximum is 14.7 ticks/s on M5 and 14.6 ticks/s on M15, with the top bars clustered near that
  ceiling. Across the five widest-range ventiles of M5 bars, the median range grows 2.8x (7.9 to 21.8) while the
  median volume grows only 19% (1,978 to 2,362).

## 5. Funnel after the volume filter
| Stage | M15 with R41 | M15 without filter (v1) | M5 with R41 |
|---|---|---|---|
| identifying FVGs rejected by volume | 2,107 | 0 | 6,845 |
| activated | 20 | 2,093 | 29 |
| touched | 15 | 1,662 | 25 |
| confirmed | 6 | 665 | 14 |
| placed | 6 | 576 | 14 |
| filled | 5 | 456 | 10 |

The stages after activation keep similar pass rates with and without the filter (M15 touched 75% vs 79%,
filled/placed 83% vs 79%). The volume gate removes about 99% of identifying FVGs; it is the blocker.

## Conclusion
This is not a calculation error. The formula, window, timeframe, exclusion of the push bar and absence of future
data were verified, and the logged ratios match an independent recomputation. It is not a tester artifact either.
The cause is a data limitation that makes the condition genuinely rare at this broker. The XAUUSD.s tick volume is
a throttled quote count that saturates near 15 ticks per second, so active bars cannot rise far above the 24h
average: p99 is 1.74x, and only 0.27% of bars reach 2x.
