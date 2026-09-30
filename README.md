# nwq-mql5 — OB-FVG retest EA and its research

MT5 Expert Advisor for the Order Block → return → confirmation FVG → retest strategy on XAUUSD.s,
researched with a gated, pre-registered protocol in an isolated MT5 copy (Strategy Tester only).

- Plan / protocol: `docs/plans/2026-09-30-2310-feat-ob-fvg-retest-ea-plan.md`
- EA and its research-logging build: `mql5/Experts/`
- Python pipeline: `research/` (`python -m pytest research/tests`, CLI `python research/cli.py --help`)
- Run constants: `research/run_constants.json`; frozen protocol (after the chart gate): `research/preregistration.json`
- Previous strategy (`new_test`, SweepOB) and all of its evidence: `archive/2026-09-30-new-test-sweepob/`

Safety: the pipeline never touches the live MT5 terminal or account, runs only while the live terminal
is closed, and keeps no credentials in git.
