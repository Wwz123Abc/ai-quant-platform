"""News for TradingAgents' analysts from Finnhub and SEC filings, both reachable from mainland China.

- Company news: Finnhub's articles about the ticker, then the company's own SEC filings (8-K events, quarterly and
  annual reports) in the same window. The filings need no key and are the authoritative record of material events.
- Market news: Finnhub's general news.
- Insider transactions: Finnhub's record of Form 4 filings, counting only those filed by the analysis date.

The key is FINNHUB_API_KEY (free at finnhub.io), sent in a header, never in a URL. Without it, or when Finnhub fails,
a call raises VendorUnavailableError, which TradingAgents turns into "data unavailable, do not make anything up".
Only articles published inside the requested window are returned, so nothing from after the analysis date leaks in."""
import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from ..config import get_settings
from ..errors import QuantLabError

API = "https://finnhub.io/api/v1/"
ITEMS = {  # 8-K item numbers SEC uses, and what they announce
    "1.01": "entered a material agreement", "1.02": "ended a material agreement", "1.05": "material cybersecurity incident",
    "2.01": "completed an acquisition or disposal", "2.02": "results of operations (earnings release)",
    "2.03": "new material debt", "2.05": "exit or restructuring costs", "2.06": "material impairment",
    "3.01": "delisting notice", "3.02": "unregistered sale of shares", "4.01": "auditor change",
    "4.02": "earlier financial statements no longer reliable", "5.02": "director or officer change",
    "5.03": "amended articles or bylaws", "5.07": "shareholder vote results", "7.01": "Regulation FD disclosure",
    "8.01": "other material events", "9.01": "financial statements and exhibits",
}
REPORTS = {"10-Q": "quarterly report", "10-K": "annual report", "8-K": "current report", "6-K": "foreign issuer report",
           "20-F": "foreign annual report", "DEF 14A": "proxy statement"}


def _unavailable(message):
    from tradingagents.dataflows.errors import VendorUnavailableError
    return VendorUnavailableError(message)


def _finnhub(path, **params):
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        raise _unavailable("no news source on this server (FINNHUB_API_KEY is not set)")
    req = urllib.request.Request(API + path + "?" + urllib.parse.urlencode(params),
                                 headers={"X-Finnhub-Token": key, "User-Agent": "quantlab"})
    try:
        with urllib.request.urlopen(req, timeout=get_settings().http_timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        reason = {401: "key rejected", 403: "not in the free plan", 429: "rate limit reached"}.get(e.code, "HTTP %d" % e.code)
        raise _unavailable("Finnhub %s: %s" % (path, reason)) from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise _unavailable("Finnhub unreachable: %s" % e) from e


def _day(ts):
    return dt.datetime.fromtimestamp(int(ts), dt.UTC).date()


def _articles(rows, start, end, limit):
    """Articles published from start to end inclusive, newest first, one per headline."""
    seen, out = set(), []
    for a in sorted(rows or [], key=lambda a: a.get("datetime") or 0, reverse=True):
        title = (a.get("headline") or "").strip()
        if not title or title in seen or not a.get("datetime") or not start <= _day(a["datetime"]) <= end:
            continue
        seen.add(title)
        text = "### %s (source: %s, %s)\n" % (title, a.get("source") or "unknown", _day(a["datetime"]))
        if (a.get("summary") or "").strip():
            text += a["summary"].strip() + "\n"
        if a.get("url"):
            text += "Link: %s\n" % a["url"]
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _filings(symbol, start, end):
    """The company's SEC filings in the window as news items, or None when SEC has no record of the ticker."""
    from ..ml import sec
    try:
        cik = sec.cik_map().get(symbol)
        recent = sec.submissions(cik).get("recent") if cik else None
    except QuantLabError:
        return None
    if recent is None:
        return None
    out = []
    for f in recent:
        if f["form"] not in REPORTS or not start.isoformat() <= f["filingDate"] <= end.isoformat():
            continue
        what = ", ".join(ITEMS.get(i.strip(), "item " + i.strip()) for i in (f.get("items") or "").split(",") if i.strip())
        url = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s" % (cik, f["accessionNumber"].replace("-", ""), f["primaryDocument"])
        out.append("### %s %s filed %s%s\nLink: %s\n" % (symbol, f["form"], f["filingDate"], ": " + what if what else
                                                       " (%s)" % REPORTS[f["form"]], url))
    return out


def get_news(ticker, start_date, end_date):
    from tradingagents.dataflows.config import get_config

    from .cn_vendor import _symbol
    sym = _symbol(ticker)
    start, end = dt.date.fromisoformat(start_date), dt.date.fromisoformat(end_date)
    filings = _filings(sym, start, end)
    try:
        articles = _articles(_finnhub("company-news", symbol=sym, **{"from": start_date, "to": end_date}),
                             start, end, get_config()["news_article_limit"])
        finnhub_note = None
    except Exception as e:  # filings alone are still worth reporting
        if not filings:
            raise
        articles, finnhub_note = [], str(e)
    parts = ["## %s News, from %s to %s:\n" % (sym, start_date, end_date)]
    if articles:
        parts.append("Articles (Finnhub):\n\n" + "\n".join(articles))
    elif finnhub_note:
        parts.append("News articles unavailable (%s); do not infer that there was no news.\n" % finnhub_note)
    else:
        parts.append("Finnhub has no articles about %s in this window (funds and smaller companies are often not covered).\n" % sym)
    if filings:
        parts.append("Company filings with the SEC (material events are disclosed here):\n\n" + "\n".join(filings))
    elif filings == []:
        parts.append("No SEC filings by %s in this window.\n" % sym)
    return "\n".join(parts)


def get_global_news(as_of_date, look_back_days=None, limit=None):
    from tradingagents.dataflows.config import get_config
    cfg = get_config()
    end = dt.date.fromisoformat(as_of_date)
    start = end - dt.timedelta(days=look_back_days if look_back_days is not None else cfg["global_news_lookback_days"])
    articles = _articles(_finnhub("news", category="general"), start, end,
                         limit if limit is not None else cfg["global_news_article_limit"])
    if not articles:
        return "No market news from Finnhub between %s and %s; do not infer that nothing happened." % (start, end)
    return "## Market news (Finnhub), from %s to %s:\n\n%s" % (start, end, "\n".join(articles))


def get_insider_transactions(ticker, curr_date=None):
    from .cn_vendor import _symbol
    sym = _symbol(ticker)
    end = dt.date.fromisoformat(curr_date) if curr_date else dt.date.today()
    start = end - dt.timedelta(days=180)
    rows = (_finnhub("stock/insider-transactions", symbol=sym, **{"from": start.isoformat(), "to": end.isoformat()})
            or {}).get("data") or []
    rows = [r for r in rows if (r.get("filingDate") or "9999") <= end.isoformat()]  # known by the analysis date
    if not rows:
        return "# Insider transactions for %s\nNone filed between %s and %s (Finnhub, from SEC Form 4)." % (sym, start, end)
    lines = ["filing_date,transaction_date,name,code,shares_changed,shares_after,price"]
    for r in sorted(rows, key=lambda r: r.get("filingDate") or "", reverse=True)[:50]:
        lines.append("%s,%s,%s,%s,%s,%s,%s" % (r.get("filingDate"), r.get("transactionDate"), (r.get("name") or "").replace(",", " "),
                                              r.get("transactionCode"), r.get("change"), r.get("share"), r.get("transactionPrice")))
    return ("# Insider transactions for %s, filed %s to %s (Finnhub, from SEC Form 4)\n"
            "# Codes: P open-market purchase, S open-market sale, A grant or award, M option exercise, F tax withholding, G gift\n"
            % (sym, start, end)) + "\n".join(lines)
