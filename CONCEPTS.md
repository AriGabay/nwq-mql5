# Concepts

Shared domain vocabulary for this project — entities, named processes, and status concepts with project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound and ce-compound-refresh process learnings; direct edits are fine. Glossary only, not a spec or catch-all.

## Test environment

### Live terminal
The user's own MetaTrader 5 terminal, connected to a real trading account, which research work must never start, stop, reconfigure, or attach to.
*Avoid:* main terminal, production terminal

Research runs happen only while the live terminal is closed, because running a second terminal through the same macOS MetaTrader application coincided with the live terminal shutting down.

### Isolated copy
A separate, portable MetaTrader 5 installation used only for Strategy Tester research, kept in its own Wine prefix and populated from an allowlist of files taken read-only from the live terminal's data.
*Avoid:* research terminal, sandbox copy

It holds no password written by the pipeline; the Strategy Tester still requires an authenticated account there, which the user supplies by logging in to the isolated copy themselves. It must be trade-safe before every run.

### Trade-safe
The state of the isolated copy in which it cannot place orders or expose trading tools: automated trading disabled, the terminal's built-in MCP server off, and no stored password in its configuration.

## Strategy

### Order Block
The most recent opposite-colored candle at or before the first candle of an Identifying FVG, qualified under the selected mode (the Identifying FVG alone, or together with a break of structure); its zone is that candle's full high-to-low range.
*Avoid:* OB zone, supply/demand zone

### Touch
The first return of price into an active Order Block after it qualified; it starts the search for a confirming FVG but never triggers an entry by itself.

### Identifying FVG
The Fair Value Gap formed by the impulse away from an Order Block; it is how the Order Block is found and qualified, completes before any touch, and never confirms an entry.
*Avoid:* displacement FVG, impulse FVG

### Confirmation FVG
A new Fair Value Gap in the trade direction whose first candle is at or after the touch bar, valid only once its third candle has closed within the allowed window; it marks the reaction to the touch and is the only FVG that can lead to an order.

### Retest
The second return of price, after the confirmation FVG completes, to the pre-chosen entry level in the FVG or the Order Block; the entry fills only on the retest.

### Retired
The state of an Order Block after its single setup was filled, cancelled, expired or skipped; a retired Order Block never starts another setup.
