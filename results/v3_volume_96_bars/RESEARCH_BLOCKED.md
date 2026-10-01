# Research blocked: not enough trades (R20 frequency rule)

Rules: plan R4-R18, R36-R38, R41 (volume push >= 2.0 x average of the previous 24h of bars on the
identifying FVG's middle candle only), R42. Default inputs, 2025.12.01-2026.07.31, Model=4 real ticks,
EA sha256 61c6fa21c19070250492437c813932cc9073e920b9055d5ed018d5462da2e042, 0 conformance violations.

| TF | setups activated | confirmed | placed | filled | fills per month | identifying FVGs rejected by volume |
|---|---|---|---|---|---|---|
| M15 | 20 | 6 | 6 | 5 | 0.63 | 2107 |
| M5 | 29 | 14 | 14 | 10 | 1.25 | 6845 |

Pre-registered rule (R20): research timeframe = the longer TF averaging >= 15 fills per month; neither does.
Per the user's instruction (2026-10-01), the research stops before pre-registration and optimization.
Changing the volume rule is a separate decision for the user. Only fill counts were used here.

History of this gate: `results/v1_before_gate_changes/` (no volume filter: 57.1 fills/month on M15),
`results/v2_volume_both_fvgs/` (filter on both FVGs: 0.25 fills/month on M15).
