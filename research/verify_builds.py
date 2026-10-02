"""Pre-freeze verification (U5): FVG+BOS-mode conformance and delivered-vs-research build equivalence.

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
    cfg = env.load_config()
    out = {"window": [a.start, a.end]}
    ea = {"ea_sha256": cli.ea_sha()}

    _, rep = pipeline.run_single(cfg, "smoke_m15_bos", "research", "M15", a.start, a.end, overrides={"ObMode": 1},
                                 role="smoke", purpose="U5 FVG+BOS conformance", log_extra=ea)
    curate.curate("smoke_m15_bos", "smoke")
    c = cli.conformance_report("smoke_m15_bos", "smoke")
    out["bos_conformance"] = {"setups": c["setups"], "violations": c["violations"]}

    reps = {}
    for kind in ("research", "delivered"):
        run_id = f"equiv_{kind}_m15"
        _, reps[kind] = pipeline.run_single(cfg, run_id, kind, "M15", a.start, a.end, role="equivalence",
                                            purpose="U5 delivered vs research build", log_extra=ea)
        curate.curate(run_id, "smoke")
    r, d = deals(reps["research"]), deals(reps["delivered"])
    out["equivalence"] = {"research_deals": len(r), "delivered_deals": len(d),
                          "identical": bool(len(r) == len(d) and r.astype(str).equals(d.astype(str)))}
    path = cli.RESULTS / "smoke" / "verify_builds.json"
    path.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    if out["bos_conformance"]["violations"] or not out["equivalence"]["identical"]:
        raise SystemExit("verification failed")


if __name__ == "__main__":
    main()
