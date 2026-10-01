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


def test_multi_line_funnel_is_merged():
    # AMENDMENT B: the EA prints the funnel on several short lines because MT5's journal truncates long ones
    text = (INIT + "2026.07.31 23:59:59   Funnel: activated=120 touched=80 confirmed=40 placed=30\n"
            "2026.07.31 23:59:59   Funnel: filled=22 filled_late=1 skipped_cap=3\n"
            "2026.07.31 23:59:59   unrelated line\n"
            "2026.07.31 23:59:59   Funnel: warmup_dropped=7 idfvg_rejected_volume=4 market_closed_retries=2\n")
    assert journal.facts(text)["funnel"] == {
        "activated": 120, "touched": 80, "confirmed": 40, "placed": 30, "filled": 22, "filled_late": 1,
        "skipped_cap": 3, "warmup_dropped": 7, "idfvg_rejected_volume": 4, "market_closed_retries": 2}


def test_later_funnel_keys_override_earlier_ones():
    text = INIT + "Funnel: activated=1 touched=1\nFunnel: activated=5 placed=2\n"
    assert journal.facts(text)["funnel"] == {"activated": 5, "touched": 1, "placed": 2}


def test_run_facts_merge_only_the_last_test(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "Tester__logs__20260731.log").write_text(
        "testing of Experts\\old.ex5 started\nFunnel: activated=9 skipped_cap=9\nFunnel: market_closed_retries=9\n"
        "testing of Experts\\ob_fvg_retest.ex5 started\n" + INIT
        + "Funnel: activated=3 touched=2\nFunnel: market_closed_retries=1\n")
    assert journal.run_facts(tmp_path)["funnel"] == {"activated": 3, "touched": 2, "market_closed_retries": 1}
