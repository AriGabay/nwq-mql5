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


# ------------------------------------------------------------------ manual web export (checked 2026-10-05)
# The Dukascopy "Historical Data Export" widget, period "1 Tick", writes one CSV per offer side (ASK or BID):
# header "Etc/UTC,Open,High,Low,Close,Volume", one row per tick with Open = High = Low = Close = the price, an ISO
# time to the second ("2024-01-02T10:00:00+00:00", no milliseconds) and the volume in units of the feed volume
# x 1e6. Verified against the feed file of the same hour (4,969 ticks): same rows in the same order and the same
# prices; the time is the feed time without its milliseconds (5 ticks at .999 show the next second).
EXPORT_HEADER = ["Etc/UTC", "Open", "High", "Low", "Close", "Volume"]


def read_export_csv(path) -> pd.DataFrame:
    """One side of a manual tick export, in file order: time_s (epoch seconds, UTC), price, volume, raw (price x
    1000). Refuses (never repairs) a file whose header, time zone or rows do not have the checked layout."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(df.columns) != EXPORT_HEADER:
        raise ValueError(f"{path}: header {list(df.columns)} is not the checked tick-export layout")
    if not df["Etc/UTC"].str.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00").all():
        raise ValueError(f"{path}: a time is not 'YYYY-MM-DDTHH:MM:SS+00:00' (UTC, whole seconds)")
    ohlc = df[["Open", "High", "Low", "Close"]]
    if not ohlc.eq(ohlc["Close"], axis=0).all().all():
        raise ValueError(f"{path}: a row has Open/High/Low/Close not all equal - not one tick per row")
    price = df["Close"].astype(float)
    if (price <= 0).any():
        raise ValueError(f"{path}: a price is not positive")
    t = pd.to_datetime(df["Etc/UTC"], utc=True)
    return pd.DataFrame({"time_s": (t - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1),
                         "price": price.to_numpy(), "volume": df["Volume"].astype(float).to_numpy(),
                         "raw": np.round(price.to_numpy() * 1000).astype(np.int64)})


def pair_export_sides(ask: pd.DataFrame, bid: pd.DataFrame) -> pd.DataFrame:
    """Quotes from the two side files, paired by row position - never by time, because many ticks share a second.
    Row pairing is accepted only when both files have the same number of rows and the same time on every row and no
    pair is crossed; otherwise it refuses. This is a necessary condition, not proof: that row i of both files is the
    same tick must be shown against a source carrying both sides (the feed file of the same hour)."""
    if len(ask) != len(bid):
        raise ValueError(f"ASK has {len(ask)} rows and BID {len(bid)}: rows cannot be paired")
    if not (ask["time_s"].to_numpy() == bid["time_s"].to_numpy()).all():
        n = int((ask["time_s"].to_numpy() != bid["time_s"].to_numpy()).sum())
        raise ValueError(f"{n} rows have a different time in ASK and BID: rows cannot be paired")
    if (ask["raw"].to_numpy() < bid["raw"].to_numpy()).any():
        raise ValueError("a row pair has ask below bid: rows are not the same quote")
    return pd.DataFrame({"time_s": ask["time_s"].to_numpy(), "ask": ask["price"].to_numpy(),
                         "bid": bid["price"].to_numpy(), "ask_vol": ask["volume"].to_numpy(),
                         "bid_vol": bid["volume"].to_numpy(), "raw_ask": ask["raw"].to_numpy(),
                         "raw_bid": bid["raw"].to_numpy()})


def compare_export_to_feed(pairs: pd.DataFrame, feed: pd.DataFrame) -> dict:
    """Row by row against the decoded feed file of the same period: prices (exact, in 0.001), volume (x 1e6) and
    time (the export's second against the feed's millisecond time). A count match alone is never reported as a
    match."""
    out = {"rows_export": int(len(pairs)), "ticks_feed": int(len(feed)), "same_count": len(pairs) == len(feed)}
    if not out["same_count"]:
        return out | {"identical_prices_in_order": False}
    fs = feed["time_msc"].to_numpy() // 1000
    dsec = pairs["time_s"].to_numpy() - fs
    ms = feed["time_msc"].to_numpy() % 1000
    out["ask_mismatch"] = int((pairs["raw_ask"].to_numpy() != feed["raw_ask"].to_numpy()).sum())
    out["bid_mismatch"] = int((pairs["raw_bid"].to_numpy() != feed["raw_bid"].to_numpy()).sum())
    out["volume_mismatch"] = int(((np.round(pairs["ask_vol"].to_numpy()) != np.round(feed["ask_vol"].to_numpy() * 1e6))
                                  | (np.round(pairs["bid_vol"].to_numpy()) != np.round(feed["bid_vol"].to_numpy() * 1e6))).sum())
    out["identical_prices_in_order"] = out["ask_mismatch"] == 0 and out["bid_mismatch"] == 0
    out["time_equal_to_feed_second"] = int((dsec == 0).sum())
    out["time_one_second_later"] = int((dsec == 1).sum())
    out["time_one_second_later_feed_ms"] = sorted({int(x) for x in ms[dsec == 1]})
    out["time_other"] = int(((dsec != 0) & (dsec != 1)).sum())
    out["feed_ticks_with_nonzero_ms"] = int((ms != 0).sum())
    sec = pd.Series(fs).value_counts()
    out["feed_seconds_with_several_ticks"] = int((sec > 1).sum())
    out["feed_ticks_sharing_a_second"] = int(sec[sec > 1].sum())
    out["feed_max_ticks_in_one_second"] = int(sec.max()) if len(sec) else 0
    return out
