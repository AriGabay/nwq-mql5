import pathlib

from mt5r import setfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
ORIG = (ROOT / "original" / "new_test_v1.03.mq5").read_text()
NEW = (ROOT / "mql5" / "Experts" / "new_test.mq5").read_text()


def test_original_has_25_inputs_with_code_defaults():
    specs = {s.name: s for s in setfile.parse_inputs(ORIG)}
    assert len(specs) == 25
    assert specs["OppBreakCancel"].default == "1"
    assert specs["EntryMode"].default == "0"
    assert specs["LotMode"].default == "1"
    assert specs["SweepToSetupBars"].default == "12"
    assert specs["VolumeMultiplier"].default == "2.0"
    assert specs["MinVolumeSamples"].default == "20"
    assert specs["TradeComment"].default == "SweepOB"
    assert specs["DrawObjects"].default == "true"


def test_v104_adds_signal_tf_and_research_tag_only_in_research_build():
    base = [s.name for s in setfile.parse_inputs(NEW)]
    research = [s.name for s in setfile.parse_inputs(NEW, research=True)]
    assert base[0] == "SignalTF" and len(base) == 26
    assert research == base + ["ResearchRunTag"]
    assert setfile.parse_inputs(NEW)[0].default == "5"


def test_original_set_round_trips(tmp_path):
    specs = setfile.parse_inputs(ORIG)
    path = tmp_path / "o.set"
    setfile.write_set(path, setfile.render_lines(specs), header="original")
    back = setfile.read_set(path)
    assert back == {s.name: s.default for s in specs}
    assert back["OppBreakCancel"] == "1"
    assert path.read_bytes()[:2] == b"\xff\xfe"


def test_saved_set_lacks_opp_break_cancel():
    saved = setfile.read_set(ROOT / "original" / "new_test_saved.set")
    assert "OppBreakCancel" not in saved
    assert saved["SweepToSetupBars"] == "96"


def test_optimize_ranges_and_overrides():
    specs = setfile.parse_inputs(NEW)
    lines = setfile.render_lines(specs, {"SignalTF": 15, "VolumeMultiplier": 1.2, "DrawObjects": False},
                                 {"PivL": (2, 1, 5)})
    assert "SignalTF=15||15||0||15||N" in lines
    assert "PivL=3||2||1||5||Y" in lines
    assert "VolumeMultiplier=1.2||1.2||0||1.2||N" in lines
    assert "DrawObjects=false||false||0||false||N" in lines
    assert "TradeComment=SweepOB" in lines


def test_unknown_override_rejected():
    import pytest
    with pytest.raises(KeyError):
        setfile.render_lines(setfile.parse_inputs(NEW), {"NoSuchInput": 1})
