"""Copy the evidence of a run into results/ (reports, research CSVs, ini, manifest; never raw logs).
The M1 bar log is large (about 240k rows over 8 months), so its curated copy is gzipped (KTD10)."""
import gzip
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
        if f.name.startswith("rl_bars_m1_") and f.suffix == ".csv":
            with open(f, "rb") as src_f, gzip.open(out / (f.name + ".gz"), "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f)
        elif f.suffix in (".htm", ".xml", ".csv", ".ini") or f.name == "manifest.json":
            shutil.copy2(f, out / f.name)
    (out / "journal_facts.json").write_text(json.dumps(journal.run_facts(src), indent=1))
    return out
