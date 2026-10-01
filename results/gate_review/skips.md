# Skip reasons with the numbers behind them (pilot M15)

Bid at placement = open of the bar after the confirmation candle 3 (the placement tick is that bar's first tick). Ask is estimated as Bid + that day's median spread. Balance at placement = deposit + net of deals closed before it. Exposures are reconstructed from the setup log.

### skipped_volume (7 in the pilot)

| setup | dir | entry | SL | stop distance | balance at placement | risk 1% USD | loss per 1.00 lot at SL | computed lots | broker min lots |
|---|---|---|---|---|---|---|---|---|---|
| 825 | L | 5551.11 | 5445.42 | 105.69 | 9026.50 | 90.26 | 10569.00 | 0.0085 | 0.01 |
| 835 | L | 5295.09 | 5105.21 | 189.88 | 9068.74 | 90.69 | 18988.00 | 0.0048 | 0.01 |
| 852 | L | 4827.58 | 4695.51 | 132.07 | 9448.61 | 94.49 | 13207.00 | 0.0072 | 0.01 |

### skipped_too_close (6 in the pilot)

| setup | dir | entry | placement tick (first bar after c3) | Bid at placement | median spread that day | est. Ask | distance to entry (points; <= 0 = already past) | stops level (points) | broker refusal |
|---|---|---|---|---|---|---|---|---|---|
| 702 | S | 4594.45 | 2026-01-13 20:15 | 4594.38 | 0.14 | 4594.52 | 7 | 20 | - |
| 1463 | S | 4814.28 | 2026-04-20 23:00 | 4814.26 | 0.24 | 4814.50 | 2 | 20 | - |
| 1747 | S | 4487.89 | 2026-05-27 09:45 | 4487.72 | 0.24 | 4487.96 | 17 | 20 | - |

### skipped_price_past (10 in the pilot)

| setup | dir | entry | placement tick (first bar after c3) | Bid at placement | median spread that day | est. Ask | distance to entry (points; <= 0 = already past) | stops level (points) | broker refusal |
|---|---|---|---|---|---|---|---|---|---|
| 1431 | S | 4793.07 | 2026-04-16 01:00 | 4794.47 | 0.24 | 4794.71 | -140 | 20 | - |
| 1991 | S | 4011.56 | 2026-07-01 01:00 | 4011.60 | 0.24 | 4011.84 | -4 | 20 | - |
| 2231 | S | 4103.50 | 2026-07-31 01:00 | 4104.24 | 0.24 | 4104.48 | -74 | 20 | - |
| 525 | S | 4342.06 | 2025-12-18 01:00 | 4337.50 | 0.07 | 4337.57 | 456 | 20 | 10018 Market closed |
| 616 | L | 4313.47 | 2026-01-02 01:00 | 4328.21 | 0.14 | 4328.35 | 1488 | 20 | 10018 Market closed |
| 1074 | S | 5331.25 | 2026-03-03 01:00 | 5315.36 | 0.20 | 5315.56 | 1589 | 20 | 10018 Market closed |

### skipped_cap (66 in the pilot)

| setup | dir | entry | time | pending orders | open positions | total vs cap 3 |
|---|---|---|---|---|---|---|
| 379 | L | 4241.89 | 2025-12-01 07:00 | 0 | 3 | 3 / 3 |
| 383 | L | 4235.20 | 2025-12-01 09:00 | 0 | 3 | 3 / 3 |
| 384 | L | 4244.74 | 2025-12-01 10:15 | 0 | 3 | 3 / 3 |