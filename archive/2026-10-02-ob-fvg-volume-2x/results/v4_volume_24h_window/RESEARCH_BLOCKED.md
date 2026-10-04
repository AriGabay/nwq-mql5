# Research blocked: not enough trades (R20 frequency rule)

Rules: plan R4-R18, R36-R38, R42, and R41 with AMENDMENT C (2026-10-02): a volume push >= 2.0 x the average
tick volume of the bars opening in the 24 wall-clock hours before the push bar, on the identifying FVG's middle
candle only. Default inputs, 2025.12.01-2026.07.31, Model=4 real ticks, EA sha256
f375835e4d21ad87d38a87c4887d1176e630f97fc7955d52a828146f0a4ab24c, 0 conformance violations. One pilot run per
timeframe, no optimization. Only fill counts were used: no profit or loss was looked at.

| TF | setups activated | confirmed | placed | filled | fills per month | idFVGs rejected by volume | idFVGs with an empty window |
|---|---|---|---|---|---|---|---|
| M15 | 25 | 6 | 6 | 5 | 0.63 | 2091 | 14 |
| M5 | 28 | 11 | 11 | 7 | 0.88 | 6826 | 24 |

No identifying FVG lacked history (`idfvg_volume_no_history` = 0).

Pre-registered rule (R20): the research timeframe is the longer timeframe averaging >= 15 fills per month; neither
timeframe does. Following the user's instruction, the research stays blocked before pre-registration and
optimization. No multiplier, frequency threshold or trading rule was changed. `pilot_summary.json` names M5 only
as the formal fallback of the rule; with 0.88 fills per month, the research does not continue on it.

History of this gate:
- `results/v1_before_gate_changes/`: no volume filter, 57.1 fills/month on M15.
- `results/v2_volume_both_fvgs/`: filter on both FVGs, 0.25 fills/month on M15.
- `results/v3_volume_96_bars/`: filter on the identifying FVG with a fixed count of 96/288 bars, 0.63 fills/month on
  M15 and 1.25 on M5.

Volume audit for this version: `results/volume_audit/audit.md`.
