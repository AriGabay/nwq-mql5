# Fidelity checklist (U5, plan R3/R6/R9/R10/R11)

All runs: isolated portable MT5 copy (separate Wine prefix, `C:\\mt5r`), terminal build 6230, logged into the user's account by the user (tester only; Experts/AutoTrading disabled, built-in MCP disabled). Live terminal closed during runs (runner refuses otherwise).

| Item | Result | Evidence |
|---|---|---|
| R3 tester runs in isolated copy | PASS after user login (account-number-only config was refused: `tester not started because the account is not specified`) | runs/pilot logs (not committed: contain account id) |
| Model=4, every tick based on real ticks | PASS — report "History Quality: 100% real ticks" | `results/fidelity/fidelity_m15_dev_baseline/` |
| Period M15, SignalTF=15 loaded | PASS — report inputs block equals the generated set (0 mismatches) | same |
| Deposit 10,000 USD, leverage 1:100 | PASS — "Initial Deposit: 10 000.00", "Leverage: 1:100" | same |
| Warm-up | PASS — 3000 bars pre-processed | journal_facts.json |
| Real-tick coverage 2025.12.01-2026.07.31 | **LIMITATION** — 24 whole days and 33707 of 235857 minute bars (14.3%) used generated ticks; concentrated in Dec 2025 (22 days) and Jan 2026; Feb-Jul fully real | journal_facts.json |
| Symbol spec | tick 0.01, stops level 20 pts, contract size 100 (derived: 0.33 lot x 3.02 price = 99.66 USD) | regression deals |
| Commission | 0 on XAUUSD.s — matches live-produced tester reports (ASGG 2025/2026: 322 and 156 deals, all commission 0.00); cost is in the real-tick spread (median 0.14-0.24 USD) | results/regression_m5 |
| Swap | applied by tester (non-zero swap on overnight positions) | regression deals |
| R6 M5 regression v1.03 vs v1.04 vs research build | PASS — Jan-Jul 2026, active params (VolumeMultiplier 1.2, SweepToSetupBars 24): 121 deals identical in every field incl. comment; defaults: 1 trade identical; Mar 2026 defaults: 0 trades each | results/regression_m5 |
| Research-build optimization = delivered single runs | PASS — PivL 2/3 passes: -144.74/-142.14, 5 trades each, identical to v1.04 single runs; frames CSV and Custom (daily Sharpe) produced | results/fidelity/optsmoke_* |
| Recovery Factor definition | net profit / max equity drawdown (USD): -2007.52/2320.00 = -0.865 vs reported -0.87 | fixture report |
| Baseline (code defaults) on M15 dev period | 0 trades: 0 — funnel: {'rejected_by_volume': 1465, 'fvg_candidates': 1474, 'orders_sent': 0} (max volume ratio in Mar 2026 was 1.95 < 2.0) | journal_facts.json |
| R14 independence | NOT independent (see independence_check.md) | results/independence_check.md |

Terminal auto-updated from build 6182 to 6230 after login; all research runs used 6230 (recorded per run in the experiment log).
