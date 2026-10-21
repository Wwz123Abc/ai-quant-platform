import datetime as dt

import pandas as pd
import pytest

from quantlab.config import get_settings
from quantlab.errors import Conflict, InvalidInput, Unavailable
from quantlab.trading import daily, engine
from quantlab.trading.broker import PaperBroker, connect

TODAY = dt.date(2026, 10, 2)


def bars(prices, opens=None, end="2026-10-01"):
    idx = pd.bdate_range(end=end, periods=len(prices))
    opens = opens or prices
    return pd.DataFrame({"open": opens, "high": prices, "low": prices, "close": prices, "volume": 1e6}, index=idx, dtype=float)


@pytest.fixture
def con():
    c = connect()
    engine.create_account(c, "P", "paper", 500)
    yield c
    c.close()


def acct(con, name="P"):
    return engine.get_account(con, name)


# ---------------------------------------------------------------- accounts

def test_account_rules(con):
    with pytest.raises(Conflict):
        engine.create_account(con, "P", "paper", 500)
    with pytest.raises(InvalidInput):
        engine.create_account(con, "Q", "paper", 5000)  # above the configured capital limit
    with pytest.raises(InvalidInput):
        engine.create_account(con, "Q", "live", 100)
    with pytest.raises(Unavailable):
        engine.set_mode(con, "P", "live")  # live trading is not enabled
    engine.set_mode(con, "P", "plan")
    assert acct(con)["mode"] == "plan"


def test_live_needs_weeks_of_paper_trading(con, monkeypatch):
    monkeypatch.setenv("QUANTLAB_TRADING_LIVE_ENABLED", "true")
    get_settings.cache_clear()
    with pytest.raises(InvalidInput):
        engine.set_mode(con, "P", "live")


# ---------------------------------------------------------------- orders and risk

def test_orders_move_holdings_to_target(con):
    state = {"cash": 100.0, "positions": {"A": 2.0, "C": 1.0}}
    w = pd.Series({"A": 0.5, "B": 0.5})
    o = engine.orders_for(acct(con), state, w, {"A": 100.0, "B": 50.0, "C": 50.0}, "2026-09-30")
    assert [(x["side"], x["symbol"]) for x in o][0][0] == "sell"           # sells first
    by = {x["symbol"]: x for x in o}
    # value 100 + 200 + 50 = 350 < capital 500: sized on 350
    assert by["A"]["side"] == "sell" and by["A"]["qty"] == pytest.approx(2 - 175 / 100)
    assert by["B"]["qty"] == pytest.approx(175 / 50) and by["C"]["qty"] == pytest.approx(1.0) and by["C"]["side"] == "sell"
    assert by["B"]["client_id"] == "P-2026-09-30-B-buy"


def test_risk_limits(con):
    o = lambda sym, qty, px, side="buy": {"client_id": sym, "signal_date": "d", "symbol": sym, "side": side, "qty": qty, "est_price": px}
    w = pd.Series({"A": 0.2, "B": 0.2, "BIG": 0.2, "X": 0.2, "W": 0.5})
    orders = [o("A", 1, 90), o("NOTALLOWED", 1, 10), o("BIG", 1, 250), o("W", 1, 10), o("X", 1, 50, "sell")]
    ok, bad = engine.risk_check(acct(con), orders, w, allowed={"A", "B", "BIG", "W"})
    assert [x["symbol"] for x in ok] == ["A", "X"]                       # selling a non-listed holding is allowed
    reasons = {x["symbol"]: x["reason"] for x in bad}
    assert "名单" in reasons["NOTALLOWED"] and "单笔" in reasons["BIG"] and "单只上限" in reasons["W"]
    ok, bad = engine.risk_check(acct(con), [o("A", 1, 90)], w, {"A"}, sent_today=950)
    assert not ok and "当天成交额" in bad[0]["reason"]
    engine.halt(con, "P", "test")
    ok, bad = engine.risk_check(acct(con), [o("A", 1, 90)], w, {"A"})
    assert not ok and "暂停" in bad[0]["reason"]


# ---------------------------------------------------------------- paper broker

def test_paper_fills_at_next_open_sells_first_and_never_overdraws(con):
    market = {"A": bars([100, 100, 100], opens=[100, 100, 110], end="2026-10-02"), "B": bars([50, 50, 50], end="2026-10-02")}
    load = lambda s, refresh=False: market[s]
    b = PaperBroker(con, "P", load)
    with con:
        con.execute("INSERT INTO positions VALUES ('P', 'B', 2)")
    assert b.submit({"client_id": "1", "signal_date": "2026-09-30", "symbol": "A", "side": "buy", "qty": 5, "est_price": 100}) == "pending"
    assert b.submit({"client_id": "1", "signal_date": "2026-09-30", "symbol": "A", "side": "buy", "qty": 5, "est_price": 100}) == "duplicate"
    b.submit({"client_id": "2", "signal_date": "2026-09-30", "symbol": "B", "side": "sell", "qty": 2, "est_price": 50})
    fills = b.settle()
    assert [f["symbol"] for f in fills] == ["B", "A"]                     # the sale pays for the purchase
    a_fill = fills[1]
    assert a_fill["date"] == dt.date(2026, 10, 1) and a_fill["fill_price"] == pytest.approx(100 * 1.0005)
    # 500 + 2 * 50 * 0.9995 = 599.95 cash; 5 shares cost 500.25
    st = b.account()
    assert st["positions"] == {"A": 5} and st["cash"] == pytest.approx(599.95 - 500.25)
    b.submit({"client_id": "3", "signal_date": "2026-10-01", "symbol": "A", "side": "buy", "qty": 100, "est_price": 110})
    f = b.settle()[0]
    assert f["qty"] == pytest.approx(st["cash"] / (110 * 1.0005)) and b.account()["cash"] == pytest.approx(0, abs=1e-9)


def test_pending_until_the_session_opens(con):
    market = {"A": bars([100, 100], end="2026-09-30")}
    b = PaperBroker(con, "P", lambda s, refresh=False: market[s])
    b.submit({"client_id": "1", "signal_date": "2026-09-30", "symbol": "A", "side": "buy", "qty": 1, "est_price": 100})
    assert b.settle() == [] and len(b.open_orders()) == 1


# ---------------------------------------------------------------- rebalance

def test_rebalance_once_per_signal(con):
    w = pd.Series({"A": 0.2, "B": 0.2})
    res = engine.rebalance(con, acct(con), w, {"A": 50.0, "B": 25.0}, "2026-09-30", {"A", "B"}, dt.date(2026, 9, 30), today=TODAY)
    assert res["status"] == "ok" and res["sent"] == 2
    again = engine.rebalance(con, acct(con), w, {"A": 50.0, "B": 25.0}, "2026-09-30", {"A", "B"}, dt.date(2026, 9, 30), today=TODAY)
    assert again["status"] == "skipped"
    statuses = {o["symbol"]: o["status"] for o in engine.orders(con, "P")}
    assert statuses == {"A": "pending", "B": "pending"}


def test_plan_mode_sends_nothing(con):
    engine.set_mode(con, "P", "plan")
    res = engine.rebalance(con, acct(con), pd.Series({"A": 0.2}), {"A": 50.0}, "2026-09-30", {"A"}, dt.date(2026, 9, 30), today=TODAY)
    assert res["planned"] == 1 and res["sent"] == 0
    assert [o["status"] for o in engine.orders(con, "P")] == ["planned"]
    assert PaperBroker(con, "P").open_orders() == []


@pytest.mark.parametrize("prices,previous,data_date", [
    ({"A": 50.0}, {}, dt.date(2026, 9, 20)),            # stale data
    ({"A": 0.0}, {}, dt.date(2026, 9, 30)),             # no price
    ({"A": 50.0}, {"A": 20.0}, dt.date(2026, 9, 30)),   # +150% in a day
])
def test_bad_data_halts_instead_of_trading(con, prices, previous, data_date):
    res = engine.rebalance(con, acct(con), pd.Series({"A": 0.2}), prices, "2026-09-30", {"A"}, data_date, previous, today=TODAY)
    assert res["status"] == "halted" and acct(con)["halted"]
    assert engine.orders(con, "P") == []


def test_drawdown_breaker(con):
    b = PaperBroker(con, "P")
    engine.mark(con, acct(con), b, {}, dt.date(2026, 9, 30))
    with con:
        con.execute("UPDATE accounts SET cash = 300 WHERE name = 'P'")  # 40% below the peak of 500
    engine.mark(con, acct(con), b, {}, dt.date(2026, 10, 1))
    assert "回撤" in acct(con)["halted"]
    assert len(engine.equity_curve(con, "P")) == 2


def test_month_end_rules(con):
    assert daily.is_month_end(pd.Timestamp("2026-09-30")) and not daily.is_month_end(pd.Timestamp("2026-09-29"))
    assert daily.needs_signal(con, pd.Timestamp("2026-09-30"))
    assert not daily.needs_signal(con, pd.Timestamp("2026-10-05"))       # mid-month, nothing missed
    with con:
        con.execute("INSERT INTO orders (client_id, account, signal_date, symbol, side, qty, est_price, status, created)"
                    " VALUES ('x', 'P', '2026-08-31', 'A', 'buy', 1, 1, 'filled', 'now')")
    assert daily.needs_signal(con, pd.Timestamp("2026-10-05"))           # September's signal was missed: catch up


def test_daily_run_end_to_end(con):
    market = {"QQQ": bars([400] * 30, end="2026-09-30"), "A": bars([50] * 30, end="2026-09-30"), "B": bars([25] * 30, end="2026-09-30")}
    load = lambda s, refresh=False: market[s]
    sig = lambda: {"weights": pd.Series({"A": 0.2, "B": 0.2}), "signal_date": dt.date(2026, 9, 30), "data_date": dt.date(2026, 9, 30)}
    import quantlab.ml.universe as U
    U_load = U.load
    U.load = lambda refresh=False: {"symbols": ["A", "B"], "as_of": "x"}
    try:
        out = daily.run(load=load, signal_fn=sig, today=TODAY)
        assert out["P"]["rebalance"]["sent"] == 2 and out["P"]["equity"] == 500
        for s in market:  # the next session opens
            market[s] = bars(list(market[s]["close"]) + [market[s]["close"].iloc[-1]], end="2026-10-01")
        out = daily.run(load=load, signal_fn=sig, today=TODAY)
        assert out["P"]["filled"] == 2 and "rebalance" not in out["P"]
        st = PaperBroker(con, "P").account()
        assert st["positions"] == {"A": pytest.approx(2.0), "B": pytest.approx(4.0)}
    finally:
        U.load = U_load
