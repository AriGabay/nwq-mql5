# Parameter table

| Input | Default | Candidate | Range tested | Note |
|---|---|---|---|---|
| `ObMode` | 0 | 1 | 0, 1 (categorical) | pre-registered selection on the final train window (KTD12) |
| `EntryMode` | 0 | 1 | 0, 1, 2, 3 (categorical) | pre-registered selection on the final train window (KTD12) |
| `ObMaxAgeBars` | 96 | 48 | 48, 96, 144 | pre-registered selection on the final train window (KTD12) |
| `FvgWindowBars` | 12 | 18 | 6, 12, 18 | pre-registered selection on the final train window (KTD12) |
| `OrderExpiryBars` | 12 | 6 | 6, 12, 18 | pre-registered selection on the final train window (KTD12) |
| `SignalTF` | M15 | M15 | M5, M15 (pilot) | frequency pilot rule (R20) |
| all other inputs | code defaults | code defaults | not optimized | fixed by KTD12 (risk 1%, 3 exposures, RR 2.0) |
