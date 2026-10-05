"""Dukascopy tick decoding and data-quality checks (data check, 2026-10-05): no strategy, no P&L."""
import datetime as dt
import lzma
import struct

import pandas as pd
import pytest

from mt5r import dukascopy as dk

H0 = dt.datetime(2024, 1, 2, 10, tzinfo=dt.timezone.utc)


def blob(rows):
    """rows: (ms offset, raw ask, raw bid, ask vol, bid vol) -> an LZMA .bi5 hour."""
    return lzma.compress(b"".join(struct.pack(">IIIff", *r) for r in rows), format=lzma.FORMAT_ALONE)


def test_url_uses_a_zero_based_month_and_utc_hour():
    assert dk.url("XAUUSD", H0) == "https://datafeed.dukascopy.com/datafeed/XAUUSD/2024/00/02/10h_ticks.bi5"
    assert dk.url("XAUUSD", dt.datetime(2026, 3, 31, 23, tzinfo=dt.timezone.utc)).endswith("/2026/02/31/23h_ticks.bi5")


def test_every_hour_of_the_month_in_utc():
    hs = dk.month_hours(2024, 1)
    assert len(hs) == 31 * 24 and hs[0] == dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    assert hs[-1] == dt.datetime(2024, 1, 31, 23, tzinfo=dt.timezone.utc)


def test_decode_scales_prices_and_adds_the_hour_start():
    df = dk.decode(blob([(128, 2077255, 2076965, 0.00012, 0.00024), (3599210, 2076265, 2075925, 1.5, 2.5)]), H0)
    t0 = int(H0.timestamp() * 1000)
    assert df["time_msc"].tolist() == [t0 + 128, t0 + 3599210]
    assert df["ask"].tolist() == pytest.approx([2077.255, 2076.265]) and df["bid"].iloc[0] == pytest.approx(2076.965)
    assert df["raw_ask"].tolist() == [2077255, 2076265]
    assert df["bid_vol"].iloc[0] == pytest.approx(0.00024)


def test_an_empty_hour_decodes_to_no_ticks_and_a_truncated_one_fails():
    assert len(dk.decode(b"", H0)) == 0
    bad = lzma.compress(b"\x00" * 19, format=lzma.FORMAT_ALONE)
    with pytest.raises(ValueError, match="not a multiple of 20"):
        dk.decode(bad, H0)


def ticks(rows):
    t0 = int(H0.timestamp() * 1000)
    return pd.DataFrame([{"time_msc": t0 + ms, "ask": a, "bid": b, "ask_vol": 1.0, "bid_vol": 1.0,
                          "raw_ask": int(round(a * 1000)), "raw_bid": int(round(b * 1000))} for ms, a, b in rows])


def test_quality_counts_crossed_prices_same_millisecond_out_of_order_and_exact_duplicates():
    df = ticks([(0, 2000.30, 2000.00), (10, 2000.30, 2000.00), (10, 2000.30, 2000.00), (10, 2000.31, 2000.01),
                (5, 2000.20, 2000.25), (20, 2000.00, 2000.00)])
    q = dk.quality(df)
    assert q["ticks"] == 6
    assert q["crossed"] == 1 and q["zero_spread"] == 1 and q["nonpositive"] == 0
    assert q["out_of_order"] == 1                      # 10 -> 5
    assert q["same_ms_as_previous"] == 2               # the second and third tick at 10 ms
    assert q["exact_duplicates"] == 1                  # only the identical copy, not the different tick at 10 ms
    assert len(df) == 6                                # nothing is removed


def test_gaps_are_classified_without_filling_them():
    t = lambda s: int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    kind = lambda a, b: [g["kind"] for g in dk.gaps(pd.DataFrame({"time_msc": [t(a), t(b)]}), min_seconds=300)]
    assert kind("2024-01-03T21:58:00", "2024-01-03T23:01:00") == ["daily_break"]
    assert kind("2024-01-05T21:58:00", "2024-01-07T23:00:30") == ["weekend"]
    assert kind("2024-01-08T10:00:00", "2024-01-08T10:20:00") == ["unexplained"]
    assert kind("2024-01-08T10:00:00", "2024-01-08T10:04:59") == []
    holiday = dk.gaps(pd.DataFrame({"time_msc": [t("2024-01-01T10:00:00"), t("2024-01-01T12:00:00")]}),
                      holidays=[dt.date(2024, 1, 1)])
    assert holiday[0]["kind"] == "holiday" and holiday[0]["seconds"] == 7200


def test_m1_bars_are_bid_ohlc_with_tick_count_and_spread():
    df = ticks([(0, 2000.30, 2000.00), (30_000, 2001.30, 2001.00), (59_999, 1999.30, 1999.00),
                (60_000, 2000.50, 2000.10)])
    b = dk.bars(df, 60)
    assert len(b) == 2 and b.iloc[0][["open", "high", "low", "close"]].tolist() == [2000.00, 2001.00, 1999.00, 1999.00]
    assert b["ticks"].tolist() == [3, 1] and b["spread_max"].iloc[0] == pytest.approx(0.30)


def test_the_clock_offset_is_found_from_the_data_not_assumed():
    rng = pd.Series(range(600)).astype(float)
    duka = pd.DataFrame({"time": 1_700_000_000 + 60 * rng.index, "close": 2000 + (rng * 7 % 13)})
    other = duka.assign(time=duka["time"] + 2 * 3600, close=duka["close"] + 0.05)      # server clock = UTC + 2 h
    res = dk.best_offset(duka, other)
    assert res["best_hours"] == 2 and res["scores"][2]["median_abs_close_diff"] == pytest.approx(0.05)
    assert res["scores"][0]["minutes"] > 0 and res["scores"][0]["median_abs_close_diff"] > 1


# ------------------------------------------------------------------ manual web export (one file per side)
def export_csv(path, rows):
    """rows: (iso second, price, volume) -> a tick-export CSV as the widget writes it."""
    lines = ["Etc/UTC,Open,High,Low,Close,Volume"] + [f"{t},{p},{p},{p},{p},{v}" for t, p, v in rows]
    path.write_text("\n".join(lines), encoding="ascii")
    return path


T = "2024-01-02T10:00:00+00:00"
T1 = "2024-01-02T10:00:01+00:00"


def test_the_export_reader_refuses_layouts_it_was_not_checked_on(tmp_path):
    ok = dk.read_export_csv(export_csv(tmp_path / "ok.csv", [(T, "2077.255", 120), (T, "2077.225", 120)]))
    assert ok["raw"].tolist() == [2077255, 2077225] and ok["time_s"].tolist() == [int(H0.timestamp())] * 2
    bar = tmp_path / "bar.csv"
    bar.write_text(f"Etc/UTC,Open,High,Low,Close,Volume\n{T},1.0,2.0,0.5,1.5,10", encoding="ascii")
    with pytest.raises(ValueError, match="not all equal"):
        dk.read_export_csv(bar)
    other_tz = tmp_path / "tz.csv"
    other_tz.write_text("Etc/UTC,Open,High,Low,Close,Volume\n2024-01-02T12:00:00+02:00,1,1,1,1,1", encoding="ascii")
    with pytest.raises(ValueError, match="UTC"):
        dk.read_export_csv(other_tz)
    head = tmp_path / "head.csv"
    head.write_text("Time,Bid,Ask\n2024-01-02 10:00:00.123,1,2", encoding="ascii")
    with pytest.raises(ValueError, match="header"):
        dk.read_export_csv(head)


def test_sides_are_paired_by_row_never_joined_by_a_shared_second(tmp_path):
    ask = dk.read_export_csv(export_csv(tmp_path / "a.csv", [(T, "2000.30", 1), (T, "2000.50", 1), (T1, "2000.40", 1)]))
    bid = dk.read_export_csv(export_csv(tmp_path / "b.csv", [(T, "2000.00", 2), (T, "2000.20", 2), (T1, "2000.10", 2)]))
    p = dk.pair_export_sides(ask, bid)
    assert list(zip(p["raw_ask"], p["raw_bid"])) == [(2000300, 2000000), (2000500, 2000200), (2000400, 2000100)]
    # a time join would match each of the two ticks at 10:00:00 with both bids (4 rows instead of 2)
    assert len(ask.merge(bid, on="time_s")) == 5


def test_pairing_is_refused_when_rows_cannot_be_the_same_quote(tmp_path):
    ask = dk.read_export_csv(export_csv(tmp_path / "a.csv", [(T, "2000.30", 1), (T1, "2000.40", 1)]))
    short = dk.read_export_csv(export_csv(tmp_path / "s.csv", [(T, "2000.00", 1)]))
    with pytest.raises(ValueError, match="cannot be paired"):
        dk.pair_export_sides(ask, short)
    shifted = dk.read_export_csv(export_csv(tmp_path / "t.csv", [(T, "2000.00", 1), (T, "2000.10", 1)]))
    with pytest.raises(ValueError, match="different time"):
        dk.pair_export_sides(ask, shifted)
    crossed = dk.read_export_csv(export_csv(tmp_path / "c.csv", [(T, "2000.00", 1), (T1, "2000.50", 1)]))
    with pytest.raises(ValueError, match="ask below bid"):
        dk.pair_export_sides(ask, crossed)


def test_the_feed_comparison_is_row_by_row_and_reports_the_lost_milliseconds(tmp_path):
    feed = ticks([(5, 2000.30, 2000.00), (700, 2000.50, 2000.20), (999, 2000.40, 2000.10)])
    rows = [(T, "2000.30", "2000.00"), (T, "2000.50", "2000.20"), (T1, "2000.40", "2000.10")]
    ask = dk.read_export_csv(export_csv(tmp_path / "a.csv", [(t, a, 1e6) for t, a, _ in rows]))
    bid = dk.read_export_csv(export_csv(tmp_path / "b.csv", [(t, b, 1e6) for t, _, b in rows]))
    r = dk.compare_export_to_feed(dk.pair_export_sides(ask, bid), feed)
    assert r["identical_prices_in_order"] and r["volume_mismatch"] == 0
    assert r["time_equal_to_feed_second"] == 2 and r["time_one_second_later"] == 1
    assert r["time_one_second_later_feed_ms"] == [999] and r["feed_ticks_sharing_a_second"] == 3
    # the same ticks in another order within the second: same count, prices no longer identical in order
    swapped = [rows[1], rows[0], rows[2]]
    ask2 = dk.read_export_csv(export_csv(tmp_path / "a2.csv", [(t, a, 1e6) for t, a, _ in swapped]))
    bid2 = dk.read_export_csv(export_csv(tmp_path / "b2.csv", [(t, b, 1e6) for t, _, b in swapped]))
    r2 = dk.compare_export_to_feed(dk.pair_export_sides(ask2, bid2), feed)
    assert r2["same_count"] and not r2["identical_prices_in_order"] and r2["ask_mismatch"] == 2
