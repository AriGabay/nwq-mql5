# R41 volume filter: frequency feasibility (no P&L used)

Pilot with the R41 filter as decided (middle candle >= 2.0 x average of the previous 24h of bars, on both
FVGs), 2025.12.01-2026.07.31, default inputs:

| TF | setups activated | fills | fills per month | identifying FVGs rejected by volume | confirmation FVGs rejected by volume |
|---|---|---|---|---|---|
| M15 | 23 | 2 | 0.25 | 2107 | 10 |
| M5 | 33 | 1 | 0.13 | 6845 | 17 |

Before R41 the same rules gave 456 fills on M15 (57.1 per month) and 1536 on M5 (192.4 per month).

XAUUSD.s tick volume at this broker is smooth (M15 hourly medians 2.7k-7.2k ticks per bar), so a single bar
rarely reaches 2x the 24h average: 0.3% of all M15 bars do. Share of FVG middle candles that pass, by definition
(each FVG is tested separately; requiring both FVGs multiplies the rarity):

| TF | average over | k = 1.3 | k = 1.5 | k = 2.0 |
|---|---|---|---|---|
| M15 | previous 20 bars | 22.6% | 10.8% | 1.2% |
| M15 | previous 4h | 23.0% | 11.3% | 1.6% |
| M15 | previous 24h | 20.4% | 7.6% | 0.4% |
| M5 | previous 20 bars | 15.5% | 7.0% | 1.1% |
| M5 | previous 4h | 20.4% | 9.7% | 1.3% |
| M5 | previous 24h | 19.2% | 7.8% | 0.4% |

Source: `runs/pilot_m15`, `runs/pilot_m5` `rl_bars` tick volume; computation in this session, frequency only.
