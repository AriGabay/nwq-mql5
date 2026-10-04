"""After the post-research EA fixes (OnInit reset, retest flag on late fills): the tester must place exactly the
same deals as the frozen research EA on the same window, on both builds.

python research/verify_ea_fixes.py -> results/code_review/ea_fix_equivalence.json
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cli  # noqa: E402
import verify_builds as vb  # noqa: E402
from mt5r import curate, env, pipeline, reports  # noqa: E402

START, END = "2026.03.01", "2026.03.31"


def main():
    cfg = env.load_config()
    out = {"window": [START, END], "ea_sha256": cli.ea_sha(), "builds": {}}
    for kind in ("research", "delivered"):
        run_id = f"fixcheck_{kind}_m15"
        _, rep = pipeline.run_single(cfg, run_id, kind, "M15", START, END, role="code_review",
                                     purpose="post-research EA fixes: same deals as the frozen EA",
                                     log_extra={"ea_sha256": cli.ea_sha()})
        curate.curate(run_id, "code_review")
        ref = reports.parse_html(cli.RESULTS / "smoke" / f"equiv_{kind}_m15" / f"equiv_{kind}_m15.htm")
        a, b = vb.deals(rep), vb.deals(ref)
        out["builds"][kind] = {"deals_now": len(a), "deals_frozen_ea": len(b),
                               "identical": bool(len(a) == len(b) and a.astype(str).equals(b.astype(str)))}
    path = cli.RESULTS / "code_review" / "ea_fix_equivalence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    if not all(v["identical"] for v in out["builds"].values()):
        raise SystemExit("the fixed EA trades differently")


if __name__ == "__main__":
    main()
