"""R22 cost stress applied to closed trades."""
import pandas as pd


def extra_cost(trades_df, contract_size, k_spread, spread_by_day=None, fixed_spread=None) -> pd.Series:
    """Extra round-trip cost per trade = k * spread * volume * contract_size (USD).

    Spread comes from spread_by_day (indexed by day, matched on open_time's day)
    or else from fixed_spread.
    """
    if spread_by_day is not None:
        day = pd.to_datetime(trades_df["open_time"]).dt.normalize()
        by_day = spread_by_day.copy()
        by_day.index = pd.to_datetime(by_day.index).normalize()
        spread = day.map(by_day)
        if spread.isna().any():
            missing = sorted(day[spread.isna()].unique())
            raise ValueError(f"no spread for days: {missing[:5]}")
    elif fixed_spread is not None:
        spread = pd.Series(float(fixed_spread), index=trades_df.index)
    else:
        raise ValueError("give spread_by_day or fixed_spread")
    return k_spread * spread * trades_df["volume"] * contract_size


def apply(trades_df, extra, commission_scale=1.0) -> pd.DataFrame:
    """Copy with profit_net_stressed = profit + commission*scale + swap - extra."""
    out = trades_df.copy()
    out["profit_net_stressed"] = (out["profit"] + out["commission"] * commission_scale
                                  + out["swap"] - extra)
    return out
