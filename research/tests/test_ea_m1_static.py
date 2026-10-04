"""Static checks of the M5 OB + M1 structure EA source (plan 2026-10-03-0013, U2/U3).

Behaviour (AE1-AE7, R25) is proven by the U4 conformance checker on tester output in U7; these tests pin the
source-level contract only: inputs and enums, the KTD2 processing order, the entry routine (KTD7), the research
blocks and every log header, reason code, event kind and funnel key of research/mt5r/m1_contract.py.
"""
import pathlib
import re

import pytest

from mt5r import m1_contract as mc
from mt5r import setfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
EA = ROOT / "mql5" / "Experts" / mc.EA_SOURCE
WRAPPER = ROOT / "mql5" / "Experts" / mc.EA_RESEARCH_SOURCE
PREDECESSOR = ROOT / "archive" / "2026-10-03-ob-fvg-retest-m15" / "mql5" / "Experts" / "ob_fvg_retest.mq5"

FUNNEL_LINE_MAX = 180  # MT5's journal truncates long lines; journal.py merges several "Funnel:" lines
RESEARCH_BLOCK = re.compile(r"#ifdef RESEARCH_LOG\b(.*?)#endif", re.S)
# KTD2: per-tick steps and per-M1-close setup steps, as called from OnTick / ProcessM1Close
TICK_STEPS = ["SyncTrades", "ProcessNewM1Bars", "ProcessNewM5Bars", "TickChecks", "RunEntries"]
CLOSE_STEPS = ["UpdatePivots", "StepBreak", "StepOpposing", "StepLapse", "StepStructure", "StepFvg", "StepHl",
               "StepReaction"]
ENTRY_ROUTINE = "TryEntry"
HEADER_DEFINES = {"setups": "RL_SETUPS_HEADER", "events": "RL_EVENTS_HEADER", "pivots": "RL_PIVOTS_HEADER",
                  "bars_m1": "RL_BARS_HEADER", "bars_m5": "RL_BARS_HEADER"}


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


def _calls(body: str, name: str) -> list:
    """Argument strings of every call name(...) in body (balanced parentheses)."""
    out = []
    for m in re.finditer(r"\b" + re.escape(name) + r"\s*\(", body):
        depth, i = 1, m.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(body[i], 0)
            i += 1
        out.append(body[m.end():i - 1])
    return out


def _split_args(args: str) -> list:
    parts, depth, cur = [], 0, ""
    for ch in args:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    return parts


def _file_header(text: str, first_col: str) -> str:
    """Header written with FileWrite(h, "a", "b", ...)."""
    m = re.search(r'FileWrite\(h,\s*("' + first_col + r'"[^;]*?)\);', text)
    assert m, f"FileWrite header starting with {first_col} not found"
    return ",".join(re.findall(r'"([^"]*)"', m.group(1)))


def _define(text: str, name: str) -> str:
    m = re.search(r"#define\s+" + name + r'\s+"([^"]*)"', text)
    assert m, f"#define {name} not found"
    return m.group(1)


def _order(body: str, names: list) -> list:
    pos = []
    for n in names:
        m = re.search(r"\b" + n + r"\s*\(", body)
        assert m, f"{n} is not called"
        pos.append(m.start())
    return pos


# --- inputs and enums --------------------------------------------------------------------------------------------
def test_wrapper_is_exactly_define_plus_include():
    lines = [ln.strip() for ln in WRAPPER.read_text(encoding="utf-8").splitlines()]
    meaningful = [ln for ln in lines if ln and not ln.startswith("//")]
    assert meaningful == ["#define RESEARCH_LOG", f'#include "{mc.EA_SOURCE}"']


def test_every_enum_member_has_explicit_int_and_matches_contract(src):
    enums = re.findall(r"enum\s+(\w+)\s*\{(.*?)\}\s*;", _strip_comments(src), flags=re.S)
    assert enums
    for name, body in enums:
        for member in (m.strip() for m in body.split(",") if m.strip()):
            assert re.fullmatch(r"\w+\s*=\s*-?\d+", member), f"{name}: {member!r} lacks an explicit int"
    parsed = {name: {k: int(v) for k, v in re.findall(r"(\w+)\s*=\s*(-?\d+)", body)} for name, body in enums}
    for name, members in mc.ENUMS.items():
        assert parsed.get(name) == members, name


def test_inputs_equal_contract_order_and_defaults(src):
    assert not re.search(r"\bsinput\b", _code(src))
    base = setfile.parse_inputs(src)
    assert [(s.type, s.name, s.default) for s in base] == mc.INPUTS
    research = setfile.parse_inputs(src, research=True)
    extra = [(t, n, d.strip('"') if t == "string" else d) for t, n, d in mc.RESEARCH_INPUTS]
    assert [(s.type, s.name, s.default) for s in research] == mc.INPUTS + extra


def test_no_volume_bos_or_age_input(src):
    names = [s.name for s in setfile.parse_inputs(src, research=True)]
    for name in names:
        assert not re.search(r"vol|bos|age|expir", name, re.I), name
    code = _strip_comments(src)
    for gone in ("ObMaxAgeBars", "BosWindowBars", "FvgWindowBars", "OrderExpiryBars", "VolumeMultiplier",
                 "ObMode", "EntryMode", "SignalTF"):
        assert not re.search(r"\b" + gone + r"\b", code), gone


# --- trading ---------------------------------------------------------------------------------------------------------
def test_no_limit_or_pending_order_api(src):
    code = _code(src)
    for bad in ("BuyLimit", "SellLimit", "BuyStop", "SellStop", "OrderOpen", "OrderDelete", "OrderModify",
                "ORDER_TYPE_BUY_LIMIT", "ORDER_TYPE_SELL_LIMIT", "ORDER_TYPE_BUY_STOP", "ORDER_TYPE_SELL_STOP",
                "TRADE_ACTION_PENDING", "OrderSend(", ".PositionOpen(", ".PositionClose("):
        assert bad not in code, bad


def test_market_orders_only_in_entry_routine_and_always_with_sl(src):
    code = _strip_comments(src)
    entry = _function_body(code, ENTRY_ROUTINE)
    outside = code.replace(entry, "")
    assert not re.search(r"\.(Buy|Sell)\s*\(", outside), "trade.Buy/trade.Sell outside the entry routine"
    buys, sells = _calls(entry, "trade.Buy"), _calls(entry, "trade.Sell")
    assert len(buys) == 1 and len(sells) == 1
    for args in buys + sells:
        a = _split_args(args)
        # CTrade::Buy/Sell(volume, symbol, price, sl, tp, comment): the 4th argument is the structural SL
        assert len(a) == 6 and a[3] == "sl", args


def test_tp_is_modified_after_the_fill_from_the_deal_price(src):
    entry = _function_body(_strip_comments(src), ENTRY_ROUTINE)
    send = re.search(r"trade\.(Buy|Sell)\s*\(", entry).start()
    deal = entry.index("DEAL_PRICE")
    modify = entry.index("trade.PositionModify(")
    assert send < deal < modify, "fill price read from the deal after the send, then TP modified"
    assert re.search(r"fill\s*\+\s*d\s*\*\s*RiskRR\s*\*\s*MathAbs\(\s*fill\s*-\s*sl\s*\)", entry), \
        "TP = fill +/- RiskRR * |fill - SL| (R19)"
    margs = _split_args(_calls(entry, "trade.PositionModify")[0])
    assert margs[1] == "sl" and margs[2] == "tp"


def test_no_clamp_to_minimum_volume(src):
    code = _code(src)
    assert not re.search(r"MathMax\s*\([^;]*(?:VOLUME_MIN|min[A-Z]\w*)", code)
    assert not re.search(r"if\s*\(\s*(\w+)\s*<\s*(min\w*|\w*VOLUME_MIN\w*)\s*\)\s*\1\s*=\s*\2\s*;", code, re.I)
    entry = _function_body(_strip_comments(src), ENTRY_ROUTINE)
    assert "SYMBOL_VOLUME_MIN" in entry and "SYMBOL_VOLUME_STEP" in entry
    assert re.search(r"MathFloor\(", entry), "volume rounded down to the lot step (R20)"
    assert re.search(r'<\s*minVol[^;]*\)\s*\{?\s*SkipEntry\(\s*s\s*,\s*"skipped_volume"', entry)
    assert re.search(r'OrderCalcMargin[^;]*\)[^;]*\{?\s*SkipEntry\(\s*s\s*,\s*"skipped_margin"', entry, re.S)


def test_stops_level_read_at_entry_and_no_constant_distance(src):
    code = _code(src)
    no_inputs = "\n".join(ln for ln in code.splitlines() if not re.match(r"\s*input\b", ln))
    assert not re.search(r"(?<![\w.])0\.20?(?!\d)", no_inputs), "literal 0.2/0.20 found"
    assert not re.search(r"(?<![\w.])20(?![\w.])", no_inputs), "literal 20 (points) found outside the inputs"
    entry = _function_body(_strip_comments(src), ENTRY_ROUTINE)
    assert "SYMBOL_TRADE_STOPS_LEVEL" in entry and "SYMBOL_TRADE_FREEZE_LEVEL" in entry
    assert '"skipped_stops_level"' in entry


def test_stop_crossed_check_and_short_spread(src):
    code = _strip_comments(src)
    entry = _function_body(code, ENTRY_ROUTINE)
    # R15 / AE7: long Bid <= SL, short Ask >= SL, checked before any order is sent
    assert re.search(r"tk\.bid\s*<=\s*sl", entry) and re.search(r"tk\.ask\s*>=\s*sl", entry)
    crossed = entry.index('"skipped_stop_crossed"')
    assert crossed < re.search(r"trade\.(Buy|Sell)\s*\(", entry).start()
    # R18: the short stop adds the spread at entry; the request price is Ask long, Bid short (KTD7)
    stop = _function_body(code, "StopFor")
    assert re.search(r"tk\.ask\s*-\s*tk\.bid", stop)
    assert "StopBufferPoints" in stop
    assert re.search(r"\(\s*d\s*==\s*1\s*\)\s*\?\s*tk\.ask\s*:\s*tk\.bid", entry)


def test_skips_never_end_a_setup_and_market_closed_is_retried_without_cap(src):
    code = _strip_comments(src)
    skip = _function_body(code, "SkipEntry")
    assert "Finalize" not in skip and ".reason" not in skip and "ST_DONE" not in skip
    assert re.search(r"\.queued\s*=\s*false", skip), "a skip consumes only the reaction candle (KTD7)"
    entry = _function_body(code, ENTRY_ROUTINE)
    mc_branch = entry[entry.index("TRADE_RETCODE_MARKET_CLOSED"):]
    assert "mcWait = true" in mc_branch[:400] and "SkipEntry" not in mc_branch[:mc_branch.index("return")]
    assert not re.search(r"#define\s+\w*RETRY\w*", code), "no attempt cap (KTD7)"
    assert re.search(r"gCntMcRetries\+\+", _function_body(code, "RunEntries"))


def test_cap_counts_open_positions_only(src):
    code = _strip_comments(src)
    cnt = _function_body(code, "CountOpenPositions")
    assert "PositionsTotal" in cnt and "OrdersTotal" not in cnt
    entry = _function_body(code, ENTRY_ROUTINE)
    assert re.search(r'CountOpenPositions\(\)\s*>=\s*MaxExposures[^;]*\)\s*\{?\s*SkipEntry\(\s*s\s*,\s*"skipped_cap"',
                     entry)


def test_competition_orders_by_touch_then_ob_candle_then_id(src):
    beats = _function_body(_strip_comments(src), "Beats")
    assert re.search(r"touchMsc\s*>", beats) and re.search(r"obTime\s*>", beats) and re.search(r"id\s*<", beats)
    run = _function_body(_strip_comments(src), "RunEntries")
    assert '"lost_competition"' in run and "Beats(" in run


# --- KTD2 processing order, pivots and init ----------------------------------------------------------------------
def test_ontick_calls_steps_in_ktd2_order(src):
    body = _function_body(_strip_comments(src), "OnTick")
    pos = _order(body, TICK_STEPS)
    assert pos == sorted(pos), "OnTick must call " + " -> ".join(TICK_STEPS)


def test_m1_close_runs_setup_steps_in_ktd2_order(src):
    body = _function_body(_strip_comments(src), "ProcessM1Close")
    pos = _order(body, CLOSE_STEPS)
    assert pos == sorted(pos), "ProcessM1Close must call " + " -> ".join(CLOSE_STEPS)
    # cancellations beat everything later on the same bar: a cancelled setup leaves the loop
    for step in ("StepBreak", "StepOpposing"):
        assert re.search(r"if\s*\(\s*" + step + r"\([^;]*\)\s*\)\s*continue\s*;", body), step


def test_pivot_confirmation_needs_bar_p_plus_n_closed(src):
    body = _function_body(_strip_comments(src), "UpdatePivots")
    assert re.search(r"int\s+p\s*=\s*n\s*-\s*SwingStrengthM1\s*;", body)
    assert re.search(r"IsPivotHigh\(\s*p\s*,\s*SwingStrengthM1\s*\)", body)
    assert re.search(r"IsPivotLow\(\s*p\s*,\s*SwingStrengthM1\s*\)", body)
    ph = _function_body(_strip_comments(src), "IsPivotHigh")
    assert re.search(r"!\s*\(\s*gH1\[p\]\s*>\s*gH1\[j\]\s*\)", ph), "strict pivot high (R9)"
    assert "replacedBy" in _function_body(_strip_comments(src), "AppendPivot"), "KTD3 compression keeps replaced_by"


def test_oninit_guards_and_resets_state_before_warmup(src):
    code = _strip_comments(src)
    init = _function_body(code, "OnInit")
    assert "ResetState()" in init and init.index("ResetState()") < init.index("Warmup(")
    assert "PERIOD_M1" in init and "_Period" in init and "INIT_FAILED" in init
    assert "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING" in init and "INIT_PARAMETERS_INCORRECT" in init
    assert not re.search(r"\.(Buy|Sell|PositionModify)\s*\(", init), "no trading side effects in OnInit"
    warm = _function_body(code, "Warmup")
    assert re.search(r"CopyRates\(\s*_Symbol\s*,\s*PERIOD_M1", warm) and re.search(r"CopyRates\(\s*_Symbol\s*,\s*PERIOD_M5", warm)
    assert "WarmupDays" in warm and "86400" in warm
    reset = _function_body(code, "ResetState")
    for name in ("gO1", "gH1", "gL1", "gC1", "gT1", "gO5", "gH5", "gL5", "gC5", "gT5", "gPiv", "gSeq", "gUsedOb",
                 "gOb", "S", "gCand"):
        assert re.search(r"ArrayResize\(\s*" + name + r"\s*,\s*0\s*\)", reset), name
    for name in ("gM1", "gM5", "gLast1", "gLast5", "gCntIdentified", "gCntTouched", "gCntMcRetries", "gEvSeq"):
        assert re.search(name + r"\s*=\s*0\s*;", reset), name
    assert re.search(r"gNextId\s*=\s*1\s*;", reset)


def test_m5_is_read_through_copyrates(src):
    body = _function_body(_strip_comments(src), "ProcessNewM5Bars")
    assert re.search(r"CopyRates\(\s*_Symbol\s*,\s*PERIOD_M5", body)
    nc = _function_body(_strip_comments(src), "NewCandidate")
    assert "MarkObUsed" in nc and "ImpulseWindowBars" in nc
    assert not re.search(r"Bos|Volume|Age", nc)


# --- reason codes, events, funnel, journal ---------------------------------------------------------------------
def test_every_ktd16_code_event_kind_and_funnel_key_is_present(src):
    code = _strip_comments(src)
    for name in [*mc.REASONS, *mc.SKIP_EVENTS, *mc.EVENT_KINDS, *mc.SL_ANCHORS, *mc.EXIT_KINDS]:
        assert f'"{name}"' in code, name


def test_funnel_is_printed_on_short_lines_with_every_contract_key(src):
    code = _strip_comments(src)
    m = re.search(r"#define\s+FUNNEL_LINE_MAX\s+(\d+)", code)
    assert m and int(m.group(1)) <= FUNNEL_LINE_MAX
    funnel = _function_body(code, "PrintFunnel")
    add = _function_body(code, "FunnelAdd")
    prints = re.findall(r"\bPrint\(([^;]*)\);", funnel + add)
    assert len(prints) >= 2 and all(a.strip() == "line" for a in prints)
    inits = re.findall(r"\bline\s*=\s*([^;]+);", funnel + add)
    assert inits and all(i.strip() == '"Funnel:"' for i in inits)
    assert "FUNNEL_LINE_MAX" in add and "StringLen" in add
    keys = re.findall(r'FunnelAdd\(\s*line\s*,\s*"(\w+)"', funnel)
    assert keys == mc.FUNNEL_KEYS
    limit, lines, line = int(m.group(1)), [], "Funnel:"
    for k in keys:
        kv = f" {k}=1234567"
        if len(line) + len(kv) > limit and line != "Funnel:":
            lines.append(line)
            line = "Funnel:"
        line += kv
    lines.append(line)
    assert len(lines) >= 2 and all(len(x) <= FUNNEL_LINE_MAX for x in lines)


def test_journal_init_line(src):
    init = _function_body(_strip_comments(src), "OnInit")
    assert "Warm-up bars: " in init
    assert re.search(r'", tick "', init) and re.search(r'", stops level "', init) and '" pts"' in init


# --- research build ------------------------------------------------------------------------------------------------
def test_research_code_only_inside_non_nested_research_blocks(src):
    blocks = RESEARCH_BLOCK.findall(src)
    assert blocks
    for body in blocks:
        assert not re.search(r"#if", body), "nested preprocessor block inside RESEARCH_LOG"
        assert "#else" not in body
    outside = _code(RESEARCH_BLOCK.sub("", src))
    assert "ResearchRunTag" not in outside
    assert not re.search(r"\bRL_\w+", outside), "research helper referenced outside a research block"
    assert not re.search(r"\brl[A-Z]\w*", outside), "research state referenced outside a research block"
    assert "OnTester" not in outside and "FileOpen" not in outside


def test_research_csv_headers_equal_contract(src):
    research = "".join(RESEARCH_BLOCK.findall(src))
    for name, define in HEADER_DEFINES.items():
        assert _define(research, define) == ",".join(mc.FILES[name]), name
    assert _file_header(research, "date") == ",".join(mc.DAYS_COLUMNS)
    assert _file_header(research, "time") == ",".join(mc.DEALS_COLUMNS)
    write = _function_body(_strip_comments(research), "RL_WriteRunFiles")
    for name in mc.FILES:
        assert f'"rl_{name}_"' in write, name
    for define in set(HEADER_DEFINES.values()):
        assert define in write, define


def test_rl_days_and_rl_deals_headers_equal_predecessor(src):
    old = PREDECESSOR.read_text(encoding="utf-8")
    for first in ("date", "time"):
        assert _file_header(src, first) == _file_header(old, first)


# --- 1R trailing stop (plan 2026-10-05-0007, U2) ----------------------------------------------------------------------
def test_trail_runs_every_tick_after_sync_and_only_with_its_input(src):
    code = _strip_comments(src)
    tick = _function_body(code, "OnTick")
    assert tick.index("SyncTrades(") < tick.index("ManageTrails(") < tick.index("ProcessNewM1Bars(")
    body = _function_body(code, "ManageTrails")
    assert re.match(r"\s*if\s*\(\s*!EnableTrailingStop\s*\)\s*return\s*;", body)


def test_every_trail_side_effect_is_behind_the_input(src):
    code = _strip_comments(src)
    assert re.search(r"if\s*\(\s*EnableTrailingStop\s*\)\s*TrailRegister\s*\(", _function_body(code, "TryEntry"))
    assert re.search(r"if\s*\(\s*EnableTrailingStop\s*\)\s*TrailRestore\s*\(", _function_body(code, "OnInit"))
    write = _function_body(code, "RL_WriteRunFiles")
    guarded = write[write.index("if(EnableTrailingStop)"):]
    assert '"rl_trail_"' in guarded and '"rl_sl_moves_"' in guarded
    # nothing else registers, saves or restores trail state
    outside = re.sub(r"\b(?:TrailRegister|TrailRestore|TrailStore|TrailFlush|TrailLoad|TrailSend|TrailNotSent|TrailClose|TrailForget|"
                     r"ManageTrails)\s*\([^;{]*\)\s*\{", "", code)
    for name in ("TrailRegister", "TrailRestore"):
        assert len(re.findall(r"\b" + name + r"\s*\(", outside)) == 1, name


def test_trail_modifies_by_ticket_and_verifies_retcode_and_the_stop_on_the_position(src):
    body = _function_body(_strip_comments(src), "TrailSend")
    args = _calls(body, "trade.PositionModify")
    assert len(args) == 1 and _split_args(args[0]) == ["t.ticket", "req", "posTp"]
    assert "ResultRetcode()" in body and "TRADE_RETCODE_DONE" in body
    assert "PositionSelectByTicket(t.ticket)" in body and "PositionGetDouble(POSITION_SL)" in body
    assert "PositionGetDouble(POSITION_TP)" in body
    assert re.search(r"acc\s*=\s*ok\s*&&\s*rc\s*==\s*TRADE_RETCODE_DONE\s*&&\s*MathAbs\(slRead\s*-\s*req\)", body)
    assert "PositionModify" not in _function_body(_strip_comments(src), "ManageTrails")


def test_trail_request_formula_rounding_and_no_spread_term(src):
    body = _code(_function_body(src, "ManageTrails"))
    assert re.search(r"RoundTick\(gTr\[i\]\.best\s*-\s*gTr\[i\]\.r0,\s*false\)", body)
    assert re.search(r"RoundTick\(gTr\[i\]\.best\s*\+\s*gTr\[i\]\.r0,\s*true\)", body)
    assert re.search(r"tk\.bid\s*>=\s*gTr\[i\]\.fill\s*\+\s*gTr\[i\]\.r0", body)
    assert re.search(r"tk\.ask\s*<=\s*gTr\[i\]\.fill\s*-\s*gTr\[i\]\.r0", body)
    assert "tk.ask - tk.bid" not in body and "spread" not in body.lower()
    assert re.search(r"MathMax\(gTr\[i\]\.best,\s*tk\.bid\)", body)
    assert re.search(r"MathMin\(gTr\[i\]\.best,\s*tk\.ask\)", body)
    assert "SYMBOL_TRADE_STOPS_LEVEL" in body and "SYMBOL_TRADE_FREEZE_LEVEL" in body


def test_r0_is_set_only_from_the_fill_and_the_accepted_sl0_never_from_the_current_stop(src):
    code = _code(src)
    sets = re.findall(r"\b(\w+)\.r0\s*=(?!=)", code)
    assert sets == ["t", "t", "t"]                                   # TrailInit, TrailLoad, TrailRegister
    reg = _function_body(_strip_comments(src), "TrailRegister")
    assert re.search(r"t\.r0\s*=\s*MathAbs\(t\.fill\s*-\s*t\.sl0\)", reg)
    assert re.search(r"t\.sl0\s*=\s*PositionGetDouble\(POSITION_SL\)", reg)
    restore = _function_body(_strip_comments(src), "TrailRestore")
    assert "TrailLoad(" in restore and not re.search(r"\.sl0\s*=", restore)
    assert "PositionModify" not in restore


def test_trail_state_is_stored_per_magic_and_ticket_and_flushed_separately(src):
    code = _strip_comments(src)
    assert re.search(r'"OBM1T\."\s*\+\s*IntegerToString\(MagicNumber\)\s*\+\s*"\."\s*\+\s*'
                     r'IntegerToString\(\(long\)ticket\)', code)
    store = _function_body(code, "TrailStore")
    for key in ("dir", "e", "sl0", "r0", "tp", "best", "act", "actms"):
        assert f'p + "{key}"' in store, key
    assert "GlobalVariablesFlush" not in store and "gTrDirty = true" in store      # plan 0128 KTD4
    assert "GlobalVariablesFlush()" in _function_body(code, "TrailFlush")
    assert "TrailFlush(" in _function_body(code, "TrailRegister")
    manage = _function_body(code, "ManageTrails")
    assert re.search(r"gTrDirty\s*&&\s*tk\.time_msc\s*-\s*gTrLastFlush\s*>=\s*TRAIL_FLUSH_MS", manage)
    deinit = _function_body(code, "OnDeinit")
    assert deinit.index("TrailFlush(") < deinit.index("TrailClose(")
    assert re.search(r"#define\s+TRAIL_FLUSH_MS\s+10000\b", code)


def test_trail_best_is_stored_on_every_change_before_any_hold(src):
    body = _function_body(_strip_comments(src), "ManageTrails")
    store = body.index("TrailStore(gTr[i])")
    assert store < body.index("MathMin(gTr[i].best, tk.ask)") + 200 and store < body.index("closedBar == bar")
    assert store < body.index("rejectedMsc") and store < body.index("gTrBackoffUntil")


def test_trail_retry_wait_backoff_and_one_retry_per_tick(src):
    code = _strip_comments(src)
    assert re.search(r"#define\s+TRAIL_REJECT_WAIT_MS\s+1000\b", code)
    assert re.search(r"TRAIL_BACKOFF_S\[\]\s*=\s*\{\s*1,\s*2,\s*4,\s*8,\s*16,\s*30\s*\}", code)
    body = _function_body(code, "ManageTrails")
    assert re.search(r"tk\.time_msc\s*-\s*gTr\[i\]\.rejectedMsc\s*<\s*TRAIL_REJECT_WAIT_MS", body)
    assert re.search(r"tk\.time_msc\s*<\s*gTrBackoffUntil", body)
    assert re.search(r"retry\s*&&\s*gTrSlotMsc\s*==\s*tk\.time_msc", body)
    assert body.index("closedBar == bar") < body.index("rejectedMsc") < body.index("gTrBackoffUntil")         < body.index("gTrSlotMsc ==") < body.index("SYMBOL_TRADE_STOPS_LEVEL")
    send = _function_body(code, "TrailSend")
    assert re.search(r"t\.rejectedMsc\s*=\s*tk\.time_msc", send)
    assert re.search(r"rc\s*==\s*TRADE_RETCODE_TOO_MANY_REQUESTS", send)
    assert re.search(r"gTrBackoffUntil\s*=\s*tk\.time_msc\s*\+\s*TrailBackoffMs\(gTrStreak\)", send)
    acc = send[send.index("if(acc)"):send.index("else")]
    assert "t.rejectedMsc = 0" in acc and "gTrStreak = 0" in acc


def test_every_fill_gets_a_registry_entry_and_untrailable_ones_never_send(src):
    code = _strip_comments(src)
    reg = _function_body(code, "TrailRegister")
    assert reg.count("TrailAdd(t)") == 3                         # not selectable, no risk, trailed
    assert '"not_selectable"' in reg and '"no_risk"' in reg
    body = _function_body(code, "ManageTrails")
    assert body.index("!gTr[i].trailable") < body.index("TrailSend(")
    assert "TrailTracked(" in body                               # a tracked position is left to SyncPosition


def test_trailed_stop_exit_is_classified_trail(src):
    body = _function_body(_strip_comments(src), "SyncPosition")
    assert re.search(r'DEAL_REASON_SL\)\s*\?\s*\(moved\s*\?\s*"trail"\s*:\s*"sl"\)', body)


def test_trail_csv_headers_equal_contract(src):
    research = "".join(RESEARCH_BLOCK.findall(src))
    assert _define(research, "RL_TRAIL_HEADER") == ",".join(mc.TRAIL_COLUMNS)
    assert _define(research, "RL_SLMOVES_HEADER") == ",".join(mc.SL_MOVE_COLUMNS)


def test_market_closed_blocks_trail_requests_until_the_next_bar(src):
    code = _strip_comments(src)
    assert re.search(r"if\s*\(\s*rc\s*==\s*TRADE_RETCODE_MARKET_CLOSED\s*\)\s*t\.closedBar\s*=\s*bar", _function_body(code, "TrailSend"))
    body = _function_body(code, "ManageTrails")
    assert body.index("closedBar == bar") < body.index("RoundTick(")
