# Pilot setups for the R39 chart gate

Bar-time columns (OB, identifying FVG, activation, touch, confirmation FVG) show the bar open time in server time; for the two FVG columns this is candle 3. placement, fill and exit are tick times with milliseconds. Net is profit + commission + swap; realized R = net / (|intended entry - SL| x volume x contract size 100).

| setup | category | dir | OB | identifying FVG | activation | touch | confirmation FVG | placement | fill | exit | intended entry | fill price | SL | TP | planned RR | realized R | net after costs (USD) | reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 386 | long_loser | L | 2026-03-04 03:15 | 2026-03-04 04:00 | 2026-03-04 04:00 | 2026-03-04 04:30 | 2026-03-04 06:00 | 2026-03-04 06:15:00.043 | 2026-03-04 06:24:09.946 | 2026-03-04 08:01:26.642 | 5164.42 | 5164.33 | 5146.68 | 5199.90 | 2.00 | -1.00 | -88.90 | filled |
| 383 | short_loser | S | 2026-03-03 20:45 | 2026-03-03 21:15 | 2026-03-03 21:15 | 2026-03-03 21:45 | 2026-03-03 22:45 | 2026-03-03 23:00:00.072 | 2026-03-04 01:16:53.420 | 2026-03-04 02:47:44.002 | 5127.12 | 5127.15 | 5138.27 | 5104.82 | 2.00 | -1.03 | -92.16 | filled |

Chart stage labels: OB, identifying FVG, touch, confirmation FVG, placement, fill, entry, SL, TP, exit.

Missing categories: long_winner, long_filled_shortfall_1_of_3, short_winner, short_filled_shortfall_1_of_3, rejections.
