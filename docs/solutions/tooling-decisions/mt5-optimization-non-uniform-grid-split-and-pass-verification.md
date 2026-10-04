---
title: "MT5 optimization over a non-evenly spaced grid: split the ranges, take inputs from the frames, and prove every pass ran"
module: research/mt5r (gridrun, numeric_v1 study, isolated MT5 tester)
date: 2026-10-04
problem_type: tooling_decision
component: tooling
severity: medium
applies_when:
  - "An MT5 Strategy Tester optimization must cover an input whose values are not evenly spaced (e.g. 10, 20, 40)"
  - "A research pipeline must prove that every optimization pass really ran, rather than trusting that the command finished"
  - "An optimization of the same EA, inputs and window may be started again (resume after a crash or a failed check)"
symptoms:
  - "One MT5 range cannot express {10, 20, 40}: step 10 adds 30, step 30 skips 20"
  - "The optimization XML has no column for an input that was fixed in that run, so the merged table lost the buffer value of the fixed-value run"
  - "A finished optimization command says nothing about how many passes were computed versus answered from tester/cache"
related_components:
  - testing_framework
tags:
  - mt5
  - strategy-tester
  - optimization
  - tester-cache
  - non-uniform-grid
  - pass-verification
  - frames
---

# MT5 optimization over a non-evenly spaced grid: split the ranges, take inputs from the frames, and prove every pass ran

## Context
The numeric_v1 research optimized StructureVariant {0,1} × SwingStrengthM1 {2,3,4} × StopBufferPoints {10,20,40}, which is 18 combinations in each of six train windows (plan `docs/plans/2026-10-04-1851-feat-ob-m1-numeric-grid-research-plan.md`, KTD3 and KTD4). Two things in the MT5 tester made a naive run unsafe.

- **Ranges are start/step/stop only.** `[TesterInputs]` takes `value||start||step||stop||Y`, so one run cannot produce 10, 20 and 40 without also producing 30 or dropping 20.
- **A finished command proves nothing.** The earlier A/B research raised the suspicion that "optimize ends too fast". The audit (`results/diagnostics/optimize_audit.md`) answered it from the tester's own output: the frames, the XML, the manager log and the `tester/cache` folder (session history). The same evidence is now the automatic check.

## Guidance
1. **Split an uneven axis into evenly spaced segments, one optimization run per combination of segments.** `split_runs` (`research/mt5r/numeric_v1.py:80`) cuts each axis into runs with one constant step (`_segments`, line 67), and a one-value segment becomes a fixed input. Here that gives two runs per window:
   - "lo": StopBufferPoints 10..20 step 10, which is 12 passes.
   - "hi": StopBufferPoints fixed at 40, which is 6 passes.
2. **Take each pass's inputs from the frames file, not the XML.** The XML only has columns for inputs that were optimized in that run. In the "hi" run it has `StructureVariant` and `SwingStrengthM1` but no `StopBufferPoints` (`results/numeric_v1/final_selection/nv1_final_grid_hi/nv1_final_grid_hi.xml`). The research build's frames file (`rl_frames_<run>.csv`) carries the full input string of every pass, including `StopBufferPoints=40`. Each pass adds its frame in `OnTester` via `FrameAdd` (`mql5/Experts/ob_m1_structure.mq5:583`), and `OnTesterDeinit` writes the CSV from those frames after the optimization ends (line 590).
   - `run_table` (`research/mt5r/gridrun.py:92`) reads the inputs from the frames and joins the XML metrics by pass number.
   - `verify_optimization` (`research/mt5r/gridrun.py:39`) fails a pass whose XML and frames disagree, or whose input lies outside the grid.
   - `merge` (`research/mt5r/gridrun.py:108`) then requires each grid tuple exactly once, via `wfo.merge_grids`.
3. **Verify completion against four independent counts in the tester's output.** A run counts as complete only when all four equal the expected pass count:
   - frames P-rows;
   - XML rows;
   - the manager log's `total passes N`;
   - `N new records saved to cache`.

   Check `gridrun.py:53-54`. The manager log is one file per day that holds every run of that day, so read only this run's section, from the last `complete optimization started` (`START_MARK`, `gridrun.py:17`, `run_log_section`, line 20). Reading the whole file let an earlier run's lines answer for this one; `results/diagnostics/optimize_audit.md` records the same class of bug in `journal.run_facts`.
4. **Treat a missing "new records" line as a failure, and never re-optimize a verified window.** A window that verifies is saved with `status: verified` and reused on resume (`run_window`, `research/numeric_cli.py:107`, check at line 114).

## Why This Matters
- **A single 10..40 range silently changes the protocol.** Step 10 adds a fourth value (30) that the pre-registration never approved, and step 30 drops one that it did.
- **Inputs read from the XML alone lose fixed values.** The "hi" run's 6 passes then have no buffer value, and the merge either fails or mislabels them.
- **The `.opt` cache is the risky part.** The tester names cache files after the EA, symbol, period, dates and a hash, for example `ob_m1_structure_research.XAUUSD.s.M1.20251201.20260301.40.<hash>.opt`. It writes `N new records saved to cache` after computing passes.
  - On a re-run with the same EA and inputs, the expectation is that the tester answers from that file without computing, so no `OnTester` call and no frame for those passes, and no new-records line.
  - In this pipeline the inputs include `ResearchRunTag`, which `pipeline._prepare` sets to the run ID (`research/mt5r/pipeline.py:200`). So the same run ID means the same inputs.
  - That cache-hit behaviour was not observed in this project. Every run here reported `N new records` (`results/numeric_v1/smoke/optsmoke.json`, `results/numeric_v1/wfo/windows.csv`: 108/108, cached 0).
  - The check fails closed on a missing line either way. As a result, a window that failed half-way cannot be re-run under the same run ID: the check would reject whatever the cache returns (code review residual risk, `deliverables/numeric_v1/report_he.md`, section on code review).

## When to Apply
- Any optimization whose grid values are not one arithmetic progression per axis.
- Any pipeline step that turns an optimization into a decision (selection, acceptance), where an empty, partial or cached result must not pass as a completed one.
- Any resume logic around optimizations. Reuse verified records. Do not re-run, and do not delete `tester/cache` without the user's decision.

## Examples
The two runs of one window, as rendered into `[TesterInputs]` (`results/numeric_v1/wfo/f1_selection/`):

```ini
; lo: 12 passes
StructureVariant=0||0||1||1||Y
SwingStrengthM1=3||2||1||4||Y
StopBufferPoints=20||10||10||20||Y
; hi: 6 passes
StructureVariant=0||0||1||1||Y
SwingStrengthM1=3||2||1||4||Y
StopBufferPoints=40||40||0||40||N
```

A completion record that passes (abridged from `runs[0]` of `results/numeric_v1/smoke/optsmoke.json`):

```json
{"run_id": "nv1_smoke_grid_lo", "status": "ok", "expected": 12, "completed": 12, "failed": 0, "cached": 0,
 "log_total_passes": 12, "new_cache_records": 12, "xml_rows": 12}
```

The tests in `research/tests/test_numeric_v1.py` cover the failure cases:
- a missing frame;
- no new-records line;
- buffer 30 in a pass;
- XML and frames disagreeing;
- a duplicated tuple;
- a daily log that holds an earlier run's section.
