# Concepts

Shared domain vocabulary for this project — entities, named processes, and status concepts with project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound and ce-compound-refresh process learnings; direct edits are fine. Glossary only, not a spec or catch-all.

## Test environment

### Live terminal
The user's own MetaTrader 5 terminal, connected to a real trading account, which research work must never start, stop, reconfigure, or attach to.
*Avoid:* main terminal, production terminal

Research runs happen only while the live terminal is closed, because running a second terminal beside it once coincided with the live terminal shutting down.

### Isolated copy
A separate, portable MetaTrader 5 installation used only for Strategy Tester research, kept apart from the live terminal's installation and data and populated from an allowlist of files taken read-only from the live terminal's data.
*Avoid:* research terminal, sandbox copy

It holds no password written by the pipeline; the Strategy Tester still requires an authenticated account there, which the user supplies by logging in to the isolated copy themselves. It must be trade-safe before every run.

### Trade-safe
The state of the isolated copy in which it cannot place orders or expose trading tools: automated trading disabled, the terminal's built-in MCP server off, and no stored password in its configuration.

### Quote-only minute
The first minute after a break in a symbol's quotes (the daily break or the weekend) in which prices arrive but no order, stop or target executes and no stop modification is accepted, because the symbol's trading session starts a minute after its quote session.

A level reached only during a quote-only minute fills from the first tradeable tick or later, possibly much later if price moves back; a check that re-derives execution from bars treats that bar as no-execution and reports such cases as unverifiable rather than as violations.

## Strategy

The active strategy is the M5 OB + M1 structure EA (`ob_m1_structure`, plan `docs/plans/2026-10-03-0013-feat-m5-ob-m1-structure-ea-plan.md`, rules R3–R23). Earlier strategies (SweepOB, OB-FVG retest with a confirmation FVG and a limit retest) keep their own vocabulary in their `archive/` folders.

### Identifying FVG
The M5 Fair Value Gap formed by the impulse away from an Order Block, known only when its third candle closes; it is how the Order Block is found and never leads to an entry.
*Avoid:* displacement FVG, impulse FVG

### Order Block
The most recent opposite-colored M5 candle within two bars back from the first candle of an Identifying FVG; its zone is that candle's full high-to-low range, it has no age limit, and it starts at most one trade.
*Avoid:* OB zone, supply/demand zone

### Setup
The life of one Order Block from its Touch until a fill or a cancellation; a setup trades at most once.

### Touch
The first tick at which Bid reaches the Order Block's near edge after the Identifying FVG completed; it starts M1 monitoring of the setup and is never an entry.

### Break and return
A break is an M1 close beyond the Order Block's far edge; the return is the first tick back inside the zone after it. One break with a return is allowed, no entry happens between them, and a second break cancels the setup.

### Structure change
The first M1 close after the Touch beyond the latest confirmed M1 pivot in the trade direction: above the pivot high for a long (HH), below the pivot low for a short (LL). M1 pivots are causal: a pivot exists only once the N bars after it have closed.

### Variant A / Variant B
The one rule the research compares, set by the `StructureVariant` input. Variant A needs only the Structure change. Variant B also needs the next confirmed pivot against the move (a higher low after an HH, a lower high after an LL) that holds beyond the move's origin, and the Reaction candle must close after that pivot is confirmed.

### Entry FVG
An M1 Fair Value Gap in the trade direction from the move that made the Structure change; an M1 close beyond its far edge ends it, and the setup then waits for a new Structure change.
*Avoid:* confirmation FVG (the archived strategy's term)

### Reaction candle
An M1 bar that touches the Entry FVG and closes beyond it in the trade direction (long: green, close above the FVG's upper boundary); the Market entry follows on the first tradeable tick after it closes, and one reaction candle opens at most one trade per direction.

### Fixed baseline
A parameter set held constant in every fold of a walk-forward study and run on the same out-of-window months as the selection procedure, so the procedure can be compared with it; in the M5 OB + M1 studies these are variant A and variant B at N = 3 and a 20-point buffer.

### Fallback
The default parameters carried into an out-of-window month when no train pass met the eligibility thresholds (`no_eligible_pass`); it completes the evaluation path and is never a selected or improved candidate.

### Original risk (R0)
The distance between a position's actual fill price and the stop accepted on it at the fill, |E − SL0|. It is fixed for the whole trade: R figures of a run with a trailing stop are measured against it, never against a trailed stop.

### 1R trailing stop
The optional exit change behind `EnableTrailingStop` (default off). From +1R on Bid (long) or Ask (short), the stop follows the best Bid or Ask since the fill at a distance of one Original risk, on every tick and only in the trade's favour; the 2R target does not move. An exit at a stop that had moved is the exit kind `trail`, which is not necessarily a loss.

A rejected stop update is retried only after a fixed wait, and a too-many-requests answer pauses every trail request of the EA for a growing interval, so under rejections the stop on the position can lag the trail rule's level.

### Recovery attempt
A new try of an optimization window that failed verification, under its own attempt ID, run IDs and folders. Earlier attempts' records stay as they are, a verified window is never retried, and the tester cache is not deleted.

### Result provenance
Where an optimization's passes came from: computed fresh, reused from the tester cache, or partial. It is read from the tester's own output and the cache folder's state, never inferred from a new run ID. Only a fresh, complete computation verifies a window.

### Opposing-structure cancellation
One swing sequence against the trade after the Touch (for a long: a lower high, then an M1 close below the pivot low before it) that ends a waiting setup for good.
