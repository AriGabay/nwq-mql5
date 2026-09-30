---
title: "Running the MT5 Strategy Tester headless from an isolated portable copy on macOS, next to a live account-connected terminal"
module: research/mt5r (isolated MT5 tester runner)
date: 2026-09-30
problem_type: tooling_decision
component: tooling
severity: high
applies_when:
  - "Automating MT5 Strategy Tester runs on macOS with the Wine bundled inside MetaTrader 5.app"
  - "A second, isolated portable MT5 copy lives in its own WINEPREFIX on the same Mac as the user's live terminal"
  - "Driving the tester headless via terminal64.exe /portable /config:<ini>"
  - "Trying to run the tester without handing credentials to an agent or script"
symptoms:
  - "Tester journal: tester not started because the account is not specified (no account, Login= without password, or [Common] Login= in the /config ini)"
  - "Live terminal logged terminal stopped due to system shutdown about 7 s after an isolated run exited"
  - "Isolated terminal auto-started its built-in MCP server (trade tools) on 127.0.0.1:22346"
  - "wineserver -w never returns for a prefix with Wine services running"
  - "No report file written after a finished test"
root_cause: incomplete_setup
resolution_type: tooling_addition
related_components:
  - development_workflow
  - testing_framework
tags:
  - mt5
  - strategy-tester
  - wine
  - wineprefix
  - macos
  - headless
  - trade-safety
  - mcp
---

## Context

Paths such as `config/common.ini`, `config/assistant.ini`, `config/accounts.dat`, `config/servers.dat`, `MQL5/Include` and `reports/` are relative to an MT5 install or portable data directory (live: `.../MetaTrader 5/`, isolated: `C:\mt5r`), not to this repository.

The research pipeline runs the MetaTrader 5 Strategy Tester headless on macOS. The user's live terminal is account-connected and runs under the Wine that ships inside `MetaTrader 5.app`. To keep research away from it, the pipeline builds a second, portable MT5 install at `C:\mt5r` inside a separate `WINEPREFIX`. It copies only allowlisted files from the live data directory (binaries, `MQL5/Include`, `config/servers.dat`, symbol specs, and history/ticks for one server and symbol) and writes its own config files (`research/mt5r/env.py:20-33`, `build()` at `research/mt5r/env.py:184-204`). A run launches `wine64 C:\mt5r\terminal64.exe /portable /config:C:\mt5r\runs_ini\<run_id>.ini` with the isolated prefix's environment (`research/mt5r/runner.py:93-96`, `Config.env()` at `research/mt5r/env.py:53-58`).

Several parts of this setup behaved differently than you would expect. This doc records them so the next person does not spend a session finding them again.

## Guidance

### 1. The tester will not run without a logged-in account. Plan for a one-time manual login by the user.

This session tried three ways to run the tester without credentials (observed in the session's tester journal; `results/fidelity.md:7` records the account-number-only case). All three failed with the same tester journal line, `tester not started because the account is not specified`:

1. No account in the config at all.
2. `Login=<n>` with no password in `config/common.ini`.
3. `[Common] Login=` in the `/config` ini passed on the command line.

What worked: the user opened the isolated terminal's GUI once and logged in there. The agent never saw or handled the password. After that login the terminal auto-updated itself from build 6182 to 6230, and every later research run used 6230 (`results/fidelity.md:3`, `results/fidelity.md:7`, `results/fidelity.md:22`). Record the build per run, because the login can silently change the binary you are testing with.

The generated config still carries at most an account number, never a password (`research/mt5r/env.py:126-139`):

```ini
[Common]
Server=<server>
Login=<account number, only if user-approved>
NewsEnable=0
ProxyEnable=0
[Experts]
AllowLiveTrading=0
AllowDllImport=0
Enabled=0
```

`check_allowlist()` treats any `password=`, `certpassword=` or `proxypassword=` line, and any `login=` other than the approved one, as a violation (`research/mt5r/env.py:35`, `research/mt5r/env.py:104-123`; tests at `research/tests/test_env.py:45-52`). `build()` raises if that check finds anything (`research/mt5r/env.py:198-200`). Allowlisting also keeps `config/accounts.dat`, `community.ini`, certificates and `Bases/<server>/trades` out of the copy (`research/tests/test_env.py:24-33`, `research/tests/test_env.py:36-42`).

### 2. Do not run the isolated copy while the live terminal is running

Observed in the live terminal's own journal (not stored in this repo): at 2026-09-30 18:08:36 it logged `terminal stopped due to system shutdown`. That was about 7 s after an isolated run exited. The live account had 0 positions and 0 orders, so nothing was traded. The cause is not proven. The working hypothesis is that both installs share the `MetaTrader 5.app` Wine and app-bundle integration, even though their prefixes are separate. Until someone disproves that, the runner refuses to start while the live terminal is running (`research/mt5r/runner.py:44-47`, `research/mt5r/runner.py:73-74`):

```python
LIVE_EXE_MARKER = "C:\\Program Files\\MetaTrader 5\\terminal64.exe"

def live_terminal_running() -> bool:
    return any(LIVE_EXE_MARKER in l for l in _ps_lines())

# in run():
if live_terminal_running():
    raise RuntimeError("live MT5 terminal is running; research runs only while it is closed")
```

The runner also refuses to start when an isolated instance is already running (`research/mt5r/runner.py:75-77`). It tells the two installs apart by their command lines: `C:\mt5r\` for the isolated copy and `C:\Program Files\MetaTrader 5\` for the live one (`research/mt5r/runner.py:21-22`, `research/mt5r/runner.py:39-41`; tests at `research/tests/test_runner.py:32-55`).

### 3. Turn off the terminal's built-in MCP server in the isolated copy

Observed in the isolated terminal's journal: after its first launch it started its built-in MCP server on `127.0.0.1:22346` without being asked. That server exposes trade tools. `disable_mcp()` rewrites `config/assistant.ini` (UTF-16LE): it turns an `Enable=1` line that directly follows an `[MCP.*]` header into `Enable=0`, blanks every `ApiKey=`, and appends `[MCP.MetaEditor]` and `[MCP.MetaTrader]` with `Enable=0` when they are missing. `assert_trade_safety()` is the backstop for any other shape (`research/mt5r/env.py:172-181`; test at `research/tests/test_env.py:69-76`):

```ini
[MCP.MetaEditor]
Enable=0
[MCP.MetaTrader]
Enable=0
```

Before every launch, `assert_trade_safety()` checks the config again. It refuses to launch unless `common.ini [Experts]` has `AllowLiveTrading=0` and `Enabled=0`, no password key is stored, and every `[MCP.*]` section has `Enable=0` (`research/mt5r/env.py:153-169`). `run()` calls it before it starts the terminal (`research/mt5r/runner.py:78`; tests at `research/tests/test_env.py:84-105` and `research/tests/test_runner.py:58-71`, which checks that `Popen` is never called when the config is unsafe).

### 4. Stop only the isolated prefix, with `WINEPREFIX=<iso> wineserver -k`. Never use `wineserver -w` or a kill by process name.

Observed in this session: `wineserver -w` never returned for a prefix whose Wine services were running, so it cannot be used as a "wait until idle" step. The safe way to stop the isolated prefix is `wineserver -k` with the isolated environment. The live terminal has its own `wineserver` process. A kill by name (`pkill`, `killall`) could hit that process, so the runner never targets anything by name (`research/mt5r/runner.py:50-57`):

```python
def shutdown_prefix(cfg):
    envmod.assert_isolated(cfg)   # the isolated prefix must not be, contain, or sit inside the live one
    subprocess.run([str(cfg.wine_dir / "bin" / "wineserver"), "-k"], env=cfg.env(), timeout=60)
    # then poll isolated_processes() for up to 30 s
```

`cfg.env()` sets `WINEPREFIX` to the isolated prefix (`research/mt5r/env.py:53-58`). `assert_isolated()` rejects any overlap between the isolated and live prefixes (`research/mt5r/env.py:71-75`). The tests check the rules above directly against the runner's source: no `pkill`, `killall` or `wineboot` (`research/tests/test_runner.py:9-11`), every `wineserver` subprocess call passes `cfg.env()` (`research/tests/test_runner.py:14-16`), and `shutdown_prefix` runs nothing when the prefixes overlap (`research/tests/test_runner.py:19-29`). After the terminal exits, the runner waits up to 120 s until no `C:\mt5r\` process remains, because the terminal can hand work to child processes (`research/mt5r/runner.py:103-106`).

### 5. Details that make headless runs produce output

- **The reports folder must already exist.** The terminal saves the report only if `reports/` exists in the portable data directory. The runner creates it and removes stale reports for the same run id before launch (`research/mt5r/runner.py:86-88`).
- **`/config` ini files and `config/*.ini` are UTF-16LE with a BOM.** They are written through `write_utf16()` (`research/mt5r/textio.py:15-18`), and `read_text()` detects the BOM when reading (`research/mt5r/textio.py:8-12`).
- **`ShutdownTerminal=1` makes the terminal exit when the test finishes.** That exit is what lets `proc.wait()` return. The tester ini also sets `ReplaceReport=1` and a `Report=` path (`research/mt5r/ini.py:41-43`, `research/mt5r/runner.py:98-102`).

## Why This Matters

The isolated copy runs on the same machine as a live, account-connected terminal. Once the user logs the copy in (which the tester requires), the copy is also connected to the account. If it were misconfigured it could trade: through Experts or AutoTrading, or through the MCP server that starts on its own. A carelessly scoped Wine command such as a name-based kill, a `wineboot` on the wrong prefix, or a hanging `wineserver -w` could also take the live terminal down or stall the pipeline. The one unexplained live-terminal stop in this session was harmless only because there were no open positions or pending orders. The guards turn each of these facts into a check that fails closed before launch, instead of a rule someone has to remember. One gap: a missing `assistant.ini` skips the MCP part of the trade-safety check (`research/mt5r/env.py:164`); it is closed in practice because `build()` always writes that file.

## When to Apply

- Any automation that launches `terminal64.exe` or `metatester64.exe` under the Wine that ships in `MetaTrader 5.app` on a Mac that also runs a live terminal.
- Adding a new launch path, a new cleanup or timeout path, or a new config file to `research/mt5r`: keep the `run()` preflight order (disk, live terminal, busy isolated copy, trade safety) and route every Wine call through `cfg.env()`.
- When a tester run produces no report: check that `reports/` exists, that the ini is UTF-16LE, and that the journal does not contain `account is not specified`.
- Re-test whether the live-terminal guard is still needed only as a deliberate experiment with a flat live account. Do not drop it because the cause is unproven.

## Examples

A preflight order that fails closed (`research/mt5r/runner.py:71-78`):

```python
if _free_gb(cfg.mt5_dir) < MIN_FREE_GB: raise RuntimeError(...)
if live_terminal_running():            raise RuntimeError("live MT5 terminal is running; ...")
if isolated_processes():               raise RuntimeError("isolated terminal already running: ...")
envmod.assert_trade_safety(cfg)        # AllowLiveTrading=0, Enabled=0, no password, MCP Enable=0
```

The launch command, which runs only the isolated install, in portable mode:

```text
WINEPREFIX=<isolated prefix> WINEDEBUG=-all \
  <wine_dir>/bin/wine64 'C:\mt5r\terminal64.exe' /portable '/config:C:\mt5r\runs_ini\<run_id>.ini'
```

The part of the tester ini that controls headless behaviour (UTF-16LE on disk):

```ini
[Tester]
...
Report=<report path>
ReplaceReport=1
ShutdownTerminal=1
```

Stopping only the isolated prefix:

```sh
WINEPREFIX=<isolated prefix> <wine_dir>/bin/wineserver -k   # never: wineserver -w, pkill, killall
```
