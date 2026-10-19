"""Daily bars from Yahoo Finance, adjusted for splits and dividends, cached in SQLite."""
import datetime as dt
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import pandas as pd

from .. import storage
from ..config import get_settings
from ..errors import DataSourceError, InvalidInput, NotFound

log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
HK = ZoneInfo("Asia/Hong_Kong")
SYMBOL_RE = re.compile(r"^(\d{1,5}|[A-Z][A-Z0-9.\-]{0,9})$")  # Hong Kong codes or US tickers

MIGRATIONS = [
    """CREATE TABLE IF NOT EXISTS bars (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
                                         PRIMARY KEY (symbol, date));
       CREATE TABLE IF NOT EXISTS fetched (symbol TEXT PRIMARY KEY, at REAL)""",
]


def db_path():
    return get_settings().data_dir / "prices.db"


def normalize(sym):
    """Upper-cased symbol, checked against what Yahoo accepts, so it is safe to put in a URL or a file name."""
    key = str(sym).strip().upper()
    if not SYMBOL_RE.match(key):
        raise InvalidInput("股票代码格式不对：%s（美股写代码如 QQQM，港股写数字如 700）" % sym)
    return key


def market_tz(sym):
    return HK if sym.strip().isdigit() else NY


def yahoo_symbol(sym):
    """Hong Kong codes like 700 become 0700.HK; US tickers pass through (BRK.B -> BRK-B)."""
    sym = sym.strip().upper()
    return sym.zfill(4) + ".HK" if sym.isdigit() else sym.replace(".", "-")


def fetch(sym):
    """The whole daily history of one symbol, from the configured source."""
    if get_settings().price_source == "cn":
        if sym.isdigit():
            raise NotFound("国内行情源暂不支持港股：%s" % sym)
        from . import cn_data
        return cn_data.fetch(sym)
    return fetch_yahoo(sym)


def fetch_yahoo(sym):
    """The whole daily history of one symbol as a DataFrame indexed by date.

    Yahoo's close is split-adjusted and its adjclose also folds in dividends. Open, high and low are scaled by
    adjclose / close so every column is on the same total-return basis."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(yahoo_symbol(sym)) +
           "?period1=0&period2=%d&interval=1d&events=div,split" % (time.time() + 86400))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=get_settings().http_timeout) as r:
            chart = json.load(r)["chart"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFound("Yahoo 找不到 %s 的数据，请检查代码是否正确" % sym) from e
        raise DataSourceError("Yahoo 返回错误 %d，请稍后再试" % e.code) from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise DataSourceError("连不上 Yahoo 行情：%s" % getattr(e, "reason", e)) from e
    if chart.get("error") or not chart.get("result"):
        raise NotFound("Yahoo 没有 %s 的数据：%s" % (sym, (chart.get("error") or {}).get("description", "空结果")))
    res = chart["result"][0]
    q, adj = res["indicators"]["quote"][0], res["indicators"]["adjclose"][0]["adjclose"]
    tz, rows = market_tz(sym), []
    for i, ts in enumerate(res.get("timestamp") or []):
        o, h, l, c, v, a = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i], adj[i]
        if None in (o, h, l, c, a) or c <= 0:
            continue
        f = a / c
        rows.append((dt.datetime.fromtimestamp(ts, tz).date().isoformat(), o * f, h * f, l * f, a, v or 0))
    if not rows:
        raise NotFound("Yahoo 没有 %s 的日线数据" % sym)
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"]).drop_duplicates("date", keep="last")
    return df.set_index(pd.to_datetime(df.pop("date")))


def completed(df, tz=NY, now=None):
    """Drops today's bar while the session is still running: its close is only the latest price.

    Both New York and Hong Kong close at 16:00 local time."""
    now = (now or dt.datetime.now(tz)).astimezone(tz)
    if len(df) and df.index[-1].date() == now.date() and now.hour < 16:
        return df.iloc[:-1]
    return df


def load(sym, refresh=False):
    """Cached bars for one symbol, refetched when older than the cache age or when refresh is set.

    The whole history is replaced on each fetch: a new dividend changes every earlier adjusted price."""
    key = normalize(sym)
    con = storage.connect(db_path(), MIGRATIONS)
    try:
        row = con.execute("SELECT at FROM fetched WHERE symbol = ?", (key,)).fetchone()
        if refresh or not row or time.time() - row[0] > get_settings().cache_max_age:
            log.info("downloading %s", key)
            df = fetch(key)
            with con:
                con.execute("DELETE FROM bars WHERE symbol = ?", (key,))
                con.executemany("INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?)",
                                [(key, d.date().isoformat(), *r) for d, r in zip(df.index, df.itertuples(index=False), strict=True)])
                con.execute("INSERT OR REPLACE INTO fetched VALUES (?, ?)", (key, time.time()))
        df = pd.read_sql("SELECT date, open, high, low, close, volume FROM bars WHERE symbol = ? ORDER BY date", con,
                         params=(key,), parse_dates=["date"], index_col="date")
    finally:
        con.close()
    return completed(df, market_tz(key))
