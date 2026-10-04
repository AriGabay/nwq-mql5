# Archive: OB-FVG retest without the volume filter (M15), 2026-10-02

This archive holds the OB-FVG retest research run without the R41 volume filter (AMENDMENT D) and all of its evidence. It was moved here intact on 2026-10-03, when the M5 OB + M1 structure EA replaced it (plan `docs/plans/2026-10-03-0013-feat-m5-ob-m1-structure-ea-plan.md`, R1). Nothing was deleted. Git tag `archive/ob-fvg-retest-m15` marks the last commit before the move.

The study closed as failed: no out-of-sample improvement over the baseline. Over March–July 2026 the procedure lost 1,502.04 USD and the baseline lost 1,241.91 USD, and 2 of 10 acceptance criteria passed. The Hebrew report with the post-closure diagnostics is `deliverables/report_he.md`.

| Path | Contents |
|---|---|
| `mql5/Experts/` | `ob_fvg_retest.mq5` with the post-research fixes, plus its research wrapper. The version that was researched is `deliverables/ob_fvg_retest.mq5` (sha256 995158ec...) |
| `deliverables/` | `.set` files (original, baseline, candidate marked not validated), comparison and parameter tables, per-trade tables, charts, the Hebrew report and the forward-test protocol |
| `results/` | the pilot and smoke runs, compile logs, WFO folds and grids, final selection, holdout (non-independent, run once), robustness, `acceptance.json`, input and `.set` validation, code-review checks, post-closure diagnostics, the v1 pilot, and a copy of the experiment log at the time of the move |
| `research/` | snapshot of the pipeline, CLI, pre-registration, scripts and tests used for this research |
| `docs/plans/` | the research plan and protocol |
| `runs/` | raw tester runs (local only, git-ignored: logs may contain the account id) |

SHA256 of the archived EA sources:
- `ob_fvg_retest.mq5` (post-research fixes): ac31d2a07de7f66880751a9db3e1ef7c41be22bbcd2ade1c93e1605494fdfeb3
- `ob_fvg_retest_research.mq5`: 367652c0c03c1ec24db499e6939f0e4e5cc7c183abe74158ad744b0e0c9e47e0

The active `results/experiment_log.*` keeps growing in place, so the trial history stays continuous across studies.
