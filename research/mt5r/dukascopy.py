"""Dukascopy tick history: decoding and data-quality checks (data check of 2026-10-05; no strategy, no P&L).

Source: the free Dukascopy historical data feed, one LZMA-compressed file per UTC hour,
    https://datafeed.dukascopy.com/datafeed/<SYMBOL>/<YYYY>/<MM-1>/<DD>/<HH>h_ticks.bi5
(the month in the path is zero-based). Each record is 20 bytes, big-endian: uint32 milliseconds since the hour
start, uint32 ask, uint32 bid, float32 ask volume, float32 bid volume. Prices are integers in units of
1/PRICE_DIVISOR; for XAUUSD the divisor is 1000 (verified against the market level, not assumed from FX pairs).

Nothing here removes, fills or corrects ticks: the checks count what they find and report it.
"""
import datetime as dt
import lzma
import struct

import numpy as np
import pandas as pd

BASE = "https://datafeed.dukascopy.com/datafeed"
RECORD = struct.Struct(">IIIff")
PRICE_DIVISOR = {"XAUUSD": 1000}
COLUMNS = ["time_msc", "ask", "bid", "ask_vol", "bid_vol", "raw_ask", "raw_bid"]
UTC = dt.timezone.utc


def url(symbol: str, hour: dt.datetime) -> str:
    """The feed file of one UTC hour; the month in the path is zero-based."""
    return f"{BASE}/{symbol}/{hour.year:04d}/{hour.month - 1:02d}/{hour.day:02d}/{hour.hour:02d}h_ticks.bi5"


def month_hours(year: int, month: int) -> list:
    """Every UTC hour of a calendar month."""
    t = dt.datetime(year, month, 1, tzinfo=UTC)
    end = dt.datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
    out = []
    while t < end:
        out.append(t)
        t += dt.timedelta(hours=1)
    return out


def decode(data: bytes, hour: dt.datetime, divisor: int = 1000) -> pd.DataFrame:
    """One hour file -> ticks with epoch-millisecond times (UTC) and prices divided by ``divisor``.
    An empty file is an hour without ticks; a payload that is not whole records is an error, never truncated."""
    if not data:
        return pd.DataFrame(columns=COLUMNS)
    raw = lzma.decompress(data)
    if len(raw) % RECORD.size:
        raise ValueError(f"{hour:%Y-%m-%d %H}h: {len(raw)} bytes is not a multiple of {RECORD.size}")
    rec = np.array(list(RECORD.iter_unpack(raw)), dtype=float).reshape(-1, 5)
    t0 = int(hour.timestamp() * 1000)
    return pd.DataFrame({"time_msc": t0 + rec[:, 0].astype(np.int64),
                         "ask": rec[:, 1] / divisor, "bid": rec[:, 2] / divisor,
                         "ask_vol": rec[:, 3], "bid_vol": rec[:, 4],
                         "raw_ask": rec[:, 1].astype(np.int64), "raw_bid": rec[:, 2].astype(np.int64)})


def quality(df: pd.DataFrame) -> dict:
    """Counts of what is unusual in a tick table, in file order. Nothing is removed."""
    t = df["time_msc"].to_numpy()
    d = np.diff(t)
    spread = (df["ask"] - df["bid"]).to_numpy()
    out = {"ticks": int(len(df)),
           "nonpositive": int(((df["ask"] <= 0) | (df["bid"] <= 0)).sum()),
           "crossed": int((spread < 0).sum()),
           "zero_spread": int((spread == 0).sum()),
           "out_of_order": int((d < 0).sum()),
           "same_ms_as_previous": int((d == 0).sum()),
           "exact_duplicates": int(df.duplicated(subset=[c for c in COLUMNS if c in df]).sum())}
    if len(df):
        q = np.quantile(spread, [0, 0.01, 0.5, 0.99, 1])
        out["spread"] = {k: round(float(v), 4) for k, v in zip(("min", "p01", "median", "p99", "max"), q)}
        if "raw_bid" in df:
            out["raw_last_digit_bid"] = {int(k): int(v) for k, v in
                                         pd.Series(df["raw_bid"] % 10).value_counts().sort_index().items()}
    return out


def _spans_saturday(a: dt.datetime, b: dt.datetime) -> bool:
    d = a.date()
    while d <= b.date():
        if d.weekday() == 5:
            return True
        d += dt.timedelta(days=1)
    return False


def gaps(df: pd.DataFrame, min_seconds: int = 300, holidays=()) -> list:
    """Every pause of at least ``min_seconds`` between consecutive ticks, classified (never filled):
    weekend (the pause covers a Saturday), daily_break (starts 20:45-22:15 UTC and lasts at most 80 minutes),
    holiday (starts on a listed date), otherwise unexplained."""
    t = np.sort(df["time_msc"].to_numpy())
    out = []
    for a, b in zip(t[:-1], t[1:]):
        sec = (b - a) / 1000
        if sec < min_seconds:
            continue
        sa = dt.datetime.fromtimestamp(a / 1000, UTC)
        sb = dt.datetime.fromtimestamp(b / 1000, UTC)
        minute = sa.hour * 60 + sa.minute
        if _spans_saturday(sa, sb):
            kind = "weekend"
        elif 20 * 60 + 45 <= minute <= 22 * 60 + 15 and sec <= 80 * 60:
            kind = "daily_break"
        elif sa.date() in set(holidays):
            kind = "holiday"
        else:
            kind = "unexplained"
        out.append({"start": sa.isoformat(), "end": sb.isoformat(), "seconds": round(sec, 3), "kind": kind})
    return out


def bars(df: pd.DataFrame, seconds: int) -> pd.DataFrame:
    """Bid OHLC bars of ``seconds`` built from the ticks (bar time = UTC open, epoch seconds), with the tick count
    and the mean and maximum spread. Bars without ticks are absent, never filled."""
    if not len(df):
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "ticks", "spread_mean", "spread_max"])
    t = df["time_msc"].to_numpy() // 1000
    g = pd.DataFrame({"time": t - t % seconds, "bid": df["bid"].to_numpy(),
                      "spread": (df["ask"] - df["bid"]).to_numpy()}).groupby("time", sort=True)
    out = g["bid"].agg(open="first", high="max", low="min", close="last")
    out["ticks"] = g.size()
    out["spread_mean"] = g["spread"].mean()
    out["spread_max"] = g["spread"].max()
    return out.reset_index()


def density(df: pd.DataFrame) -> pd.DataFrame:
    """Ticks and median spread per UTC weekday (0 = Monday) and hour."""
    t = pd.to_datetime(df["time_msc"], unit="ms", utc=True)
    g = pd.DataFrame({"weekday": t.dt.weekday, "hour": t.dt.hour,
                      "spread": (df["ask"] - df["bid"]).to_numpy()}).groupby(["weekday", "hour"])
    return pd.DataFrame({"ticks": g.size(), "spread_median": g["spread"].median()}).reset_index()


def best_offset(duka_m1: pd.DataFrame, other_m1: pd.DataFrame, candidates_h=range(-3, 6)) -> dict:
    """Which whole-hour shift aligns another source's M1 bar times (its own clock) with Dukascopy's UTC bars,
    judged from the data: for each candidate, the median absolute difference of the bar closes over the minutes
    both have. Returns every candidate's score and the best one; nothing is shifted here."""
    d = duka_m1.set_index("time")["close"]
    scores = {}
    for h in candidates_h:
        o = other_m1.assign(time=other_m1["time"] - h * 3600).set_index("time")["close"]
        common = d.index.intersection(o.index)
        scores[h] = {"minutes": int(len(common)),
                     "median_abs_close_diff": float((d[common] - o[common]).abs().median()) if len(common) else None}
    ok = {h: s for h, s in scores.items() if s["minutes"]}
    best = min(ok, key=lambda h: ok[h]["median_abs_close_diff"]) if ok else None
    return {"best_hours": best, "scores": scores}
