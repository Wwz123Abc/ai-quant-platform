"""Parameter scan with an out-of-sample check: choose parameters on an earlier period, then test them on a later one."""
import pandas as pd

from ..errors import InvalidInput
from . import backtest, metrics, strategy

FAST = (5, 10, 15, 20, 30, 40, 50, 75, 100)
SLOW = (50, 75, 100, 125, 150, 175, 200, 250, 300)
KEYS = ("cagr", "sharpe", "mdd", "trades", "exposure")


def windows(bars, slowest, start=None, end=None, split=None):
    """In-sample and out-of-sample bars.

    Both begin after the slowest average has enough history, so every pair is tested on the same days.
    Without a split date the last 30% of the days are held out."""
    if len(bars) < slowest + 500:
        raise InvalidInput("数据只有 %d 个交易日，扣掉 %d 日均线的预热期后不够分成两段" % (len(bars), slowest))
    first = bars.index[slowest - 1]
    lo = max(first, pd.Timestamp(start)) if start else first
    w = bars.loc[lo:end] if end else bars.loc[lo:]
    cut = pd.Timestamp(split) if split else w.index[int(len(w) * 0.7)]
    ins, oos = w[w.index < cut], w[w.index >= cut]
    if len(ins) < 250 or len(oos) < 250:
        raise InvalidInput("样本内和样本外都至少要有一年（250 个交易日）的数据")
    return ins, oos


def scan(bars, ins, oos, fasts=FAST, slows=SLOW, rf=0.0, **kw):
    """One row per (fast, slow) pair with its in-sample (is_) and out-of-sample (oos_) figures.

    Averages come from the full history, which only ever looks backwards, so the out-of-sample period starts in
    whatever position the strategy held at the end of the in-sample period."""
    rows = []
    for f in fasts:
        for s in slows:
            if f >= s:
                continue
            target = strategy.ma_cross(bars["close"], f, s)["target"]
            a = metrics.summary(backtest.run(ins, target, **kw), rf)
            b = metrics.summary(backtest.run(oos, target, **kw), rf)
            rows.append({"fast": f, "slow": s, **{"is_" + k: a[k] for k in KEYS}, **{"oos_" + k: b[k] for k in KEYS}})
    return pd.DataFrame(rows)


def rank(df, by="sharpe"):
    """Adds in-sample and out-of-sample ranks (1 = best) and sorts by the in-sample rank."""
    df = df.copy()
    df["is_rank"] = df["is_" + by].rank(ascending=False, method="min").astype(int)
    df["oos_rank"] = df["oos_" + by].rank(ascending=False, method="min").astype(int)
    return df.sort_values(["is_rank", "oos_rank"]).reset_index(drop=True)


def rank_corr(df, by="sharpe"):
    """Spearman correlation between in-sample and out-of-sample results: does a good past rank carry forward?"""
    return df["is_" + by].rank().corr(df["oos_" + by].rank())
