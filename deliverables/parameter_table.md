# Parameter table

| Input | Original (code default) | Candidate | Range tested | Reason |
|---|---|---|---|---|
| `PivL` | 3 | 5 | 2, 3, 4, 5 | highest neighbor-smoothed after-cost recovery factor on 2026.05.01-07.31 (R16/R17) |
| `PivR` | 3 | 3 | 2, 3, 4 | unchanged: selected value equals the default |
| `SweepToSetupBars` | 12 | 24 | 6, 12, 18, 24 | highest neighbor-smoothed after-cost recovery factor on 2026.05.01-07.31 (R16/R17) |
| `VolumeMultiplier` | 2.0 | 1.2 | 1.2, 1.5, 2.0, 2.5 | highest neighbor-smoothed after-cost recovery factor on 2026.05.01-07.31 (R16/R17) |
| `ConfirmationBars` | 6 | 6 | 3, 6, 9 | unchanged: selected value equals the default |
| `SignalTF` | M5 (v1.03 had no input; chart was M5) | M15 | M15 only | user decision (research on 15-minute bars) |
| all other inputs | code defaults | code defaults | not optimized | fixed by protocol (risk 1%, 3 positions, logic switches) |
