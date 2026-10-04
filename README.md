# nwq-mql5 — M5 OB + M1 structure EA and its research

MT5 Expert Advisor for XAUUSD.s, researched with a gated, pre-registered protocol in an isolated MT5 copy
(Strategy Tester only). Current state, results and next step: [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md).

## Active strategy (`ob_m1_structure`)

1. **M5 zone.** A closed M5 identifying FVG qualifies the most recent opposite-colour candle before it as an
   Order Block (full high-low range, no age limit). The first tick at which Bid reaches the OB's near edge is the
   touch; it starts M1 monitoring and is never an entry. One break (an M1 close beyond the far edge) is allowed if
   price returns into the zone; a second break cancels the setup.
2. **M1 structure.** Causal M1 pivots (N = 3 bars each side, known only once confirmed). A long structure change
   is the first M1 close after the touch above the latest confirmed pivot high (HH); shorts mirror it (LL).
   - **Variant A** (`StructureVariant = 0`): the HH/LL alone is enough.
   - **Variant B** (`StructureVariant = 1`): also needs a confirmed higher low after the HH (lower high after the
     LL for shorts), above the move's origin low; the reaction candle must close after that confirmation.
3. **M1 entry.** The entry FVG is an M1 FVG from the structure-changing move. A reaction candle touches it and
   closes beyond it in the trade direction (long: green, close above its upper boundary). Entry is a Market order on
   the first tradeable tick after the reaction candle closes.
4. **Risk.** Stop beyond the farther of the OB edge and the M1 structure level, plus a 20-point buffer (plus the
   spread for shorts); target 2R from the fill; 1% of balance per trade; at most 3 open positions; no breakeven
   or partial exits. An opposing M1 swing sequence after the touch cancels a waiting setup.
5. **Optional 1R trailing stop** (`EnableTrailingStop`, default **false** = the rules above unchanged).
   - **What R is:** R0 = |fill − original SL|, fixed for the whole trade.
   - **When it starts:** at +1R (Bid ≥ E + R0 for a long, Ask ≤ E − R0 for a short).
   - **How it moves:** the stop trails 1R behind the best Bid (long) or the best Ask (short) since the fill, on every
     tick, and only in the trade's favour.
   - **What stays fixed:** the TP stays 2R.
   - **Status:** development comparison only, in
     [`results/trailing_v1/dev_comparison_he.md`](results/trailing_v1/dev_comparison_he.md); plan
     [`docs/plans/2026-10-05-0007-feat-trailing-stop-1r-plan.md`](docs/plans/2026-10-05-0007-feat-trailing-stop-1r-plan.md).

Full rules: R3–R23 in the plan below. Terms: [`CONCEPTS.md`](CONCEPTS.md).

## Where things are

- Plan and protocol: [`docs/plans/2026-10-03-0013-feat-m5-ob-m1-structure-ea-plan.md`](docs/plans/2026-10-03-0013-feat-m5-ob-m1-structure-ea-plan.md);
  frozen pre-registration: [`research/preregistration.json`](research/preregistration.json)
- EA: [`mql5/Experts/ob_m1_structure.mq5`](mql5/Experts/ob_m1_structure.mq5) and its research-logging build
  `ob_m1_structure_research.mq5`
- Python pipeline: `research/` (`python -m pytest research/tests`, CLI `python research/cli.py --help`);
  run constants `research/run_constants.json`
- Latest research report (Hebrew): [`deliverables/report_he.md`](deliverables/report_he.md); evidence under `results/`
- Learnings: `docs/solutions/`
- Earlier strategies and all of their evidence: `archive/` (SweepOB `new_test`, OB-FVG retest M15, OB-FVG volume 2x)

## Latest result

The research finished on 2026-10-04 and **failed its pre-registered acceptance**: no recommendation and no
`recommended.set`. Out of sample (March–July), the protocol's candidate (variant A, chosen by fallback because neither
variant met the train drawdown limit) lost 468.51 USD with a 31.5% equity drawdown. See `docs/PROJECT_STATUS.md`.

## Setup and safety

Platforms: native Windows (portable copy at `isolated_dir`, e.g. `C:\mt5r`) or macOS (Wine prefix, `wine_dir`);
see `research/config.example.yaml`. On a new machine: `python research/cli.py setup`, then log in once in the
isolated terminal's GUI (`C:\mt5r\terminal64.exe /portable`, user only — the tester needs an account), then `install`.

The pipeline never touches the live MT5 terminal or account, runs only while the live terminal is closed, and keeps
no credentials in git (`research/config.yaml` is gitignored). Raw tester run folders (`runs/`) and tick history
(inside the isolated MT5 copy) are not in the repository; curated evidence is under `results/`.
