"""Daily bars from Yahoo Finance, adjusted for splits and dividends, cached in SQLite."""
import datetime as dt
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")
HK = ZoneInfo("Asia/Hong_Kong")
DB = Path(__file__).resolve().parent.parent / "data" / "prices.db"
MAX_AGE = 6 * 3600  # refetch a cached symbol after this many seconds


def market_tz(sym):
    return HK if sym.strip().isdigit() else NY


def yahoo_symbol(sym):
    """Hong Kong codes like 700 become 0700.HK; US tickers pass through (BRK.B -> BRK-B)."""
    sym = sym.strip().upper()
    return sym.zfill(4) + ".HK" if sym.isdigit() else sym.replace(".", "-")


def fetch(sym):
    """The whole daily history of one symbol as a DataFrame indexed by date.

    Yahoo's close is split-adjusted and its adjclose also folds in dividends. Open, high and low are scaled by
    adjclose / close so every column is on the same total-return basis."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(yahoo_symbol(sym)) +
           "?period1=0&period2=%d&interval=1d&events=div,split" % (time.time() + 86400))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        chart = json.load(r)["chart"]
    if chart.get("error") or not chart.get("result"):
        raise ValueError("Yahoo 没有 %s 的数据：%s" % (sym, (chart.get("error") or {}).get("description", "空结果")))
    res = chart["result"][0]
    q, adj = res["indicators"]["quote"][0], res["indicators"]["adjclose"][0]["adjclose"]
    tz, rows = market_tz(sym), []
    for i, ts in enumerate(res.get("timestamp") or []):
        o, h, l, c, v, a = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i], adj[i]
        if None in (o, h, l, c, a) or c <= 0:
            continue
        f = a / c
        rows.append((dt.datetime.fromtimestamp(ts, tz).date().isoformat(), o * f, h * f, l * f, a, v or 0))
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
    """Cached bars for one symbol, refetched when older than MAX_AGE or when refresh is set.

    The whole history is replaced on each fetch: a new dividend changes every earlier adjusted price."""
    DB.parent.mkdir(exist_ok=True)
    key = sym.strip().upper()
    with sqlite3.connect(DB) as con:
        con.execute("CREATE TABLE IF NOT EXISTS bars (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,"
                    " PRIMARY KEY (symbol, date))")
        con.execute("CREATE TABLE IF NOT EXISTS fetched (symbol TEXT PRIMARY KEY, at REAL)")
        row = con.execute("SELECT at FROM fetched WHERE symbol = ?", (key,)).fetchone()
        if refresh or not row or time.time() - row[0] > MAX_AGE:
            df = fetch(key)
            con.execute("DELETE FROM bars WHERE symbol = ?", (key,))
            con.executemany("INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [(key, d.date().isoformat(), *r) for d, r in zip(df.index, df.itertuples(index=False))])
            con.execute("INSERT OR REPLACE INTO fetched VALUES (?, ?)", (key, time.time()))
        df = pd.read_sql("SELECT date, open, high, low, close, volume FROM bars WHERE symbol = ? ORDER BY date", con,
                         params=(key,), parse_dates=["date"], index_col="date")
    return completed(df, market_tz(key))
