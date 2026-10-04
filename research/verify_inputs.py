"""Input-load check, after the fact, of every curated single run whose intended inputs are known: WFO OOS
(procedure = the fold's selected variant, fixed A, fixed B), the KTD15 stability runs, and August-September.
Reads only the archived MT5 reports; runs nothing.

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
    rows = []
    for f in json.loads(cli.FOLDS.read_text()) if cli.FOLDS.exists() else []:
        for who in cli.SERIES:
            o = f["oos"][who]
            rows.append(check("wfo", o["run_id"], o["params"]))
    acc_path = cli.RESULTS / "acceptance.json"
    if acc_path.exists():
        acc = json.loads(acc_path.read_text())
        cand = acc["candidate"]
        for r in acc["stability"]["rows"]:
            rows.append(check("robustness", r["run_id"], {**cand, **r["perturbation"]}))
    for who, name in cli.HOLDOUT_SETS.items():
        if (cli.RESULTS / "aug_sep_check" / f"holdout_{who}").exists():
            rows.append(check("aug_sep_check", f"holdout_{who}", setfile.read_set(cli.DELIV / name)))
    bad = [r for r in rows if r["mismatches"]]
    out = {"runs_checked": len(rows), "runs_with_mismatches": len(bad), "rows": rows}
    evaluate.save(out, cli.RESULTS / "input_checks.json")
    print(json.dumps({k: out[k] for k in ("runs_checked", "runs_with_mismatches")}))
    if bad:
        raise SystemExit(f"inputs not loaded as intended: {[r['run_id'] for r in bad]}")


if __name__ == "__main__":
    main()
