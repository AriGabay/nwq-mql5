# Archive: `new_test` (SweepOB) research, 2026-09-30

The previous strategy and all of its evidence, moved here intact when the OB-FVG retest EA replaced it
(plan `docs/plans/2026-09-30-2310-feat-ob-fvg-retest-ea-plan.md`, R1 and KTD10). Nothing was deleted.
Git tag `archive/new-test-sweepob` marks the last commit before the move.

| Path | Contents |
|---|---|
| `original/` | byte copies of `new_test.mq5` v1.03 and the saved `.set`, with `SHA256SUMS` |
| `mql5/Experts/` | `new_test.mq5` v1.04 and its research-logging wrapper |
| `results/` | MT5 reports, optimization XML, run evidence, `experiment_log.jsonl`, `acceptance.json`, `fidelity.md`, `independence_check.md` |
| `deliverables/` | `.set` files, tables, charts and the Hebrew report of that research |
| `docs/plans/` | the research plan and protocol |
| `research/` | snapshot of the old-EA-specific pipeline code, CLI, pre-registration and tests |
| `runs/` | raw tester run folders (local only, git-ignored: logs contain the account id) |

## Exposure of 2026.08.01-2026.09.29 (cited by the new plan's R24)

This period is **not independent** for any strategy researched in this repo:

- The previous research ran its "non-independent check period" 2026.08.01-2026.09.29 with `new_test`
  (`results/check_period/`, net -109.65 USD on 3 trades).
- The live terminal's tester ran `new_test` on XAUUSD.s M5 over 2026.09.01-2026.09.28 on 2026-09-30 at
  17:24 (`results/independence_check.md`).
- The whole real-tick history (from 2025.11.24) was used for development of the previous strategy.

The OB-FVG retest research therefore labels 2026.08.01-2026.09.29 a frozen, run-once, non-independent
holdout, and issues no `recommended` `.set`.
