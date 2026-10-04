# Pilot setups for the R39 chart gate

Bar-time columns (OB, identifying FVG, activation, touch, confirmation FVG) show the bar open time in server time; for the two FVG columns this is candle 3. placement, fill and exit are tick times with milliseconds. Net is profit + commission + swap; realized R = net / (|intended entry - SL| x volume x contract size 100). idFVG / cFVG vol ratio = tick volume of the FVG's middle candle / mean tick volume of the bars opening in the 24 wall-clock hours before it, as logged by the EA (recomputed from the bars with 24 h when not logged). There is no volume filter (AMENDMENT D): both ratios are informational.

| setup | category | dir | OB | identifying FVG | activation | touch | confirmation FVG | idFVG vol ratio | cFVG vol ratio (info) | placement | fill | exit | intended entry | fill price | SL | TP | planned RR | realized R | net after costs (USD) | reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1424 | long_winner | L | 2026-04-14 20:15 | 2026-04-14 20:45 | 2026-04-14 20:45 | 2026-04-14 21:45 | 2026-04-14 22:15 | 1.18 | 0.90 | 2026-04-14 22:30:00.088 | 2026-04-15 01:01:44.990 | 2026-04-15 04:16:22.620 | 4838.56 | 4838.22 | 4824.66 | 4866.36 | 2.00 | +2.03 | +141.05 | filled |
| 1398 | long_loser | L | 2026-04-10 14:00 | 2026-04-10 14:45 | 2026-04-10 14:45 | 2026-04-10 18:30 | 2026-04-10 21:30 | 0.81 | 1.11 | 2026-04-10 21:45:00.072 | 2026-04-10 21:46:27.987 | 2026-04-10 23:04:59.897 | 4765.97 | 4765.89 | 4753.21 | 4791.49 | 2.00 | -1.01 | -77.58 | filled |
| 1161 | long_winner | L | 2026-03-12 02:45 | 2026-03-12 03:30 | 2026-03-12 03:30 | 2026-03-12 05:45 | 2026-03-12 08:45 | 1.09 | 1.07 | 2026-03-12 09:00:00.009 | 2026-03-12 09:17:11.419 | 2026-03-12 11:01:24.508 | 5151.65 | 5151.61 | 5132.95 | 5189.05 | 2.00 | +2.03 | +151.84 | filled |
| 1151 | short_winner | S | 2026-03-11 09:00 | 2026-03-11 09:45 | 2026-03-11 09:45 | 2026-03-11 10:00 | 2026-03-11 11:30 | 0.92 | 0.95 | 2026-03-11 11:45:00.061 | 2026-03-11 11:52:52.587 | 2026-03-12 01:56:26.844 | 5187.54 | 5187.55 | 5210.46 | 5141.70 | 2.00 | +2.33 | +160.18 | filled |
| 1536 | short_loser | S | 2026-04-29 03:30 | 2026-04-29 04:15 | 2026-04-29 04:15 | 2026-04-29 05:00 | 2026-04-29 06:45 | 1.41 | 0.61 | 2026-04-29 07:00:00.041 | 2026-04-29 07:00:55.370 | 2026-04-29 07:36:05.155 | 4596.53 | 4596.59 | 4604.97 | 4579.65 | 2.00 | -1.00 | -75.87 | filled |
| 2220 | short_winner | S | 2026-07-29 21:45 | 2026-07-29 22:30 | 2026-07-29 22:30 | 2026-07-30 01:45 | 2026-07-30 03:00 | 1.82 | 1.24 | 2026-07-30 03:15:00.034 | 2026-07-30 04:04:31.851 | 2026-07-30 08:18:33.976 | 4086.61 | 4086.64 | 4109.86 | 4040.11 | 2.00 | +2.00 | +139.80 | filled |
| 886 | rejected_cancelled_no_fvg | S | 2026-02-05 04:15 | 2026-02-05 05:00 | 2026-02-05 05:00 | 2026-02-05 07:45 | - | 1.37 | - | - | - | - | - | - | - | - | - | - | - | cancelled_no_fvg |
| 652 | rejected_expired_unfilled | S | 2026-01-07 03:15 | 2026-01-07 03:45 | 2026-01-07 03:45 | 2026-01-07 04:00 | 2026-01-07 06:15 | 1.78 | 1.11 | 2026-01-07 06:30:00.097 | - | - | 4472.08 | - | 4478.85 | 4458.54 | 2.00 | - | - | expired_unfilled |
| 1468 | rejected_expired_untouched | S | 2026-04-21 05:45 | 2026-04-21 06:15 | 2026-04-21 06:15 | - | - | 0.97 | - | - | - | - | - | - | - | - | - | - | - | expired_untouched |
| 1077 | rejected_invalidated_active | L | 2026-03-03 02:45 | 2026-03-03 03:15 | 2026-03-03 03:15 | - | - | 1.15 | - | - | - | - | - | - | - | - | - | - | - | invalidated_active |
| 1127 | rejected_invalidated_touched | S | 2026-03-09 02:45 | 2026-03-09 03:15 | 2026-03-09 03:15 | 2026-03-09 03:30 | - | 1.08 | - | - | - | - | - | - | - | - | - | - | - | invalidated_touched |
| 2244 | rejected_run_end_pending | S | 2026-07-31 22:30 | 2026-07-31 23:15 | 2026-07-31 23:15 | 2026-07-31 23:30 | - | 0.87 | - | - | - | - | - | - | - | - | - | - | - | run_end_pending |
| 1634 | rejected_skipped_cap | L | 2026-05-11 19:30 | 2026-05-11 20:15 | 2026-05-11 20:15 | 2026-05-11 20:30 | 2026-05-11 23:00 | 1.06 | 0.94 | - | - | - | 4729.99 | - | 4707.92 | 4774.13 | 2.00 | - | - | skipped_cap |
| 1431 | rejected_skipped_price_past | S | 2026-04-15 20:15 | 2026-04-15 20:45 | 2026-04-15 20:45 | 2026-04-15 21:00 | 2026-04-15 23:45 | 1.16 | 0.42 | - | - | - | 4793.07 | - | 4808.85 | 4761.51 | 2.00 | - | - | skipped_price_past |
| 702 | rejected_skipped_too_close | S | 2026-01-13 17:45 | 2026-01-13 18:15 | 2026-01-13 18:15 | 2026-01-13 18:45 | 2026-01-13 20:00 | 1.34 | 1.40 | - | - | - | 4594.45 | - | 4620.81 | 4541.73 | 2.00 | - | - | skipped_too_close |
| 1367 | rejected_skipped_volume | L | 2026-04-07 23:45 | 2026-04-08 01:15 | 2026-04-08 01:15 | 2026-04-08 01:30 | 2026-04-08 02:00 | 1.16 | 1.34 | - | - | - | 4801.61 | - | 4706.18 | 4992.47 | 2.00 | - | - | skipped_volume |

Chart stage labels: OB, identifying FVG, touch, confirmation FVG, placement, fill, entry, SL, TP, exit.

Missing categories: none.
