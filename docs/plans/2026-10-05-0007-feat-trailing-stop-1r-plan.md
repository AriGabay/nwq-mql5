---
title: 1R Trailing Stop for the M5 OB + M1 Structure EA - Plan
type: feat
date: 2026-10-05
topic: trailing-stop-1r
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# 1R Trailing Stop for the M5 OB + M1 Structure EA - Plan

## Goal Capsule

- **Objective:** The active EA can trail each open position's stop continuously by 1R once the position reaches +1R, as the user specified. The user can see, from full isolated-tester runs on March 2026, exactly how positions behave with the trail on versus off.
  - With the trail off, the trades stay identical to the previous baseline.
  - Every stop move is logged and independently checked.
  - The comparison is reported as development evidence only.
- **Means:**
  - an `EnableTrailingStop` input (default false) and a per-position trail manager in `mql5/Experts/ob_m1_structure.mq5` (KTD1–KTD5);
  - a Python reference model, contract and checker extensions (KTD6, KTD7);
  - a separate development CLI for the March 2026 runs (KTD8).
- **Product authority:** the user's request of 2026-10-05. It approves this change, isolated-tester runs on March 2026, and PR + merge per `CLAUDE.md`. It does not approve grid expansion, optimization research, a `recommended.set`, Dukascopy data, forward runs or any connected-account trading.
- **Stop conditions:**
  - the live terminal is running when a tester run is due: ask the user to close it, never close it;
  - with the trail off, the trades do not match the baseline: stop and diagnose before any trail-on comparison. First check the report's History Quality and tick counts, because a history refresh in the isolated copy would cause a mismatch without any EA defect;
  - any step would edit a frozen pre-registration or existing evidence.
- **Who finishes:** `ce-work` implements and verifies; LFG simplifies, reviews, commits, opens and merges PRs.

---

## Product Contract

### Summary

Add a continuous trailing stop with these properties:
- it activates at +1R and trails at 1R from the best Bid (long) or best Ask (short) since the fill;
- R stays the original risk |fill − original SL|;
- the 2R TP never moves.

The trail sits behind a default-off input, is persisted per position, and every modification is logged. A new independent check and stop-path charts verify it, from full tester runs on March 2026 (development only).

### Problem Frame

The user wants to test a specific exit change on the active strategy, the trailing stop. Every earlier rule stays as it is. The change must be:
- implemented exactly as specified;
- safe in real MT5 execution: per ticket, verified acceptance, broker limits, rejection handling, restart;
- invisible when turned off.

The research tooling was built around a fixed stop. Some of it would give wrong answers for trailed positions if used unchanged.

### Key Decisions

- **The trail rule is fixed by the user.** Governs R1–R6. (session-settled: user-directed — chosen over recomputing R from the current SL, stepped trailing, deactivation on pullbacks, or an extra spread term for shorts: the user specified this exact rule.)
- **Everything else stays.** Entries, variants A/B, risk, position cap, the initial SL and the 2R TP do not change. There are no partial exits and no break-even rule. Governs R5, R7. (session-settled: user-directed — chosen over other exit changes: bounded change.)
- **Off by default, not a parameter to tune.** `EnableTrailingStop` defaults to false. Activation and distance are fixed at 1R. Governs R7, R8. (session-settled: user-directed — chosen over a tunable trail: no optimization research approved.)
- **Development comparison only.** It runs on the already-exposed March 2026, makes no improvement claim and produces no `recommended.set`. The rule is never changed from the results. Governs R17–R20. (session-settled: user-directed — chosen over treating results as validation: the period is exposed.)
- **Evidence untouched.** Old research, frozen pre-registrations and evidence stay unchanged. New runs get new IDs and folders. Governs R21. (session-settled: user-directed — chosen over resetting old guards: evidence preservation.)

### Requirements

**Trail rule**

- R1. For each position:
  - **E** is the actual fill price;
  - **SL0** is the stop accepted on the position after the fill;
  - **R0** = |E − SL0|.

  R0 is fixed for the life of the trade and never recomputed from a trailed stop.
- R2. **Long:** track the highest Bid since the fill. The trail activates when Bid ≥ E + R0. The requested SL is the highest Bid − R0.
- R3. **Short:** track the lowest Ask since the fill. The trail activates when Ask ≤ E − R0. The requested SL is the lowest Ask + R0. No extra spread term is added.
- R4. The trail is continuous on every tick, not whole-R steps. At 1R the requested SL equals E; at 1.5R it is E ± 0.5R. The SL only moves in the trade's favor, and once active the trail never deactivates.
- R5. The TP stays 2R from E and SL0 and is never moved.

**Configuration**

- R6. Activation distance and trail distance are both exactly 1R.
- R7. A new input, `EnableTrailingStop`, defaults to false. With it false, the EA behaves exactly as before.
- R8. A new `.set` file enables the trail with all other inputs at their code defaults. It is not a recommendation.

**MT5 execution**

- R9. Each position of this EA's symbol and `MagicNumber` is managed separately. The SL is modified by the position ticket.
- R10. A modification counts as accepted only when the result code is done and the SL actually on the position equals the requested SL to within half a tick. A `true` return alone is not enough.
- R11. Tick size, stops level, freeze level and trading hours are respected.
- R12. A rejected or unsent update keeps the active SL. The rejection is logged, and a retry happens only when the request is valid and allowed, never as a flood of identical requests. No false success, retroactive stop or invented fill is recorded.
- R13. E, SL0, R0 and the trail state are persisted per position, so the trail can be restored after a restart. SL0 is never inferred from the current SL. The report states exactly what was verified about restoration.

**Logs and checking**

- R14. The research logs record, separately:
  - E, SL0 and R0;
  - the activation time;
  - Bid, Ask and the best price;
  - for every modification: the requested SL, the accepted SL and the result code;
  - the exit class: original SL, trailed SL, TP or end of run.
- R15. Every R figure for these runs is measured against R0. An SL exit is not necessarily a loss.
- R16. The contract (`research/mt5r/m1_contract.py`) and the independent checker (`research/mt5r/conformance_m1.py`) support a moving stop. Research tools that assume a fixed stop are listed. Those that cannot handle a trailed run refuse it, starting with the 01:00 session sensitivity.

**Verification**

- R17. Unit tests for long and short cover:
  - no activation before 1R, and activation exactly at 1R;
  - continuous advance between 1R and 2R;
  - no retreat;
  - R0 and TP unchanged;
  - rounding, a rejected update, and several concurrent positions.
- R18. Both builds (delivered and research) compile. With the trail on, they produce the same trades on March 2026.
- R19. On March 2026 in the isolated tester:
  - with the trail off, the trades match the previous baseline trades for A and for B;
  - with the trail on, the behaviour is verified from the Bid/Ask logged at every request, by replaying those requests through the reference model, plus a bar-level completeness check (KTD7). It is shown in charts of the stop path.
- R20. On-versus-off is compared only from full MT5 runs, without requiring identical entries. It is reported as a development comparison, with no claim of improvement and no `recommended.set`.
- R21. Earlier research, the frozen pre-registrations and the evidence stay unchanged. New runs use new run IDs and folders. The live terminal is never touched.

### Acceptance Examples

- AE1. **Covers R2, R4.**
  - **Given** a long with E = 2000.00 and SL0 = 1995.00 (R0 = 5.00), **when** the Bid first reaches 2004.99, **then** there is no activation and no request.
  - **When** the Bid reaches 2005.00, **then** the trail activates and requests 2000.00.
  - **When** the highest Bid reaches 2007.50, **then** it requests 2002.50.
  - **When** the Bid falls back to 2003.00, **then** the requested SL stays 2002.50.
- AE2. **Covers R3, R4.** **Given** a short with E = 2000.00 and SL0 = 2005.20 (R0 = 5.20), **when** the Ask reaches 1994.80, **then** the trail activates and requests 2000.00. **When** the lowest Ask is 1993.00, **then** it requests 1998.20.
- AE3. **Covers R10, R12.** **Given** a modification whose result code is done but whose position SL reads back unchanged, **then** it is logged as rejected (not accepted), the active SL is kept, and the same value is not re-sent on the next tick.
- AE4. **Covers R7, R19.** **Given** `EnableTrailingStop=false` on March 2026, variant A, **then** the deal list equals that of `results/numeric_v1/wfo/nv1_f1_oos_baseline_a` field for field.

### Scope Boundaries

- No grid, no optimization, no tuning of 1R.
- No `recommended.set`.
- No forward, demo or live run.
- No Dukascopy data.
- The frozen studies' protocol commands are not re-enabled. They refuse to run with the changed EA source by design.
- No break-even rule, partial exits or TP movement.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **A separate trail registry, keyed by position ticket.**
  - A new `TrailPos` array (`gTr[]`) holds: ticket, direction, E, SL0, R0, TP, the best price, whether the trail is active, the activation time, the last accepted SL, the last rejected request, and the last request time.
  - It is independent of the setup array `S[]`, which every `OnInit` rebuilds from scratch.
  - It is filled at the fill in `TryEntry`, from `DEAL_PRICE` and the `POSITION_SL` and `POSITION_TP` read back after the TP modify.
  - **`EnableTrailingStop` is the single switch for every side effect of the trail.** When it is false, none of the following runs:
    - the registry fill;
    - writing, flushing or deleting global variables;
    - the restore in `OnInit`;
    - the event `trail_state_roundtrip`;
    - the files `rl_trail` and `rl_sl_moves`.

    So a run with the trail off behaves and writes exactly as before (R7).
  - Governs R1, R9.
- KTD2. **A per-tick `ManageTrails(tk)` step, run after `SyncTrades` and only when `EnableTrailingStop` is true.**
  1. For every registry entry whose position is still open, update the best price (Bid for longs, Ask for shorts).
  2. Test activation with a half-tick tolerance, since all prices sit on the tick grid.
  3. Compute the requested SL as `RoundTick(best − R0, down)` for longs and `RoundTick(best + R0, up)` for shorts. This rounding is conservative and never beyond the exact level.
  4. Send a modification only when the request improves the SL actually on the position by at least one tick.
  - Governs R2–R4.
- KTD3. **Modify by ticket, then verify.**
  - The call is `trade.PositionModify(ticket, newSL, currentTP)`, with the TP read from the position.
  - Success requires `ResultRetcode() == TRADE_RETCODE_DONE` and a re-read `POSITION_SL` within half a tick of the request, with `POSITION_TP` unchanged.
  - **Pre-checks, not sent and logged as `not_sent:<reason>`:**
    - stops level: the distance from the current Bid (long) or Ask (short) to the new SL must be at least `SYMBOL_TRADE_STOPS_LEVEL` points;
    - freeze level: the current price must not be within `SYMBOL_TRADE_FREEZE_LEVEL` points of the current SL or the TP.
  - **Retry policy:** a value that was rejected or not sent is not re-sent until the requested value changes or a new M1 bar opens, whichever is first. This covers `MARKET_CLOSED`, including the quote-only minute at 01:00.
  - Governs R10–R12.
- KTD4. **Persistence in terminal global variables.**
  - On the fill, named `OBM1T.<magic>.<ticket>.{e,sl0,r0,tp,best,act,actms}` is written and then `GlobalVariablesFlush()` is called.
  - `best` and `act` are updated whenever they change. The variables are deleted when the position closes.
  - **`OnInit`, after `ResetState`:**
    - for each open position of this symbol and magic, the registry entry is rebuilt from the global variables;
    - if they are missing, the event `trail_state_missing` is logged and the position is not trailed. SL0 is never taken from the current SL.
    - `OnInit` makes no `PositionModify` call, as the existing static test requires.
  - **What can be verified:** at each fill the research build writes the values, reads them back through the same restore function, and logs `trail_state_roundtrip` with any mismatch.
  - **What cannot:** a real terminal restart cannot be exercised in the strategy tester. The report says so.
  - Governs R13.
- KTD5. **Exit classification.** `SyncPosition` classifies an exit as:
  - `trail`: `DEAL_REASON_SL` after at least one accepted move;
  - `sl`: `DEAL_REASON_SL` at the original stop;
  - `tp`;
  - `end`.

  `rl_setups.sl` keeps meaning SL0, so `trades.trade_table`, R18 and R19 checks and old tooling remain correct. Governs R14, R15.
- KTD6. **Research logs and contract.**
  - `rl_setups` and `rl_events` keep their columns, which keeps old evidence valid.
  - Two new optional files are added:
    - `rl_trail_<tag>.csv`, one row per position: `position_id, setup_id, dir, fill_price, sl0, r0, tp, activated_msc, activation_bid, activation_ask, best_price, final_sl, requests, accepted, rejected, exit_kind`;
    - `rl_sl_moves_<tag>.csv`, one row per request or skip: `position_id, msc, bid, ask, best, requested_sl, sl_before, accepted_sl, retcode, outcome` (`accepted|rejected|not_sent:<reason>`).
  - `m1_contract` gains:
    - the input `EnableTrailingStop` (bool, false), after `TradeComment`;
    - `"trail"` in `EXIT_KINDS`;
    - the two file schemas;
    - the event kinds `trail_state_missing` and `trail_state_roundtrip`.
  - Governs R14.
- KTD7. **The Python reference model is the spec, and the checker replays it.**
  - `research/mt5r/trailing.py` implements R1–R4 and KTD2–KTD3 as pure functions: activation, the requested SL with rounding, the send/no-send decision, and the result handling.
  - The unit tests in R17 run against it.
  - `conformance_m1` gains the rule `trail_r23`. It replays each run's `rl_sl_moves` through the model and checks:
    - every request equals the model's value for the logged best price;
    - the best price never retreats, and Bid/Ask are consistent with the best price;
    - the requested best price stays inside the M1 bar range;
    - activation happens only at or after ±1R;
    - accepted SLs only improve;
    - R0 and TP stay constant;
    - a `trail` exit price sits at the last accepted SL, within the existing exit tolerance;
    - no exit is classified `sl` after an accepted move.
  - **Completeness, so a missing row cannot pass:**
    - **Longs:** if any full M1 bar strictly between the fill bar and the exit bar has a Bid high ≥ E + R0, an activation must be logged no later than that bar. `rl_trail.best_price` must be at least the highest Bid of those bars, within half a tick.
    - **Shorts:** the M1 bars carry only Bid, so the check applies the necessary condition, Bid low ≤ E − R0 − the logged spread. The remaining cases are counted under a new UNVERIFIABLE key, `short_trail_ask`, in the same way as the existing `short_exit_ask`.
  - `_check_exit` takes the stop in force at each minute from the accepted moves, instead of the fixed `sl`.
  - Governs R16, R19.
- KTD8. **A separate development CLI: `research/trail_cli.py`.**
  - It does not touch the frozen studies' CLIs or guards.
  - **Run IDs:** prefixed `tr1_`. **Window:** fixed to 2026.03.01–2026.03.31, refusing any other.
  - **Results:** `results/trailing_v1/`. Compile logs go there too, never to `results/compile/`.
  - **Steps:** install and compile both sources; run, check and chart the runs.
  - **Runs:** four research-build runs plus two delivered-build twins, six in total:
    - `tr1_off_a`, `tr1_off_b`;
    - `tr1_on_a`, `tr1_on_b`;
    - `tr1_on_a_delivered`, `tr1_on_b_delivered` (delivered build).
  - It uses the existing guard that the installed EA matches the repo, and the runner's live-terminal guard.
  - Before any run it points `explog.LOG` at `results/trailing_v1/experiment_log.jsonl`, as `numeric_cli.use_study_log` does. This keeps the old research's `results/experiment_log.jsonl` from changing.
  - Governs R18–R21.
- KTD9. **A fixed-stop audit, with refusals where a tool would be wrong.**
  - `session_sensitivity` and `session_probe.cases` refuse a trailed run. A trailed run is one with a `trail` exit or a non-empty `rl_trail` file. Their 01:00 re-pricing assumes a fixed SL.
  - `research/wfo_diagnostics.py` (`r_levels`) refuses the same way.
  - `stress` (the cost stress) refuses trailed runs too. Its stop-slippage model has not been specified for a moving stop, and nothing in this plan stress-tests a trailed run.
  - The charts draw the stop path as a step line from the accepted moves.
  - The full audit table goes into the development report.
  - Governs R16.

### High-Level Technical Design

The per-tick decision for one open position (KTD2, KTD3):

```mermaid
flowchart TB
  A[tick: Bid, Ask] --> B[update best: long max Bid, short min Ask]
  B --> C{active?}
  C -->|no| D{long Bid >= E+R0 / short Ask <= E-R0}
  D -->|no| Z[nothing]
  D -->|yes| E[active = true, log activation, persist]
  C -->|yes| F
  E --> F[req = round(best -/+ R0), conservative]
  F --> G{req better than position SL by >= 1 tick?}
  G -->|no| Z
  G -->|yes| H{same value rejected before and no new M1 bar?}
  H -->|yes| Z
  H -->|no| I{stops level and freeze level OK?}
  I -->|no| J[log not_sent:reason, remember value]
  I -->|yes| K[PositionModify by ticket, SL=req, TP=position TP]
  K --> L{retcode DONE and POSITION_SL == req and TP unchanged?}
  L -->|yes| M[log accepted, persist]
  L -->|no| N[log rejected with retcode and SL read back, keep active SL, remember value]
```

### Assumptions

- XAUUSD.s on `Bybit-Live-4`: tick size 0.01, observed stops level 20 points, freeze level read at runtime.
- March 2026 has real ticks (`results/pilot/tick_coverage.json`).
- In the strategy tester, global variables live in the tester's own space and are cleared between runs. This is why restoration is checked as a round trip (KTD4).
- In the tester, a stop is filled at the stop price or worse. The existing exit tolerance in `conformance_m1` applies to `trail` exits.

### Sequencing

U1 → U2 → U3 → U4 → U5 → U6. U1 (the model) fixes the spec that U2 (the EA) and U3 (the checker) must match. U5 needs U2–U4.

Each unit is committed when its own tests pass. The branch is pushed and merged to `main` only after the U5 gates pass:
- 0 compile errors;
- the trail-off runs equal the baseline;
- the builds produce the same trades;
- 0 conformance violations.

U6 is the closing documentation commit.

---

## Implementation Units

### U1. Trailing reference model

- **Goal:** the spec as executable, tested Python.
- **Requirements:** R1–R6, R10–R12, R17 (KTD7).
- **Dependencies:** none.
- **Files:** `research/mt5r/trailing.py` (new), `research/tests/test_trailing.py` (new).
- **Approach:**
  - pure functions: `r0(fill, sl0)`, `activated(dir, fill, r0, bid, ask)`, `requested_sl(dir, best, r0, tick)`;
  - `decide(state, bid, ask, pos_sl, tp, stops_pts, freeze_pts, point, bar_time)`, returning send / not_sent(reason) / nothing;
  - `on_result(state, retcode, sl_read_back, tp_read_back)`;
  - state as a small dataclass.
- **Test scenarios:**
  - Covers AE1: long, no activation at E + R0 − 1 tick; activation at exactly E + R0 requests E; best E + 1.5R requests E + 0.5R; a pullback keeps the request; a later higher best advances.
  - Covers AE2: the same sequence for a short, using Ask and no extra spread.
  - Advance between 1R and 2R is continuous: every new best tick gives a new request one tick apart.
  - R0 and TP are unchanged after many moves; the TP passed to the modification always equals the position TP.
  - Rounding: a best price whose best − R0 falls between ticks rounds down for longs and up for shorts.
  - Covers AE3: done-but-unchanged read-back counts as rejected; the same value is not re-sent on the next tick; it is re-sent after a new M1 bar or when the value improves.
  - The stops level or freeze level blocks a send, logs not_sent and keeps the SL.
  - Two concurrent positions (one long, one short) evolve independently.
- **Verification:** all scenarios pass.

### U2. EA implementation and contract

- **Goal:** the trail in `ob_m1_structure.mq5` per KTD1–KTD6, invisible when off.
- **Requirements:** R1–R14 (KTD1–KTD6).
- **Dependencies:** U1.
- **Files:** `mql5/Experts/ob_m1_structure.mq5`, `research/mt5r/m1_contract.py`, `research/tests/test_ea_m1_static.py`.
- **Approach:**
  1. Add the input and the `TrailPos` registry. Fill the registry at the fill.
  2. Add `ManageTrails` in `OnTick` after `SyncTrades`.
  3. Add the modify/verify helper, the global-variable persistence and the restore in `OnInit`.
  4. Classify `trail` in `SyncPosition`.
  5. Add the research-only writers for `rl_trail` and `rl_sl_moves`, plus the round-trip event.
  6. Update the contract.
- **Constraints from the existing static tests:**
  - no `.Buy(` or `.Sell(` outside `TryEntry`;
  - no `OrderModify` or `PositionClose`;
  - no literal 20 or 0.2;
  - research identifiers only inside research blocks;
  - every contract event and exit kind present in the source;
  - `OnInit` without `PositionModify`;
  - `ResetState` clears the new registry.
- **Test scenarios (static):**
  - the input exists with default false in contract order;
  - `ManageTrails` is called after `SyncTrades` and returns at once when the input is false;
  - the modification uses the registry ticket and passes the position TP;
  - `ResultRetcode` and a `POSITION_SL` read-back both appear in the trail modify helper;
  - R0 is assigned only from the fill price and the accepted SL0 at the fill, never from `POSITION_SL` later;
  - `RoundTick(... , false)` for longs and `RoundTick(... , true)` for shorts in the request;
  - no spread term in the short request;
  - the global-variable names include the magic number and the ticket;
  - the new CSV headers equal the contract.
- **Verification:** the static tests pass; both builds compile in U5 with 0 errors. A static test checks that the fill-time trail code in `TryEntry`, the restore in `OnInit` and the research writers all sit behind `EnableTrailingStop`.

### U3. Checker support for a moving stop

- **Goal:** the independent check of every trail move and exit (KTD7).
- **Requirements:** R14–R16, R19.
- **Dependencies:** U1, U2 (schemas).
- **Files:** `research/mt5r/conformance_m1.py`, `research/tests/test_conformance_m1.py`, `research/tests/fixtures/m1/` (new trail fixtures).
- **Approach:**
  1. Read the optional `rl_trail` and `rl_sl_moves` files.
  2. Add rule `trail_r23`, which replays the moves through `trailing.py`.
  3. Make `_check_exit` use the stop path.
  4. Keep old logs valid: no trail files means no `trail_r23` checks.
- **Test scenarios:**
  - a valid trail fixture passes;
  - a request not equal to the model value is flagged;
  - a retreating accepted SL is flagged;
  - activation before 1R is flagged;
  - a changed TP or R0 is flagged;
  - a `trail` exit not at the last accepted SL is flagged;
  - an `sl` exit after an accepted move is flagged;
  - a missed long activation, where a bar reached E + R0 with no activation logged, is flagged;
  - a long trail that stalls, with a final best price below the highest Bid of the bars, is flagged;
  - an unverifiable short case is counted under `short_trail_ask`, not as a violation;
  - the existing old-run fixtures still pass unchanged.
- **Verification:** the conformance tests pass, the old fixtures are unchanged, and `results/numeric_v1` conformance re-read on one old run still gives 0 violations.

### U4. Fixed-stop audit, refusals and stop-path charts

- **Goal:** no fixed-stop analysis is silently run on trailed runs, and charts show the stop path (KTD9).
- **Requirements:** R15, R16, R19.
- **Dependencies:** U2.
- **Files:**
  - `research/mt5r/session_sensitivity.py`, `research/session_probe.py`, `research/wfo_diagnostics.py`, `research/mt5r/stress.py`;
  - `research/mt5r/charts_m1.py`, or a new `research/mt5r/charts_trail.py`;
  - their tests in `research/tests/`.
- **Approach:**
  - a shared helper `trailing.is_trailed_run(folder, tag)`;
  - refusals that raise with a message naming the fixed-stop assumption;
  - the stress charge includes `trail` exits;
  - the chart draws the M1 Bid path from shortly before the fill to after the exit, E, SL0 and TP lines, the accepted-SL step line, the activation marker and the exit marker.
- **Test scenarios:**
  - session sensitivity, `session_probe.cases`, `wfo_diagnostics` and `stress` refuse a run containing a `trail` exit or a non-empty `rl_trail` file, and still work on an old run;
  - the chart for a fixture trail position writes a PNG, and its stop series equals the accepted moves.
- **Verification:** tests pass, and the outputs for old runs are unchanged.

### U5. Development CLI, compile, March 2026 runs and comparisons

- **Goal:** the R18–R20 evidence from full isolated-tester runs (KTD8).
- **Requirements:** R8, R13, R18–R21.
- **Dependencies:** U2–U4.
- **Files:**
  - `research/trail_cli.py` (new) and `research/tests/test_trail_cli.py` (new);
  - `deliverables/trailing_v1/ob_m1_structure_trailing.set` (new);
  - `results/trailing_v1/` (new: run folders, `compile/`, `runs.json`, `baseline_match.json`, `builds_match.json`, `conformance/`, `charts/`, `dev_comparison.md`).
- **Approach:**
  1. Install the sources into the isolated copy and compile both builds into `results/trailing_v1/compile`.
  2. Run `tr1_off_a` and `tr1_off_b`. Compare their deal lists with `results/numeric_v1/wfo/nv1_f1_oos_baseline_{a,b}/rl_deals_*.csv`, field for field. A mismatch stops the unit.
  3. Run `tr1_on_a` and `tr1_on_b`, plus their delivered-build twins, and compare the twins' deals.
  4. Run the conformance check, with `trail_r23`, on the on runs.
  5. Draw charts for a fixed selection: the first two positions per class among trail exit, TP and original SL, long and short where present.
  6. Write the `.set` and validate that a delivered run loads it.
  7. Write `dev_comparison.md`. It is labelled development-only and gives, per variant, on versus off:
     - trades, net and tester equity DD;
     - mean R against R0;
     - the mix of exit classes;
     - the count of activations and of accepted, rejected and not-sent moves;
     - the fixed-stop tool audit table;
     - the restoration statement (KTD4).
- **Test scenarios (unit, no tester):**
  - a window other than March 2026 is refused;
  - run IDs carry `tr1_` and resolve to new folders;
  - an existing run folder is never reused;
  - the deal comparison flags a single changed field;
  - a run never appends to `results/experiment_log.jsonl`;
  - with the trail off, the run writes no `rl_trail` or `rl_sl_moves` file, which is checked on `tr1_off_*`;
  - the comparison report contains the development-only label and no `recommended` file is written.
- **Verification:**
  - the live terminal is closed;
  - both builds compile with 0 errors;
  - the off runs match the baseline exactly;
  - the delivered twins match the on runs;
  - conformance reports 0 violations;
  - the charts are written;
  - the `.set` loads with 0 mismatches.

### U6. Status and sync

- **Goal:** the user can see the state and the evidence.
- **Requirements:** R20, R21.
- **Dependencies:** U5.
- **Files:** `docs/PROJECT_STATUS.md`, `README.md` (the input), `CONCEPTS.md` (the trail terms, if missing).
- **Approach:** record the feature (off by default), the development comparison and its limits, and the next step (none approved).
- **Test expectation:** none — documentation.
- **Verification:** the docs agree with `results/trailing_v1/`.

---

## Verification Contract

| Gate | Command / check | Applies to |
|---|---|---|
| Unit tests | `python -m pytest research/tests -q` | U1–U5 |
| Compile | `python research/trail_cli.py install` → 0 errors for both builds, logs in `results/trailing_v1/compile/` | U2, U5 |
| Trail off equals baseline | `tr1_off_{a,b}` deals equal `nv1_f1_oos_baseline_{a,b}` deals, field for field | U5 |
| Build equivalence | `tr1_on_{a,b}` deals equal the delivered twins' deals | U5 |
| Conformance | 0 violations, including `trail_r23`, on all `tr1_` runs | U3, U5 |
| Evidence untouched | `git diff` shows no change to `research/preregistration*.json` or existing `results/` | all |
| Safety | the runner's live-terminal guard; no connected-account action | U5 |
| Public repo | staged diff scanned for the account number | all |

---

## Definition of Done

- U1–U6 are implemented, and all gates in the Verification Contract pass.
- The development comparison exists, labelled development-only, with the fixed-stop audit and the restoration statement.
- The trailing `.set` exists and loads. There is no `recommended.set`.
- The work is merged to `main` with merge commits. No abandoned experimental code is left.
