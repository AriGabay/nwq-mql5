# Chart-gate decisions (R27), recorded before the protocol freeze

Plan: docs/plans/2026-10-03-0013-feat-m5-ob-m1-structure-ea-plan.md. These decisions were made at the chart gate,
before any optimization or WFO run. U8 copies them into research/preregistration.json. None of them was chosen from
profit or loss.

## User approvals (2026-10-03)

- Entries from different OBs on one structure stay allowed. Each setup enters at most once (R17), each entry needs
  its own new reaction candle (R16), and at most 3 positions are open (R21). Pilot: 0 repeat entries from one
  setup; A had 52 groups (105 fills), B had 26 groups (53 fills), all from distinct OBs.
- M1 pivot strength N = 3 and stop buffer = 20 points are fixed research constants. They are not claimed optimal.
  The buffer (20 points beyond the structural level) is separate from the broker stops level, which the EA reads at
  every entry, measured from the current price. A short's stop and its size both include the entry spread (checked
  for every fill, R20).

## SL/TP levels reached only in the quote-only minute (38 pilot positions)

Evidence (results/pilot/session_probe/; probe EA research/mql5/session_probe.mq5, tester only, no strategy rules):

- Symbol sessions in the tester: quotes Mon-Thu 01:00-23:58 and Fri 01:00-23:57; trade sessions Mon-Thu
  01:01-23:58 and Fri 01:01-23:57 (server time).
- Market order on the first tick after each 01:00 quote gap: refused 169 of 169 (retcode 10018, market closed).
  On the first tick of 01:01: filled 169 of 169. Opens at other times (holidays: 01:01, 01:03, 03:25) were filled.
- All 38 positions had a trigger tick on the correct side in the 01:00 minute (buy: Bid <= SL or Bid >= TP;
  sell: Ask >= SL or Ask <= TP), and none before the gap.
- In 27 cases the tester's exit is the first trigger tick from 01:01 on. In 11 cases price was back inside by 01:01;
  the exit came later at a correct-side trigger tick. In 3 of those, the outcome flipped (A #4403 and B #4403: SL in
  quotes, TP later; A #5014: TP in quotes, SL later).
- A replica of each position (same side, SL and TP; 0.01 lot; opened by the probe before the gap) was closed by the
  tester at the same millisecond as the EA's exit in 38 of 38 cases.

Classification: a trading-session limitation (no trading 01:00-01:01). It is not a checker error: the sides were
re-checked tick by tick. There is no evidence of a tester fault.

Residual uncertainty:
1. The tester applies today's symbol sessions to all of history; the tester cannot show the Dec-Jul sessions.
2. 12 of the 38 cases fall in Dec 2025 or Jan 2026 (December fully generated ticks, January 12.5%).
3. How the live server treats quotes in that minute is outside the tester.

Pre-registered handling:
- Primary results stay exactly as the tester produced them. No trade is removed and no exit is changed.
- Sensitivity (research/mt5r/session_sensitivity.py, tests in research/tests/test_session_sensitivity.py): each
  affected position is re-priced at the first trigger tick of its quote-only minute, at that tick's price. Net and
  closed-balance max drawdown are reported next to the primary result for every WFO OOS fold, the WFO aggregate and
  the August-September check. Affected positions come from the same rule as the checker; their ticks come from a
  probe run over those runs (research/session_probe.py with the run ids).
- Method: this is a re-pricing of a fixed trade list, not a simulation that recomputes the rest of the path. The
  same positions, with the same entries, sizes and SL/TP, stay in the list. Only each affected position's exit time
  and exit price change, to the first trigger tick of its quote-only minute.
- What it does not reproduce: an earlier or different exit would change the balance, and with it the size of every
  later position (R20). It would free or keep a cap slot at other times (R21), which changes which later signals
  would have been taken or skipped (skipped_cap, R16 competition). It would also change swap. None of this is
  re-simulated: later positions keep their actual sizes, entries and cap slots, and swap and commission keep their
  actual values.
- Drawdown: computed on the corrected timeline. Each affected position's result is booked at its re-priced exit time,
  and the balance curve is re-sorted by exit time.
- Equity limits: the drawdown is measured on the closed-trade balance curve, for both the primary and the
  sensitivity figure. It contains no floating (intra-trade) equity, so it is not the tester report's equity drawdown
  and is usually lower. The primary result's official equity drawdown stays the tester's figure. The sensitivity
  states only the change on the closed-balance basis.
- The sensitivity is never used for a choice. If any acceptance verdict differs between primary and sensitivity,
  the report says the conclusion depends on the quote-only minute.
- Pilot size of the effect (R28: no absolute profit fields): A 29 of 1,381 positions, 2 flips, net change -4.31% of
  the deposit, closed max DD +4.18 points; B 9 of 367 positions, 1 flip, -3.52%, +3.44 points.

## Folds trained on generated ticks

Generated-tick share (tester journal per month, results/pilot/tick_coverage.json): December 2025 100%, January 2026
12.48%, February-July 0%.

| Fold | Train | Generated share in train | OOS | Generated share in OOS |
|---|---|---|---|---|
| 1 | Dec-Feb | 39.06% | Mar | 0% |
| 2 | Jan-Mar | 4.15% | Apr | 0% |
| 3 | Feb-Apr | 0% | May | 0% |
| 4 | Mar-May | 0% | Jun | 0% |
| 5 | Apr-Jun | 0% | Jul | 0% |

Pre-registered reporting:
- Group G (train includes generated ticks): folds 1-2. Group R (train fully real): folds 3-5.
- Each fold's report carries its train generated share, its selected variant and its OOS result.
- OOS aggregates are reported for all 5 folds (the planned acceptance basis), for group G and for group R
  separately.
- Acceptance is evaluated as planned on all folds and, for the report only, again on group R alone. If the verdicts
  differ, the report says the conclusion depends on folds trained with generated ticks. A real-tick OOS month does
  not remove the effect of generated-tick training on the variant choice.

## P&L exposure before the freeze

This stage was not free of profit-and-loss exposure. Everything below happened before the protocol freeze, and the
frozen protocol must say so.

| What | Seen by | When |
|---|---|---|
| Net result (after costs) of each chart example, in chart titles and the `net` column of setups_table.md (32 examples per chart set, both variants, both chart versions) | user and agent | chart gate, U7 and the review round |
| Pilot sensitivity effect: net change as % of the deposit and closed-balance drawdown change (A -4.31% / +4.18 points, B -3.52% / +3.44 points) | user and agent | session diagnosis |
| Absolute pilot net and closed-balance max drawdown of both variants (Dec 2025-Jul 2026, the whole WFO window): A -6,051.03 USD and 65.85%, B -190.94 USD and 23.34% | agent; written here for the user | printed once by the first sensitivity computation, before its pilot output was cut to relative figures |
| Net profit and equity drawdown of every tester run (pilot_a/b, smoke, build-equivalence runs, the eight monthly tick-coverage runs of variant A) | recorded in results/experiment_log.* by the runner; not displayed in the conversation | U7 onward |
| Exit timing of 38 positions, including 3 outcome flips (A #4403, B #4403, A #5014) | user and agent | session diagnosis |

Consequences, stated before the freeze:
- The pilot window is the whole WFO window, so every fold's train and OOS months were seen in aggregate, through the
  pilot result of each variant, before the per-fold selection. The WFO results are therefore not blind to the
  researchers, even though the selection rule itself is mechanical (train window only, pre-registered).
- No rule or parameter was changed after this exposure. N = 3 and the 20-point buffer were proposed before the
  pilot and stayed unchanged. The approval of entries from different OBs on one structure kept the rule already
  settled in the brainstorm. The session-limitation handling changes no rule: it adds a sensitivity and reporting
  rules that do not depend on P&L.
- The report will repeat this exposure record next to the WFO results.
