"""Position sizing and a multi-stock portfolio backtest.

Sizing: hold the top-scored stocks, each weighted by the inverse of its recent volatility (calmer stocks get
more, so each contributes similar risk), with a cap per stock. The backtest decides on a signal date's close,
trades at the next open in fractional shares, pays slippage and a fee per order, and marks to market at each
close."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..core import backtest

TOP_N = 10
MAX_WEIGHT = 0.20


def capped(weights, cap):
    """Weights summing to one with none above cap; what is cut is spread over the others in proportion."""
    w = weights / weights.sum()
    if cap * len(w) < 1:
        raise ValueError("持股数太少，单只上限 %.0f%% 无法分完全部资金" % (cap * 100))
    for _ in range(len(w)):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        w[~over] += excess * w[~over] / w[~over].sum()
    return w


def target_weights(scores, vol, top=TOP_N, max_weight=MAX_WEIGHT):
    """Top `top` symbols by score, inverse-volatility weighted, capped. Symbols without a volatility are skipped."""
    s = scores.dropna()
    v = vol.reindex(s.index)
    s = s[v.notna() & (v > 0)]
    picks = s.nlargest(top).index
    if len(picks) == 0:
        return pd.Series(dtype=float)
    inv = 1 / vol.reindex(picks).clip(lower=1e-4)
    return capped(inv, max(max_weight, 1 / len(picks)))


@dataclass
class PortfolioResult:
    equity: pd.Series
    turnover: pd.Series        # traded value / portfolio value, per rebalance
    holdings: dict             # execution date -> weights held after trading
    costs: float


def run(open_, close, weights_by_signal, start, end=None, cash=10_000.0, slippage_bps=5.0, fee=0.0):
    """Replays target weights: each signal date's weights are traded at the next trading day's open."""
    cal = close.index
    days = cal[(cal >= pd.Timestamp(start)) & ((cal <= pd.Timestamp(end)) if end else True)]
    nxt = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    execs = {nxt[d]: w for d, w in weights_by_signal.items() if d in nxt}
    last_close = close.ffill()
    ended = close.apply(lambda c: c.last_valid_index())  # a stock whose data stops (acquired, delisted) is paid out
    slip = slippage_bps / 10_000
    shares = pd.Series(0.0, index=close.columns)
    equity, turnover, holdings, costs = {}, {}, {}, 0.0
    for day in days:
        gone = [s for s in shares.index[shares != 0] if ended[s] is not None and ended[s] < day]
        if gone:  # paid out at the last price: cash replaces the position
            cash += float((shares[gone] * last_close.loc[day, gone]).sum())
            shares[gone] = 0.0
        if day in execs:
            px = open_.loc[day]
            mark = px.fillna(last_close.loc[day])
            value = cash + float((shares * mark).sum())
            target = execs[day].reindex(close.columns).fillna(0.0)
            tradable = px.notna() & (px > 0)
            want = (target * value / px.where(tradable)).fillna(shares)  # untradable today: keep what is held
            delta = (want - shares).where(tradable, 0.0)
            delta[delta.abs() * mark < 1e-6] = 0.0
            traded = float((delta.abs() * px.where(tradable).fillna(0)).sum())
            n_orders = int((delta != 0).sum())
            cost = traded * slip + fee * n_orders
            cash -= float((delta * px.where(tradable).fillna(0)).sum()) + cost
            shares = shares + delta
            costs += cost
            turnover[day] = traded / value if value > 0 else 0.0
            held = shares * mark
            holdings[day] = (held[held.abs() > 1e-9] / value).round(4)
        equity[day] = cash + float((shares * last_close.loc[day].fillna(0)).sum())
    return PortfolioResult(pd.Series(equity), pd.Series(turnover, dtype=float), holdings, costs)


def as_backtest_result(res, cash):
    """Wraps a portfolio result so the single-stock metrics can summarise it."""
    eq = res.equity
    trades = pd.DataFrame({"open": pd.Series(dtype=bool), "pnl": pd.Series(dtype=float), "return": pd.Series(dtype=float)})
    return backtest.Result(eq, pd.Series(np.ones(len(eq)), index=eq.index), trades, cash)


def select(scores, held=(), top=TOP_N, buffer=None):
    """The names to hold: with a buffer, current holdings ranked within the top `buffer` stay, and the free
    places go to the best-ranked others."""
    order = scores.dropna().sort_values(ascending=False).index
    if not buffer:
        return list(order[:top])
    rank = {s: i + 1 for i, s in enumerate(order)}
    keep = [s for s in held if rank.get(s, 10**9) <= buffer][:top]
    return keep + [s for s in order if s not in keep][: top - len(keep)]


def weights_for(names, vol, max_weight=MAX_WEIGHT):
    """Inverse-volatility weights for chosen names, capped; names without a volatility are dropped."""
    v = vol.reindex(names)
    v = v[v.notna() & (v > 0)]
    if v.empty:
        return pd.Series(dtype=float)
    return capped(1 / v.clip(lower=1e-4), max(max_weight, 1 / len(v)))
