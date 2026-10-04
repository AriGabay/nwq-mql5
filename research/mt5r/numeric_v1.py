"""The numeric_v1 study: a bounded train-only selection over StructureVariant x SwingStrengthM1 x StopBufferPoints
(plan docs/plans/2026-10-04-1851-feat-ob-m1-numeric-grid-research-plan.md, KTD1-KTD13).

Everything that identifies this study lives here: the grid, defaults, fixed baselines, folds, windows, paths,
run-ID prefix, the guards and the pre-registration builder. The previous research (research/preregistration.json,
results/wfo, deliverables/*.set) is never read for decisions nor written by this module.
"""
import datetime as dt
import itertools
import json
import pathlib

from . import wfo

REPO = pathlib.Path(__file__).resolve().parents[2]
STUDY = "numeric_v1"
PREFIX = "nv1_"
PLAN = "docs/plans/2026-10-04-1851-feat-ob-m1-numeric-grid-research-plan.md"
PREREG_REL = "research/preregistration_numeric_v1.json"
PREREG_PATH = REPO / PREREG_REL
RESULTS = REPO / "results" / STUDY
DELIV = REPO / "deliverables" / STUDY
LOG = RESULTS / "experiment_log.jsonl"
OLD_PREREG_REL = "research/preregistration.json"

# R5, R6: the approved grid and the fixed inputs (session-settled)
GRID = {"StructureVariant": [0, 1], "SwingStrengthM1": [2, 3, 4], "StopBufferPoints": [10, 20, 40]}
AXES = list(GRID)
CATEGORICAL = ["StructureVariant"]
DEFAULTS = {"StructureVariant": 0, "SwingStrengthM1": 3, "StopBufferPoints": 20}
BASELINES = {"baseline_a": {"StructureVariant": 0, "SwingStrengthM1": 3, "StopBufferPoints": 20},
             "baseline_b": {"StructureVariant": 1, "SwingStrengthM1": 3, "StopBufferPoints": 20}}
FIXED_INPUTS = {"ImpulseWindowBars": 2, "RiskRR": 2.0, "RiskPercent": 1.0, "MaxExposures": 3, "WarmupDays": 30}
DEPOSIT, LEVERAGE = 10000, "1:100"

# R12: the previous research's folds and final window, copied as literals so an edit there cannot move them
FOLDS = [{"fold": 1, "train": ["2025.12.01", "2026.02.28"], "test": ["2026.03.01", "2026.03.31"]},
         {"fold": 2, "train": ["2026.01.01", "2026.03.31"], "test": ["2026.04.01", "2026.04.30"]},
         {"fold": 3, "train": ["2026.02.01", "2026.04.30"], "test": ["2026.05.01", "2026.05.31"]},
         {"fold": 4, "train": ["2026.03.01", "2026.05.31"], "test": ["2026.06.01", "2026.06.30"]},
         {"fold": 5, "train": ["2026.04.01", "2026.06.30"], "test": ["2026.07.01", "2026.07.31"]}]
FINAL_TRAIN = ["2026.05.01", "2026.07.31"]
EXCLUDED_WINDOW = ["2026.08.01", "2026.09.29"]          # KTD12: August-September, never run here
SMOKE_WINDOW = ["2026.02.02", "2026.02.06"]             # KTD11: train-only, real ticks, no fold's OOS
STABILITY_WINDOW = ["2026.03.01", "2026.07.31"]         # KTD8
VALIDATION_WINDOW = ["2026.03.01", "2026.03.31"]        # KTD13
GROUPS = {"G": [1, 2], "R": [3, 4, 5]}                  # folds whose train has generated ticks / real only
TRADE_FLOOR_PER_MONTH, TRAIN_MONTHS, MAX_TRAIN_EQ_DD_PCT = 15, 3, 10.0
BEHAVIOUR_RUNS = [{"StructureVariant": 0, "SwingStrengthM1": n, "StopBufferPoints": b}
                  for n, b in ((3, 20), (2, 20), (4, 20), (3, 10), (3, 40))]

# KTD9: DSR trial count, every evaluation of a configuration of this strategy on December-July
PREVIOUS_TRIALS = {"previous_train_passes": 12, "previous_pilot_runs": 2, "previous_stability_runs": 4}


# ------------------------------------------------------------------ grid
def combos(grid: dict = None) -> list:
    """Every grid tuple as a dict, in axis order."""
    grid = grid or GRID
    return [dict(zip(grid, v)) for v in itertools.product(*grid.values())]


def n_combos(grid: dict = None) -> int:
    return len(combos(grid))


def _segments(values: list) -> list:
    """Split sorted axis values into maximal runs with one constant step; a single value is its own segment."""
    segs, cur = [], [values[0]]
    for v in values[1:]:
        if len(cur) == 1 or v - cur[-1] == cur[1] - cur[0]:
            cur.append(v)
        else:
            segs.append(cur)
            cur = [v]
    segs.append(cur)
    return segs


def split_runs(grid: dict = None) -> list:
    """KTD3: the optimizations that together cover the grid exactly once with MT5 start/step/stop ranges.

    Returns [{"part", "ranges": {name: (start, step, stop)}, "fixed": {name: value}, "expected": n}]. An axis
    whose values are not evenly spaced is cut into evenly spaced segments; a one-value segment is a fixed input."""
    grid = grid or GRID
    per_axis = {k: _segments(sorted(v)) for k, v in grid.items()}
    runs = []
    for choice in itertools.product(*per_axis.values()):
        ranges, fixed, n = {}, {}, 1
        for name, seg in zip(per_axis, choice):
            if len(seg) == 1:
                fixed[name] = seg[0]
            else:
                ranges[name] = (seg[0], seg[1] - seg[0], seg[-1])
                n *= len(seg)
        runs.append({"ranges": ranges, "fixed": fixed, "expected": n})
    names = ["lo", "hi"] if len(runs) == 2 else [f"p{i}" for i in range(len(runs))]
    for r, name in zip(runs, names):
        r["part"] = name
    return runs


def tuple_of(params: dict) -> tuple:
    return tuple(int(params[a]) for a in AXES)


def series_name(params: dict) -> str:
    """KTD7: a series is named by its full parameter tuple; only an identical tuple is a fixed baseline."""
    for name, base in BASELINES.items():
        if tuple_of(params) == tuple_of(base):
            return name
    return "candidate_" + "_".join(str(v) for v in tuple_of(params))


def neighbours(params: dict) -> list:
    """KTD8: one grid step on an ordinal axis around the candidate, inside the grid, never the candidate itself."""
    out = wfo.neighbors({a: int(params[a]) for a in AXES}, GRID, categorical=CATEGORICAL)
    return [n for n in out if tuple_of(n) != tuple_of(params)]


# ------------------------------------------------------------------ selection
def trade_floor() -> int:
    return wfo.trade_floor(TRAIN_MONTHS, TRADE_FLOOR_PER_MONTH)


def select(grid_df, floor: int = None, dd_max: float = MAX_TRAIN_EQ_DD_PCT) -> dict:
    """KTD5/KTD6: wfo.select on the 18-tuple grid plus the reason for the outcome."""
    floor = trade_floor() if floor is None else floor
    sel = wfo.select(grid_df, AXES, DEFAULTS, floor, dd_max, categorical=CATEGORICAL)
    sc = sel["scored"]
    params = {k: int(v) for k, v in sel["params"].items()}
    if sel["status"] == "no_eligible_pass":
        reason = {"eligible_passes": 0, "below_trade_floor": int((sc["trades"] < floor).sum()),
                  "above_dd_limit": int((sc["eq_dd_pct"] > dd_max).sum()),
                  "note": f"no pass met trades >= {floor} and tester equity DD <= {dd_max}%; the defaults are carried "
                          "forward as a fallback, not as a selected candidate"}
        label = "fallback"
    else:
        top = sc[sc["eligible"]].sort_values("smoothed", ascending=False)
        best = float(top["smoothed"].iloc[0])
        tied = int((abs(top["smoothed"] - best) <= abs(best) * 1e-12).sum())
        reason = {"eligible_passes": int(sc["eligible"].sum()), "smoothed_score": best, "tied_at_top": tied,
                  "note": "highest neighbour-smoothed recovery factor among eligible passes"
                          + ("; tie broken by distance to the defaults, then lowest axis indices" if tied > 1 else "")}
        label = "selected"
    return {"status": sel["status"], "label": label, "params": params, "reason": reason, "scored": sc}


# ------------------------------------------------------------------ guards
def _date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y.%m.%d").date()


def check_window(start: str, end: str) -> None:
    """KTD12: no run of this study may touch August-September 2026 (dates compared parsed, CLI dot format)."""
    a, b = _date(start), _date(end)
    lo, hi = _date(EXCLUDED_WINDOW[0]), _date(EXCLUDED_WINDOW[1])
    if a <= hi and b >= lo:
        raise SystemExit(f"{start}-{end} overlaps the excluded window {EXCLUDED_WINDOW[0]}-{EXCLUDED_WINDOW[1]} "
                         "(R23, KTD12)")


def run_id(name: str) -> str:
    """KTD2: every run ID and probe tag of this study carries the prefix (the runner deletes runs/<id>), and is a
    single path component."""
    if "/" in name or "\\" in name:
        raise ValueError(f"bad run id {name!r}")
    return name if name.startswith(PREFIX) else PREFIX + name


# ------------------------------------------------------------------ pre-registration
def dsr_trials() -> dict:
    windows = len(FOLDS) + 1
    this = n_combos() * windows
    total = this + sum(PREVIOUS_TRIALS.values())
    return {"dsr_trials": total, "dsr_trials_sensitivity": n_combos(),
            "dsr_trials_components": {"this_research_train_passes": this, **PREVIOUS_TRIALS},
            "dsr_trials_note": "every evaluation of a configuration of this strategy on December-July: this "
                               f"research's {this} train passes ({n_combos()} x {windows} windows), the previous "
                               "research's 12 train passes, 2 pilot runs and 4 stability perturbations. Excluded: "
                               "the previous OOS runs, the August-September runs, the tick-coverage runs and the "
                               "session probe (re-runs of counted configurations, or not strategy evaluations). "
                               "Trials share data and overlapping configurations, so the effective number is lower; "
                               "126 overstates it, which makes the DSR stricter. Sensitivity: the 18 distinct "
                               "configurations.",
            "var_sr": "evaluate.trial_sharpe_variance over this research's six merged scored_grid.csv files: the "
                      "per-window sample variance of the OnTester daily Sharpe (Custom column) over passes with "
                      "trades, averaged over the six windows"}


def run_budget() -> dict:
    runs = split_runs()
    windows = len(FOLDS) + 1
    return {"train_passes": n_combos() * windows, "train_optimization_runs": len(runs) * windows,
            "smoke_optimization_runs": len(runs), "smoke_passes": n_combos(),
            "behaviour_single_runs": len(BEHAVIOUR_RUNS),
            "oos_single_runs": len(FOLDS) * 3,
            "robustness_single_runs": "3 to 5 (the static candidate or fallback plus 2-4 neighbours, KTD8)",
            "session_probe_runs": 1,
            "delivery_validation_runs": "4 on the delivered build plus at most 1 research-build twin (KTD13)"}


def build_prereg(ea_sha256: str, previous_prereg: dict, evidence: dict) -> dict:
    """R16: written once by `numeric_cli.py freeze-rules-nv1`, committed and pushed before any research run.

    previous_prereg: research/preregistration.json, read only to carry its acceptance thresholds and statistics
    settings unchanged (KTD10); evidence: the U5 smoke files and their SHA-256 hashes."""
    acc = dict(previous_prereg["acceptance"])
    acc.update({"gating_series": "procedure", "reported_series": list(BASELINES),
                "net_vs_baseline": "the procedure's OOS net must be > 0 and exceed the OOS net of baseline_a AND "
                                   "of baseline_b (KTD10)",
                "positive_folds_rule": "strict majority of the folds evaluated (3 of 5; 2 of 3 for group R)",
                "stability_label": "of the final candidate's (or fallback's) grid neighbours (KTD8)"})
    stats = dict(previous_prereg["stats"])
    stats.update(dsr_trials())
    stats.pop("pilot_runs", None)
    runs = split_runs()
    return {
        "study": STUDY,
        "protocol": f"{PLAN} (R1-R28, KTD1-KTD13)",
        "frozen_note": "Committed and pushed before the first research run of numeric_v1. Nothing here changes "
                       "afterwards. The previous research's protocol (research/preregistration.json) is unchanged.",
        "strategy": previous_prereg["strategy"],
        "chart_period": previous_prereg["chart_period"], "zone_period": previous_prereg["zone_period"],
        "deposit": DEPOSIT, "leverage": LEVERAGE,
        "fixed_inputs": dict(FIXED_INPUTS),
        "grid": {k: list(v) for k, v in GRID.items()},
        "grid_combinations": n_combos(),
        "categorical": list(CATEGORICAL),
        "defaults": dict(DEFAULTS),
        "optimization_runs_per_window": [{"part": r["part"], "ranges": {k: list(v) for k, v in r["ranges"].items()},
                                          "fixed": r["fixed"], "expected_passes": r["expected"]} for r in runs],
        "pass_verification": "per optimization run: frames P-rows = XML rows = the manager log's 'total passes N' "
                             "= 'N new records saved to cache' (this run's log section) = expected; every pass's "
                             "inputs inside the grid; the merged lo+hi table holds each of the 18 tuples exactly "
                             "once (KTD3, KTD4)",
        "baselines": {k: dict(v) for k, v in BASELINES.items()},
        "series": {"procedure": "per fold, the train selection, or the defaults labelled fallback when no pass is "
                                "eligible", "baseline_a": "(0, 3, 20) in every fold",
                   "baseline_b": "(1, 3, 20) in every fold",
                   "identity": "a series is named by its full parameter tuple; a candidate is never merged into a "
                               "baseline because its variant matches (KTD7)"},
        "folds": FOLDS, "final_train": list(FINAL_TRAIN),
        "excluded_window": list(EXCLUDED_WINDOW),
        "excluded_window_rule": "August-September 2026 is not run by this study and is not a holdout (R23)",
        "oos_label": "outside the train window, not blind and not independent: all of December-July was seen in "
                     "earlier research and pilots (R23)",
        "selection": {"trade_floor_per_month": TRADE_FLOOR_PER_MONTH, "train_months": TRAIN_MONTHS,
                      "trade_floor": trade_floor(), "max_equity_dd_pct": MAX_TRAIN_EQ_DD_PCT,
                      "drawdown_metric": "the tester's maximum equity drawdown of the train pass",
                      "score": "recovery factor (net profit / max equity DD in money) of eligible passes, 0 for "
                               "ineligible passes",
                      "smoothing": "mean of the pass and its existing one-step neighbours on SwingStrengthM1 and "
                                   "StopBufferPoints (grid index steps) within the same StructureVariant",
                      "tie_break": "smallest distance to the defaults (index steps on ordinal axes, 1 for a different "
                                   "variant), then the lowest axis indices in the order StructureVariant, "
                                   "SwingStrengthM1, StopBufferPoints",
                      "fallback": "no eligible pass: status no_eligible_pass, the defaults (0, 3, 20) carried into "
                                  "the OOS month labelled fallback, never a selected or improved candidate (KTD6)",
                      "uses": "train data only; OOS results and robustness runs never change a choice"},
        "acceptance": acc,
        "stability": {"neighbours": "every one-step move on SwingStrengthM1 or StopBufferPoints inside the grid "
                                    "around the final candidate or fallback; the variant is categorical and is not "
                                    "a neighbour axis; a tuple equal to the candidate is never a neighbour",
                      "window": list(STABILITY_WINDOW), "deposit": DEPOSIT,
                      "criterion": "share of neighbours with net > 0 >= 0.60: an acceptance criterion of the "
                                   "procedure; never read by selection (KTD8)"},
        "loss_limits": dict(previous_prereg["loss_limits"]),
        "stats": stats,
        "drawdowns_reported": ["the tester's maximum equity and balance drawdown per run (maximum across folds)",
                               "the drawdown rebuilt from the daily equity records",
                               "the closed-trade balance drawdown"],
        "reporting": {"groups": {"G": GROUPS["G"], "R": GROUPS["R"],
                                 "rule": "all folds, group G (train includes generated ticks) and group R (train "
                                         "real ticks only) reported apart; acceptance also evaluated on group R "
                                         "for the report"},
                      "session_minute": previous_prereg["reporting"]["session_minute"],
                      "pnl_exposure": "all of December-July and August-September were seen in earlier research"},
        "behaviour_checks": {"window": list(SMOKE_WINDOW), "runs": BEHAVIOUR_RUNS,
                             "criterion": "zero conformance violations and checked cases > 0 for the checker's "
                                          "pivot_causality_r9 and sl_r18 rules in every run; pivot sets differ "
                                          "across N; logged stop-to-anchor distances equal the buffer (KTD11)"},
        "run_budget": run_budget(),
        "deliverables": "at most a candidate .set (only when the final selection is 'selected') and a forward-test "
                        "protocol on new data; a fallback .set otherwise; never a recommended .set (R24)",
        "pre_freeze_evidence": evidence,
        "ea_source_sha256": ea_sha256,
    }


def load_prereg() -> dict:
    return json.loads(PREREG_PATH.read_text(encoding="utf-8"))
