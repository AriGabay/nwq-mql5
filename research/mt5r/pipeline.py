"""High-level run helpers shared by the CLI steps.

KTD9: fixed run facts come from research/run_constants.json (loaded at import); the frozen protocol
research/preregistration.json is created only at U7, so it is read lazily and may be absent.
"""
import json
import pathlib

from . import explog, ini, m1_contract, reports, runner, setfile

REPO = pathlib.Path(__file__).resolve().parents[2]
RUN = json.loads((REPO / "research" / "run_constants.json").read_text())
PREREG_PATH = REPO / "research" / "preregistration.json"
EA_SRC = REPO / "mql5" / "Experts" / m1_contract.EA_SOURCE

BUILDS = {
    # kind: (ex5 name, source for input specs, research inputs?)
    "delivered": (m1_contract.EA_SOURCE.replace(".mq5", ".ex5"), EA_SRC, False),
    "research": (m1_contract.EA_RESEARCH_SOURCE.replace(".mq5", ".ex5"), EA_SRC, True),
}

# KTD14, fixed before data: the structure variant is the only optimized input; A (0) is the default and baseline.
GRID = {"StructureVariant": [0, 1]}
DEFAULTS = {"StructureVariant": 0}
CATEGORICAL = ["StructureVariant"]


def prereg():
    """The frozen pre-registration, or None before U7."""
    try:
        return json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def grid() -> dict:
    p = prereg()
    return {k: list(v) for k, v in (p["grid"] if p else GRID).items()}


def params() -> list:
    return list(grid())


def defaults() -> dict:
    p = prereg()
    return dict(p["defaults"] if p else DEFAULTS)


def categorical() -> list:
    p = prereg()
    return list(p["categorical"] if p else CATEGORICAL)


FOLDS = [{"fold": 1, "train": ["2025.12.01", "2026.02.28"], "test": ["2026.03.01", "2026.03.31"]},
         {"fold": 2, "train": ["2026.01.01", "2026.03.31"], "test": ["2026.04.01", "2026.04.30"]},
         {"fold": 3, "train": ["2026.02.01", "2026.04.30"], "test": ["2026.05.01", "2026.05.31"]},
         {"fold": 4, "train": ["2026.03.01", "2026.05.31"], "test": ["2026.06.01", "2026.06.30"]},
         {"fold": 5, "train": ["2026.04.01", "2026.06.30"], "test": ["2026.07.01", "2026.07.31"]}]
# KTD15: one frozen constant moved at a time on the candidate; reported, never selected
STABILITY = [{"SwingStrengthM1": 2}, {"SwingStrengthM1": 4}, {"StopBufferPoints": 10}, {"StopBufferPoints": 40}]


def build_prereg(pilot_runs: list, ea_sha256: str, gate: dict) -> dict:
    """R29 record (KTD14, KTD15 and the chart-gate decisions). Written once by `cli.py freeze-rules`.

    pilot_runs: ids of this strategy's pilot runs (DSR trials); gate: the chart-gate record
    (results/pilot/gate_decisions.md and the facts it rests on)."""
    n_passes = 1
    for v in GRID.values():
        n_passes *= len(v)
    fixed = {name: default for _, name, default in m1_contract.INPUTS}
    return {
        "protocol": "docs/plans/2026-10-03-0013-feat-m5-ob-m1-structure-ea-plan.md (R29, KTD14, KTD15)",
        "frozen_note": "Committed before the first optimization run. Nothing here changes afterwards.",
        "strategy": "M5 OB + M1 structure change + M1 FVG retest with a reaction candle (ob_m1_structure.mq5)",
        "chart_period": m1_contract.CHART_PERIOD, "zone_period": m1_contract.ZONE_PERIOD,
        "deposit": RUN["deposit"],
        "fixed_constants": {k: fixed[k] for k in ("ImpulseWindowBars", "SwingStrengthM1", "StopBufferPoints",
                                                  "RiskRR", "RiskPercent", "MaxExposures", "WarmupDays")},
        "fixed_constants_note": "N = SwingStrengthM1 = 3 and the 20-point buffer are fixed research constants, "
                                "approved at the chart gate, not claimed optimal",
        "baseline": "variant A (StructureVariant = 0, the code default) with the run_constants risk inputs",
        "series": {"procedure": "per fold, the variant selected on that fold's train window",
                   "fixed_a": "variant A in every fold (the baseline)", "fixed_b": "variant B in every fold"},
        "folds": FOLDS, "final_train": ["2026.05.01", "2026.07.31"],
        "holdout": list(RUN["windows"]["holdout_non_independent"]), "holdout_independent": False,
        "holdout_label": "בדיקה היסטורית לא עצמאית",
        "holdout_rules": "runs once, after the candidate freeze, for variant A and variant B (the candidate is "
                         "the frozen one of them); reported apart from the WFO; never used to choose parameters, "
                         "the variant or rules; not an acceptance criterion (R31)",
        "grid": {k: list(v) for k, v in GRID.items()},
        "grid_ranges": {k: [v[0], v[1] - v[0] if len(v) > 1 else 1, v[-1]] for k, v in GRID.items()},
        "categorical": list(CATEGORICAL),
        "defaults": dict(DEFAULTS),
        "passes_per_fold": n_passes,
        "selection": {"trade_floor_per_month": 15, "train_months": 3, "max_equity_dd_pct": 10.0,
                      "drawdown_metric": "the tester's maximum equity drawdown of the train pass",
                      "score": "recovery factor (net profit / max equity DD), ineligible passes score 0",
                      "tie_break": "variant A (the default)",
                      "fallback": "variant A with status no_eligible_pass"},
        "acceptance": {"gating_series": "procedure",
                       "reported_series": ["fixed_a", "fixed_b"],
                       "min_fills_per_month": 15, "days_per_month": 30.44, "bootstrap_alpha": 0.05,
                       "min_positive_folds": 3, "positive_folds_rule": "strict majority of the folds evaluated "
                                                                       "(3 of 5; 2 of 3 for the group R report)",
                       "remove_top_events": 2, "event_bar": "M1",
                       "mc_breach_prob_max": 0.10, "spread_stress_k": 1.0, "stop_slippage_points": 10,
                       "entry_slippage_points": 10, "min_stability_profitable_share": 0.60,
                       "dsr_min": 0.90, "dsr_min_days": 60,
                       "net_vs_baseline": "the procedure's OOS net must exceed fixed A's",
                       "risk_metric": "loss limits on the daily equity records (eq_min) and MC on daily equity; "
                                      "the train selection uses the tester's maximum equity drawdown"},
        "stability": {"runs": STABILITY, "window": ["2026.03.01", "2026.07.31"], "applies_to": "candidate",
                      "criterion": "share of the 4 perturbations with net > 0 >= 0.60: an acceptance criterion "
                                   "of the candidate; the runs are never used for selection (KTD15)"},
        "loss_limits": {"daily_loss_pct_of_initial": 5.0, "total_floor": 9000.0, "trailing": False},
        "stats": {"seed": 20260930, "bootstrap_resamples": 10000, "mean_block_days": 5, "mc_paths": 10000,
                  "mc_horizon_days": 252, "dsr_trials": n_passes * len(FOLDS) + len(pilot_runs),
                  "dsr_trials_note": "2 variants x 5 folds + this strategy's pilot runs; the final-train selection, "
                                     "the tick-coverage runs (variant A, single months) and the session probe are "
                                     "not separate trials",
                  "pilot_runs": list(pilot_runs)},
        "reporting": gate,
        "ea_source_sha256": ea_sha256,
        "gate_rule_changes": [],
    }


GATE_RECORD = {
    "decisions": "results/pilot/gate_decisions.md",
    "chart_gate": "approved by the user (2026-10-04): the 8 charts in results/pilot/gate_package match the agreed "
                  "rules; the approval covers the examples and the start of the research only",
    "shared_structure": "entries from different OBs on one structure stay allowed (R16, R17, R21 unchanged)",
    "both_variants": "A and B both stay; B is not chosen, no parameter changes and no grid widening because of the "
                     "exposed pilot results",
    "drawdowns": "every result reports the closed-trade balance drawdown and the tester's equity drawdown apart",
    "session_minute": {"finding": "quotes 01:00, trade session 01:01: SL/TP reached only in the quote-only minute "
                                  "execute from 01:01 (results/pilot/session_probe)",
                       "primary": "tester results unchanged",
                       "sensitivity": "research/mt5r/session_sensitivity.py: re-pricing of a fixed trade list at the "
                                      "first trigger tick of the quote-only minute; balance drawdown on the "
                                      "corrected timeline, closed trades only; later sizes, cap slots and signals "
                                      "are not re-simulated; reported for every OOS fold, the WFO aggregate and "
                                      "August-September; never used for a choice"},
    "generated_ticks": {"months": {"2025.12": 100.0, "2026.01": 12.48}, "folds_group_G": [1, 2],
                        "folds_group_R": [3, 4, 5],
                        "rule": "OOS aggregates for all folds, group G and group R; acceptance also evaluated on "
                                "group R alone for the report; a difference is stated"},
    "pnl_exposure": "results/pilot/gate_decisions.md#pl-exposure-before-the-freeze: pilot net and drawdown of both "
                    "variants were seen before this freeze; the WFO results are not blind to the researchers",
}


def specs(kind: str) -> list:
    ex5, src, research = BUILDS[kind]
    return setfile.parse_inputs(src.read_text(), research=research)


def base_values(kind: str) -> dict:
    """Fixed run inputs (run_constants risk + display inputs); kind kept for callers. The EA runs on the M1
    chart and has no timeframe input: the chart period goes to the ini only."""
    vals = dict(RUN["display_inputs"])
    vals.update(RUN["risk_inputs"])
    return vals


def run_single(cfg, run_id: str, kind: str, period: str, start: str, end: str, deposit: float = None,
               overrides: dict = None, execution_mode: int = 0, role: str = "", purpose: str = "",
               timeout: int = 7200, log_extra: dict = None):
    """One single test; returns (RunResult, parsed report or None). Every run is logged (R28)."""
    deposit = RUN["deposit"] if deposit is None else deposit
    vals, lines, ex5 = _prepare(run_id, kind, period, overrides)
    text = _ini(ex5, period, start, end, deposit, run_id, lines, execution_mode=execution_mode)
    res = runner.run(cfg, run_id, text, ex5, timeout=timeout, meta={"kind": kind, "role": role, "values": vals})
    rep = reports.parse_html(res.report) if res.report else None
    entry = {"id": run_id, "purpose": purpose, "role": role, "expert": ex5, "period": period, "from": start,
             "to": end, "deposit": deposit, "status": res.status, "seconds": res.seconds,
             "overrides": overrides or {}, "execution_mode": execution_mode, "has_report": rep is not None}
    entry.update(log_extra or {})
    if rep:
        s = reports.summary(rep)
        entry.update({"net_profit": s["net_profit"], "trades": s["trades"], "equity_dd_pct": s["equity_dd_pct"],
                      "build": rep["header"].get("Build"), "history_quality": rep["header"].get("History Quality")})
    explog.append(entry)
    return res, rep


def _ini(ex5, period, start, end, deposit, run_id, lines, **kw) -> str:
    return ini.render(expert=ex5, symbol=RUN["symbol"], period=period, from_date=start, to_date_inclusive=end,
                      deposit=deposit, report=f"reports\\{run_id}", set_lines=lines, leverage=RUN["leverage"],
                      currency=RUN["currency"], model=RUN["model"], **kw)


def _prepare(run_id: str, kind: str, period: str, overrides: dict = None, ranges: dict = None):
    """Input values, [TesterInputs] lines and ex5 name for one run."""
    vals = base_values(kind)
    vals.update(overrides or {})
    if kind == "research":
        vals["ResearchRunTag"] = run_id
    return vals, setfile.render_lines(specs(kind), vals, ranges), BUILDS[kind][0]


def check_inputs_loaded(rep: dict, expected: dict) -> list:
    """Mismatches between the report's input block and the intended values (R26)."""
    bad = []
    for k, v in expected.items():
        got = rep["inputs"].get(k)
        if got is None or not _same(got, v):
            bad.append((k, v, got))
    return bad


def _same(a, b) -> bool:
    a, b = str(a).strip().lower(), str(b).strip().lower()
    if a == b:
        return True
    try:
        return abs(float(a) - float(b)) < 1e-9
    except ValueError:
        return False


def run_optimization(cfg, run_id: str, kind: str, period: str, start: str, end: str, fixed: dict,
                     ranges: dict, deposit: float = None, role: str = "", purpose: str = "",
                     timeout: int = 14400):
    """Complete (Optimization=1) run; returns (RunResult, passes DataFrame or None)."""
    deposit = RUN["deposit"] if deposit is None else deposit
    vals, lines, ex5 = _prepare(run_id, kind, period, fixed, ranges)
    text = _ini(ex5, period, start, end, deposit, run_id, lines, optimization=1)
    res = runner.run(cfg, run_id, text, ex5, timeout=timeout,
                     meta={"kind": kind, "role": role, "values": vals, "ranges": ranges})
    df = reports.parse_opt_xml(res.report) if res.report and res.report.suffix == ".xml" else None
    explog.append({"id": run_id, "purpose": purpose, "role": role, "expert": ex5, "period": period, "from": start,
                   "to": end, "deposit": deposit, "status": res.status if df is not None else "failed",
                   "seconds": res.seconds, "fixed": fixed, "ranges": ranges, "has_report": df is not None,
                   "passes": None if df is None else len(df)})
    return res, df
