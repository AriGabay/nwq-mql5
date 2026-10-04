"""R24 loss limits on daily equity records."""
import pandas as pd


def evaluate(days_df, initial=10000.0, daily_loss_pct=5.0, total_floor=9000.0) -> pd.DataFrame:
    """Per day: daily floor (00:00 balance minus % of initial) and breach flags.

    Neither limit trails. A breach is equity strictly below the floor.
    """
    out = days_df.copy()
    out["daily_floor"] = out["bal_open"] - initial * daily_loss_pct / 100.0
    out["daily_breach"] = out["eq_min"] < out["daily_floor"]
    out["total_breach"] = out["eq_min"] < total_floor
    return out


def first_breach(days_df, initial=10000.0, daily_loss_pct=5.0, total_floor=9000.0):
    """First breached day as a dict (date, type, eq_min, daily_floor), or None."""
    ev = evaluate(days_df, initial, daily_loss_pct, total_floor)
    if "date" in ev:
        ev = ev.sort_values("date")
    hit = ev[ev["daily_breach"] | ev["total_breach"]]
    if hit.empty:
        return None
    row = hit.iloc[0]
    kind = {(True, False): "daily", (False, True): "total", (True, True): "both"}
    return {
        "date": row["date"] if "date" in row else None,
        "type": kind[(bool(row["daily_breach"]), bool(row["total_breach"]))],
        "eq_min": float(row["eq_min"]),
        "daily_floor": float(row["daily_floor"]),
    }
