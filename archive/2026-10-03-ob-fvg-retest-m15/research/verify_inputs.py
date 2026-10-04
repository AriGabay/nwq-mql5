"""R26 input-load check, after the fact, of every curated single run whose intended inputs are known: WFO OOS
(procedure = the fold's selected params, baseline = pre-registered defaults), the static candidate and neighbour
runs of robustness, and the holdout. Reads only the archived MT5 reports; runs nothing.

python research/verify_inputs.py -> results/input_checks.json (exit 1 on any mismatch)
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cli  # noqa: E402
from mt5r import evaluate, pipeline, reports, setfile  # noqa: E402


def check(folder: str, run_id: str, expected: dict) -> dict:
    rep = reports.parse_html(cli.RESULTS / folder / run_id / f"{run_id}.htm")
    return {"run_id": run_id, "expected": expected, "mismatches": pipeline.check_inputs_loaded(rep, expected)}


def main():
    P = pipeline.prereg()
    rows = []
    for f in json.loads(cli.FOLDS.read_text()) if cli.FOLDS.exists() else []:
        rows.append(check("wfo", f["oos"]["procedure"]["run_id"], f["selection"]["params"]))
        rows.append(check("wfo", f["oos"]["baseline"]["run_id"], dict(P["defaults"])))
    acc = cli.RESULTS / "acceptance.json"
    if acc.exists():
        for r in json.loads(acc.read_text())["neighbors"]["rows"]:
            rows.append(check("robustness", r["run_id"], r["params"]))
    for who, name in (("candidate", cli.CAND_SET), ("baseline", cli.BASE_SET)):
        if (cli.RESULTS / "holdout" / f"holdout_{who}").exists():
            rows.append(check("holdout", f"holdout_{who}", setfile.read_set(cli.DELIV / name)))
    bad = [r for r in rows if r["mismatches"]]
    out = {"runs_checked": len(rows), "runs_with_mismatches": len(bad), "rows": rows}
    evaluate.save(out, cli.RESULTS / "input_checks.json")
    print(json.dumps({k: out[k] for k in ("runs_checked", "runs_with_mismatches")}))
    if bad:
        raise SystemExit(f"inputs not loaded as intended: {[r['run_id'] for r in bad]}")


if __name__ == "__main__":
    main()
