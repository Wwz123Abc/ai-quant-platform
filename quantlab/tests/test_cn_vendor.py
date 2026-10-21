"""TradingAgents on domestic data: every tool an analyst calls gets an answer without touching Yahoo."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("tradingagents")

from tradingagents.dataflows import router  # noqa: E402
from tradingagents.dataflows.config import set_config  # noqa: E402

from quantlab.ai import engine, news  # noqa: E402
from quantlab.config import get_settings  # noqa: E402
from quantlab.core import cn_data, data  # noqa: E402
from quantlab.errors import DataSourceError, NotFound  # noqa: E402
from quantlab.ml import sec  # noqa: E402

DAY = "2026-10-16"


def bars(n=400, seed=5):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.012, n)))
    idx = pd.bdate_range(end=DAY, periods=n)
    return pd.DataFrame({"open": close * 0.998, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e6}, index=idx)


@pytest.fixture
def cn(monkeypatch):
    """The cn price source, fake bars for QQQM and AAPL, and Yahoo made to fail loudly if anything calls it."""
    import yfinance

    monkeypatch.setenv("QUANTLAB_PRICE_SOURCE", "cn")
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr(sec, "cik_map", lambda: {})  # no network: tests that need SEC say what it returns
    get_settings.cache_clear()
    market = {"QQQM": bars(seed=1), "AAPL": bars(seed=2), "SPY": bars(seed=3)}

    def load(sym, refresh=False):
        sym = data.normalize(sym)
        if sym == "DOWN":
            raise DataSourceError("腾讯行情接口返回错误 502")
        if sym not in market:
            raise NotFound("腾讯行情找不到 %s" % sym)
        return market[sym]

    def no_yahoo(*a, **k):
        raise AssertionError("Yahoo was called")

    monkeypatch.setattr(data, "load", load)
    monkeypatch.setattr(yfinance, "Ticker", no_yahoo)
    monkeypatch.setattr(yfinance, "download", no_yahoo)
    cfg = engine.build_config()
    set_config(cfg)
    return cfg


def test_config_selects_the_vendor(cn):
    assert cn["data_vendors"]["core_stock_apis"] == "quantlab"
    assert cn["data_vendors"]["news_data"] == "quantlab"
    assert cn["tool_vendors"]["get_income_statement"] == "sec_edgar"
    assert "quantlab" in router.VENDOR_METHODS["get_indicators"]


def test_yahoo_config_is_untouched_locally(monkeypatch):
    monkeypatch.setenv("QUANTLAB_PRICE_SOURCE", "yahoo")
    get_settings.cache_clear()
    assert engine.build_config()["data_vendors"]["core_stock_apis"] == "yfinance"


def test_stock_data(cn):
    out = router.route_to_vendor("get_stock_data", "QQQM", "2026-10-01", DAY)
    lines = out.splitlines()
    assert lines[0] == "# Stock data for QQQM from 2026-10-01 to %s" % DAY and "Tencent" in out
    assert lines[4].startswith("Date,Open,High,Low,Close,Volume") and lines[-1].startswith(DAY)
    assert round(float(lines[-1].split(",")[4]), 2) == round(bars(seed=1)["close"].iloc[-1], 2)


def test_indicators_and_snapshot_use_the_same_prices(cn):
    from tradingagents.dataflows.vendors.yahoo.snapshot import build_verified_market_snapshot
    rsi = router.route_to_vendor("get_indicators", "QQQM", "rsi", DAY, 10)
    assert rsi.startswith("## rsi values") and "%s: " % DAY in rsi
    value = float(rsi.split("%s: " % DAY)[1].split("\n")[0])
    assert 0 < value < 100
    snap = build_verified_market_snapshot("QQQM", DAY, 30)
    assert "%.2f" % bars(seed=1)["close"].iloc[-1] in snap


def test_no_look_ahead(cn):
    out = router.route_to_vendor("get_stock_data", "QQQM", "2026-09-01", "2026-09-30")
    assert out.splitlines()[-1] < "2026-10"


def test_unknown_symbol_and_outage_become_sentinels(cn):
    assert router.route_to_vendor("get_stock_data", "ZZZZ", "2026-10-01", DAY).startswith("NO_DATA_AVAILABLE")
    assert router.route_to_vendor("get_stock_data", "DOWN", "2026-10-01", DAY).startswith("DATA_UNAVAILABLE")


def test_news_without_a_key_is_reported_unavailable(cn):
    for method, args in (("get_news", ("QQQM", "2026-10-09", DAY)), ("get_global_news", (DAY, 7, 5)),
                         ("get_insider_transactions", ("AAPL", DAY)), ("get_prediction_markets", ("fed rates", 5, DAY))):
        out = router.route_to_vendor(method, *args)
        assert out.startswith("DATA_UNAVAILABLE") and "fabricate" in out, method


def ts(day):
    return int(pd.Timestamp(day + " 15:00", tz="UTC").timestamp())


def test_company_news_from_finnhub_and_sec_filings(cn, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    seen = []

    def finnhub(path, **params):
        seen.append((path, params))
        return [{"headline": "Apple beats", "source": "Reuters", "summary": "Revenue up.", "url": "https://x/1", "datetime": ts("2026-10-15")},
                {"headline": "Apple beats", "source": "CNBC", "datetime": ts("2026-10-14")},  # same story twice
                {"headline": "From the future", "source": "X", "datetime": ts("2026-10-18")}]
    monkeypatch.setattr(news, "_finnhub", finnhub)
    monkeypatch.setattr(sec, "cik_map", lambda: {"AAPL": 320193})
    monkeypatch.setattr(sec, "submissions", lambda cik: {"recent": [
        {"accessionNumber": "0000320193-26-000099", "filingDate": "2026-10-19", "form": "8-K", "items": "5.02", "primaryDocument": "b.htm"},
        {"accessionNumber": "0000320193-26-000090", "filingDate": "2026-10-15", "form": "8-K", "items": "2.02,9.01", "primaryDocument": "a.htm"},
        {"accessionNumber": "0000320193-26-000080", "filingDate": "2026-10-15", "form": "4", "items": "", "primaryDocument": "f4.xml"}]})
    out = router.route_to_vendor("get_news", "AAPL", "2026-10-09", "2026-10-16")
    assert seen == [("company-news", {"symbol": "AAPL", "from": "2026-10-09", "to": "2026-10-16"})]
    assert out.count("Apple beats") == 1 and "Revenue up." in out and "From the future" not in out
    assert "AAPL 8-K filed 2026-10-15: results of operations (earnings release), financial statements and exhibits" in out
    assert "edgar/data/320193/000032019326000090/a.htm" in out
    assert "2026-10-19" not in out and "f4.xml" not in out  # after the window; Form 4 is not news


def test_filings_still_reported_when_finnhub_fails(cn, monkeypatch):
    def down(path, **params):
        raise news._unavailable("Finnhub company-news: rate limit reached")
    monkeypatch.setattr(news, "_finnhub", down)
    monkeypatch.setattr(sec, "cik_map", lambda: {"AAPL": 320193})
    monkeypatch.setattr(sec, "submissions", lambda cik: {"recent": [
        {"accessionNumber": "0000320193-26-000090", "filingDate": "2026-10-15", "form": "10-Q", "items": "", "primaryDocument": "q.htm"}]})
    out = router.route_to_vendor("get_news", "AAPL", "2026-10-09", DAY)
    assert "News articles unavailable (Finnhub company-news: rate limit reached)" in out and "10-Q filed 2026-10-15 (quarterly report)" in out


def test_global_news_and_insiders_stay_inside_the_window(cn, monkeypatch):
    monkeypatch.setattr(news, "_finnhub", lambda path, **p: {
        "news": [{"headline": "Fed holds", "source": "AP", "datetime": ts("2026-10-15")},
                 {"headline": "Old", "source": "AP", "datetime": ts("2026-09-01")},
                 {"headline": "Tomorrow", "source": "AP", "datetime": ts("2026-10-17")}],
        "stock/insider-transactions": {"data": [
            {"name": "Tim Cook", "filingDate": "2026-10-10", "transactionDate": "2026-10-08", "transactionCode": "S",
             "change": -1000, "share": 3000000, "transactionPrice": 250},
            {"name": "Later Filer", "filingDate": "2026-10-20", "transactionDate": "2026-10-15", "transactionCode": "S",
             "change": -5, "share": 5, "transactionPrice": 251}]},
    }[path])
    out = router.route_to_vendor("get_global_news", DAY, 7, 5)
    assert "Fed holds" in out and "Old" not in out and "Tomorrow" not in out
    out = router.route_to_vendor("get_insider_transactions", "AAPL", DAY)
    assert "Tim Cook" in out and "Later Filer" not in out  # filed after the analysis date


def test_finnhub_key_goes_in_a_header(monkeypatch):
    import io
    import urllib.error
    import urllib.request
    monkeypatch.setenv("FINNHUB_API_KEY", "secret-key")
    sent = []

    def urlopen(req, timeout):
        sent.append(req)
        if "fail" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b""))
        return io.BytesIO(b"[]")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert news._finnhub("company-news", symbol="AAPL") == []
    assert "secret-key" not in sent[0].full_url and sent[0].get_header("X-finnhub-token") == "secret-key"
    from tradingagents.dataflows.errors import VendorUnavailableError
    with pytest.raises(VendorUnavailableError, match="key rejected"):
        news._finnhub("fail")


def test_settlement_closes(cn):
    from tradingagents.memory import settlement
    closes = settlement.get_closes("QQQM", "2026-10-01", "2026-10-08")
    assert list(closes.index.strftime("%Y-%m-%d")) == ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"]


def test_profiles(cn, monkeypatch):
    from tradingagents.agents import context
    quotes = {"AAPL": ["200", "苹果", "AAPL.OQ"] + [""] * 43 + ["Apple Inc."],
              "QQQM": ["200", "纳斯达克100指数ETF-Invesco", "QQQM.OQ"] + [""] * 43 + ["Invesco NASDAQ 100 ETF"]}
    monkeypatch.setattr(cn_data, "tencent_quote", lambda s: quotes[s])
    monkeypatch.setattr(sec, "cik_map", lambda: {"AAPL": 320193})
    monkeypatch.setattr(sec, "submissions", lambda cik: {"name": "Apple Inc.", "entityType": "operating",
                                                         "sicDescription": "Electronic Computers"})
    context._identity.cache_clear()
    assert context.resolve_instrument_identity("AAPL") == {"company_name": "Apple Inc.", "industry": "Electronic Computers",
                                                           "exchange": "NASDAQ", "quote_type": "EQUITY"}
    assert context.resolve_instrument_identity("QQQM") == {"company_name": "Invesco NASDAQ 100 ETF", "exchange": "NASDAQ",
                                                           "quote_type": "ETF"}


def test_fundamentals(cn, monkeypatch):
    from quantlab.holdings import signals
    f = {"market_cap": 3.5e12, "pe": 33.0, "pb": 50.1, "revenue_ttm": 4.1e11, "rev_growth": 0.06, "net_income_ttm": 1.1e11,
         "op_margin": 0.31, "roa": 0.29, "cfo_to_ni": 1.1, "sue": 1.4}
    monkeypatch.setattr(signals, "fundamentals", lambda sym, close: f if sym == "AAPL" else None)
    monkeypatch.setattr(sec, "cik_map", lambda: {"AAPL": 320193})
    out = router.route_to_vendor("get_fundamentals", "AAPL", DAY)
    assert "Market Cap: $3500.00B" in out and "PE Ratio (TTM): 33.00" in out and "Operating Margin (TTM): 31.0%" in out
    assert "Not available" in router.route_to_vendor("get_fundamentals", "QQQM", DAY)


def test_sec_outage_is_unavailable_not_absent(cn, monkeypatch):
    """A blocked SEC request must not read as "this company has no financials", and the profile keeps Tencent's part."""
    from tradingagents.agents import context

    def blocked():
        raise DataSourceError("SEC 返回错误 403")
    monkeypatch.setattr(sec, "cik_map", blocked)
    assert router.route_to_vendor("get_fundamentals", "AAPL", DAY).startswith("DATA_UNAVAILABLE")
    monkeypatch.setattr(cn_data, "tencent_quote", lambda s: ["200", "苹果", "AAPL.OQ"] + [""] * 43 + ["Apple Inc."])
    context._identity.cache_clear()
    assert context.resolve_instrument_identity("AAPL") == {"company_name": "Apple Inc.", "exchange": "NASDAQ"}
    monkeypatch.setattr(cn_data, "tencent_quote", lambda s: ["200", "纳斯达克100指数ETF-Invesco", "QQQM.OQ"])
    context._identity.cache_clear()
    assert context.resolve_instrument_identity("QQQM")["quote_type"] == "ETF"
