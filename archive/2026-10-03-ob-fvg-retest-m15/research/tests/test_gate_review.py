"""volume_examples.md rows of the gate review (AMENDMENT B: pass/fail applies to the identifying FVG only)."""
import pathlib

import gate_review as gr
from mt5r import conformance as cf

FX = pathlib.Path(__file__).parent / "fixtures" / "ob_fvg"
P = 900


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def test_volume_rows_judge_only_the_identifying_fvg():
    setups, bars = cf.read_setups(FX / "rl_setups.csv"), cf.read_bars(FX / "rl_bars.csv")
    bars.loc[10, "tick_volume"] = 150  # long cFVG middle candle 150 / 107.5 = 1.40 < 2.0: still not a FAIL
    rows = gr.volume_rows(setups, bars, P, lookback_hours=1)
    body = [_cells(r) for r in rows[2:]]
    ident = [c for c in body if c[3] == "identifying"]
    conf = [c for c in body if c[3] == "confirmation"]
    assert len(ident) == 2 and len(conf) == 2
    assert all(c[-1] == "pass" for c in ident)
    assert all(c[-1] == "info" for c in conf)
    assert conf[0][9] == "1.3953"  # the ratio itself is still shown
    assert conf[0][7] == "4"       # 1 h window on M15 with no closure: 4 bars (AMENDMENT C)
    assert "FAIL" not in "\n".join(rows)


def test_volume_rows_flag_a_failing_identifying_fvg():
    setups, bars = cf.read_setups(FX / "rl_setups.csv"), cf.read_bars(FX / "rl_bars.csv")
    bars.loc[4, "tick_volume"] = 200  # 200 / 107.5 = 1.86 < 2.0
    rows = gr.volume_rows(setups, bars, P, lookback_hours=1)
    first = _cells(next(r for r in rows if r.startswith("| 1 |") and "identifying" in r))
    assert first[-1] == "FAIL"


def test_volume_examples_heading_says_identifying_only(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "_exists", lambda run, folder: False)
    text = gr.volume_examples({"M15": "nope"}, "pilot")
    assert "identifying FVG" in text and "informational" in text
