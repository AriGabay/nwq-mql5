"""R27 chart-gate diagnostics for the M5 OB + M1 structure pilot (no profit or loss): how the agreed rules behave in
the pilot runs, so the user can judge whether the examples represent the strategy.

python research/gate_review_m1.py -> results/pilot/gate_review.json
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cli  # noqa: E402
from mt5r import conformance_m1 as cm, runner  # noqa: E402


def q(x, p):
    x = pd.Series(x).dropna()
    return None if x.empty else round(float(x.quantile(p)), 2)


def review(run_id: str) -> dict:
    r = cm.read_run(runner.RUNS / run_id, run_id)
    s, ev = r["setups"], r["events"]
    filled = s[s["reason"] == "filled"].copy()
    by = ev.groupby("setup_id")
    sc_count = by.apply(lambda g: int((g["kind"] == "sc_hh").sum()), include_groups=False)
    lapse_count = by.apply(lambda g: int((g["kind"] == "fvg_lapsed").sum()), include_groups=False)
    filled["sc_before_entry"] = filled["setup_id"].map(sc_count).fillna(0)
    filled["lapses_before_entry"] = filled["setup_id"].map(lapse_count).fillna(0)
    sign = np.where(filled["dir"] == "L", 1, -1)
    edge = np.where(sign > 0, filled["ob_high"], filled["ob_low"])
    filled["entry_from_ob_edge"] = (filled["fill_price"] - edge) * sign      # >0: entry outside the zone, away
    filled["stop_distance"] = (filled["fill_price"] - filled["sl"]).abs()
    filled["touch_to_fill_h"] = (filled["fill_msc"] - filled["touch_msc"]) / 3.6e6
    sb = ev[ev["kind"] == "cancelled_second_break"]
    ret = ev[ev["kind"] == "return"].groupby("setup_id")["bar_time"].max()
    same_bar = int(sum(ret.get(x.setup_id) == x.bar_time for x in sb.itertuples()))
    sc_bar = ev[ev["kind"] == "sc_hh"].groupby("setup_id")["bar_time"].max()
    fvg = ev[ev["kind"] == "fvg_fixed"].groupby("setup_id")[["ref_time", "lo", "hi"]].last()
    keys = filled["setup_id"].map(lambda i: (sc_bar.get(i), tuple(fvg.loc[i]) if i in fvg.index else None))
    groups = keys.value_counts()
    stacked = int(groups[groups > 1].sum())
    return {
        "setups": len(s), "fills": len(filled),
        "reasons": {str(k): int(v) for k, v in s["reason"].value_counts().items()},
        "skip_events": {str(k): int(v) for k, v in ev["kind"].value_counts().items() if str(k).startswith("skipped")
                        or k == "lost_competition"},
        "sl_anchor": {str(k): int(v) for k, v in filled["sl_anchor"].value_counts().items()},
        "structure_changes_before_entry": {"median": q(filled["sc_before_entry"], .5),
                                           "p90": q(filled["sc_before_entry"], .9),
                                           "max": q(filled["sc_before_entry"], 1.0)},
        "fvg_lapses_before_entry": {"median": q(filled["lapses_before_entry"], .5),
                                    "p90": q(filled["lapses_before_entry"], .9)},
        "entry_distance_from_ob_edge_usd": {"median": q(filled["entry_from_ob_edge"], .5),
                                            "p90": q(filled["entry_from_ob_edge"], .9),
                                            "max": q(filled["entry_from_ob_edge"], 1.0)},
        "stop_distance_usd": {"p10": q(filled["stop_distance"], .1), "median": q(filled["stop_distance"], .5),
                              "p90": q(filled["stop_distance"], .9), "max": q(filled["stop_distance"], 1.0)},
        "touch_to_fill_hours": {"median": q(filled["touch_to_fill_h"], .5), "p90": q(filled["touch_to_fill_h"], .9),
                                "max": q(filled["touch_to_fill_h"], 1.0)},
        "ob_age_at_entry_bars": {"median": q(filled["ob_age_bars_entry"], .5),
                                 "p90": q(filled["ob_age_bars_entry"], .9),
                                 "max": q(filled["ob_age_bars_entry"], 1.0)},
        "second_break_cancels": int(len(sb)), "second_break_same_bar_as_return": same_bar,
        "fills_in_stacked_groups": stacked,
    }


def main():
    out = {n: review(f"pilot_{n.lower()}") for n in cli.VARIANTS}
    summary = json.loads(cli.PILOT.read_text())
    for n in out:
        out[n]["fills_per_month"] = summary["runs"][n]["fills_per_month"]
    dst = cli.RESULTS / "pilot" / "gate_review.json"
    dst.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
