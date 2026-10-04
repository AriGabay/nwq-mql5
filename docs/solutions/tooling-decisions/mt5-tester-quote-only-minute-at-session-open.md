---
title: "MT5 tester: XAUUSD.s quotes from 01:00 but trades from 01:01, so nothing executes in the first minute after a quote gap"
date: 2026-10-04
category: tooling-decisions
module: research/mt5r (conformance checker and isolated MT5 tester)
problem_type: integration_issue
component: tooling
symptoms:
  - "Independent conformance checker flagged 38 exit violations: an M1 bar between fill and exit already reached the SL or TP"
  - "Every flagged bar was the first M1 bar after a quote gap (01:00 server time after the daily break or the weekend)"
  - "Positions survived a 01:00 bar whose Bid range crossed their stop and closed at 01:01 or hours later"
  - "No fills and no exits ever logged in a session-open bar even though ticks arrived in it"
root_cause: logic_error
resolution_type: code_fix
severity: medium
retire_when: "Holds while the XAUUSD.s trade session, as measured in the tester for Bybit-Live-4, starts one minute after its quote session (Mon-Thu quotes 01:00-23:58, trades 01:01-23:58; Fri ends 23:57). Check the symbol's Specification > Sessions in the terminal, or the rl_sessions_<tag>.csv a session_probe run writes; retire or rewrite when the two sessions start together."
related_components:
  - testing_framework
tags:
  - mt5
  - strategy-tester
  - trade-session
  - quote-session
  - conformance-checker
  - xauusd
  - sl-tp-execution
  - retcode-10018
---

# MT5 tester: XAUUSD.s quotes from 01:00 but trades from 01:01, so nothing executes in the first minute after a quote gap

## Problem
An exit check that re-derives SL/TP hits from logged M1 Bid bars assumed a position closes on the first bar whose range reaches its level. On XAUUSD.s the first minute after every quote gap carries quotes but no trading, so levels reached only in that minute do not execute, and the check reported false violations.

## Symptoms
- The SL/TP execution check in the independent conformance checker (`_check_exit`, `research/mt5r/conformance_m1.py:1040`) flagged 38 positions across the two pilot runs (29 in variant A, 9 in B) and one in a March smoke run: "bar ... between the fill and the exit already reaches SL or TP".
- Every flagged bar opened at 01:00 server time, the first bar after the daily break (23:58-01:00) or the weekend.
- In both pilot runs: 0 fills and 0 exits in any first-bar-after-gap, while those bars had ticks (about 280 per minute) and touch events.

## What Didn't Work
- Reading "no exit in that bar" as a tester fault. Absence of execution alone does not show the tester ignores stops; the user rejected that inference and asked for direct evidence of the session limits first.
- Re-checking only with bars: M1 bars carry Bid only, so the short side (stops and targets trigger on Ask) cannot be settled from bars. Tick-level evidence was needed.
- Earlier sessions (session history) never touched the session boundary: the native-Windows move on 2026-10-01 was verified only on a March smoke window, where the checker had no exit rule yet.

## Solution
1. **Evidence from the tester itself.** A diagnostic EA that runs only in the tester (`research/mql5/session_probe.mq5`, refuses outside the tester at line 106) and its driver (`research/session_probe.py`):
   - prints `SymbolInfoSessionQuote` / `SymbolInfoSessionTrade` for every weekday: quotes Mon-Thu 01:00-23:58, Fri 01:00-23:57; trades start 01:01;
   - sends a 0.01-lot Market order on the first tick after every gap > 5 min and on the first tick of the next minute: 169/169 refused with retcode 10018 (market closed) at 01:00, 169/169 filled at 01:01;
   - logs every Bid/Ask tick around each case: all 38 had a correct-side trigger tick in the 01:00 minute (buy: Bid <= SL or Bid >= TP; sell: Ask >= SL or Ask <= TP) and none before the gap;
   - opens a replica of each position (same side, SL, TP) before the gap: the tester closed all 38 at the same millisecond as the EA. 27 closed at the first trigger tick from 01:01; 11 closed later because price came back inside by 01:01 (3 flipped outcome, e.g. SL hit by quotes at 01:00, TP hours later).
2. **Checker change.** The first bar after a quote gap of more than 5 minutes is marked as no-execution (`research/mt5r/conformance_m1.py:252`):

   ```python
   # first bar after a quote gap > 5 min (daily break, weekend): quotes arrive, nothing executes
   self.gap_open = [i > 0 and self.t[i] - self.t[i - 1] > 300 for i in range(self.n)]
   ```

   A level reached only in such bars is reported as unverifiable (`level_in_session_open_bar`, lines 1073-1075), not as a pass and not as a violation, because the later exit tick is not in the bar logs.
3. **Results handling.** The tester results stay as they are. A pre-registered sensitivity (`research/mt5r/session_sensitivity.py`) re-prices each affected position at the first trigger tick of its quote-only minute, as a fixed-trade-list re-pricing with closed-trade drawdown, reported next to the primary figures and never used for a choice.

## Why This Works
The tester applies the symbol's trade session to execution: no order, stop or target fills before 01:01 even though the 01:00 minute has quotes. A check that assumes execution at any quote misreads that minute. Marking the session-open bar as no-execution matches what the tester does, and labelling those cases unverifiable keeps the checker honest: the bars alone cannot show when, or at what price, the position later closed. The probe's replicas show the behaviour belongs to the tester, not to the EA.

## Prevention
- An exit or entry check built from bars must model trading sessions, not only quote gaps. The two can differ, and the tester appears to apply the current session table to all of history (unverified for earlier periods; the tester cannot show past session tables).
- Shorts trigger on Ask. With Bid bars, the checker can only test necessary conditions for shorts (no earlier bar whose Bid high reaches the SL). As written, the sensitivity's short cases come only from that SL branch.
- When an independent checker finds a class of deviations, confirm the cause with tester evidence (sessions, order retcodes, replica positions, tick dumps) before calling it a checker artefact or a tester fault. Keep the evidence (`results/pilot/session_probe/`).
- An EA that retries a market-closed Market order on every tick (`TRADE_RETCODE_MARKET_CLOSED`) sends one rejected order per tick in that minute on a live account. Cap or delay the retry if the EA is ever used outside the tester.

## Related Issues
- `docs/solutions/tooling-decisions/mt5-headless-tester-isolated-portable-copy-beside-live-terminal.md`: how the isolated tester is built and kept trade-safe (environment layer; this doc covers execution semantics). That doc describes the earlier macOS/Wine setup and needs a refresh for native Windows.
- Gate record with the pre-registered handling: `results/pilot/gate_decisions.md`; the per-case table is `results/pilot/session_probe/case_evidence.csv`.
