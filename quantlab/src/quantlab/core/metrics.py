"""Performance figures for a backtest result."""
import math


def max_drawdown(equity):
    """The largest fall from a running peak, with the peak and trough dates."""
    dd = equity / equity.cummax() - 1
    trough = dd.idxmin()
    peak = equity[:trough].idxmax()
    return dd.min(), peak, trough


def summary(res, rf=0.0):
    """Total and annualized return, volatility, Sharpe ratio, drawdown, time in market and trade stats.

    rf is the annual risk-free rate used for the Sharpe ratio."""
    eq = res.equity
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    daily = eq.pct_change().dropna()
    vol = daily.std() * math.sqrt(252)
    mdd, peak, trough = max_drawdown(eq)
    closed = res.trades[~res.trades["open"]]
    return {
        "start": eq.index[0].date(), "end": eq.index[-1].date(), "years": years,
        "final": eq.iloc[-1],
        "total": eq.iloc[-1] / res.cash - 1,
        "cagr": (eq.iloc[-1] / res.cash) ** (1 / years) - 1,
        "vol": vol,
        "sharpe": (daily.mean() * 252 - rf) / vol if vol > 0 else float("nan"),
        "mdd": mdd, "mdd_peak": peak.date(), "mdd_trough": trough.date(),
        "exposure": res.in_market.mean(),
        "trades": len(res.trades),
        "win_rate": (closed["pnl"] > 0).mean() if len(closed) else float("nan"),
        "avg_trade": closed["return"].mean() if len(closed) else float("nan"),
    }
