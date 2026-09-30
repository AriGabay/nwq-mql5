from mt5r import setfile

SRC = """
enum ENUM_OB_MODE { OB_FVG = 0, OB_FVG_BOS = 1 };
enum ENUM_ENTRY_MODE { ENTRY_FVG_EDGE = 0, ENTRY_FVG_MID = 1, ENTRY_OB_EDGE = 2, ENTRY_OB_MID = 3 };
input ENUM_TIMEFRAMES SignalTF = PERIOD_M15;  // signal timeframe
input ENUM_OB_MODE    ObMode = OB_FVG;        // OB qualification
input ENUM_ENTRY_MODE EntryMode = ENTRY_OB_MID;
input int             ObMaxAgeBars = 96;
input double          RiskPercent = 1.0;
input bool            DrawObjects = true;
input string          TradeComment = "OBR";
#ifdef RESEARCH_LOG
input string          ResearchRunTag = "";
#endif
"""


def test_inputs_resolve_enums_and_builtin_timeframes():
    specs = {s.name: s for s in setfile.parse_inputs(SRC)}
    assert list(specs) == ["SignalTF", "ObMode", "EntryMode", "ObMaxAgeBars", "RiskPercent", "DrawObjects",
                           "TradeComment"]
    assert specs["SignalTF"].default == "15"
    assert specs["ObMode"].default == "0"
    assert specs["EntryMode"].default == "3"
    assert specs["RiskPercent"].default == "1.0"
    assert specs["DrawObjects"].default == "true"
    assert specs["TradeComment"].default == "OBR"
    assert specs["SignalTF"].comment == "signal timeframe"


def test_research_inputs_only_in_research_build():
    base = [s.name for s in setfile.parse_inputs(SRC)]
    research = [s.name for s in setfile.parse_inputs(SRC, research=True)]
    assert research == base + ["ResearchRunTag"]


def test_set_round_trips(tmp_path):
    specs = setfile.parse_inputs(SRC)
    path = tmp_path / "o.set"
    setfile.write_set(path, setfile.render_lines(specs), header="original")
    back = setfile.read_set(path)
    assert back == {s.name: s.default for s in specs}
    assert path.read_bytes()[:2] == b"\xff\xfe"


def test_optimize_ranges_and_overrides():
    specs = setfile.parse_inputs(SRC)
    lines = setfile.render_lines(specs, {"SignalTF": 5, "RiskPercent": 1.0, "DrawObjects": False},
                                 {"EntryMode": (0, 1, 3)})
    assert "SignalTF=5||5||0||5||N" in lines
    assert "EntryMode=3||0||1||3||Y" in lines
    assert "RiskPercent=1.0||1.0||0||1.0||N" in lines
    assert "DrawObjects=false||false||0||false||N" in lines
    assert "TradeComment=OBR" in lines


def test_unknown_override_rejected():
    import pytest
    with pytest.raises(KeyError):
        setfile.render_lines(setfile.parse_inputs(SRC), {"NoSuchInput": 1})
