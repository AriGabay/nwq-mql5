from mt5r import journal

INIT = "OB-FVG retest initialised. Warm-up bars: 3000, tick 0.01, stops level 20 pts\n"


def test_funnel_line_parses_to_int_dict():
    text = (INIT + "some other line\n"
            "2026.07.31 23:59:59   Funnel: activated=120 touched=80 confirmed=40 placed=30 filled=22 "
            "filled_late=1 expired_untouched=40 skipped_cap=3 run_end_pending=0 warmup_dropped=7\n")
    f = journal.facts(text)
    assert f["funnel"] == {"activated": 120, "touched": 80, "confirmed": 40, "placed": 30, "filled": 22,
                           "filled_late": 1, "expired_untouched": 40, "skipped_cap": 3, "run_end_pending": 0,
                           "warmup_dropped": 7}
    assert f["warmup_bars"] == 3000 and f["stops_level_pts"] == 20 and f["tick"] == 0.01


def test_last_funnel_line_wins_and_missing_funnel_is_empty():
    text = INIT + "Funnel: activated=1 filled=0\nFunnel: activated=5 filled=2\n"
    assert journal.facts(text)["funnel"] == {"activated": 5, "filled": 2}
    assert journal.facts(INIT)["funnel"] == {}


def test_old_funnel_lines_are_not_parsed():
    text = INIT + "orders sent: 12\nrejected by volume: 3\n"
    assert journal.facts(text)["funnel"] == {}
