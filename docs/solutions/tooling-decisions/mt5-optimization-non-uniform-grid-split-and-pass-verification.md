---
title: "MT5 optimization over a non-evenly spaced grid: split the ranges, take inputs from the frames, and prove every pass ran"
module: research/mt5r (gridrun, numeric_v1 study, isolated MT5 tester)
date: 2026-10-04
last_updated: 2026-10-04
problem_type: tooling_decision
component: tooling
severity: medium
applies_when:
  - "An MT5 Strategy Tester optimization must cover an input whose values are not evenly spaced (e.g. 10, 20, 40)"
  - "A research pipeline must prove that every optimization pass really ran, rather than trusting that the command finished"
  - "An optimization of the same EA, inputs and window may be started again (resume after a crash or a failed check)"
  - "A train window that failed verification must be retried without overwriting evidence or trusting a cached result"
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
  - recovery-attempt
  - result-provenance
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
   - `run_table` (`research/mt5r/gridrun.py:127`) reads the inputs from the frames and joins the XML metrics by pass number.
   - `verify_optimization` (`research/mt5r/gridrun.py:57`) fails a pass whose XML and frames disagree, or whose input lies outside the grid.
   - `merge` (`research/mt5r/gridrun.py:143`) then requires each grid tuple exactly once, via `wfo.merge_grids`.
3. **Verify completion against four independent counts in the tester's output.** A run counts as complete only when all four equal the expected pass count:
   - frames P-rows;
   - XML rows;
   - the manager log's `total passes N`;
   - `N new records saved to cache`.

   The counts are compared in `verify_optimization` (`gridrun.py:77` onward). The manager log is one file per day that holds every run of that day, so read only this run's section, from the last `complete optimization started` (`START_MARK`, `gridrun.py:17`, `run_log_section`, line 20). Reading the whole file let an earlier run's lines answer for this one; `results/diagnostics/optimize_audit.md` records the same class of bug in `journal.run_facts`.
4. **Classify where the passes came from, and let only a fresh computation verify.**
   - `verify_optimization(..., cache=(before, after))` takes a snapshot of the window's own cache files before and after the run (`cache_snapshot`, `gridrun.py:28`). It compares them (`cache_change`: new, modified or unchanged, line 39).
   - **Provenance:**
     - **computed:** all four counts equal `expected`, and the cache changed;
     - **reused:** no new records, the XML holds every pass, and the cache is unchanged;
     - **partial:** anything else.
   - Only computed verifies (line 110).
   - Never infer provenance from a new run ID.
   - The cache files are named `<ex5 stem>.<symbol>.<period>.<start YYYYMMDD>.<end+1 day YYYYMMDD>.<...>.opt`. The tester uses the ini ToDate, which `ini.next_day` sets to the end date + 1, so a glob on the end date itself finds nothing.
5. **Retry a failed window as a new attempt, never by re-running the same run ID.** `run_window` (`research/numeric_cli.py:143`) works like this:
   - It reuses a verified `window.json` and never re-optimizes it (line 158).
   - Otherwise it reserves `attempts/a<n>.started.json` before any tester call and writes the outcome to `attempts/a<n>.json`. Both files are created exclusively (`_create`, line 136, mode `x`) and are never overwritten.
   - An interrupted attempt still counts, so it cannot block the next one.
   - Attempt 1 keeps the original run IDs. Later attempts use `grid_run_id` (`numeric_v1.py:171`), which produces `<tag>_grid_<part>_a<n>`.
   - A run ID that already exists in `runs/` or in the curated folder is refused, because the runner deletes `runs/<id>` before reuse.
   - The tester cache is never deleted.

## Why This Matters
- **A single 10..40 range silently changes the protocol.** Step 10 adds a fourth value (30) that the pre-registration never approved, and step 30 drops one that it did.
- **Inputs read from the XML alone lose fixed values.** The "hi" run's 6 passes then have no buffer value, and the merge either fails or mislabels them.
- **The `.opt` cache is the risky part.**
  - The tester keys a cache file on the EA, symbol, period, dates and a hash of the inputs. It writes `N new records saved to cache` after computing passes.
  - The inputs include `ResearchRunTag`, which `pipeline._prepare` sets to the run ID (`research/mt5r/pipeline.py:200`). So a new attempt's run ID gives a new input set.
  - In `C:\mt5r\Tester\cache` each train window has three `.opt` files: the previous A/B grid plus the numeric_v1 `lo` and `hi` runs, one per distinct input set.
  - A re-run with identical inputs is expected to be answered from the cache, with no frames and no new-records line. That cache hit was never observed in this project: every run reported `N new records` (`results/numeric_v1/smoke/optsmoke.json`, `results/numeric_v1/wfo/windows.csv`: 108/108, cached 0). `reused` is therefore a classification of expected behaviour, and it fails closed.
- **Residual risks:**
  - A crash between writing `attempts/a<n>.json` (verified) and `window.json` makes the next call start a new attempt and re-optimize under new run IDs. Evidence is kept, but tester time is spent again.
  - If the cache key ignored `ResearchRunTag`, a retry after an attempt whose `lo` part succeeded and `hi` part failed would see `lo` reused and fail before `hi` runs. This fails closed, but blocks the window.
  - If a terminal build renamed the cache files, both snapshots would come back empty and every run would be partial. This also fails closed.

## When to Apply
- Any optimization whose grid values are not one arithmetic progression per axis.
- Any pipeline step that turns an optimization into a decision (selection, acceptance), where an empty, partial or cached result must not pass as a completed one.
- Any resume or retry logic around optimizations. Reuse verified records, retry under a new attempt with its own run IDs and records, and do not delete `tester/cache` without the user's decision.

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

The tests cover these cases. In `research/tests/test_numeric_v1.py`:
- a missing frame;
- no new-records line;
- buffer 30 in a pass;
- XML and frames disagreeing;
- a duplicated tuple;
- a daily log that holds an earlier run's section;
- provenance: computed with a new cache file, reused (no new records, unchanged cache), partial (complete counts but an unchanged cache), a grown file counted as modified, and the snapshot taking only this window's `research` `.opt` files dated end + 1.

In `research/tests/test_numeric_cli.py`:
- a failed attempt kept byte for byte while the retry uses `_a2` IDs;
- an interrupted attempt that does not block the next;
- an existing run ID refused before any tester call;
- a non-verified legacy `window.json` left untouched;
- snapshot, then optimization, then snapshot for each run, passed in that order to the verification.
