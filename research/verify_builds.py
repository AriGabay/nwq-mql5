"""U7 build equivalence: the delivered build and the research build of the M5 OB + M1 structure EA must place
exactly the same deals on the same window (R2), for each structure variant.

python research/verify_builds.py [--start 2026.03.01 --end 2026.03.31] -> results/smoke/verify_builds.json
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cli  # noqa: E402
from mt5r import curate, env, pipeline  # noqa: E402

DEAL_COLS = ["Time", "Deal", "Symbol", "Type", "Direction", "Volume", "Price", "Order", "Profit", "Balance"]


def deals(rep):
    d = rep["deals"]
    return d[[c for c in DEAL_COLS if c in d.columns]].reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026.03.01")
    ap.add_argument("--end", default="2026.03.31")
    a = ap.parse_args()
    cli.refuse_holdout_window(a.start, a.end)
    cfg = env.load_config()
    out = {"window": [a.start, a.end], "ea_sha256": cli.ea_sha(), "variants": {}}
    for name, value in cli.VARIANTS.items():
        reps = {}
        for kind in ("research", "delivered"):
            run_id = f"equiv_{kind}_{name.lower()}"
            _, reps[kind] = pipeline.run_single(cfg, run_id, kind, cli.CHART_PERIOD, a.start, a.end,
                                                overrides={"StructureVariant": value}, role="equivalence",
                                                purpose=f"R2 delivered vs research build, variant {name}",
                                                log_extra={"ea_sha256": cli.ea_sha()})
            if reps[kind] is None:
                raise SystemExit(f"{run_id}: no report")
            curate.curate(run_id, "smoke")
        r, d = deals(reps["research"]), deals(reps["delivered"])
        out["variants"][name] = {"research_deals": len(r), "delivered_deals": len(d),
                                 "identical": bool(len(r) == len(d) and r.astype(str).equals(d.astype(str)))}
    path = cli.RESULTS / "smoke" / "verify_builds.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    if not all(v["identical"] for v in out["variants"].values()):
        raise SystemExit("delivered and research builds trade differently")


if __name__ == "__main__":
    main()
