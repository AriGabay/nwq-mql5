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
