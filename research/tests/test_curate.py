import gzip
import json

import pandas as pd

from mt5r import curate


def test_curate_gzips_the_m1_bars_and_keeps_the_rest_plain(tmp_path, monkeypatch):
    """KTD10: curated results keep the (large) M1 bar log gzipped; every other evidence file is copied as is."""
    monkeypatch.setattr(curate, "REPO", tmp_path)
    run = tmp_path / "runs" / "pilot_a"
    run.mkdir(parents=True)
    (run / "rl_bars_m1_pilot_a.csv").write_text("time,open\n1,2.5\n")
    (run / "rl_bars_m5_pilot_a.csv").write_text("time,open\n1,2.5\n")
    (run / "pilot_a.htm").write_text("<html></html>")
    (run / "manifest.json").write_text(json.dumps({"run_id": "pilot_a"}))
    (run / "tester.log").write_text("raw log stays out")
    monkeypatch.setattr(curate.journal, "run_facts", lambda src: {})
    out = curate.curate("pilot_a", "pilot")
    names = sorted(p.name for p in out.iterdir())
    assert names == ["journal_facts.json", "manifest.json", "pilot_a.htm", "rl_bars_m1_pilot_a.csv.gz",
                     "rl_bars_m5_pilot_a.csv"]
    assert pd.read_csv(out / "rl_bars_m1_pilot_a.csv.gz")["open"].tolist() == [2.5]
    with gzip.open(out / "rl_bars_m1_pilot_a.csv.gz", "rt") as f:
        assert f.read() == "time,open\n1,2.5\n"
