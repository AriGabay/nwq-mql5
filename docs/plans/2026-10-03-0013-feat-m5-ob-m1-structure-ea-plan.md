---
title: M5 OB + M1 Structure EA - Plan
type: feat
date: 2026-10-03
topic: m5-ob-m1-structure-ea
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-brainstorm
execution: code
---

# M5 OB + M1 Structure EA - Plan

## Goal Capsule

- **Objective:** The user gets an MT5 Expert Advisor that trades their M5 Order Block → M1 structure change → M1 FVG retest with a reaction candle, exactly as they specify, in two structure variants. They also get a pre-registered answer from real Strategy Tester runs: does either variant, or the per-fold choice between them, show a robust out-of-sample edge on XAUUSD.s after costs? The answer is a Hebrew report that does not present the new version as a proven improvement.
- **Means:**
  - The failed OB-FVG retest research moves to its own archive (R1).
  - The new EA and an independent M5/M1 conformance checker run through the existing isolated-tester tooling.
  - The protocol is gated: pilot and real-tester charts, a stop for the user's approval, a commit-frozen pre-registration, WFO on December–July, one non-independent run on August–September, robustness checks, then the report and code review.
- **Product authority:** The user, as strategy owner. The trading rules are R3-R23. Any change to them needs the user's explicit consent, and a change made at the chart gate is recorded with its reason before the freeze.
- **Who finishes:** The executing agent builds, verifies, runs the pilot and stops at the chart gate (R27). After approval it runs the research, commits locally (no remote), writes the Hebrew report and completes code review (R34). Live use stays with the user.
- **Execution profile:** Real MT5 runs are required. Code, tests or commits alone do not complete the research.
- **Stop conditions:**
  - **Hard gate:** stop after the pilot charts (R27) until the user approves that the examples represent the strategy. No freeze or optimization before that.
  - Stop and ask if any step would touch the live terminal or account, or if the isolated copy cannot run the tester.
  - Stop and ask if a rule in R3-R23 proves unimplementable as written.
- **Open blockers:** None.
- **Known ceiling:** Every period from December 2025 to September 2026 was already seen by earlier research, so the best possible deliverable is `candidate.set` plus a forward-test protocol on new data (R33).

---

## Product Contract

### Summary

A new EA marks Order Blocks with their identifying FVG on closed M5 bars, as the approved definition already does. The first touch of an OB starts M1 monitoring. Entry needs a causal M1 structure change in the trade direction, either a higher high alone (variant A) or a higher high plus a confirmed higher low (variant B). After that it needs a new M1 FVG from that move, then a retest of that FVG with a reaction candle. The order is a Market entry after the reaction candle closes, the stop sits beyond the farther structural level, and the target is fixed at 2R. The research compares A, B and a per-fold choice between them under one frozen protocol.

### Problem Frame

The OB-FVG retest research (M15, Limit entries at the FVG or OB) closed as a failure on 2026-10-02. Out of sample over March–July 2026 the procedure lost 1,502 USD and the baseline lost 1,242 USD, and 2 of 10 acceptance criteria passed. Its post-closure diagnostics found that setups resolved TP-before-SL no more often than random entries with the same side, stop distance and 2R target. A 23% fall in gold explained the long/short split. Spread cost was about 1.5% of R.

That points to entry timing carrying no directional information, not to costs or parameters. The user's new version changes the timing logic itself. Entry now waits for evidence on M1 that price has turned at the zone, and then for a candle that reacts at the new FVG. Whether this adds information is the open question. A failed research is an acceptable outcome.

### Key Decisions

- **No time limit at any stage.** OBs wait for their first touch indefinitely, and setups live until R8 or R22 cancels them or a fill ends them. OB age is logged for diagnosis only. Governs R4. (session-settled: user-directed — chosen over carrying the old 96-bar OB age limit, and over an age limit plus a maximum wait after the touch: the user wants no limit carried from the failed version, and only the agreed structural cancellations.)
- **One entry per reaction candle per direction.** Governs R16. (session-settled: user-directed — chosen over every qualifying setup entering independently up to the cap, and over entering once and cancelling the rest: avoids multiplying exposure on one event while keeping the other setups alive.)
- **August–September runs once, after the freeze, as a non-independent historical check.** Governs R31. (session-settled: user-directed — chosen over adding it to the WFO as extra OOS folds, and over not using it: keeps a run-once period that this strategy's selection never saw, comparable with the previous research.)
- **The existing approved OB/FVG definition, in FVG-only mode, with no added filters.** Governs R3. (session-settled: user-directed — chosen over M5 BOS, a liquidity-sweep requirement or a volume filter, and over inheriting the failed candidate's parameters.)
- **A return after an OB break is a renewed touch, not a close inside the zone.** Governs R7. (session-settled: user-directed — chosen over requiring an M1 close back inside the OB: the user's rule is "break once, then come back to the zone".)
- **The entry FVG may complete one bar after the structure break.** Governs R12. (session-settled: user-directed — chosen over requiring all three FVG candles to close by the break bar: the break bar can be the FVG's middle candle.)
- **Opposing structure is one swing sequence that forms after the touch.** Governs R22. (session-settled: user-directed — chosen over cancelling on any lower high plus any lower low seen during the wait: an old pivot confirmed late, or a low from another move, must not combine with a new high.)
- **M1 pivot strength N = 3, fixed before the pilot and not optimized.** On M1, N = 2 marks five-minute wiggles as swings. N = 3 needs a seven-minute extreme and confirms three minutes after it. N = 5 delays confirmation by five minutes and leaves few structures on M1. The value matches the approved definition's swing default (3) and was not chosen from any result. Governs R9. Shown to the user before the pilot and confirmed at the chart gate (R27).
- **Stop buffer = 20 points (0.20 USD), fixed before the pilot.** It equals the broker's measured stops level and the median XAUUSD.s spread. A stop placed one typical spread beyond the level is not triggered by spread noise alone. It does not inherit the old 10-point buffer and is never widened for results. The short-side spread adjustment keeps both sides symmetric in Bid terms. Governs R18. Shown to the user before the pilot and confirmed at the chart gate (R27).
- **The only optimized axis is the structure variant.** With no time limits, entry modes or filters left, the variant is the strategy's sole free choice. N, the buffer and ImpulseWindowBars are frozen constants, and their sensitivity is reported only as a stability check (R32). Governs R29, R30.

### Requirements

**Archive and build**

- R1. The OB-FVG retest research moves intact to its own archive folder with a git tag, and nothing is deleted. The archive covers its EA source, research-code snapshot, results, deliverables, plan and pre-registration. The shared isolated-tester tooling stays in place.
- R2. The new EA is a separate source with a research-logging build. On the same run, the delivered build and the research build produce identical trades.

**M5 zone**

- R3. The identifying FVG and its OB follow the approved definition on closed M5 bars.
  - A bullish FVG needs candle 3's low above candle 1's high, and it is known only when candle 3 closes.
  - The OB is the most recent opposite-colour candle, searched from candle 1 inclusive back ImpulseWindowBars (2) bars. A doji is neither colour. The OB uses the candle's full high-low range.
  - Each candle becomes an OB at most once.
  - An M5 close beyond the OB between the OB candle and candle 3 discards the candidate.
  - Shorts mirror all of this. There is no M5 BOS, sweep or volume condition.
- R4. An OB has no age limit, and a setup has no time limit after its touch. OB age, in M5 bars and minutes from candle 3's close, is recorded at the touch and at entry, for diagnosis only.

**Touch, break and return**

- R5. The touch is the first tick after candle 3 closes at which Bid reaches the OB's near edge (long: Bid ≤ OB high; short: Bid ≥ OB low). Its tick time is recorded, and no later data from that candle is used.
  - A bar that opens inside or beyond the zone touches at its first tick.
  - The touch starts M1 monitoring and is never an entry.
  - Only M1 events after the touch count for the setup.
- R6. A break is an M1 bar after the touch that closes beyond the OB's far edge (long: close < OB low; short: close > OB high).
  - A wick beyond the edge is not a break.
  - A gap counts only through that close.
  - Consecutive closes beyond the edge before a return belong to one break episode.
- R7. A return is the first tick after a break bar closes at which Bid is back inside the zone (long: Bid ≥ OB low; short: Bid ≤ OB high). A renewed touch suffices. Between a break and its return no entry may happen, while structure tracking and R22 continue.
- R8. A second break, meaning an M1 close beyond the far edge after a return, cancels the setup. The one-break allowance neither resets the setup nor overrides R22.

**M1 swings and structure change**

- R9. M1 pivots are causal.
  - A pivot high is a bar whose high is strictly above the highs of the N bars on each side. A pivot low mirrors this.
  - A pivot exists only once bar p+N has closed. Its peak time and its confirmation time are both recorded.
  - Pivots form one alternating sequence. Of two consecutive same-type pivots, only the more extreme is kept; on a tie, the earlier one.
  - Nothing repaints, and no later confirmation justifies an earlier entry.
- R10. A long structure change (HH) is the first M1 close after the touch above the reference high.
  - The reference high is the latest pivot high in the sequence that is confirmed at that bar's close.
  - The move's origin is the bar with the lowest low from the reference high's peak to the HH bar, inclusive.
  - Shorts mirror this: an LL closes below the reference low, and the origin is the highest high.
- R11. Variant A needs only R10. Variant B also needs a confirmed HL: the first pivot low peaking after the HH bar whose low is above the origin low. Shorts mirror this with an LH. The variant is an EA input.

**Entry FVG**

- R12. The entry FVG is an M1 FVG in the trade direction from the structure-changing move.
  - Its candle 1 is at or after the origin bar, and its middle candle is at or before the HH bar. So it is known no later than the close of the bar after the HH bar.
  - When several qualify, the one with the latest candle 3 is used.
  - FVGs from earlier or later moves never qualify. If none qualifies once the bar after the HH bar has closed, this structure change gives no entry, and the setup waits for a new R10 event.
- R13. An M1 close beyond the entry FVG's far edge before an entry ends that FVG and its structure change (long: close below the FVG's lower boundary). The setup then waits for a new R10 event; this is not a cancellation.

**Reaction and entry**

- R14. A long reaction candle is an M1 bar that touches the entry FVG (low ≤ the FVG's upper boundary), closes green and closes above the FVG's upper boundary. A short reaction candle touches (high ≥ lower boundary), closes red and closes below the lower boundary.
  - The reaction must close on a bar strictly after every confirmation the variant needs has closed: the HH bar, the FVG's candle 3, and in variant B the HL's confirmation bar.
  - If the OB was broken, the reaction must also come after the return.
  - A reaction before those points is never used retroactively.
- R15. Entry is a Market order on the first tradeable tick after the reaction candle closes, after re-checking that the setup is still valid and within limits.
  - There is no Limit order and no fill inside the reaction candle.
  - A setup is skipped, with a reason code, when the price at that tick is already beyond the stop or the target, or when the order fails broker stop or freeze levels, the lot step or margin.
- R16. One reaction candle opens at most one new trade per direction.
  - Among competing qualifying setups, the one with the latest touch time wins. Ties go to the later OB candle, then the lower setup id.
  - The others stay active under R8 and R22, and need a new reaction candle to enter.
  - The log records the competitors, the winner and those left waiting.
- R17. A setup trades at most once. A fill ends it, and its OB never trades again.

**Stop, target and size**

- R18. The long stop is the lower of the OB's low and the structure low, minus the 20-point buffer.
  - The structure low is the confirmed HL in variant B.
  - In variant A it is the HL if one is confirmed by entry, otherwise the latest confirmed M1 pivot low.
  - The short stop is the higher of the OB's high and the structure high, plus the buffer, plus the spread at entry.
  - The chosen anchor and the buffer are recorded.
- R19. The target is 2R from the actual fill: fill ± 2 × |fill − stop|, set right after the fill. There is no breakeven, trailing stop or partial exit.
- R20. Size is 1% of the balance at entry over the stop distance, rounded down to the lot step.
  - Below the minimum lot, or without enough margin, the setup is skipped and never clamped.
  - Every skip carries a reason code.
- R21. At most 3 positions are open at once. Deposit 10,000 USD, leverage 1:100.

**Opposing-structure cancellation**

- R22. A long setup is cancelled by one bearish swing sequence after the touch.
  - It starts with a lower high: a pivot high H2 that peaked after the touch, below the preceding pivot high H1 in the sequence.
  - It ends with an M1 close below L1, the pivot low between H1 and H2. That close must come after H2 is confirmed.
  - An M1 close above H2 before that voids this lower high, and tracking restarts from the newest pivots.
  - Pivots that peaked before the touch only serve as comparison levels.
  - Shorts mirror this with a higher low and a close above the pivot high between them.
  - R22 applies in every waiting state, including before the first structure change and between a break and its return. It never applies after a fill.
- R23. Cancellation by R8 or R22 is final for the setup.

**Logging, verification and the chart gate**

- R24. The research build logs, per setup:
  - every stage time: touch tick, breaks, returns, pivots with peak and confirmation times, HH/LL, HL/LH, the FVG's three bars, the reaction candle and the entry tick;
  - prices, the stop anchor, OB age, the reason code and the competing setups;
  - the M5 and M1 bars needed to chart and re-check it.
- R25. An independent M5/M1 conformance checker re-derives every rule from the logged bars. It covers synchronization between the timeframes, no pivot used before its confirmation, first and second breaks with returns, cancellation while waiting, entry only after the reaction close, and R16.
- R26. Charts from real tester runs cover both variants, long and short.
  - They show an OB broken and returned, both kinds of cancellation, winners and losers.
  - M5 and M1 panels sit side by side, with identification, confirmation, entry, stop and target times.
  - Examples are picked by a documented, seeded method.
- R27. Hard gate: the work stops after the pilot and charts. Nothing is frozen or optimized until the user approves that the examples represent the strategy. N and the buffer are shown to the user at that point.

**Research protocol**

- R28. A frequency pilot runs December–July on M5/M1 with both variants at the frozen constants, counting fills only. The timeframes never switch to M15, and no rule changes to reach a frequency. A shortfall against 15 fills per month is reported at the gate.
- R29. After approval, a commit freezes the pre-registration before any optimization: rules, fixed constants, grid, trial budget and acceptance criteria.
- R30. WFO runs on December–July in 5 rolling folds of 3-month train and 1-month OOS (March–July).
  - Each fold chooses the variant on its train window only.
  - The report covers variant A alone, variant B alone and the selection procedure separately, under the same windows, costs and risk.
  - The baseline is the code defaults.
- R31. August–September 2026 runs once, after the candidate freeze, labelled "בדיקה היסטורית לא עצמאית".
  - It is never used to choose parameters or the variant, or to change rules.
  - It is reported apart from the WFO.
  - The report states that the WFO periods were also seen by earlier research.
- R32. Robustness covers:
  - stability: the frozen candidate with N ± 1 and the buffer halved and doubled, reported only, never selected;
  - costs and stress: extra spread, plus slippage on stop exits and on Market entries;
  - Monte Carlo: trade shuffle and block bootstrap;
  - DSR with the pre-registered trial count;
  - the random-entry comparison and MFE/MAE diagnostics from the previous research, as non-gating diagnostics.
  
  Every experiment, failures included, is logged. No search widens automatically when results are poor.
- R33. The deliverables are:
  - the code;
  - `.set` files validated on the delivered build;
  - a comparison table;
  - a Hebrew report;
  - the evidence.
  
  The ceiling is `candidate.set` plus a forward-test protocol on unseen data; `recommended.set` is never produced. Without a robust edge, the report states there is no recommendation and the work stops.
- R34. Code review and fixes are completed even if the research fails.
- R35. Every run goes through the isolated runner and its guards. The live terminal and account are never touched, closed or traded.

### Key Flows

- F1. Long setup to fill (variant B)
  - **Trigger:** An M5 bullish FVG's candle 3 closes, and an OB qualifies (R3).
  - **Steps:**
    1. Bid touches the OB high, and M1 monitoring starts (R5).
    2. An M1 close goes above the reference pivot high (R10), and the entry FVG is fixed by the bar after the break (R12).
    3. A pivot low above the origin is confirmed after the HH (R11).
    4. A later M1 candle touches the FVG and closes green above it (R14).
    5. A Market buy goes in on the next tradeable tick (R15), with the stop per R18 and the target per R19.
  - **Covered by:** R3, R5, R9-R12, R14, R15, R18-R20
- F2. Break, return, then entry or cancellation
  - **Trigger:** After the touch, an M1 close falls below the OB low (R6).
  - **Steps:** Entry is blocked while structure tracking continues. Then one of three things happens:
    - Bid re-enters the zone (R7), and the flow continues as in F1.
    - A second close below the OB low after the return cancels the setup (R8).
    - A completed bearish sequence cancels it at any point (R22).
  - **Covered by:** R6-R8, R22, R23

### Acceptance Examples

- AE1. **Covers R12.** The HH close happens on bar k, and bar k is also the middle candle of a bullish FVG whose candle 3 is bar k+1. When bar k+1 closes, the FVG qualifies as the entry FVG. An FVG whose middle candle is bar k+1 does not qualify.
- AE2. **Covers R6, R7.** After the touch, bar j's low goes below the OB low, but bar j closes inside the zone: no break. Bar m closes below the OB low: a break. A later tick with Bid back at the OB low is the return, even though no bar has closed inside the zone yet.
- AE3. **Covers R22.** Before the touch, pivot high H0 and pivot low L0 exist. After the touch, a pivot low L1 below L0 confirms, then a pivot high H2 below H0 confirms, and later a bar closes below L1. The setup is cancelled. If the close below L1 had come only after a close above H2, nothing is cancelled and tracking restarts.
- AE4. **Covers R22.** Mixing moves is forbidden. A close below an old low L0 happens during one decline, and a lower high forms later in a different swing that never closes below its own preceding low. No cancellation.
- AE5. **Covers R14, R11.** In variant B, a candle touches the FVG and closes green above it at 10:05. The HL is confirmed at 10:07. The 10:05 candle never triggers an entry; only a qualifying candle closing after 10:07 can.
- AE6. **Covers R16.** Two long setups from overlapping OBs, touched at 09:10 and 09:40, both qualify on the same reaction candle. Only the 09:40 setup enters. The 09:10 setup needs a later reaction candle.
- AE7. **Covers R15, R18.** A short's reaction candle closes at Friday 23:54, and the next tradeable tick is Monday 01:00, with Bid already above the stop. The setup is skipped with a reason code, and no order is sent.

### Success Criteria

- The conformance checker reports 0 violations on every pilot, WFO, stability and August–September run, or every violation is explained as a checker artefact and fixed before the result is reported.
- The user approves, at the chart gate, that the examples represent the strategy.
- The Hebrew report answers the research question for A, B and the selection procedure separately. It says "no recommendation" when the pre-registered criteria fail, and never presents the new version as a proven improvement.

### Scope Boundaries

- No OB age limit, wait limit, FVG window or order expiry.
- No M15 or other timeframe switch, and no rule change to reach a frequency.
- No M5 BOS, liquidity sweep or volume filter, and no other filter the user did not request.
- No breakeven, trailing stop, partial exit, or Limit entry.
- No grid widening after results, and no use of August–September for any choice.
- No live trading, no live-terminal control, and no `recommended.set`.
- The volume-filter versions and the M15 OB-FVG retest stay archived. They are not re-run or extended here.

### Dependencies / Assumptions

- The isolated MT5 copy (`C:\mt5r`, build 6231, user-logged-in, English UI) runs M1-based tests with Model 4 real ticks.
- In December 2025–January 2026 about 14.3% of minutes came from generated ticks (`research/run_constants.json`). This affects M1 structure more than it affected M15, so it is reported per fold.
- The broker's stops level (20 points at the last read) is read at run time, not assumed.

### Outstanding Questions

**Deferred to Planning**

- How the acceptance criteria and the selection trade floor adapt to a two-choice grid (KTD12 analogue), fixed in the pre-registration before any optimization.
- The Market-entry slippage value for the stress test, and the retry behaviour when the first tick after the reaction is refused as market-closed.
- How the M5 and M1 logs are laid out so that R25's checker and R26's charts share one source.

### Sources / Research

- Approved OB/FVG definition: `mql5/Experts/ob_fvg_retest.mq5` (NewCandidate) and `docs/plans/2026-09-30-2310-feat-ob-fvg-retest-ea-plan.md` (R5-R7, KTD3).
- The failed research and its diagnostics: `deliverables/report_he.md`, `results/diagnostics/wfo_diagnostics.json`.
- The isolated runner and its guards: `research/mt5r/runner.py`, `research/mt5r/env.py`. Run constants: `research/run_constants.json`.
- The user's four schematic images (M5 context, M1 confirmation variants, FVG reaction entry, setup cancellation). They illustrate order and rules only, not distances, durations or parameters.
