"""TradingAgents data from sources a server in mainland China can reach (QUANTLAB_PRICE_SOURCE=cn).

TradingAgents takes prices, indicators, company profiles and news from Yahoo, which rate-limits servers in mainland
China. install() adds a "quantlab" vendor to TradingAgents' routing table and points the few places that call Yahoo
directly (the price loader behind indicators and the verified snapshot, settlement closes, the company profile) at
QuantLab's own data; DATA_VENDORS and TOOL_VENDORS select it in the config. TradingAgents itself is not changed.

- Prices: QuantLab's Tencent bars with Sina's adjustment factors, the same series the backtests use.
- Profile and fundamentals: SEC EDGAR (registrant details and company facts as first reported).
- Statements: TradingAgents' own SEC EDGAR vendor.
- News: Finnhub articles and the company's SEC filings; market news and insider trades from Finnhub (see news.py).
  Without FINNHUB_API_KEY those calls report the data as unavailable, which TradingAgents passes to the agents
  with an instruction not to make anything up.

QuantLab analyses only the latest close, so the profile and fundamentals are today's; prices are cut at the
analysis date like TradingAgents' own loader does."""
import logging
import os
import threading

import pandas as pd

from ..core import cn_data, data
from ..errors import DataSourceError, QuantLabError

log = logging.getLogger(__name__)
VENDOR = "quantlab"
DATA_VENDORS = {
    "core_stock_apis": VENDOR, "technical_indicators": VENDOR, "fundamental_data": VENDOR, "news_data": VENDOR,
    "macro_data": "fred", "prediction_markets": VENDOR,
}
TOOL_VENDORS = {"get_balance_sheet": "sec_edgar", "get_cashflow": "sec_edgar", "get_income_statement": "sec_edgar"}
EXCHANGES = {"OQ": "NASDAQ", "N": "NYSE", "AM": "NYSE American", "PS": "NYSE Arca"}
_lock = threading.Lock()
_installed = False


def _errors():
    from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
    return NoMarketDataError, VendorUnavailableError


def _symbol(symbol):
    """QuantLab's form of a ticker TradingAgents passes in Yahoo's form (BRK-B -> BRK.B)."""
    return data.normalize(str(symbol).replace("-", "."))


def _bars(symbol):
    NoMarketDataError, VendorUnavailableError = _errors()
    try:
        return data.load(_symbol(symbol))
    except DataSourceError as e:
        raise VendorUnavailableError("domestic price source unreachable: %s" % e) from e
    except QuantLabError as e:  # unknown or invalid symbol
        raise NoMarketDataError(symbol, symbol, str(e)) from e


def load_ohlcv(symbol, as_of_date, fill_gaps=True):
    """Stands in for TradingAgents' Yahoo loader: the same frame (a Date column and title-case prices), cut at
    as_of_date, gaps filled the same way and stale data refused the same way."""
    from tradingagents.dataflows.vendors.yahoo import ohlcv as yahoo
    NoMarketDataError, _ = _errors()
    frame = _bars(symbol).rename(columns=str.title)[["Open", "High", "Low", "Close", "Volume"]]
    frame = yahoo._clean_dataframe(frame.rename_axis("Date").reset_index())
    frame = frame[frame["Date"] <= pd.to_datetime(as_of_date).normalize()]
    if frame.empty:
        raise NoMarketDataError(symbol, symbol, "no price rows on or before %s" % as_of_date)
    frame = yahoo._fill_price_gaps(frame) if fill_gaps else frame.dropna(subset=["Close"]).copy()
    yahoo._assert_ohlcv_not_stale(frame, as_of_date, symbol, symbol)
    return frame


def get_stock_data(symbol, start_date, end_date):
    NoMarketDataError, _ = _errors()
    frame = load_ohlcv(symbol, end_date, fill_gaps=False)
    frame = frame[frame["Date"] >= pd.to_datetime(start_date)].set_index("Date")
    if frame.empty:
        raise NoMarketDataError(symbol, symbol, "no price rows between %s and %s" % (start_date, end_date))
    frame[["Open", "High", "Low", "Close"]] = frame[["Open", "High", "Low", "Close"]].round(2)
    frame["Volume"] = frame["Volume"].round().astype("int64")
    return ("# Stock data for %s from %s to %s\n# Total records: %d\n"
            "# Source: Tencent daily bars, split- and dividend-adjusted with Sina's factors\n\n"
            % (symbol.upper(), start_date, end_date, len(frame))) + frame.to_csv()


def get_closes(symbol, start_date, end_date):
    """Daily closes from start_date up to, not including, end_date (for settling past decisions)."""
    close = _bars(symbol)["close"]
    return close[(close.index >= pd.Timestamp(start_date)) & (close.index < pd.Timestamp(end_date))]


def get_company_profile(ticker):
    """Name, type, industry and exchange: SEC's registrant details for companies, Tencent's names for funds."""
    from ..ml import sec
    sym = _symbol(ticker)
    fields = cn_data.tencent_quote(sym)
    reg = {}
    try:  # without SEC the names and exchange still come from Tencent
        cik = sec.cik_map().get(sym)
        reg = sec.submissions(cik) if cik else {}
    except QuantLabError as e:
        log.warning("SEC registrant details for %s: %s", sym, e)
    operating = reg.get("entityType") == "operating"
    english = fields[46].strip() if len(fields) > 46 else ""
    if reg:
        kind = "EQUITY" if operating else "ETF"
    else:  # SEC unreachable or not a filer: only a fund's name says what it is
        kind = "ETF" if "ETF" in (fields[1] + " " + english).upper() else ""
    profile = {
        "longName": reg.get("name") if operating else (english or fields[1]),
        "quoteType": kind,
        "exchange": EXCHANGES.get(fields[2].rsplit(".", 1)[-1], ""),
    }
    if operating and reg.get("sicDescription"):
        profile["industry"] = reg["sicDescription"]
    return {k: v for k, v in profile.items() if v}


def get_fundamentals(ticker, curr_date=None):
    """A company overview from SEC company facts and the latest close, in the shape of TradingAgents' overview."""
    from ..holdings.signals import fundamentals
    from ..ml import sec
    _, VendorUnavailableError = _errors()
    bars = _bars(ticker)
    if curr_date:
        bars = bars[bars.index <= pd.Timestamp(curr_date)]
    sym, close, day = _symbol(ticker), float(bars["close"].iloc[-1]), bars.index[-1].date()
    try:
        filer = sym in sec.cik_map()
    except QuantLabError as e:  # SEC unreachable says nothing about the company
        raise VendorUnavailableError("SEC EDGAR unreachable: %s" % e) from e
    f = fundamentals(sym, close) if filer else None
    if filer and not f:
        raise VendorUnavailableError("SEC company facts for %s could not be read" % sym)
    if not f:
        return ("# Company fundamentals for %s\n\nNot available: SEC EDGAR has no company financials for %s (for "
                "example an ETF, a fund or a foreign filer). Do not estimate these figures.\n" % (sym, sym))

    def money(v):
        return "N/A" if v is None else "$%.2fB" % (v / 1e9)

    def ratio(v, pct=False):
        return "N/A" if v is None else ("%.1f%%" % (v * 100) if pct else "%.2f" % v)

    rows = [
        ("Price", "$%.2f (close of %s)" % (close, day)), ("Market Cap", money(f["market_cap"])),
        ("PE Ratio (TTM)", ratio(f["pe"])), ("Price to Book", ratio(f["pb"])),
        ("Revenue (TTM)", money(f["revenue_ttm"])), ("Revenue Growth (YoY)", ratio(f["rev_growth"], True)),
        ("Net Income (TTM)", money(f["net_income_ttm"])), ("Operating Margin (TTM)", ratio(f["op_margin"], True)),
        ("Return on Assets", ratio(f["roa"], True)), ("Operating Cash Flow / Net Income", ratio(f["cfo_to_ni"])),
        ("Standardized Unexpected Earnings", ratio(f["sue"])),
    ]
    return ("# Company fundamentals for %s\n# Source: SEC EDGAR company facts as first reported, trailing twelve "
            "months; market values use the close above\n\n" % sym) + "".join("%s: %s\n" % r for r in rows)


def _unreachable(what):
    def call(*args, **kwargs):
        _, VendorUnavailableError = _errors()
        raise VendorUnavailableError("%s cannot be reached from this server" % what)
    return call


def install():
    """Registers the vendor and redirects TradingAgents' direct Yahoo calls. Safe to call more than once."""
    global _installed
    with _lock:
        if _installed:
            return
        from tradingagents.agents import context
        from tradingagents.dataflows import router
        from tradingagents.dataflows.vendors.yahoo import market, snapshot
        from tradingagents.memory import settlement

        from . import news

        methods = router.VENDOR_METHODS
        own = {
            "get_stock_data": get_stock_data,
            "get_indicators": market.get_stock_stats_indicators_window,  # TradingAgents' indicators on our prices
            "get_fundamentals": get_fundamentals,
            "get_news": news.get_news,
            "get_global_news": news.get_global_news,
            "get_insider_transactions": news.get_insider_transactions,
            "get_prediction_markets": _unreachable("Polymarket"),
        }
        for method, fn in own.items():
            methods[method][VENDOR] = fn
        market.load_ohlcv = snapshot.load_ohlcv = load_ohlcv
        settlement.get_closes = get_closes
        context.get_company_profile = get_company_profile
        context._identity.cache_clear()
        _installed = True
        log.info("TradingAgents uses domestic data: Tencent/Sina prices, SEC fundamentals, news %s",
                 "from Finnhub and SEC" if os.environ.get("FINNHUB_API_KEY") else "from SEC filings only")
