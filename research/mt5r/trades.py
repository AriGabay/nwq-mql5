"""Per-trade table (R40): setup rows (rl_setups) joined to research deal records (rl_deals) by position id."""
import pandas as pd

CONTRACT_SIZE = 100.0   # XAUUSD.s (run_constants symbol_spec.contract_size)
COLUMNS = ["setup_id", "dir", "open_time", "intended_entry", "fill_price", "sl", "tp", "planned_rr", "realized_r",
           "gross_profit", "commission", "swap", "net", "exit_kind", "reason",
           # extras used by the stress and reporting code
           "position_id", "volume", "close_time"]


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def trade_table(setups: pd.DataFrame, deals: pd.DataFrame, contract_size: float = CONTRACT_SIZE) -> pd.DataFrame:
    """One row per position opened by the EA; net = profit + commission + swap over all its deals.

    planned_rr = |tp - entry| / |entry - sl| from the intended entry; realized_r = net / (|entry - sl| *
    volume * contract_size). A position without a setup row keeps its money columns and empty setup fields,
    so the table's net always equals the deal records' net.
    """
    d = deals[deals["type"].isin([0, 1])].copy()
    if d.empty:
        return pd.DataFrame(columns=COLUMNS)
    d["position_id"] = _num(d["position_id"])
    d = d.sort_values(["time", "ticket"], kind="mergesort")
    g = d.groupby("position_id", sort=False)
    pos = pd.DataFrame({
        "gross_profit": g["profit"].sum(), "commission": g["commission"].sum(), "swap": g["swap"].sum(),
        "open_time": d[d["entry"] == 0].groupby("position_id")["time"].first(),
        "deal_volume": d[d["entry"] == 0].groupby("position_id")["volume"].sum(),
        "deal_price": d[d["entry"] == 0].groupby("position_id")["price"].first(),
        "close_time": d[d["entry"] != 0].groupby("position_id")["time"].last(),
        "exit_comment": d[d["entry"] != 0].groupby("position_id")["comment"].last(),
    }).reset_index()
    pos["net"] = pos["gross_profit"] + pos["commission"] + pos["swap"]

    keep = ["setup_id", "dir", "entry", "sl", "tp", "volume", "fill_price", "exit_kind", "reason", "position_id"]
    if "entry" not in setups.columns and "request_price" in setups.columns:   # M5/M1 EA log contract
        setups = setups.rename(columns={"request_price": "entry"})
    s = setups.reindex(columns=keep)
    s["position_id"] = _num(s["position_id"])
    s = s[s["position_id"].notna()]
    t = pos.merge(s, on="position_id", how="left")
    for c in ("entry", "sl", "tp", "volume", "fill_price"):
        t[c] = _num(t[c])
    t["intended_entry"] = t["entry"]
    t["fill_price"] = t["fill_price"].fillna(t["deal_price"])
    t["volume"] = t["volume"].fillna(t["deal_volume"])
    # tester exit deals are commented "sl <price>" / "tp <price>"; used when no setup row gives exit_kind
    from_comment = t["exit_comment"].astype(str).str.extract(r"^\[?(sl|tp)\b", expand=False)
    t["exit_kind"] = t["exit_kind"].where(t["exit_kind"].notna() & (t["exit_kind"].astype(str) != ""), from_comment)
    risk_px = (t["intended_entry"] - t["sl"]).abs()
    t["planned_rr"] = (t["tp"] - t["intended_entry"]).abs() / risk_px
    t["realized_r"] = t["net"] / (risk_px * t["volume"] * contract_size)
    return t.sort_values("open_time", kind="mergesort").reset_index(drop=True)[COLUMNS]
