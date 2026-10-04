"""Log and input contract of the M5 OB + M1 structure EA (plan 2026-10-03-0013, KTD10, KTD16).

One source of truth shared by the EA's static tests, the conformance checker (conformance_m1.py), the charts
(charts_m1.py) and the pipeline. The EA writes these CSV headers verbatim; Python code reads columns by these names.

Times: bar times are bar OPEN times in epoch seconds (server time); tick-event times are epoch milliseconds.
Prices are Bid unless a column says otherwise. Empty field = not applicable.
"""

EA_SOURCE = "ob_m1_structure.mq5"
EA_RESEARCH_SOURCE = "ob_m1_structure_research.mq5"
CHART_PERIOD = "M1"     # the tester chart; OnTick sees every M1 close
ZONE_PERIOD = "M5"      # identification timeframe (CopyRates)

# --- inputs, in source order: (type, name, default) -------------------------------------------------------------
INPUTS = [
    ("ENUM_STRUCTURE_VARIANT", "StructureVariant", "0"),   # 0 = A (HH only), 1 = B (HH + confirmed HL)
    ("int", "ImpulseWindowBars", "2"),
    ("int", "SwingStrengthM1", "3"),
    ("int", "StopBufferPoints", "20"),
    ("double", "RiskRR", "2.0"),
    ("double", "RiskPercent", "1.0"),
    ("int", "MaxExposures", "3"),
    ("int", "WarmupDays", "30"),
    ("long", "MagicNumber", "770201"),
    ("string", "TradeComment", "OBM1"),
]
RESEARCH_INPUTS = [("string", "ResearchRunTag", '""')]   # research build only
ENUMS = {"ENUM_STRUCTURE_VARIANT": {"SV_HH_ONLY": 0, "SV_HH_HL": 1}}

# --- research log files: rl_<name>_<tag>.csv ------------------------------------------------------------------
BARS_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "warmup"]   # rl_bars_m1, rl_bars_m5
PIVOT_COLUMNS = ["pivot_id", "type", "peak_time", "conf_time", "level", "replaced_by", "outside_bar"]
EVENT_COLUMNS = ["setup_id", "seq", "kind", "bar_time", "tick_msc", "price", "lo", "hi", "ref_id", "ref_time",
                 "detail"]
SETUP_COLUMNS = [
    "setup_id", "dir", "variant", "ob_time", "ob_high", "ob_low", "idfvg_c1_time", "idfvg_c3_time", "idfvg_low",
    "idfvg_high", "identified_in_warmup", "touch_msc", "touch_bar_time", "ob_age_bars_touch", "ob_age_min_touch",
    "breaks", "returns", "sc_bar_time", "origin_time", "ref_pivot_id", "hl_pivot_id", "fvg_c1_time", "fvg_low",
    "fvg_high", "reaction_bar_time", "entry_request_msc", "request_price", "attempts", "sl", "sl_anchor",
    "sl_anchor_price", "buffer_pts", "tp", "volume", "fill_msc", "fill_price", "position_id", "ob_age_bars_entry",
    "ob_age_min_entry", "exit_msc", "exit_price", "exit_kind", "reason", "reason_msc",
]
DAYS_COLUMNS = ["date", "bal_open", "eq_open", "eq_min", "eq_max", "bal_close", "eq_close", "spread_median"]
DEALS_COLUMNS = ["time", "ticket", "position_id", "type", "entry", "volume", "price", "profit", "commission", "swap",
                 "magic", "comment"]
FILES = {"bars_m1": BARS_COLUMNS, "bars_m5": BARS_COLUMNS, "pivots": PIVOT_COLUMNS, "events": EVENT_COLUMNS,
         "setups": SETUP_COLUMNS, "days": DAYS_COLUMNS, "deals": DEALS_COLUMNS}

# --- event kinds (rl_events.kind) and what their columns carry -------------------------------------------------
EVENT_KINDS = {
    "touch":            "tick_msc, price=Bid, bar_time=M1 bar containing the tick",
    "break":            "bar_time=closing bar, price=close",
    "return":           "tick_msc, price=Bid, bar_time=M1 bar containing the tick",
    "sc_hh":            "structure change (HH long / LL short): bar_time=break bar, price=reference level, "
                        "ref_id=reference pivot, ref_time=origin bar time",
    "sc_superseded":    "bar_time, ref_id=new reference pivot (the live structure change was replaced)",
    "fvg_fixed":        "bar_time=bar whose close fixed it, ref_time=FVG candle 1 time, lo/hi=zone",
    "fvg_none":         "bar_time=bar k+1 after the HH bar; no FVG qualified",
    "fvg_lapsed":       "bar_time=closing bar beyond the FVG far edge",
    "hl":               "HL long / LH short: bar_time=confirmation bar, ref_id=pivot, price=level",
    "hl_failed":        "variant B only: bar_time=confirmation bar, ref_id=pivot, price=level",
    "reaction":         "bar_time=reaction bar",
    "lost_competition": "bar_time=reaction bar, ref_id=winning setup id",
    "entry_attempt":    "tick_msc, price=request price, lo=Bid, hi=Ask at that tick, detail=retcode",
    "skipped_stop_crossed": "tick_msc, price, lo=sl",
    "skipped_stops_level":  "tick_msc, price, detail=stops level points",
    "skipped_volume":       "tick_msc, price, detail=computed lots",
    "skipped_margin":       "tick_msc, price",
    "skipped_cap":          "tick_msc, detail=open positions",
    "skipped_broker_reject": "tick_msc, detail=retcode",
    "fill":             "tick_msc, price=fill price, lo=sl, hi=tp after modify",
    "exit":             "tick_msc, price, detail=sl|tp|end",
    "cancelled_second_break":       "bar_time=closing bar",
    "cancelled_opposing_structure": "bar_time=closing bar, ref_id=H2 (LH) pivot long / L2 (HL) pivot short, "
                                    "ref_time=peak time of L1 / H1 that was broken",
}
SKIP_EVENTS = ["skipped_stop_crossed", "skipped_stops_level", "skipped_volume", "skipped_margin", "skipped_cap",
               "skipped_broker_reject", "lost_competition"]   # never end a setup (KTD7)

# --- final reasons (rl_setups.reason) -------------------------------------------------------------------------
REASONS = ["cancelled_second_break", "cancelled_opposing_structure", "filled", "run_end_waiting",
           "run_end_untouched", "warmup_dropped"]
SL_ANCHORS = ["ob", "hl", "pivot"]
EXIT_KINDS = ["sl", "tp", "end"]

# --- journal funnel keys (printed as 'Funnel: k=v ...' on several short lines) ---------------------------------
FUNNEL_KEYS = ["identified", "touched", "structure_changes", "fvg_fixed", "ready", "reactions", "entries",
               "filled", *REASONS[:2], *SKIP_EVENTS, "warmup_dropped", "run_end_waiting", "run_end_untouched",
               "market_closed_retries"]
