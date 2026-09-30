"""Trade-event and account metrics (R25, R27, R32)."""
import math

import numpy as np
import pandas as pd


def _net(trades_df):
    return trades_df["profit"] + trades_df["commission"] + trades_df["swap"]


def trade_events(trades_df, bar_minutes=15) -> pd.DataFrame:
    """Group positions opened on the same bar in the same direction into one event."""
    t = trades_df.sort_values("open_time", kind="mergesort").copy()
    t["bar"] = pd.to_datetime(t["open_time"]).dt.floor(f"{bar_minutes}min")
    t["net"] = _net(t)
    ev = (t.groupby(["bar", "direction"], sort=True)
           .agg(n_positions=("net", "size"), profit_net=("net", "sum"),
                balance_at_open=("balance_at_open", "first"))
           .reset_index()
           .rename(columns={"bar": "open_time"}))
    ev["ret"] = ev["profit_net"] / ev["balance_at_open"]
    return ev.sort_values(["open_time", "direction"]).reset_index(drop=True)


def _max_drawdown(peaks, troughs):
    """Largest peak-to-trough drop in money and in % of that peak."""
    peaks = np.asarray(peaks, float)
    dd = peaks - np.asarray(troughs, float)
    if dd.size == 0 or dd.max() <= 0:
        return 0.0, 0.0
    return float(dd.max()), float((dd / peaks).max() * 100)


def _equity_drawdown(days_df):
    """Equity DD from daily records (approximation, chronological).

    Intraday order is unknown, so a day's eq_min is compared with the peak of
    earlier days (max of eq_max, bal_close) and the day's own eq_open; the day's
    eq_close, which is last in the day, is compared with a peak that includes
    the day's own eq_max.
    """
    d = days_df.sort_values("date")
    day_peak = np.maximum(d["eq_max"], d["bal_close"]).to_numpy(float)
    eq_open = d["eq_open"].to_numpy(float)
    prior = np.concatenate([[-np.inf], np.maximum.accumulate(day_peak)[:-1]])
    peak_before = np.maximum(prior, eq_open)
    peak_after = np.maximum(peak_before, day_peak)
    m1, p1 = _max_drawdown(peak_before, d["eq_min"])
    m2, p2 = _max_drawdown(peak_after, d["eq_close"])
    return max(m1, m2), max(p1, p2)


def summary(trades_df, days_df=None, initial=10000.0, bar_minutes=15) -> dict:
    """Headline metrics; events drive win rate, expectancy and profit factor."""
    net = _net(trades_df)
    ev = trade_events(trades_df, bar_minutes)
    wins = ev.loc[ev["profit_net"] > 0, "profit_net"].sum()
    losses = -ev.loc[ev["profit_net"] < 0, "profit_net"].sum()
    if ev.empty:
        pf = math.nan
    else:
        pf = math.inf if losses == 0 else float(wins / losses)

    order = trades_df["close_time"].argsort(kind="mergesort").to_numpy()
    balance = initial + np.concatenate([[0.0], np.cumsum(net.to_numpy(float)[order])])
    bal_dd_money, bal_dd_pct = _max_drawdown(np.maximum.accumulate(balance), balance)

    out = {
        "net_profit": float(net.sum()),
        "n_trades": int(len(trades_df)),
        "n_events": int(len(ev)),
        "win_rate": float((ev["profit_net"] > 0).mean()) if len(ev) else math.nan,
        "expectancy_per_event": float(ev["profit_net"].mean()) if len(ev) else math.nan,
        "expectancy_per_trade": float(net.mean()) if len(net) else math.nan,
        "profit_factor": pf,
        "max_balance_dd_money": bal_dd_money,
        "max_balance_dd_pct": bal_dd_pct,
    }
    if days_df is not None:
        eq_money, eq_pct = _equity_drawdown(days_df)
        out["max_equity_dd_money"] = eq_money
        out["max_equity_dd_pct"] = eq_pct
        if eq_money > 0:
            out["recovery_factor"] = out["net_profit"] / eq_money
        else:
            out["recovery_factor"] = math.inf if out["net_profit"] > 0 else math.nan
    return out


def previous_equity(days_df) -> pd.Series:
    """Equity the day's change is measured from: previous close, first day's opening equity."""
    prev = days_df["eq_close"].shift(1)
    prev.iloc[0] = days_df["eq_open"].iloc[0]
    return prev


def daily_returns(days_df) -> pd.Series:
    """Close-to-close equity returns; the first day is measured from its eq_open."""
    d = days_df.sort_values("date")
    r = d["eq_close"] / previous_equity(d) - 1
    r.index = pd.Index(d["date"], name="date")
    return r


def concentration(events) -> dict:
    """Share of net profit from the best event, and net profit without the top two."""
    p = events["profit_net"]
    net = float(p.sum())
    return {
        "largest_event_share": float(p.max() / net) if len(p) and net > 0 else math.nan,
        "net_without_top2": net - float(p.nlargest(2).sum()),
    }
