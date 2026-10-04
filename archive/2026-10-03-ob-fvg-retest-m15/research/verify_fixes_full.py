"""Post-research checks of the two corrections, on the saved WFO evidence (no optimization, no new selection).

A) EA fixes: every OOS and static run of the research (Mar-Jul 2026) is rerun with the fixed EA and exactly the
   deposit MT5 started with originally; deals must match the frozen-EA run field for field. The holdout window
   is deliberately not rerun (R24: it ran once).
B) Deposit chaining: each fold starts with int(previous fold's true final balance), the fix in cli.tester_deposit.
   Folds whose corrected deposit differs from the original start are rerun; the rest are already correct.

python research/verify_fixes_full.py -> results/code_review/{fix_equivalence_full.json, rechain.json}
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cli  # noqa: E402
import verify_builds as vb  # noqa: E402
from mt5r import curate, env, evaluate, pipeline, reports  # noqa: E402

OUT = cli.RESULTS / "code_review"


def frozen(run_id, folder):
    return evaluate.load_run(run_id, cli.RESULTS / folder)


def same_deals(rep_a, rep_b) -> bool:
    a, b = vb.deals(rep_a), vb.deals(rep_b)
    return bool(len(a) == len(b) and a.astype(str).equals(b.astype(str)))


def main():
    P = pipeline.prereg()
    cfg = env.load_config()
    folds = json.loads(cli.FOLDS.read_text())
    acc = json.loads((cli.RESULTS / "acceptance.json").read_text())
    log = {"ea_sha256": cli.ea_sha()}

    # A) fixed EA vs frozen EA, same inputs and same starting deposit
    jobs = []
    for f in folds:
        for who in ("procedure", "baseline"):
            params = f["selection"]["params"] if who == "procedure" else {}
            jobs.append((f["oos"][who]["run_id"], "wfo", f["test"], params))
    start, end = P["folds"][0]["test"][0], P["folds"][-1]["test"][1]
    for r in acc["neighbors"]["rows"]:
        jobs.append((r["run_id"], "robustness", [start, end], r["params"]))
    rows = []
    for run_id, folder, (s, e), params in jobs:
        ref = frozen(run_id, folder)
        new_id = f"fixeq_{run_id}"
        _, rep = pipeline.run_single(cfg, new_id, "research", P["period"], s, e, deposit=ref["deposit"],
                                     overrides=params, role="code_review",
                                     purpose=f"fixed EA vs frozen EA: {run_id}", log_extra=log)
        curate.curate(new_id, "code_review")
        rows.append({"run_id": run_id, "deposit": ref["deposit"], "deals_frozen": len(vb.deals(ref["report"])),
                     "deals_fixed": len(vb.deals(rep)), "identical": same_deals(rep, ref["report"])})
        print(rows[-1], flush=True)
    eq = {"window": [start, end], "runs": rows, "all_identical": all(r["identical"] for r in rows),
          "total_deals_compared": sum(r["deals_frozen"] for r in rows),
          "not_rerun": "holdout 2026.08.01-09.29 (R24: run once)"}
    evaluate.save(eq, OUT / "fix_equivalence_full.json")

    # B) corrected chaining
    chain = {}
    for who in ("procedure", "baseline"):
        dep, out = float(P["deposit"]), []
        for f in folds:
            run_id = f["oos"][who]["run_id"]
            ref = frozen(run_id, "wfo")
            want = cli.tester_deposit(dep)
            row = {"fold": f["fold"], "original_start": ref["deposit"], "corrected_start": want,
                   "original_net": ref["summary"]["net_profit"]}
            if want == int(ref["deposit"]):
                row.update(rerun=False, corrected_net=ref["summary"]["net_profit"], run_id=run_id)
            else:
                new_id = f"rechain_{run_id}"
                params = f["selection"]["params"] if who == "procedure" else {}
                _, rep = pipeline.run_single(cfg, new_id, "research", P["period"], *f["test"], deposit=want,
                                             overrides=params, role="rechain",
                                             purpose=f"corrected deposit chaining: {run_id}", log_extra=log)
                curate.curate(new_id, "code_review")
                row.update(rerun=True, corrected_net=reports.summary(rep)["net_profit"], run_id=new_id,
                           same_deals_as_original=same_deals(rep, ref["report"]))
            row["corrected_final"] = round(want + row["corrected_net"], 2)
            dep = row["corrected_final"]
            out.append(row)
            print(who, row, flush=True)
        chain[who] = {"folds": out, "net": round(sum(r["corrected_net"] for r in out), 2), "final": out[-1]["corrected_final"]}
    evaluate.save(chain, OUT / "rechain.json")
    print(json.dumps({"all_identical": eq["all_identical"], "deals": eq["total_deals_compared"],
                      "procedure_net": chain["procedure"]["net"], "baseline_net": chain["baseline"]["net"]}))


if __name__ == "__main__":
    main()
