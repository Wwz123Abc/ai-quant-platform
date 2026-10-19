"""Strategies turn a price history into a target position for each day: 1 = hold the stock, 0 = hold cash."""
import pandas as pd


def ma_cross(close, fast=50, slow=200):
    """Moving-average crossover: hold while the fast average is above the slow one.

    The target for a day uses that day's close, so it can only be acted on the next day."""
    if not 0 < fast < slow:
        raise ValueError("短均线天数要小于长均线天数")
    out = pd.DataFrame({"close": close, "fast": close.rolling(fast).mean(), "slow": close.rolling(slow).mean()})
    out["target"] = (out["fast"] > out["slow"]).astype(int).where(out["slow"].notna(), 0)
    return out
