"""Copy the evidence of a run into results/ (reports, research CSVs, ini, manifest; never raw logs)."""
import json
import pathlib
import shutil

from . import journal

REPO = pathlib.Path(__file__).resolve().parents[2]


def curate(run_id: str, dest: str) -> pathlib.Path:
    src = REPO / "runs" / run_id
    out = REPO / "results" / dest / run_id
    out.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.suffix in (".htm", ".xml", ".csv", ".ini") or f.name == "manifest.json":
            shutil.copy2(f, out / f.name)
    (out / "journal_facts.json").write_text(json.dumps(journal.run_facts(src), indent=1))
    return out
