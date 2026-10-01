# Pilot setups for the R39 chart gate

Bar-time columns (OB, identifying FVG, activation, touch, confirmation FVG) show the bar open time in server time; for the two FVG columns this is candle 3. placement, fill and exit are tick times with milliseconds. Net is profit + commission + swap; realized R = net / (|intended entry - SL| x volume x contract size 100).

| setup | category | dir | OB | identifying FVG | activation | touch | confirmation FVG | placement | fill | exit | intended entry | fill price | SL | TP | planned RR | realized R | net after costs (USD) | reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 189 | long_loser | L | 2025-12-01 01:45 | 2025-12-01 02:30 | 2025-12-01 02:30 | 2025-12-01 03:00 | 2025-12-01 03:30 | 2025-12-01 03:45:00.000 | 2025-12-01 04:40:03.697 | 2025-12-01 07:22:28.906 | 4234.99 | 4234.99 | 4221.83 | 4261.31 | 2.00 | -1.00 | -92.12 | filled |
| 200 | short_winner | S | 2025-12-02 02:30 | 2025-12-02 03:15 | 2025-12-02 03:15 | 2025-12-02 04:45 | 2025-12-02 05:45 | 2025-12-02 06:00:00.000 | 2025-12-02 07:23:29.243 | 2025-12-02 08:26:30.000 | 4221.77 | 4221.78 | 4228.69 | 4207.93 | 2.00 | +2.00 | +194.04 | filled |

Chart stage labels: OB, identifying FVG, touch, confirmation FVG, placement, fill, entry, SL, TP, exit.

Missing categories: long_winner, long_filled_shortfall_1_of_3, short_loser, short_filled_shortfall_1_of_3, rejections.
