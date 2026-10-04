# Chart gate - 8 representative strategy charts

Pilot runs Dec 2025 - Jul 2026, variants A (HH/LL only) and B (HH+HL / LL+LH). Times are server time. The chart title also shows the example's net result after costs; it is descriptive and not a basis for any choice.

| # | file | variant | direction | setup | what it shows |
|---|---|---|---|---|---|
| 1 | 1_A_long_setup3836.png | A | long | #3836 | A long: OB touch, HH, new M1 FVG, reaction candle, entry, TP |
| 2 | 2_A_short_setup4830.png | A | short | #4830 | A short: break and return, LL, R16 losses on two reaction candles, entry on the third, SL |
| 3 | 3_B_long_setup4152.png | B | long | #4152 | B long: HH, HL confirmed, reaction, entry; stop from the OB |
| 4 | 4_B_short_setup3300.png | B | short | #3300 | B short: LL, LH confirmed, reaction, entry, TP |
| 5 | 5_A_long_setup4315.png | A | long | #4315 | Return and second break in the same M1 bar: cancellation (R8) |
| 6 | 6_B_short_setup5649.png | B | short | #5649 | Entry about 40 USD beyond the OB after four structure changes; wide stop |
| 7 | 7_A_long_setup1035.png | A | long | #1035 | Two OBs on one structure: #1035 and #1033 (OB about 16 USD lower), separate reaction candles |
| 8 | 8_B_long_setup4541.png | B | long | #4541 | Two overlapping OBs on one structure: #4541 and #4543, entries 4 minutes apart |

## Markings

| marking | how it looks |
|---|---|
| M5 OB zone | blue band (M5 box from the OB candle; on M1 the lines 'OB high' / 'OB low') |
| M5 identifying FVG | violet box on the M5 panel |
| touch | orange triangle: first tick that reached the OB (time in timeline) |
| break | red x 'break n': an M1 close beyond the OB far edge |
| return | blue ring 'return n': renewed touch after a break |
| second break (R8) | red x 'second break (R8)' and the red dotted line 'cancelled_second_break' |
| M1 structure change | black star 'HH' / 'LL' on the breaking bar and the dashed line 'ref high/low' = crossed pivot level; 'origin' = lowest low / highest high of the move; faint stars = earlier, superseded structure changes |
| HL / LH | black ring 'HL' / 'LH' on its pivot (variant B needs it; in A it only feeds the stop) |
| new M1 entry FVG | aqua box 'entry FVG' (faint aqua = earlier FVGs that lapsed or were replaced) |
| reaction candle | vertical aqua band 'reaction'; '(lost R16 to #n)' = another setup took that candle |
| entry | orange diamond 'fill <price>'; on the M5 panel a small orange diamond |
| SL / TP | red / green dashed lines with their price on the right |
| exit | black X; if the trade ran longer than the M1 window: 'exit ... on the M5 panel' |
| other OB on the same structure | violet hatched band '#n OB', violet diamond '#n fill', violet band at its reaction candle (shared_structure charts only) |
| pivots | small ring = pivot peak, small grey square = close of its confirmation bar (N = 3), dotted between |
| timeline | table under the panels: when each fact became known; violet numbers on the M1 panel = row |

Each chart: top panel M5 (OB candle to the exit or cancellation, grey = the M1 window); middle panel M1; bottom the timeline. The legend under the title repeats the markings. All 32 gate charts (16 per variant) and both setup tables are in the ZIP under all_charts/.
