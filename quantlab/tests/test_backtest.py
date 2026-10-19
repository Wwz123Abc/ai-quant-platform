import datetime as dt

import pandas as pd
import pytest

from quant import backtest, data, metrics, strategy


def bars(closes, opens=None):
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    opens = opens or closes
    return pd.DataFrame({"open": opens, "high": closes, "low": closes, "close": closes, "volume": 0}, index=idx)


def test_trades_at_next_open_not_same_close():
    b = bars([10, 10, 10, 10], opens=[10, 10, 20, 30])
    target = pd.Series([0, 1, 1, 0], b.index)  # buy decided on day 1's close, sell on day 3's close
    r = backtest.run(b, target, cash=1000, slippage_bps=0)
    t = r.trades.iloc[0]
    assert t["entry_date"] == b.index[2] and t["entry_price"] == 20
    assert t["open"]  # the sell on day 3's close would trade on day 4, which does not exist yet
    assert r.in_market.tolist() == [0, 0, 1, 1]


def test_fee_and_slippage():
    b = bars([100, 100, 110, 110], opens=[100, 100, 100, 110])
    target = pd.Series([1, 1, 0, 0], b.index)
    r = backtest.run(b, target, cash=1000, fee=1, slippage_bps=100)
    # buy on day 1's open at 101: 9 shares for 909 + 1 fee; sell on day 3's open at 110 * 0.99 = 108.9
    t = r.trades.iloc[0]
    assert t["shares"] == 9
    assert t["pnl"] == pytest.approx(9 * 108.9 - 1 - (909 + 1))
    assert r.equity.iloc[-1] == pytest.approx(1000 - 910 + 9 * 108.9 - 1)


def test_buy_and_hold_buys_on_first_open():
    b = bars([10, 11, 12], opens=[10, 10.5, 11.5])
    r = backtest.buy_and_hold(b, cash=100, slippage_bps=0)
    assert r.trades.iloc[0]["entry_date"] == b.index[0]
    assert r.equity.tolist() == [100, 110, 120]


def test_target_before_window_carries_in():
    full = bars([1, 2, 3, 4, 5])
    target = pd.Series([0, 0, 1, 1, 1], full.index)
    r = backtest.run(full.iloc[3:], target, cash=100, slippage_bps=0)
    assert r.trades.iloc[0]["entry_date"] == full.index[3]  # decided on day 2, inside the warm-up


def test_ma_cross_flat_during_warm_up():
    s = strategy.ma_cross(pd.Series(range(1, 11), dtype=float), fast=2, slow=5)
    assert s["target"].tolist() == [0, 0, 0, 0, 1, 1, 1, 1, 1, 1]
    with pytest.raises(ValueError):
        strategy.ma_cross(pd.Series([1.0]), fast=5, slow=5)


def test_max_drawdown():
    eq = pd.Series([100, 120, 90, 130, 65], pd.bdate_range("2024-01-01", periods=5))
    mdd, peak, trough = metrics.max_drawdown(eq)
    assert mdd == pytest.approx(-0.5) and peak == eq.index[3] and trough == eq.index[4]


def test_unfinished_session_bar_dropped():
    b = bars([1, 2, 3])
    last = b.index[-1]
    during = dt.datetime(last.year, last.month, last.day, 11, tzinfo=data.NY)
    after = dt.datetime(last.year, last.month, last.day, 16, 5, tzinfo=data.NY)
    assert len(data.completed(b, data.NY, during)) == 2 and len(data.completed(b, data.NY, after)) == 3
    # 11:00 in New York is 23:00 in Hong Kong, after that day's close there
    assert len(data.completed(b, data.HK, during)) == 3


def test_yahoo_symbols():
    assert data.yahoo_symbol("700") == "0700.HK" and data.yahoo_symbol("brk.b") == "BRK-B"
