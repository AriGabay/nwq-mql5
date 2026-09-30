"""Static checks on the EA sources (U2): the only behavioral change vs v1.03 is SignalTF."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
ORIG = ROOT / "original" / "new_test_v1.03.mq5"
NEW = ROOT / "mql5" / "Experts" / "new_test.mq5"
RESEARCH = ROOT / "mql5" / "Experts" / "new_test_research.mq5"

INPUT_RE = re.compile(r"^\s*input\s+(\w+)\s+(\w+)\s*=", re.M)


def _strip_research_blocks(text: str) -> str:
    """Remove every #ifdef RESEARCH_LOG ... #endif block (non-nested)."""
    return re.sub(r"#ifdef RESEARCH_LOG.*?#endif[^\n]*\n", "", text, flags=re.S)


def _inputs(text: str):
    return [(m.group(1), m.group(2)) for m in INPUT_RE.finditer(text)]


def test_signal_tf_input_defaults_to_m5():
    text = NEW.read_text()
    assert re.search(r"^\s*input\s+ENUM_TIMEFRAMES\s+SignalTF\s*=\s*PERIOD_M5\s*;", text, re.M)


def test_no_hardcoded_m5_outside_default():
    text = _strip_research_blocks(NEW.read_text())
    uses = [l for l in text.splitlines() if "PERIOD_M5" in l]
    assert len(uses) == 1 and "SignalTF" in uses[0], uses
    assert " M5 " not in text and "M5 only" not in text.replace("M5 only\"", "")


def test_only_signal_tf_added_outside_research_blocks():
    orig = _inputs(ORIG.read_text())
    new = _inputs(_strip_research_blocks(NEW.read_text()))
    added = [i for i in new if i not in orig]
    removed = [i for i in orig if i not in new]
    assert added == [("ENUM_TIMEFRAMES", "SignalTF")]
    assert removed == []


def test_research_inputs_only_inside_research_blocks():
    text = NEW.read_text()
    blocks = re.findall(r"#ifdef RESEARCH_LOG(.*?)#endif", text, flags=re.S)
    inside = [n for b in blocks for _, n in _inputs(b)]
    assert inside == ["ResearchRunTag"]


def _mask(text: str) -> str:
    """Normalize v1.04 back to v1.03 shape: drop research blocks, map SignalTF back to M5."""
    text = _strip_research_blocks(text)
    text = re.sub(r"^\s*input\s+ENUM_TIMEFRAMES\s+SignalTF.*\n", "", text, flags=re.M)
    text = text.replace('input group "=== Timeframe ==="', "")
    text = text.replace("SignalTF", "PERIOD_M5")
    return text


def _logic_lines(text: str):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("//") or s.startswith("#property version"):
            continue
        # string literals (log/comment text) and TF-name formatting may differ; compare code shape
        s = re.sub(r'"[^"]*"', '""', s)
        s = s.replace("TfName()", '""')
        # adjacent string pieces (log text split around the TF name) collapse to one literal
        prev = None
        while prev != s:
            prev = s
            s = s.replace('"" + ""', '""').replace('"", ""', '""')
        out.append(s)
    return out


def test_no_incidental_logic_edits():
    orig = _logic_lines(ORIG.read_text())
    new = _logic_lines(_mask(NEW.read_text()))
    # allow the TfName helper definition only
    new = [l for l in new if "TfName" not in l and "StringSubstr(EnumToString" not in l]
    import difflib
    diff = [d for d in difflib.unified_diff(orig, new, lineterm="", n=0) if d[:1] in "+-" and d[:3] not in ("+++", "---")]
    assert diff == [], "\n".join(diff[:40])


def test_research_wrapper_is_define_plus_include():
    lines = [l.strip() for l in RESEARCH.read_text().splitlines() if l.strip() and not l.strip().startswith("//")]
    assert lines == ["#define RESEARCH_LOG", '#include "new_test.mq5"']
