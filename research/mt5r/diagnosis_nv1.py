"""Diagnosis of the finished numeric_v1 research from stored evidence only (plan 2026-10-04-2133, KTD1-KTD7).

Train windows: the 108 stored passes (6 overlapping windows x 18 combinations) with the exact rejection reason,
descriptive variation across windows and between grid neighbours. Windows share months, so nothing here sums
train results or treats windows as independent samples.

Baselines A and B: their stored OOS trades (trades.trade_table on the research build's setup and deal logs) by
direction, month, concurrent exposure and shared structure, plus costs, outliers and the 01:00 minute.

The lower-risk figures are an accounting estimate on the closed-trade sequence, never an MT5 result.
Nothing in this module runs the tester or writes files.
"""
import json
import pathlib

import numpy as np
import pandas as pd

from . import numeric_v1 as nv, trades

ESTIMATE_LABEL = "אומדן חשבונאי, לא הרצת MT5"
TIME_FMT = "%Y.%m.%d %H:%M:%S"


def combo_name(row) -> str:
    return "/".join(str(int(row[a])) for a in nv.AXES)


# ------------------------------------------------------------------ U4: train windows (R1-R3)
def load_grids(results: pathlib.Path = nv.RESULTS) -> pd.DataFrame:
    """The six stored scored grids with their window name and train dates (from wfo/windows.csv)."""
    win = pd.read_csv(results / "wfo" / "windows.csv")
    frames = []
    for _, w in win.iterrows():
        name = w["window"]
        d = results / "final_selection" if name == "final" else results / "wfo" / f"f{name.split()[-1]}_selection"
        g = pd.read_csv(d / "scored_grid.csv")
        start, end = w["train"].split("-")
        frames.append(g.assign(window=name, train_start=start, train_end=end))
    return pd.concat(frames, ignore_index=True)


def rejection(trades_n: int, dd: float, floor: int, dd_max: float) -> str:
    """The exact reason a train pass was not eligible (wfo.select: trades >= floor and equity DD <= limit)."""
    out = []
    if trades_n < floor:
        out.append(f"מתחת לרצפת העסקאות ({int(trades_n)} < {floor})")
    if dd > dd_max:
        out.append(f"מעל מגבלת ה־DD ({dd:.2f}% > {dd_max:.2f}%)")
    return "; ".join(out) or "כשיר"


def train_table(grids: pd.DataFrame, floor: int, dd_max: float) -> pd.DataFrame:
    t = grids.copy()
    t["combo"] = t.apply(combo_name, axis=1)
    t["daily_sharpe"] = t["custom"]
    t["eligible"] = (t["trades"] >= floor) & (t["eq_dd_pct"] <= dd_max)
    t["reason"] = [rejection(n, d, floor, dd_max) for n, d in zip(t["trades"], t["eq_dd_pct"])]
    t["rank_in_window"] = t.groupby("window")["profit"].rank(ascending=False, method="min").astype(int)
    cols = ["window", "train_start", "train_end", *nv.AXES, "combo", "profit", "trades", "eq_dd_pct",
            "recovery_factor", "daily_sharpe", "eligible", "reason", "rank_in_window"]
    return t[[c for c in cols if c in t.columns]].sort_values(["train_start", *nv.AXES]).reset_index(drop=True)


def combo_summary(t: pd.DataFrame) -> pd.DataFrame:
    """Per combination, descriptive only: how many windows were profitable and how far results spread."""
    g = t.groupby("combo", sort=False)
    out = pd.DataFrame({
        "windows": g.size(), "profitable_windows": g["profit"].apply(lambda s: int((s > 0).sum())),
        "eligible_windows": g["eligible"].sum().astype(int),
        "net_median": g["profit"].median(), "net_min": g["profit"].min(), "net_max": g["profit"].max(),
        "net_spread": g["profit"].max() - g["profit"].min(),
        "eq_dd_median": g["eq_dd_pct"].median(), "eq_dd_min": g["eq_dd_pct"].min(),
        "trades_median": g["trades"].median(), "daily_sharpe_median": g["daily_sharpe"].median(),
        "rank_median": g["rank_in_window"].median(), "rank_best": g["rank_in_window"].min(),
        "rank_worst": g["rank_in_window"].max()}).reset_index()
    return out


def neighbour_variation(t: pd.DataFrame) -> pd.DataFrame:
    """Per combination: the mean absolute net difference to its grid neighbours (nv.neighbours: one step on an
    ordinal axis, same StructureVariant) within each window, averaged over the windows."""
    rows = []
    for w, g in t.groupby("window", sort=False):
        net = {tuple(int(r[a]) for a in nv.AXES): r["profit"] for _, r in g.iterrows()}
        for key, value in net.items():
            nbs = [nv.tuple_of(n) for n in nv.neighbours(dict(zip(nv.AXES, key)))]
            diffs = [abs(value - net[n]) for n in nbs if n in net]
            rows.append({"window": w, "combo": "/".join(map(str, key)), "neighbours": len(diffs),
                         "abs_diff": float(np.mean(diffs)) if diffs else np.nan})
    df = pd.DataFrame(rows)
    return (df.groupby("combo", sort=False)
              .agg(neighbours=("neighbours", "first"), mean_abs_diff_net=("abs_diff", "mean")).reset_index())


def rank_correlation(t: pd.DataFrame) -> pd.DataFrame:
    """Spearman correlation of the 18 nets between consecutive windows (ordered by train start). The windows
    share two of their three months, so this measures persistence across overlapping data, not independence."""
    order = t.drop_duplicates("window").sort_values("train_start")["window"].tolist()
    piv = t.pivot_table(index="combo", columns="window", values="profit")
    rows = []
    for a, b in zip(order, order[1:]):
        rows.append({"pair": f"{a} -> {b}", "spearman": float(piv[a].rank().corr(piv[b].rank()))})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ U5: baseline trades (R4-R8)
def load_baseline_trades(series: str, results: pathlib.Path = nv.RESULTS) -> pd.DataFrame:
    """The stored OOS trades of one fixed baseline across the folds (KTD4), with fold, OOS month and the fold's
    chained deposit, plus the setup columns trade_table does not carry."""
    folds = json.loads((results / "wfo" / "folds.json").read_text())
    frames = []
    for f in folds:
        rid = f["oos"][series]["run_id"]
        d = results / "wfo" / rid
        setups = pd.read_csv(d / f"rl_setups_{rid}.csv")
        deals = pd.read_csv(d / f"rl_deals_{rid}.csv")
        t = trades.trade_table(setups, deals)
        extra = setups[pd.to_numeric(setups["position_id"], errors="coerce").notna()][
            ["position_id", "ref_pivot_id", "ob_time"]].copy()
        extra["position_id"] = pd.to_numeric(extra["position_id"])
        t = t.merge(extra.drop_duplicates("position_id"), on="position_id", how="left")
        t["open_time"] = pd.to_datetime(t["open_time"], format=TIME_FMT)
        t["close_time"] = pd.to_datetime(t["close_time"], format=TIME_FMT)
        frames.append(t.assign(fold=f["fold"], month=f["test"][0][:7].replace(".", "-"),
                               deposit=float(f["deposits"][series]), run_id=rid))
    return pd.concat(frames, ignore_index=True)


def add_balance(t: pd.DataFrame) -> pd.DataFrame:
    """balance_before: the fold's deposit plus the net of the fold's trades closed at or before this trade opened
    (the balance the EA sized the trade on), and risk_pct: the planned risk as % of it."""
    t = t.copy()
    bal = []
    for _, r in t.iterrows():
        f = t[(t["fold"] == r["fold"]) & (t["close_time"] <= r["open_time"])]
        bal.append(r["deposit"] + f["net"].sum())
    t["balance_before"] = bal
    if "realized_r" in t.columns:
        risk = t["net"] / t["realized_r"].where(t["realized_r"] != 0)
        t["risk_pct"] = risk.abs() / t["balance_before"] * 100
    return t


def add_concurrency(t: pd.DataFrame) -> pd.DataFrame:
    """concurrent: other positions of the same fold open when this one opened ([open, close) contains its
    open); cluster: maximal chain of overlapping positions in open order."""
    t = t.sort_values(["fold", "open_time"], kind="mergesort").reset_index(drop=True)
    conc, open_risk = [], []
    for i, r in t.iterrows():
        o = t[(t["fold"] == r["fold"]) & (t.index != i) & (t["open_time"] <= r["open_time"])
              & (t["close_time"] > r["open_time"])]
        conc.append(len(o))
        if "risk_pct" in t.columns:
            open_risk.append(float(o["risk_pct"].sum() + r["risk_pct"]))
    t["concurrent"] = conc
    if "risk_pct" in t.columns:
        t["open_risk_pct"] = open_risk
    cluster, cid, end, fold = [], -1, None, None
    for _, r in t.iterrows():
        if fold != r["fold"] or end is None or r["open_time"] >= end:
            cid, end, fold = cid + 1, r["close_time"], r["fold"]
        else:
            end = max(end, r["close_time"])
        cluster.append(cid)
    t["cluster"] = cluster
    return t


def add_structure(t: pd.DataFrame) -> pd.DataFrame:
    """shared_structure: the position shares its structure pivot (ref_pivot_id) with a filled position from a
    different OB (ob_time) in the same fold."""
    t = t.copy()
    key = t["ref_pivot_id"].notna()
    n_obs = t[key].groupby(["fold", "ref_pivot_id"])["ob_time"].transform("nunique")
    t["shared_structure"] = False
    t.loc[key, "shared_structure"] = n_obs > 1
    t["shared_structure"] = t["shared_structure"].astype(bool)
    return t


def trade_stats(t: pd.DataFrame) -> dict:
    win, loss = t[t["net"] > 0], t[t["net"] < 0]
    gross_loss = -loss["net"].sum()
    return {"trades": int(len(t)), "net": round(float(t["net"].sum()), 2),
            "expectancy_r": float(t["realized_r"].mean()) if len(t) else np.nan,
            "win_rate": float(len(win) / len(t)) if len(t) else np.nan,
            "avg_win": round(float(win["net"].mean()), 2) if len(win) else np.nan,
            "avg_loss": round(float(loss["net"].mean()), 2) if len(loss) else np.nan,
            "avg_win_r": float(win["realized_r"].mean()) if len(win) else np.nan,
            "avg_loss_r": float(loss["realized_r"].mean()) if len(loss) else np.nan,
            "profit_factor": float(win["net"].sum() / gross_loss) if gross_loss > 0 else np.nan}


def grouped_stats(t: pd.DataFrame, by: str) -> pd.DataFrame:
    return pd.DataFrame([{by: k, **trade_stats(g)} for k, g in t.groupby(by, sort=True)])


def concurrency_losses(t: pd.DataFrame) -> pd.DataFrame:
    """Trades and losses by the number of other positions open at entry (0, 1, 2+)."""
    level = t["concurrent"].clip(upper=2).map({0: "0", 1: "1", 2: "2+"})
    total_loss = -t.loc[t["net"] < 0, "net"].sum()
    rows = []
    for k, g in t.groupby(level, sort=True):
        loss = -g.loc[g["net"] < 0, "net"].sum()
        rows.append({"open_others": k, **trade_stats(g),
                     "share_of_trades": len(g) / len(t), "share_of_total_loss": loss / total_loss if total_loss else 0})
    return pd.DataFrame(rows)


def cluster_table(t: pd.DataFrame) -> pd.DataFrame:
    """Clusters of two or more overlapping positions: their summed net as % of the balance at the first entry."""
    rows = []
    for c, g in t.groupby("cluster"):
        if len(g) < 2:
            continue
        first = g.sort_values("open_time").iloc[0]
        rows.append({"cluster": c, "fold": int(first["fold"]), "start": first["open_time"], "positions": len(g),
                     "net": round(float(g["net"].sum()), 2),
                     "net_pct_of_balance": float(g["net"].sum() / first["balance_before"] * 100)})
    return pd.DataFrame(rows).sort_values("net").reset_index(drop=True) if rows else pd.DataFrame()


def minute_0100(t: pd.DataFrame) -> dict:
    m = t[(t["open_time"].dt.hour == 1) & (t["open_time"].dt.minute == 0)]
    return {"fills": int(len(m)), "net": round(float(m["net"].sum()), 2)}


def outliers(t: pd.DataFrame, k: int = 5) -> dict:
    """Net with and without the k largest wins and the k largest losses (dollars)."""
    wins = t[t["net"] > 0].nlargest(k, "net")["net"].sum()
    losses = t[t["net"] < 0].nsmallest(k, "net")["net"].sum()
    net = float(t["net"].sum())
    return {"k": k, "net": round(net, 2), "without_top_wins": round(net - wins, 2),
            "without_top_losses": round(net - losses, 2), "without_both": round(net - wins - losses, 2)}


def costs(t: pd.DataFrame) -> dict:
    return {"swap": round(float(t["swap"].sum()), 2), "commission": round(float(t["commission"].sum()), 2)}


def worst_days(results: pathlib.Path, run_ids: list, n: int = 5) -> pd.DataFrame:
    """Daily records (rl_days): the largest intraday equity drops from the day's opening equity."""
    frames = []
    for rid in run_ids:
        d = pd.read_csv(results / "wfo" / rid / f"rl_days_{rid}.csv")
        d["intraday_drop_pct"] = (d["eq_min"] / d["eq_open"] - 1) * 100
        d["day_change_pct"] = (d["eq_close"] / d["eq_open"] - 1) * 100
        frames.append(d.assign(run_id=rid))
    days = pd.concat(frames, ignore_index=True)
    return days.nsmallest(n, "intraday_drop_pct")[["date", "run_id", "eq_open", "eq_min", "intraday_drop_pct",
                                                    "day_change_pct"]]


# ------------------------------------------------------------------ U6: lower-risk accounting estimate (R9)
def risk_estimate(t: pd.DataFrame, factors=(1.0, 0.5, 0.25)) -> pd.DataFrame:
    """ESTIMATE, not an MT5 run (KTD7). Replays the closed trades in close order from the first fold's deposit.
    Each trade's dollar result is scaled by k and by the ratio of the scaled to the actual balance it was sized
    on, so k = 1 reproduces the stored closed-trade path. Lot rounding, margin, which trades are executable and
    the tester's own equity chaining are not modelled."""
    t = t.sort_values(["close_time", "open_time"], kind="mergesort").reset_index(drop=True)
    initial = float(t.sort_values("fold")["deposit"].iloc[0])
    rows = []
    for k in factors:
        closed_t, closed_net, path = [], [], [initial]
        for _, r in t.iterrows():
            bal_open = initial + sum(n for ct, n in zip(closed_t, closed_net) if ct <= r["open_time"])
            scaled = k * r["net"] * bal_open / r["balance_before"]
            closed_t.append(r["close_time"])
            closed_net.append(scaled)
            path.append(path[-1] + scaled)
        p = np.array(path)
        dd = float(((np.maximum.accumulate(p) - p) / np.maximum.accumulate(p)).max() * 100)
        rows.append({"k": k, "risk_percent_equivalent": k * 1.0, "net": round(float(p[-1] - initial), 2),
                     "closed_dd_pct": dd, "label": ESTIMATE_LABEL})
    return pd.DataFrame(rows)


def lot_rounding(volumes: pd.Series, k: float, step: float = 0.01) -> dict:
    """How many stored volumes the k-scaled arithmetic could not reproduce: the EA floors the volume to the
    0.01 step, so a scaled volume below one step means no trade, and flooring can move it by more than 10%."""
    v = volumes.astype(float) * k
    new = np.floor(v / step + 1e-9) * step
    below = new < step - 1e-12
    changed = (~below) & ((new - v).abs() / v > 0.10)
    return {"k": k, "trades": int(len(v)), "below_min_lot": int(below.sum()), "changed_over_10pct": int(changed.sum()),
            "label": ESTIMATE_LABEL}
