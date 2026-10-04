---
title: M5 OB + M1 Structure Numeric Grid Research - Plan
type: feat
date: 2026-10-04
topic: ob-m1-numeric-grid-research
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-brainstorm
execution: code
---

# M5 OB + M1 Structure Numeric Grid Research - Plan

## Goal Capsule

- **Objective:** The user learns, from a pre-registered train-only selection over a fixed 18-combination grid, whether choosing the structure variant, the M1 pivot strength and the stop buffer improves out-of-window results over the fixed defaults, with "no improvement" an acceptable answer.
- **Means:** a new, separate research version ("numeric v1") on the unchanged `ob_m1_structure` EA, run only in the isolated MT5 Strategy Tester.
- **Product authority:** the user approved this scope on 2026-10-04 in full; a plan that stays inside it needs no further approval round. Trading rules, the grid, the folds and the thresholds are fixed by that approval.
- **Open blockers:** none.

---

## Product Contract

### Summary

A new research version selects StructureVariant, SwingStrengthM1 and StopBufferPoints per fold on train data only, by a rule frozen and pushed before any research run. Its out-of-window results are compared with two fixed baselines, A and B at N=3 and buffer 20, then put through the existing robustness checks. The output is a Hebrew report, validated `.set` files and at most a candidate with a forward-test protocol.

### Problem Frame

The previous research (plan `docs/plans/2026-10-03-0013-feat-m5-ob-m1-structure-ea-plan.md`, protocol `research/preregistration.json`) chose only between variants A and B, with N=3 and the 20-point buffer fixed. Neither variant met the 10% train equity-drawdown limit in any window, so the protocol fell back to A everywhere and the research failed. Its stability check moved N and the buffer one at a time, and those runs swung from +964 to −1848 USD. Whether a controlled choice of these two constants helps out of window is therefore still open.

The pipeline was built for a two-choice grid and cannot answer that question as it stands. MT5 optimizes with uniform start/step/stop ranges, but the buffer values 10, 20, 40 are not evenly spaced. The candidate is mapped to the fixed series by its variant alone. The stability perturbations are fixed around N=3 and buffer 20, so for a candidate with N=2 one "neighbour" would repeat the candidate.

All of December 2025 to July 2026 has been seen in earlier research and pilots, and August–September 2026 has been used as a non-independent check. No period in this history is blind.

### Key Decisions

- **Grid and fixed inputs as approved.** (session-settled: user-directed — chosen over widening the grid or adding parameters: the user fixed the exact grid and forbade further ranges, filters or exit/risk changes.) Governs R5, R6.
- **Same folds and final window.** (session-settled: user-directed — chosen over new or extended windows: comparability with the previous research.) Governs R12.
- **Train-only selection with the existing thresholds.** (session-settled: user-directed — chosen over softening the floor or the 10% train equity-DD limit to obtain an eligible pass.) Governs R13, R14, R15.
- **Two fixed baselines, A and B at N=3 / buffer 20, in every fold.** (session-settled: user-directed — chosen over a single baseline.) Governs R17.
- **August–September is not re-run and not presented as a holdout.** (session-settled: user-directed — chosen over a second look at the same months.) Governs R23.
- **Previous research left untouched; new version, protocol, folders and run IDs.** (session-settled: user-directed — chosen over resetting the old guards to allow more runs.) Governs R1, R2.
- **"Improvement" means beating both fixed baselines out of window.** The previous research already showed fixed B ahead of fixed A out of window, so beating A alone could reflect that exposure rather than the selection. This is stricter than the previous protocol. Governs R19.
- **The primary DSR trial count is conservative.** It counts every train pass of this research plus every evaluated configuration of the previous research on the same history. Because these trials are positively dependent, the count overstates the effective number of trials, which makes passing harder. Governs R21.
- **Neighbours are one grid step on an ordinal axis around the final candidate.** The variant is categorical and is not a neighbour axis. Values outside the grid are never run. Governs R20.
- **Pre-registration checks run on a train-only window.** The window is 2026-02-02 to 2026-02-06: real ticks, inside folds 1–3 train, and outside every OOS month. Governs R9, R10.

### Requirements

**Separation from the previous research**

- R1. The previous research's plan, protocol, EA hash record, results, deliverables and run evidence stay byte-identical; its CLI guards (one-time freeze, committed candidate, holdout refusal) are not reset or bypassed.
- R2. The new research has its own version name, plan, pre-registration file, results root, deliverables folder and run-ID prefix, so no new run can overwrite or be mistaken for an old one.
- R3. The EA source and its research build are not changed; the new pre-registration records the same EA source hash, and every new run is refused when the installed tester copy differs from it.
- R4. `CLAUDE.md` states that selecting parameters on train data by a pre-frozen rule is allowed, while out-of-window results and robustness checks may never change a choice, and that simulated trades in the isolated tester are allowed while the trading ban covers connected accounts.

**Grid and pipeline correctness**

- R5. The grid is exactly StructureVariant {0, 1} (categorical), SwingStrengthM1 {2, 3, 4} and StopBufferPoints {10, 20, 40}: 18 combinations per train window.
- R6. All other inputs are fixed: ImpulseWindowBars 2, RiskRR 2.0, RiskPercent 1.0, MaxExposures 3, WarmupDays 30, deposit 10,000 USD, leverage 1:100, and the existing display inputs.
- R7. Every train window runs exactly the 18 grid combinations, never a value outside the grid such as buffer 30. After merging the tester output, each combination appears exactly once, with none missing, extra or duplicated, or the window fails.
- R8. Every optimization axis has an explicit default (StructureVariant 0, SwingStrengthM1 3, StopBufferPoints 20), and every selected or fallback candidate carries all three values into OOS runs, robustness runs, `.set` export and delivered-build validation.
- R9. Before the freeze, tester runs on the train-only check window show that changing N changes the logged pivots and structure events, and that changing the buffer changes stop distances, each by the expected amount.
- R10. The optimization smoke test and the pass-count checks expect the new grid's pass count and fail on any shortfall.
- R11. For every run, the inputs the tester actually loaded and the number of completed passes are checked against the tester's own output (report inputs, optimization table, frames, journal), and a run with missing passes, unexpected inputs, cached-only results or an empty report is never recorded as successful.

**Protocol freeze and selection**

- R12. Folds stay as before: five folds of three train months and one OOS month, OOS March–July 2026; the final selection uses May–July 2026 train data only. That is 108 train passes (18 × 5 folds + 18 final).
- R13. A pass is eligible only when it meets the existing train trade floor (15 per month × 3 months) and a tester maximum equity drawdown of at most 10% on the train window.
- R14. Among eligible passes, the selection score and tie-break are frozen in the pre-registration before any research run: recovery factor with neighbour smoothing within the same variant, ties to the smaller distance from the defaults, then to the lowest axis indices.
- R15. When no pass is eligible, the window reports `no_eligible_pass`. Any default carried forward to complete the evaluation is labelled a fallback, never an improved or selected candidate.
- R16. The pre-registration, committed and pushed before the first research run, records the exact grid, all fixed inputs, the train and test windows, selection, ties, fallback, neighbours, acceptance criteria, the DSR trial count with its reasoning, and the run budget. The budget lists, separately, the 108 train passes, the smoke and behaviour checks, the OOS runs, the robustness runs and the delivery validation.

**Comparison and robustness**

- R17. Three OOS series run in every fold with chained deposits: the procedure (each fold's selection or fallback), fixed baseline A (0, 3, 20) and fixed baseline B (1, 3, 20). Each run's deposit chain, trade records and loaded parameters are verified.
- R18. The candidate's own series is identified by all of its parameters, never mapped to a fixed baseline because its variant matches.
- R19. Acceptance requires the procedure to meet every pre-registered criterion, including an OOS net result above both fixed baselines. The other criteria carry over from the previous protocol: fill frequency, positive net, bootstrap CI, strict majority of positive folds, top-event removal, loss limits with Monte Carlo, cost stress with entry and stop slippage, neighbour stability and DSR.
- R20. Neighbour stability runs every grid point one step away from the final candidate on SwingStrengthM1 or StopBufferPoints, over March–July. It never counts a run identical to the candidate, and it never feeds back into selection.
- R21. DSR uses the daily Sharpe returned by OnTester. Its primary trial count is documented in the pre-registration: the trials counted, the prior trials included, the dependence assumptions, and a sensitivity figure using the 18 distinct configurations.
- R22. Results are reported for all folds, for folds whose training includes generated ticks (1–2), and for folds trained on real ticks only (3–5). Three drawdowns are reported apart: the tester's, one rebuilt from daily records, and one on closed trades. The 01:00 quote-only-minute sensitivity is reported as before and is never used for a choice.

**Validation limits and deliverables**

- R23. March–July results are presented as outside the train window but not blind or independent. August–September is neither run nor presented as a holdout.
- R24. Even if every criterion passes, the output is at most a `candidate.set` and a forward-test protocol on new data, with no `recommended.set`. A failed research says explicitly that no improvement was found within the tested grid.
- R25. Deliverables include a Hebrew report, a parameter table, per-fold results, the baseline comparison, the robustness results, `.set` files for the original, the two baselines and the candidate or fallback, each validated against the delivered build, plus simplification and code review of the changed code.

**Process and synchronization**

- R26. Every run goes through the isolated runner and its guards. If the live terminal is open, the user is asked to close it, and it is never closed, started or attached to by the agent.
- R27. After each verified unit of work, a focused commit is pushed to the working branch per `CLAUDE.md`, and `docs/PROJECT_STATUS.md` is updated. Each report names the branch, the full SHA and the commit link.
- R28. Every train window reports its expected, completed, failed and cached combinations, its run time, its number of eligible passes, and the selected parameters with the reason for the choice.

### Acceptance Examples

- AE1. **Covers R7.** Given the buffer axis {10, 20, 40}, when a train window's optimization is merged, then exactly 18 unique (variant, N, buffer) rows exist and no row has buffer 30; a missing or duplicated row stops the window.
- AE2. **Covers R15, R17.** Given no eligible pass in fold 2, when its OOS runs, then the procedure runs the defaults labelled `fallback (no_eligible_pass)`, and the report never calls them a selected or improved candidate.
- AE3. **Covers R18.** Given a final candidate (0, 2, 40), when robustness runs, then its series is the candidate's own, not fixed baseline A, although both have variant 0.
- AE4. **Covers R20.** Given a final candidate (1, 2, 10), when neighbour stability runs, then the neighbours are (1, 3, 10) and (1, 2, 20) only, and no run repeats (1, 2, 10).
- AE5. **Covers R20.** Given a final candidate (0, 3, 20), then the neighbours are (0, 2, 20), (0, 4, 20), (0, 3, 10) and (0, 3, 40).
- AE6. **Covers R19.** Given a procedure that beats fixed A but not fixed B out of window, then acceptance fails on the baseline criterion.

### Success Criteria

- The 108 train passes, all OOS runs and all robustness runs complete, with loaded inputs and pass counts verified against tester output (R11).
- A reader of the Hebrew report can tell, from one table per fold, what was selected or fell back and why, and whether the selection beat both baselines.
- The research reaches a final verdict, positive or negative, without a second optimization round.

### Scope Boundaries

- No change to trading rules, entries, exits, risk, filters, time limits or the EA source.
- No grid values or parameters beyond R5, and no range widening after seeing results.
- No re-run of August–September 2026 and no new holdout claim.
- No `recommended.set`, no live or demo trading, and no change to the live terminal.
- No change to the previous research's files, results or guards.
- A further optimization round, new folds or a new strategy version each need a new user approval.

### Dependencies / Assumptions

- The isolated tester at `C:\mt5r` (build 6231) and its tick history for December 2025 to July 2026 stay as they were in the previous research (real ticks from February 2026; generated ticks in December and partly January).
- The live terminal is closed during runs; if not, work pauses for the user.
- From the previous research, every train pass had a tester equity drawdown between 13.8% and 54.6%, so a `no_eligible_pass` in some or all windows is a plausible outcome. It is reported as found.
- Pushes go to `https://github.com/AriGabay/nwq-mql5.git` on the working branch, which is public, so nothing secret or local is committed (`CLAUDE.md`).

### Outstanding Questions

**Deferred to Planning**

- How the non-uniform buffer axis is split into uniform tester ranges, and how the split runs are merged and checked (R7).
- The research version name, run-ID prefix and folder layout (R2).
- How the new commands share code with the previous research's commands without reopening their guards (R1, R2).
- How the behaviour checks of R9 measure "expected amount" from the research logs and the independent checker.
- The exact DSR trial count and its arithmetic, within the rule in Key Decisions (R21).

### Sources / Research

- Previous plan and protocol: `docs/plans/2026-10-03-0013-feat-m5-ob-m1-structure-ea-plan.md`, `research/preregistration.json`.
- Optimization audit of the previous research: `results/diagnostics/optimize_audit.md`.
- Current state and the list of code changes a numeric grid needs: `docs/PROJECT_STATUS.md`.
- EA use of the axes: `mql5/Experts/ob_m1_structure.mq5` (pivots use SwingStrengthM1 near line 880; the stop buffer is applied in `StopFor` near line 1398).
- Quote-only minute: `docs/solutions/tooling-decisions/mt5-tester-quote-only-minute-at-session-open.md`.
