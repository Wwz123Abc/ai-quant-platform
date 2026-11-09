"""The AI research run is told what the owner holds and the rules they follow (ai/portfolio.py)."""
import pytest

from quantlab.ai import engine, personal
from quantlab.ai import portfolio as acct
from quantlab.errors import DataSourceError
from quantlab.holdings import ledger, policy, review

SNAP = {
    "account": "me",
    "trades": [
        {"date": "2026-09-10", "side": "buy", "symbol": "AAA", "shares": 10, "price": 100, "fees": 1, "currency": "USD"},
        {"date": "2026-09-20", "side": "buy", "symbol": "AAA", "shares": 10, "price": 120, "fees": 0, "currency": "USD"},
        {"date": "2026-10-01", "side": "sell", "symbol": "AAA", "shares": 5, "price": 130, "fees": 0, "currency": "USD"},
        {"date": "2026-09-15", "side": "buy", "symbol": "BBB", "shares": 4, "price": 50, "fees": 0, "currency": "USD"},
    ],
    "cash": [{"name": "储蓄", "currency": "USD", "amount": 1000}],
    "fixed": [],
}
PRICES = {"AAA": 150.0, "BBB": 60.0}
POL = policy.validate({"targets": {"AAA": 0.25}, "uncapped": ["BBB"], "cash_reserve": 0.10})


@pytest.fixture
def val():
    return review.valuation(SNAP, PRICES, "2026-10-20")


def test_facts_describe_the_position_and_what_the_rules_still_allow(val):
    f = acct.facts("AAA", val, POL)
    assert f["held"] and f["shares"] == 15 and f["value"] == pytest.approx(15 * 150)
    inv = 15 * 150 + 4 * 60 + 1000                                   # stocks plus cash
    assert f["investable"] == pytest.approx(inv) and f["weight"] == pytest.approx(15 * 150 / inv)
    assert f["room_cap"] == pytest.approx(max(0.0, 0.25 * inv - 15 * 150))   # AAA is already above its 25% cap: no room
    assert f["max_buy"] == pytest.approx(min(f["room_cap"], f["room_cash"]))
    other = acct.facts("BBB", val, POL)
    assert other["uncapped"] and other["cap"] == 1.0                  # a broad ETF has no single-holding cap
    none = acct.facts("CCC", val, POL)
    assert not none["held"] and none["shares"] == 0 and none["value"] == 0.0 and none["gain"] is None


def test_holdings_without_a_price_are_called_out_not_silently_dropped():
    v = review.valuation(SNAP, {"AAA": 150.0}, "2026-10-20")          # BBB has no price
    f = acct.facts("AAA", v, POL)
    assert f["unpriced"] == ["BBB"]
    assert "no price could be found for BBB" in acct.render(f) and "overstated" in acct.render(f)
    assert "BBB 没取到价格" in personal.holding_line(f)
    assert acct.facts("AAA", review.valuation(SNAP, PRICES, "2026-10-20"), POL)["unpriced"] == []


def test_the_block_for_the_agents_says_what_is_held_and_the_rules(val):
    text = acct.render(acct.facts("AAA", val, POL))
    for part in ("holds 15 shares", "average cost", "of investable funds", "at most 25%", "cash must stay at or above 10%",
                 "AAA 25%", "whole shares only", "by hand at a retail broker", "no options"):
        assert part in text, part
    etf = acct.render(acct.facts("BBB", val, POL))
    assert "no single-holding cap applies to BBB" in etf
    nothing = acct.render(acct.facts("CCC", val, POL))
    assert "holds NONE" in nothing and "nothing to act on" in nothing   # selling what is not held cannot be a view to act on


def test_the_report_says_what_the_ai_knew(val):
    held = personal.holding_line(acct.facts("AAA", val, POL))
    assert "持有 15 股" in held and "平均成本" in held and "单只上限 25%" in held and "最多还能买入约" in held
    none = personal.holding_line(acct.facts("CCC", val, POL))
    assert "没有持有" in none and "只能理解成“不买”" in none
    unknown = personal.holding_line(None)
    assert "不知道" in unknown
    assert "没有读到你的持仓" in acct.describe(None, "AAA")


def test_build_makes_a_book_the_agents_can_read(monkeypatch):
    monkeypatch.setattr(ledger, "configured", lambda: False)
    monkeypatch.setattr(ledger, "load", lambda refresh=False: SNAP)
    monkeypatch.setattr(policy, "load", lambda: POL)
    monkeypatch.setattr(review, "prices_for", lambda syms, load=None: PRICES)
    book, f = acct.build("AAA")
    text = book.render("AAA")
    assert "Current position in AAA: 15 units, average price" in text and "Cash available: 1,000.00 USD" in text
    assert "Other positions: BBB 4" in text and "Account facts for the owner" in text
    other, _ = acct.build("BBB")
    assert book.fingerprint() != other.fingerprint()                 # a changed book cannot resume a stale run
    assert f["held"] and f["symbol"] == "AAA"


def test_an_unreadable_ledger_gives_no_book_not_an_empty_one(monkeypatch):
    def broken(refresh=False):
        raise DataSourceError("账本连不上")
    monkeypatch.setattr(ledger, "configured", lambda: False)
    monkeypatch.setattr(ledger, "load", broken)
    assert acct.build("AAA") == (None, None)                          # "not provided" must never read as "flat"


def test_analyze_hands_the_book_to_tradingagents_and_shows_it_in_the_report(monkeypatch):
    import sys
    import types

    seen = {}

    class FakeGraph:
        def __init__(self, analysts, config=None, callbacks=None):
            pass

        def propagate(self, name, day, portfolio=None):
            seen["portfolio"], seen["name"] = portfolio, name
            keys = [k for k, _ in engine.REPORTS]
            return {k: "报告 %s" % k for k in keys}, "Underweight"

    mod = types.ModuleType("tradingagents.graph.trading_graph")
    mod.TradingAgentsGraph = FakeGraph
    monkeypatch.setitem(sys.modules, "tradingagents.graph.trading_graph", mod)
    monkeypatch.setattr(engine, "require_ready", lambda: None)
    monkeypatch.setattr(engine, "build_config", lambda s=None: {})
    monkeypatch.setattr(ledger, "configured", lambda: False)
    monkeypatch.setattr(ledger, "load", lambda refresh=False: SNAP)
    monkeypatch.setattr(policy, "load", lambda: POL)
    monkeypatch.setattr(review, "prices_for", lambda syms, load=None: PRICES)
    monkeypatch.setattr(engine, "personal_first", lambda s, d, r, reports, mine=None: {"mine": mine, **reports})
    rating, reports = engine.analyze("AAA", "2026-11-03", engine.Usage())
    assert rating == "Underweight" and seen["portfolio"] is not None
    assert "Current position in AAA" in seen["portfolio"].render("AAA")
    assert reports["mine"]["held"] and list(reports)[1] == "portfolio_context"
    assert "Account facts" in reports["portfolio_context"]["text"]

    # a ledger that cannot be read: the run still happens, told nothing, and the report says so
    monkeypatch.setattr(acct, "build", lambda symbol: (_ for _ in ()).throw(RuntimeError("down")))
    rating, reports = engine.analyze("AAA", "2026-11-03", engine.Usage())
    assert seen["portfolio"] is None and reports["mine"] is None and "没有读到你的持仓" in reports["portfolio_context"]["text"]
