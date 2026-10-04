# Archive: OB-FVG retest with the R41 volume filter (2.0x), 2026-10-01 to 2026-10-02

This archive holds the OB-FVG retest EA as it stood with the R41 volume push, and all of that version's evidence.
The volume filter requires the identifying FVG's middle candle to have tick volume of at least 2.0x the 24 h average.

On 2026-10-02 the user approved a defined change for a new research: remove the volume filter from identification
and entry, and keep this version and its evidence separately (plan AMENDMENT D). Nothing here was deleted. Git tag
`archive/ob-fvg-volume-2x` marks the last commit before the change (a2dc5f8).

| Path | Contents |
|---|---|
| `mql5/Experts/` | `ob_fvg_retest.mq5` with R41 AMENDMENT C (24 h wall-clock window) and its research wrapper |
| `results/v2_volume_both_fvgs/` | pilot with the filter on both FVGs (0.25 fills/month on M15) |
| `results/v3_volume_96_bars/` | pilot with the filter on the identifying FVG, fixed 96/288-bar count (M15 0.63, M5 1.25), and its audit |
| `results/v4_volume_24h_window/` | pilot with the 24 h wall-clock window (M15 0.63, M5 0.88), its smoke and compile logs |
| `results/volume_audit_24h_window/` | volume audit of the 24 h window version |
| `runs/` | raw tester runs (local only, git-ignored: logs may contain the account id) |

SHA256 of the archived sources:
- `ob_fvg_retest.mq5`: f375835e4d21ad87d38a87c4887d1176e630f97fc7955d52a828146f0a4ab24c
- `ob_fvg_retest_research.mq5`: 367652c0c03c1ec24db499e6939f0e4e5cc7c183abe74158ad744b0e0c9e47e0

Every version of this filter left the research blocked by the R20 frequency rule: at most 1.25 fills per month
against the required 15. No profit or loss was used for any decision in these versions.
