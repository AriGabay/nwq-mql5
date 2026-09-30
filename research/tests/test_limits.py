import pandas as pd
import pytest

from mt5r import limits


def days(rows):
    df = pd.DataFrame(rows, columns=["bal_open", "eq_min"])
    df.insert(0, "date", pd.date_range("2026-03-02", periods=len(df), freq="D"))
    return df


def test_ae5_daily_breach():
    out = limits.evaluate(days([(10400.0, 9890.0), (10400.0, 9901.0)]))
    assert list(out["daily_floor"]) == [9900.0, 9900.0]
    assert list(out["daily_breach"]) == [True, False]
    assert not out["total_breach"].any()


def test_total_floor_boundary():
    out = limits.evaluate(days([(9400.0, 9000.00), (9400.0, 8999.99)]))
    assert list(out["total_breach"]) == [False, True]
    assert not out["daily_breach"].any()


def test_first_breach():
    df = days([(10000.0, 9950.0), (10400.0, 9890.0), (9000.0, 8000.0)])
    fb = limits.first_breach(df)
    assert fb["date"] == pd.Timestamp("2026-03-03")
    assert fb["type"] == "daily"
    assert fb["eq_min"] == 9890.0
    assert limits.first_breach(df.iloc[:1]) is None
    assert limits.first_breach(df.iloc[2:])["type"] == "both"
