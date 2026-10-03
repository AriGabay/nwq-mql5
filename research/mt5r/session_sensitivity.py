"""Pre-registered sensitivity for SL/TP levels reached only in the quote-only minute (gate review, 2026-10-03).

XAUUSD.s quotes start at 01:00 but its trade session starts at 01:01 (tester symbol sessions; Market orders on the
first tick after a quote gap are refused with TRADE_RETCODE_MARKET_CLOSED). A position whose SL or TP is reached
only by quotes in that minute is closed later by the tester, at the first trigger tick in the trade session or
hours later when price came back (research/session_probe.py, results/pilot/session_probe/).

The primary results stay exactly as the tester produced them: no trade is removed and no exit is changed. This
sensitivity re-prices each affected position as if its stop or target had executed at the first tick of the
quote-only minute that reached the level on the trigger side (buy: Bid <= SL or Bid >= TP; sell: Ask >= SL or
Ask <= TP), at that tick's price. It is reported next to the primary result and is never used for a choice.

Method: a re-pricing of a FIXED trade list, not a path re-simulation. The same positions keep their entries, sizes
and SL/TP; only an affected position's exit time and price change. Not reproduced (stated in the report): later
position sizes that would follow from a different balance (R20), cap slots freed or held at other times (R21) and
so the later signals that would have been taken or skipped, and swap. Drawdown is computed on the corrected
timeline: each result is booked at its (re-priced) exit time on the closed-trade balance curve, the same basis for
primary and sensitivity. It has no floating equity, so it is not the tester's equity drawdown.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CONTRACT = 100.0
QUOTE_ONLY_S = 60


def trigger(sign: int, sl: float, tp: float, bid: float, ask: float):
    """'sl' / 'tp' / None for a position of side ``sign`` (+1 buy, -1 sell) at one tick."""
    if sign > 0:
        return "sl" if bid <= sl + 1e-9 else "tp" if bid >= tp - 1e-9 else None
    return "sl" if ask >= sl - 1e-9 else "tp" if ask <= tp + 1e-9 else None


def alt_exits(cases: pd.DataFrame, ticks: pd.DataFrame) -> pd.DataFrame:
    """First trigger tick inside each case's quote-only minute [gap_bar, gap_bar + 60 s).
    cases: case_id, setup_id, dir (L/S), sl, tp, gap_bar (s). ticks: tick_msc, bid, ask."""
    t = ticks.sort_values("tick_msc")
    ms = t["tick_msc"].to_numpy()
    out = []
    for c in cases.itertuples():
        sign = 1 if c.dir == "L" else -1
        a, b = np.searchsorted(ms, c.gap_bar * 1000), np.searchsorted(ms, (c.gap_bar + QUOTE_ONLY_S) * 1000)
        hit = None
        for x in t.iloc[a:b].itertuples():
            k = trigger(sign, c.sl, c.tp, x.bid, x.ask)
            if k:
                hit = (int(x.tick_msc), float(x.bid if sign > 0 else x.ask), k)
                break
        out.append(dict(case_id=c.case_id, setup_id=c.setup_id, alt_msc=hit[0] if hit else None,
                        alt_price=hit[1] if hit else None, alt_kind=hit[2] if hit else None))
    return pd.DataFrame(out)


def _max_dd_pct(times, nets, deposit) -> float:
    order = np.argsort(times, kind="mergesort")
    bal = deposit + np.cumsum(np.asarray(nets, dtype=float)[order])
    bal = np.concatenate([[deposit], bal])
    peak = np.maximum.accumulate(bal)
    return float(np.max((peak - bal) / peak) * 100)


def evaluate(setups: pd.DataFrame, deals: pd.DataFrame, alt: pd.DataFrame) -> dict:
    """Primary vs sensitivity: net and closed-balance max drawdown of one run. setups: rl_setups (filled rows with
    position_id, exit_msc, exit_price, exit_kind, dir, volume); deals: rl_deals; alt: alt_exits() for this run."""
    d = deals.copy()
    for c in ("type", "position_id", "profit", "commission", "swap"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    deposit = float(d.loc[d["type"] == 2, "profit"].sum())
    trade = d[d["type"].isin([0, 1])]
    net_by_pos = (trade["profit"] + trade["commission"] + trade["swap"]).groupby(trade["position_id"]).sum()
    f = setups[setups["reason"] == "filled"].copy()
    f["net"] = f["position_id"].astype("int64").map(net_by_pos).fillna(0.0)
    f["t"] = f["exit_msc"].astype("int64")
    alt = alt.dropna(subset=["alt_msc"]).set_index("setup_id")
    g = f.copy()
    sign = np.where(g["dir"] == "L", 1.0, -1.0)
    hit = g["setup_id"].isin(alt.index)
    ap = g["setup_id"].map(alt["alt_price"])
    delta = (ap - g["exit_price"].astype(float)) * sign * g["volume"].astype(float) * CONTRACT
    g.loc[hit, "net"] = g.loc[hit, "net"] + delta[hit]
    g.loc[hit, "t"] = g.loc[hit, "setup_id"].map(alt["alt_msc"]).astype("int64")
    flips = int((g.loc[hit, "setup_id"].map(alt["alt_kind"]) != g.loc[hit, "exit_kind"]).sum())
    return {"deposit": deposit, "positions": int(len(f)), "affected": int(hit.sum()), "outcome_flips": flips,
            "net_primary": round(float(f["net"].sum()), 2), "net_sensitivity": round(float(g["net"].sum()), 2),
            "net_delta": round(float(g["net"].sum() - f["net"].sum()), 2),
            "max_dd_closed_pct_primary": round(_max_dd_pct(f["t"], f["net"], deposit), 3),
            "max_dd_closed_pct_sensitivity": round(_max_dd_pct(g["t"], g["net"], deposit), 3)}
