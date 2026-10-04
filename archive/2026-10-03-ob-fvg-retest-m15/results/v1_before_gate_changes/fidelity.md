# Fidelity record: OB-FVG retest EA (U5, plan R19/R26)

All runs use the isolated portable MT5 copy (`C:\mt5r`, build 6230), which the user logged in to. Only the Strategy Tester was used. Experts/AutoTrading and the built-in MCP were disabled, and the live terminal was closed during runs (the runner refuses otherwise).

## Build and environment

| Item | Result | Evidence |
|---|---|---|
| Compile, delivered and research builds | 0 errors, 0 warnings | `cli.py install` output (EA sha256 recorded per run in the experiment log) |
| Model=4, real ticks | PASS | `tester.ini` in every run folder |
| Stops level read by the EA | 20 points (tick 0.01) | journal facts: `stops level 20 pts` (plan R37; the EA reads it at every placement) |
| Warm-up | 3000 bars | journal facts |
| Real-tick coverage 2025.12.01-2026.07.31 | **LIMITATION**: 24 whole days, 33707 of 235857 minute bars (14.3%) used generated ticks, concentrated in Dec 2025 and Jan 2026 | `results/pilot/pilot_m15/journal_facts.json` |
| Delivered build = research build | PASS: 117 deals identical in time, type, direction, volume, price, commission, swap and profit (M15, Mar 2026) | `results/smoke/build_equivalence.json` |
| Categorical optimization | PASS: 8 passes, integer `ObMode`/`EntryMode` columns, clean grid merge | `results/smoke/optsmoke.json` |

## Rule conformance (independent re-check of every setup against the bars)

| Run | Setups | Violations |
|---|---|---|
| smoke M15, Mar 2026 | 482 | 0 |
| smoke M5, Mar 2026 | 1015 | 0 |
| smoke M15 FVG+BOS mode, Dec 2025-Jul 2026 | 1044 | 0 |
| pilot M15, Dec 2025-Jul 2026 | 2093 | 0 |
| pilot M5, Dec 2025-Jul 2026 | 6281 | 0 |

The first M15 smoke check reported 27 violations. All of them were a checker defect: the checker required order fields on `run_end_pending` rows that ended before confirmation. It was fixed with a regression test. The EA was unchanged.

## Behavioural cases exercised (pilot M15 + FVG+BOS smoke M15)

| Case | Count | Status |
|---|---|---|
| Confirmation candle 1 = touch bar (AE1 boundary) | 91 | exercised |
| OB = identifying FVG candle 1 (R5) | 1038 | exercised |
| Touch ignored on the activation bar (KTD2) | 365 | exercised |
| Touch bar closes beyond the OB → invalidated (KTD2) | 189 | exercised |
| Duplicate OB candle deduped (R5) | 252 | exercised |
| Cap skip (AE4) | 66 | exercised |
| Stops-level too-close skip (AE8) | 6 | exercised |
| Volume skip (R14) | 7 | exercised |
| Fill, then the same bar closes beyond the OB, trade kept (AE6, R38) | 10 | exercised |
| FVG+BOS mode setups (R6, AE7 checked by the checker) | 1044 | exercised |
| Long / short | 1078 / 1015 | exercised |
| Margin skip (R14) | 0 | **not exercised** |
| Duplicate-price order skip (R16) | 0 | **not exercised** |
| Late fill on the cancellation tick (R38) | 0 | **not exercised** |
| Pending cancel on a gap close beyond the OB (AE3, gap-only) | 0 | **not exercised** |
| Doji skipped in the OB search (R5) | 0 | **not exercised** |
| Stops level 0 → order placed (AE8 second branch) | — | **not testable on XAUUSD.s** (stops level fixed at 20 points) |

The "not exercised" branches are covered only by code review and static tests, not by tester evidence.
