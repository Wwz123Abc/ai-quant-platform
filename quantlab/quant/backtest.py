"""A long-or-flat daily backtest that trades whole shares at the next day's open."""
import math
from dataclasses import dataclass

import pandas as pd


@dataclass
class Result:
    equity: pd.Series      # account value at each close
    in_market: pd.Series   # 1 on days a position was held at the close
    trades: pd.DataFrame   # one row per round trip; the last may still be open
    cash: float            # starting cash


def run(bars, target, cash=10_000.0, fee=0.0, slippage_bps=5.0):
    """Replays bars day by day.

    target[t] is decided on day t's close and traded at day t+1's open, so no day trades on prices it could not
    have seen. target may start before bars (indicator warm-up), so the first day acts on the day before it.
    Buys pay the open plus slippage, sells get the open minus slippage, and each order costs fee dollars."""
    want = target.shift(1).reindex(bars.index).fillna(0)
    slip = slippage_bps / 10_000
    start, shares, entry = cash, 0, None
    equity, held, trades = [], [], []
    for date, bar in bars.iterrows():
        if want[date] == 1 and shares == 0:
            px = bar.open * (1 + slip)
            n = math.floor((cash - fee) / px)
            if n > 0:
                cash -= n * px + fee
                shares, entry = n, (date, px, n * px + fee)
        elif want[date] == 0 and shares > 0:
            px = bar.open * (1 - slip)
            cash += shares * px - fee
            trades.append(_trade(entry, date, px, shares * px - fee, shares, False))
            shares, entry = 0, None
        equity.append(cash + shares * bar.close)
        held.append(1 if shares else 0)
    if shares:
        last = bars.iloc[-1]
        trades.append(_trade(entry, bars.index[-1], last.close, shares * last.close, shares, True))
    cols = ["entry_date", "entry_price", "exit_date", "exit_price", "shares", "pnl", "return", "open"]
    return Result(pd.Series(equity, bars.index), pd.Series(held, bars.index), pd.DataFrame(trades, columns=cols), start)


def _trade(entry, exit_date, exit_px, proceeds, shares, still_open):
    entry_date, entry_px, cost = entry
    return [entry_date, entry_px, exit_date, exit_px, shares, proceeds - cost, proceeds / cost - 1, still_open]


def buy_and_hold(bars, **kw):
    """The benchmark: buy on the first day's open and never sell."""
    before = bars.index[0] - pd.Timedelta(days=1)
    return run(bars, pd.Series(1, bars.index.insert(0, before)), **kw)
