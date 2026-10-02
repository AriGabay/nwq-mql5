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
        return json.loads(PREREG_PATH.read_text())
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


def build_prereg(period: str, pilot_runs: int, ea_sha256: str, gate_changes: list) -> dict:
    """R21 record from KTD12 plus the pilot's timeframe. Written once by `cli.py freeze-rules`."""
    folds = [{"fold": 1, "train": ["2025.12.01", "2026.02.28"], "test": ["2026.03.01", "2026.03.31"]},
             {"fold": 2, "train": ["2026.01.01", "2026.03.31"], "test": ["2026.04.01", "2026.04.30"]},
             {"fold": 3, "train": ["2026.02.01", "2026.04.30"], "test": ["2026.05.01", "2026.05.31"]},
             {"fold": 4, "train": ["2026.03.01", "2026.05.31"], "test": ["2026.06.01", "2026.06.30"]},
             {"fold": 5, "train": ["2026.04.01", "2026.06.30"], "test": ["2026.07.01", "2026.07.31"]}]
    n_passes = 1
    for v in GRID.values():
        n_passes *= len(v)
    return {
        "protocol": "docs/plans/2026-09-30-2310-feat-ob-fvg-retest-ea-plan.md (R21, KTD12)",
        "frozen_note": "Committed before the first optimization run. Nothing here changes afterwards.",
        "period": period, "signal_tf_value": ini.PERIODS[period],
        "deposit": RUN["deposit"],
        "baseline": "code defaults (R18) with SignalTF = period and the run_constants risk inputs",
        "folds": folds, "final_train": ["2026.05.01", "2026.07.31"],
        "holdout": list(RUN["windows"]["holdout_non_independent"]), "holdout_independent": False,
        "grid": {k: list(v) for k, v in GRID.items()},
        "grid_ranges": {k: [v[0], v[1] - v[0] if len(v) > 1 else 1, v[-1]] for k, v in GRID.items()},
        "categorical": list(CATEGORICAL),
        "categorical_treatment": "smoothing and neighbours use exact matches on categorical inputs; "
                                 "neighbours move one ordinal step on exactly one other input",
        "defaults": dict(DEFAULTS),
        "passes_per_fold": n_passes,
        "selection": {"trade_floor_per_month": 15, "train_months": 3, "max_equity_dd_pct": 10.0,
                      "score": "recovery factor (net profit / max equity DD), ineligible passes score 0",
                      "smoothing": "mean over the pass and its ordinal neighbours in the same categorical cell",
                      "tie_break": "closest to defaults (categorical mismatch 1, ordinal index steps), then "
                                   "smallest indices",
                      "fallback": "defaults with status no_eligible_pass"},
        "acceptance": {"min_fills_per_month": 15, "days_per_month": 30.44, "bootstrap_alpha": 0.05,
                       "min_positive_folds": 3, "remove_top_events": 2, "mc_breach_prob_max": 0.10,
                       "spread_stress_k": 1.0, "stop_slippage_points": 10,
                       "min_neighbor_profitable_share": 0.60, "dsr_min": 0.90, "dsr_min_days": 60,
                       "holdout_min_fills_per_month": 15},
        "loss_limits": {"daily_loss_pct_of_initial": 5.0, "total_floor": 9000.0, "trailing": False},
        "stats": {"seed": 20260930, "bootstrap_resamples": 10000, "mean_block_days": 5, "mc_paths": 10000,
                  "mc_horizon_days": 252, "dsr_trials": n_passes * len(folds) + int(pilot_runs),
                  "pilot_runs": int(pilot_runs)},
        "ea_source_sha256": ea_sha256,
        "gate_rule_changes": list(gate_changes),
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
