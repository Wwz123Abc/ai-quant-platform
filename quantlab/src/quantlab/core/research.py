"""Research workflows shared by the command line and the API. They return results; callers present them."""
from dataclasses import dataclass

import pandas as pd

from ..errors import InvalidInput
from . import backtest, metrics, scan, strategy


@dataclass
class BacktestRun:
    symbol: str
    fast: int
    slow: int
    signals: pd.DataFrame          # close, fast, slow and target over the whole history
    strategy: backtest.Result
    benchmark: backtest.Result
    summary: dict
    benchmark_summary: dict


def backtest_ma(symbol, bars, fast=50, slow=200, start=None, end=None, cash=10_000.0, fee=0.0, slippage_bps=5.0, rf=0.0):
    """Moving-average crossover against buy and hold over the same days.

    The window starts on the first day the slow average exists, or on start if that is later."""
    sig = strategy.ma_cross(bars["close"], fast, slow)
    first = sig["slow"].first_valid_index()
    if first is None:
        raise InvalidInput("%s 只有 %d 个交易日的数据，不够算 %d 日均线" % (symbol, len(bars), slow))
    lo = max(first, pd.Timestamp(start)) if start else first
    window = bars.loc[lo:end] if end else bars.loc[lo:]
    if len(window) < 2:
        raise InvalidInput("这个时间段内没有足够的数据")
    kw = dict(cash=cash, fee=fee, slippage_bps=slippage_bps)
    strat, bench = backtest.run(window, sig["target"], **kw), backtest.buy_and_hold(window, **kw)
    return BacktestRun(symbol, fast, slow, sig, strat, bench, metrics.summary(strat, rf), metrics.summary(bench, rf))


@dataclass
class ScanRun:
    symbol: str
    by: str
    table: pd.DataFrame            # one row per pair, sorted by in-sample rank
    in_sample: pd.DataFrame        # the bars of each period
    out_sample: pd.DataFrame
    benchmark_in: dict
    benchmark_out: dict
    rank_corr: float
    median_out: float

    @property
    def best(self):
        return self.table.iloc[0]


def scan_ma(symbol, bars, fasts=scan.FAST, slows=scan.SLOW, split=None, start=None, end=None, by="sharpe",
            cash=10_000.0, fee=0.0, slippage_bps=5.0, rf=0.0):
    """Every (fast, slow) pair ranked on the in-sample period, with its out-of-sample result."""
    if by not in ("sharpe", "cagr"):
        raise InvalidInput("排名指标只能是 sharpe 或 cagr")
    ins, oos = scan.windows(bars, max(slows), start, end, split)
    kw = dict(cash=cash, fee=fee, slippage_bps=slippage_bps)
    df = scan.scan(bars, ins, oos, fasts, slows, rf, **kw)
    if len(df) < 3:
        raise InvalidInput("参数组合太少（短均线要小于长均线），至少要 3 组才有比较意义")
    df = scan.rank(df, by)
    return ScanRun(symbol, by, df, ins, oos,
                   metrics.summary(backtest.buy_and_hold(ins, **kw), rf), metrics.summary(backtest.buy_and_hold(oos, **kw), rf),
                   scan.rank_corr(df, by), df["oos_" + by].median())


def latest_signal(symbol, bars, fast=50, slow=200):
    """The crossover state at the latest completed close: hold or flat, since when, and whether it just flipped."""
    sig = strategy.ma_cross(bars["close"], fast, slow).dropna()
    if len(sig) < 2:
        raise InvalidInput("%s 的数据不足 %d 个交易日，算不出 %d 日均线" % (symbol, slow, slow))
    last, prev = sig.iloc[-1], sig.iloc[-2]
    flips = sig.index[sig["target"].diff().fillna(0) != 0]
    since = flips[-1] if len(flips) else sig.index[0]
    return {
        "symbol": symbol, "date": sig.index[-1].date(), "close": float(last["close"]),
        "fast": float(last["fast"]), "slow": float(last["slow"]), "gap": float(last["fast"] / last["slow"] - 1),
        "hold": bool(last["target"]), "since": since.date(), "since_is_cross": bool(len(flips)),
        "days": len(sig.loc[since:]) - 1, "flipped": bool(last["target"] != prev["target"]),
    }
