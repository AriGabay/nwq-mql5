# nwq-mql5 — `new_test` robust parameter research

Research pipeline that optimizes the MT5 EA `new_test` (SweepOB, XAUUSD.s) with a pre-registered
walk-forward protocol in an isolated, credential-free MT5 copy.

- Plan / protocol: `docs/plans/2026-09-30-1722-feat-new-test-robust-optimization-plan.md`
- Original EA and saved profile (byte copies, never edited): `original/`
- EA v1.04 (adds `SignalTF`) and the research logging build: `mql5/Experts/`
- Python pipeline: `research/` (`python -m pytest research/tests`, CLI `python research/cli.py --help`)
- Curated evidence: `results/`; deliverables: `deliverables/`

Safety: the pipeline never touches the live MT5 terminal or account. It reads the live data
directory only to copy cached XAUUSD.s bars/ticks and to scan logs.
