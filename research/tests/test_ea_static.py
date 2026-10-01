"""Static checks of the OB-FVG retest EA source (plan U2/U3, interface contract).

Behavioural cases (AE1-AE8 etc.) are proven in U5 against tester output; these tests only
pin the source-level contract: inputs, enums, research blocks, headers, and forbidden patterns.
"""
import pathlib
import re

import pytest

from mt5r import setfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
EA = ROOT / "mql5" / "Experts" / "ob_fvg_retest.mq5"
WRAPPER = ROOT / "mql5" / "Experts" / "ob_fvg_retest_research.mq5"
ARCHIVED = ROOT / "archive" / "2026-09-30-new-test-sweepob" / "mql5" / "Experts" / "new_test.mq5"

CONTRACT_INPUTS = [
    ("ENUM_TIMEFRAMES", "SignalTF", "15"),
    ("ENUM_OB_MODE", "ObMode", "0"),
    ("ENUM_ENTRY_MODE", "EntryMode", "0"),
    ("int", "ImpulseWindowBars", "2"),
    ("int", "BosWindowBars", "6"),
    ("int", "SwingStrength", "3"),
    ("double", "VolumeMultiplier", "2.0"),
    ("int", "VolumeLookbackHours", "24"),
    ("int", "ObMaxAgeBars", "96"),
    ("int", "FvgWindowBars", "12"),
    ("int", "OrderExpiryBars", "12"),
    ("int", "StopBufferPoints", "10"),
    ("double", "RiskRR", "2.0"),
    ("double", "RiskPercent", "1.0"),
    ("int", "MaxExposures", "3"),
    ("int", "WarmupBars", "3000"),
    ("long", "MagicNumber", "770101"),
    ("string", "TradeComment", "OBR"),
]
CONTRACT_ENUMS = {
    "ENUM_OB_MODE": {"OB_FVG": 0, "OB_FVG_BOS": 1},
    "ENUM_ENTRY_MODE": {"ENTRY_FVG_EDGE": 0, "ENTRY_FVG_MID": 1, "ENTRY_OB_EDGE": 2, "ENTRY_OB_MID": 3},
}
SETUPS_HEADER = (
    "setup_id,dir,ob_mode,entry_mode,ob_time,ob_high,ob_low,idfvg_c1_time,idfvg_c3_time,idfvg_low,idfvg_high,"
    "bos_pivot_time,bos_pivot_conf_time,bos_level,bos_break_time,activation_time,touch_time,cfvg_c1_time,"
    "cfvg_c3_time,cfvg_low,cfvg_high,entry,sl,tp,volume,stops_level_pts,place_time_msc,order_ticket,"
    "fill_time_msc,fill_price,position_id,exit_time_msc,exit_price,exit_kind,reason,reason_time_msc,"
    "retest_seen_no_fill,idfvg_vol_ratio,cfvg_vol_ratio,market_closed_first_msc,place_attempts"
)
BARS_HEADER = "time,open,high,low,close,tick_volume"
REASON_CODES = [
    "expired_untouched", "invalidated_active", "invalidated_touched", "invalidated_confirmed",
    "invalidated_pending", "cancelled_no_fvg", "skipped_price_past", "skipped_too_close", "skipped_sl_stops",
    "skipped_volume", "skipped_margin", "skipped_cap", "skipped_duplicate", "expired_unfilled", "filled",
    "filled_late", "run_end_pending", "skipped_market_closed", "skipped_broker_reject",
]
FUNNEL_EXTRA_KEYS = ["idfvg_rejected_volume", "cfvg_rejected_volume", "market_closed_retries"]
RESEARCH_BLOCK = re.compile(r"#ifdef RESEARCH_LOG\b(.*?)#endif", re.S)


@pytest.fixture(scope="module")
def src():
    return EA.read_text(encoding="utf-8").replace("\r\n", "\n")


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _code(text: str) -> str:
    """Source without comments and without string-literal contents."""
    return re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', _strip_comments(text))


def _function_body(text: str, name: str) -> str:
    m = re.search(r"\b" + re.escape(name) + r"\s*\([^;{]*\)\s*\{", text)
    assert m, f"function {name} not found"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end():i - 1]


def _file_header(text: str, first_col: str) -> str:
    """Header written with FileWrite(h, "a", "b", ...) or as one "a,b,..." literal."""
    m = re.search(r'FileWrite\(h,\s*("' + first_col + r'"[^;]*?)\);', text)
    if m:
        return ",".join(re.findall(r'"([^"]*)"', m.group(1)))
    m = re.search(r'"(' + first_col + r',[^"]*)"', text)
    assert m, f"header starting with {first_col} not found"
    return m.group(1).replace("\\r\\n", "").replace("\\n", "")


def test_every_enum_member_has_explicit_int(src):
    enums = re.findall(r"enum\s+(\w+)\s*\{(.*?)\}\s*;", _strip_comments(src), flags=re.S)
    assert enums
    for name, body in enums:
        members = [m.strip() for m in body.split(",") if m.strip()]
        for member in members:
            assert re.fullmatch(r"\w+\s*=\s*-?\d+", member), f"{name}: {member!r} lacks an explicit int"
    parsed = {name: dict(re.findall(r"(\w+)\s*=\s*(-?\d+)", body)) for name, body in enums}
    for name, members in CONTRACT_ENUMS.items():
        assert {k: int(v) for k, v in parsed[name].items()} == members


def test_inputs_equal_contract_order_and_defaults(src):
    assert not re.search(r"\bsinput\b", _code(src))
    base = setfile.parse_inputs(src)
    assert [(s.type, s.name, s.default) for s in base] == CONTRACT_INPUTS
    research = setfile.parse_inputs(src, research=True)
    assert [(s.type, s.name, s.default) for s in research] == CONTRACT_INPUTS + [("string", "ResearchRunTag", "")]


def test_research_code_only_inside_non_nested_research_blocks(src):
    blocks = RESEARCH_BLOCK.findall(src)
    assert blocks
    for body in blocks:
        assert not re.search(r"#if", body), "nested preprocessor block inside RESEARCH_LOG"
        assert "#else" not in body
    outside = _code(RESEARCH_BLOCK.sub("", src))
    assert "ResearchRunTag" not in outside
    assert not re.search(r"\bRL_\w+", outside), "research helper referenced outside a research block"
    assert "OnTester" not in outside


def test_wrapper_is_exactly_define_plus_include():
    lines = [ln.strip() for ln in WRAPPER.read_text(encoding="utf-8").splitlines()]
    meaningful = [ln for ln in lines if ln and not ln.startswith("//")]
    assert meaningful == ["#define RESEARCH_LOG", '#include "ob_fvg_retest.mq5"']


def test_no_market_orders_and_no_volume_clamp(src):
    code = _code(src)
    for bad in (".Buy(", ".Sell(", ".PositionOpen(", "OrderSend(", "NormalizeLot", "FixedLots"):
        assert bad not in code, bad
    assert ".BuyLimit(" in code and ".SellLimit(" in code
    # no clamp up to the broker minimum volume (R14: skip, never clamp)
    assert not re.search(r"MathMax\s*\([^;]*(?:VOLUME_MIN|min[A-Z]\w*)", code)
    assert not re.search(r"if\s*\(\s*(\w+)\s*<\s*(min\w*|\w*VOLUME_MIN\w*)\s*\)\s*\1\s*=\s*\2\s*;", code, re.I)
    # the minimum volume is only ever compared against, and the comparison leads to a skip
    place = _code(_function_body(_strip_comments(src), "PlaceSetup"))
    assert "SYMBOL_VOLUME_MIN" in place
    assert re.search(r"<\s*minVol[^;]*\)\s*\{?\s*Skip\(\s*s\s*,\s*\"\"", place)


def test_rl_days_and_rl_deals_headers_equal_archived_ea(src):
    old = ARCHIVED.read_text(encoding="utf-8")
    for first in ("date", "time"):
        assert _file_header(src, first) == _file_header(old, first)
    assert _file_header(src, "date") == "date,bal_open,eq_open,eq_min,eq_max,bal_close,eq_close,spread_median"
    assert _file_header(src, "time") == \
        "time,ticket,position_id,type,entry,volume,price,profit,commission,swap,magic,comment"


def test_rl_setups_and_rl_bars_headers_equal_contract(src):
    research = "".join(RESEARCH_BLOCK.findall(src))
    assert _file_header(research, "setup_id") == SETUPS_HEADER
    assert f'"{BARS_HEADER}' in research
    for prefix in ("rl_days_", "rl_deals_", "rl_setups_", "rl_bars_", "rl_frames_"):
        assert f'"{prefix}"' in research, prefix


def test_order_comment_has_no_comma(src):
    body = _function_body(_strip_comments(src), "OrderComment")
    literals = re.findall(r'"((?:[^"\\\n]|\\.)*)"', body)
    assert literals, "comment format literals not found"
    assert all("," not in lit for lit in literals)
    assert '"L"' in body and '"S"' in body
    # a user-supplied prefix is stripped of commas
    assert re.search(r'StringReplace\(\s*gCmtPrefix\s*,\s*","\s*,\s*""\s*\)', src)


def test_no_constant_stops_distance(src):
    code = _code(src)
    assert not re.search(r"(?<![\w.])0\.20?(?!\d)", code), "literal 0.2/0.20 found"
    assert not re.search(r"(?<![\w.])20(?![\w.])", code), "literal 20 (points) found"
    place = _function_body(_strip_comments(src), "PlaceSetup")
    assert "SYMBOL_TRADE_STOPS_LEVEL" in place, "stops level must be read at each placement (R37)"
    assert "SYMBOL_TRADE_FREEZE_LEVEL" in code


def test_journal_lines_and_reason_codes(src):
    assert "Warm-up bars: " in src
    assert re.search(r'", tick "', src) and re.search(r'", stops level "', src) and '" pts"' in src
    assert '"Funnel: "' in src or '"Funnel:' in src
    for code in REASON_CODES + ["warmup_dropped"]:
        assert f'"{code}"' in src, code
    for key in ("activated", "touched", "confirmed", "placed"):
        assert f'"{key}"' in src, key
    funnel = _function_body(_strip_comments(src), "PrintFunnel")
    for key in FUNNEL_EXTRA_KEYS:
        assert re.search(r'KV\(\s*"' + key + r'"', funnel), key
    reasons = re.search(r"gReasons\[\]\s*=\s*\{(.*?)\}\s*;", src, re.S)
    assert reasons
    assert re.findall(r'"(\w+)"', reasons.group(1)) and \
        set(re.findall(r'"(\w+)"', reasons.group(1))) == set(REASON_CODES)


def test_fail_reason_fallback_is_broker_reject(src):
    body = _function_body(_strip_comments(src), "FailReason")
    assert "skipped_price_past" not in body
    returns = re.findall(r'return\s+"(\w+)"\s*;', body)
    assert returns and returns[-1] == "skipped_broker_reject"
    assert "TRADE_RETCODE_MARKET_CLOSED" not in body, "market closed is retried, never mapped to a skip code"


def test_market_closed_retry_has_cap_and_throttle(src):
    code = _strip_comments(src)
    assert "TRADE_RETCODE_MARKET_CLOSED" in _code(src)
    cap = re.search(r"#define\s+(\w+)\s+120\b", code)
    gap = re.search(r"#define\s+(\w+)\s+60000\b", code)
    assert cap and gap, "attempt cap 120 and 60 s (60000 ms) throttle constants"
    place = _function_body(code, "PlaceSetup")
    assert re.search(r"placeAttempts\s*>=\s*" + cap.group(1), place), "attempt cap check"
    assert re.search(r"time_msc\s*-\s*s\.lastAttemptMsc\s*<\s*" + gap.group(1) + r"|msc\s*-\s*s\.lastAttemptMsc\s*<\s*"
                     + gap.group(1), place), "60 s tick-time throttle"
    assert "TRADE_RETCODE_MARKET_CLOSED" in place
    assert '"skipped_market_closed"' in place
    # the window rule: OrderExpiryBars closed bars since candle 3 while awaiting placement
    bar = _function_body(code, "ProcessBar")
    assert re.search(r"ST_CONFIRMED[^;]*OrderExpiryBars|OrderExpiryBars[^;]*ST_CONFIRMED", bar) or \
        re.search(r'"skipped_market_closed"', bar)


def test_volume_filter_uses_tick_volume_and_lookback(src):
    code = _strip_comments(src)
    assert "tick_volume" in _function_body(code, "ProcessBar") or "tick_volume" in _function_body(code, "AppendBar")
    assert "VolumeLookbackHours" in code and "VolumeMultiplier" in code
    vq = _function_body(code, "VolumeQualifies")
    assert re.search(r">=\s*VolumeMultiplier", vq), "ratio >= multiplier (not >)"
    # applied to both the identifying and the confirmation FVG
    assert "VolumeQualifies" in _function_body(code, "NewCandidate")
    assert "VolumeQualifies" in _function_body(code, "ProcessBar")
    research = "".join(RESEARCH_BLOCK.findall(src))
    assert "tick_volume" in _function_body(_strip_comments(research), "RL_WriteRunFiles")


def test_oninit_guards(src):
    init = _function_body(_strip_comments(src), "OnInit")
    assert "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING" in init
    assert "INIT_PARAMETERS_INCORRECT" in init
    assert re.search(r"FvgWindowBars\s*<\s*2", init)
    assert "SignalTF" in init and "_Period" in init
    assert "CopyRates" in init
    for bad in (".BuyLimit(", ".SellLimit(", ".OrderDelete("):
        assert bad not in init, "OnInit must have no trading side effects"
