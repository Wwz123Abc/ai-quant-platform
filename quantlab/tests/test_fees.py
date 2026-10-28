import sqlite3

import pandas as pd
import pytest

from quantlab.core import backtest, fees, paper, research


def bars(opens, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(opens))
    return pd.DataFrame({"open": opens, "high": opens, "low": opens, "close": opens, "volume": 0}, index=idx)


def day(s):
    return pd.Timestamp(s)


def test_standard_commission_and_sec_fee():
    c = fees.Schedule("standard")
    assert c.charge(day("2026-01-05"), 1000, 50_000, False) == 18
    assert c.charge(day("2026-01-05"), 1200, 60_000, False) == pytest.approx(18 + 200 * 0.015)
    # sales also pay 0.00206%, rounded up to the cent: $10,000 -> $0.206 -> $0.21
    assert c.charge(day("2026-01-06"), 100, 10_000, True) == pytest.approx(18.21)


def test_trade25_free_up_to_the_monthly_turnover_then_standard():
    c = fees.Schedule("trade25")
    usd_limit = fees.TRADE25_LIMIT_HKD / fees.HKD_PER_USD  # about $32,051
    assert c.charge(day("2026-03-02"), 100, 20_000, False) == 0
    assert c.charge(day("2026-03-10"), 100, usd_limit - 20_001, True) == fees.sec_fee(usd_limit - 20_001)  # just under it
    assert c.charge(day("2026-03-20"), 10, 1_000, False) == 18  # over it
    assert c.charge(day("2026-04-01"), 10, 1_000, False) == 0  # a new month starts again from zero


def test_flat_fee_unchanged():
    c = fees.Schedule(2.5)
    assert c.charge(day("2026-01-05"), 10, 1_000, True) == 2.5


def test_unknown_plan_rejected():
    with pytest.raises(ValueError):
        fees.Schedule("free")


def test_backtest_pays_and_reports_fees():
    b = bars([100, 100, 110, 110])
    target = pd.Series([1, 1, 0, 0], b.index)
    r = backtest.run(b, target, cash=1000, fee="standard", slippage_bps=0)
    # $1000 buys 9 shares at 100 plus $18; selling 9 at 110 pays $18 and the SEC fee on $990 ($0.03)
    t = r.trades.iloc[0]
    assert t["shares"] == 9
    assert r.fees == pytest.approx(18 + 18 + 0.03)
    assert r.equity.iloc[-1] == pytest.approx(1000 - 918 + 990 - 18.03)


def test_buy_leaves_room_for_the_commission():
    b = bars([100, 100])
    r = backtest.run(b, pd.Series([1, 1], b.index), cash=1000, fee="standard", slippage_bps=0)
    assert r.trades.iloc[0]["shares"] == 9  # 10 shares would need $1,018


def test_gross_return_ignores_fees_only():
    idx = pd.bdate_range("2020-01-01", periods=400)
    close = pd.Series([100 + (i % 50) for i in range(400)], idx, dtype=float)
    b = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 0}, index=idx)
    run = research.backtest_ma("X", b, 5, 20, cash=10_000, fee="standard", slippage_bps=5)
    s = run.summary
    assert s["fees"] > 0 and s["gross_cagr"] > s["cagr"]
    free = research.backtest_ma("X", b, 5, 20, cash=10_000, fee=0, slippage_bps=5)
    assert free.summary["cagr"] == pytest.approx(s["gross_cagr"])


def test_paper_accounts_without_a_fee_move_to_trade25(tmp_path):
    path = tmp_path / "paper.db"
    old = sqlite3.connect(path)
    old.executescript(paper.MIGRATIONS[0] + ";\nPRAGMA user_version = 1;")
    old.execute("INSERT INTO accounts VALUES ('a', 'ma_cross', '{}', '[]', 10000, 0, 5, '2026-01-01', 'x')")
    old.execute("INSERT INTO accounts VALUES ('b', 'ma_cross', '{}', '[]', 10000, 3, 5, '2026-01-01', 'x')")
    old.commit()
    old.close()
    con = paper.connect(path)
    assert {a["name"]: a["fee"] for a in paper.accounts(con)} == {"a": "trade25", "b": 3}
