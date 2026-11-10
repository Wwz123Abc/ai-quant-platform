"""Daily bars from sources reachable in mainland China: Tencent prices with Sina's adjustment factors.

Tencent serves unadjusted US daily bars from 2007 (2,000 bars per request, so history comes in date windows).
Sina lists, for each ex-date, a split factor f and a cumulative dividend c in today's share terms, meant as
"adjusted = raw * f + c". Subtracting dividends that way turns old prices negative for steady payers (McDonald's
2007 price is below the dividends paid since), so the factors are turned into Yahoo-style proportional
adjustment instead: prices are split-adjusted with f, and every dividend multiplies the prices before its
ex-date by (1 - dividend / previous close). Returns then match a total-return series."""
import datetime as dt
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..config import get_settings
from ..errors import DataSourceError, NotFound

QUOTE = "https://qt.gtimg.cn/q=us%s"
KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=us%s,day,%s,%s,2000,qfq"
FACTORS = "https://finance.sina.com.cn/us_stock/company/reinstatement/%s_qfq.js"
FIRST_YEAR = 2007
_codes = {}


def _get(url, referer, encoding="utf-8"):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
    try:
        with urllib.request.urlopen(req, timeout=get_settings().http_timeout) as r:
            return r.read().decode(encoding, errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFound("找不到 %s" % url) from e
        raise DataSourceError("行情接口返回错误 %d" % e.code) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise DataSourceError("连不上国内行情接口：%s" % getattr(e, "reason", e)) from e


def tencent_quote(sym):
    """The fields of Tencent's quote: [1] Chinese name, [2] code with exchange suffix, [46] English name."""
    text = _get(QUOTE % sym, "https://gu.qq.com/", "gbk")
    m = re.search(r'="([^"]*)"', text)
    fields = m.group(1).split("~") if m else []
    if len(fields) < 3 or not fields[2]:
        raise NotFound("腾讯行情找不到 %s" % sym)
    _codes[sym] = fields[2]
    return fields


def tencent_code(sym):
    """Tencent's code with the exchange suffix, e.g. MCD -> MCD.N, NVDA -> NVDA.OQ."""
    if sym not in _codes:
        tencent_quote(sym)
    return _codes[sym]


def is_etf(sym):
    """Whether Tencent lists sym as an exchange-traded fund (field 56 is "GP-ETF"; a company's share is "GP")."""
    fields = tencent_quote(sym)
    return len(fields) > 56 and "ETF" in fields[56].upper()


def tencent_bars(sym, pause=0.1):
    """Unadjusted daily bars (open, high, low, close, volume) from 2007 to today."""
    code = tencent_code(sym)
    rows, year = [], FIRST_YEAR
    end = dt.date.today().year
    while year <= end:
        hi = min(year + 7, end)
        text = _get(KLINE % (code, "%d-01-01" % year, "%d-12-31" % hi), "https://gu.qq.com/")
        try:
            data = json.loads(text)["data"]
            data = next(iter(data.values())) if isinstance(data, dict) else {}
        except (ValueError, StopIteration, KeyError) as e:
            raise DataSourceError("腾讯行情返回的数据格式不对：%s" % sym) from e
        for r in data.get("day") or data.get("qfqday") or []:
            o, c, h, l, v = (float(x) for x in r[1:6])  # Tencent order: date, open, close, high, low, volume
            if min(o, c, h, l) > 0:
                rows.append((r[0], o, h, l, c, v))
        year = hi + 1
        time.sleep(pause)
    if not rows:
        raise NotFound("腾讯行情没有 %s 的日线" % sym)
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"]).drop_duplicates("date", keep="last")
    return df.set_index(pd.to_datetime(df.pop("date"))).sort_index()


def _frame(rows):
    """Unadjusted bars [(date, open, high, low, close, volume)] as a date-indexed frame, oldest first."""
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"]).drop_duplicates("date", keep="last")
    return df.set_index(pd.to_datetime(df.pop("date"))).sort_index()


def sina_raw_bars(sym):
    """Unadjusted daily bars from Sina (its whole history, from about 2010) when Tencent will not answer."""
    url = "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%%20_%s=/US_MinKService.getDailyK?symbol=%s&type=daily" % (
        re.sub(r"[^A-Za-z0-9]", "_", sym), urllib.parse.quote(sym))
    text = _get(url, "https://finance.sina.com.cn/")
    start, end = text.find("("), text.rfind(")")
    try:
        data = json.loads(text[start + 1:end]) if 0 <= start < end else None
    except ValueError as e:
        raise DataSourceError("新浪行情返回的数据格式不对：%s" % sym) from e
    rows = []
    for r in data or []:
        try:
            o, h, lo, c, v = (float(r[k]) for k in ("o", "h", "l", "c", "v"))
        except (KeyError, TypeError, ValueError):
            continue
        if re.match(r"^\d{4}-\d{2}-\d{2}$", str(r.get("d", ""))) and min(o, h, lo, c) > 0:
            rows.append((r["d"], o, h, lo, c, v))
    if not rows:
        raise NotFound("新浪行情没有 %s 的日线" % sym)
    return _frame(rows)


def eastmoney_raw_bars(sym):
    """Unadjusted daily bars from Eastmoney (up to 10,000 days back) when Tencent and Sina will not answer."""
    for market in ("105", "106", "107"):  # Nasdaq, NYSE, NYSE Arca: the symbol is on one of them
        url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s.%s&fields1=f1,f2,f3"
               "&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=0&lmt=10000&end=20500101") % (market, urllib.parse.quote(sym))
        try:
            rows = (json.loads(_get(url, "https://quote.eastmoney.com/")).get("data") or {}).get("klines") or []
        except ValueError as e:
            raise DataSourceError("东方财富行情返回的数据格式不对：%s" % sym) from e
        out = []
        for r in rows:
            day, o, c, h, lo, v = r.split(",")[:6]  # Eastmoney order: date, open, close, high, low, volume
            if min(float(o), float(c), float(h), float(lo)) > 0:
                out.append((day, float(o), float(h), float(lo), float(c), float(v)))
        if out:
            return _frame(out)
    raise NotFound("东方财富没有 %s 的日线" % sym)


def last_session(now=None):
    """The date of the latest US session that should be complete: today after the close (16:30 New York), else the weekday
    before. Holidays are not known, so on one a source seems a day behind and the others are looked at too."""
    now = (now or dt.datetime.now(ZoneInfo("America/New_York"))).astimezone(ZoneInfo("America/New_York"))
    day = now.date()
    if now.weekday() >= 5 or now.hour * 60 + now.minute < 16 * 60 + 30:
        day -= dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day


def raw_bars(sym):
    """Unadjusted daily bars from the first source that has the latest session: Tencent, then Sina, then Eastmoney. A source
    that fails, or that is behind, does not stop the others; when none is current the one with the newest bar wins.
    (Tencent answers 501 now and then, and some days its bars lag; 2026-11-10 it left the weekly job without prices.)"""
    log = logging.getLogger(__name__)
    want = last_session()
    best, errors = None, []
    for name, get in (("腾讯", tencent_bars), ("新浪", sina_raw_bars), ("东方财富", eastmoney_raw_bars)):
        try:
            df = get(sym)
        except (DataSourceError, NotFound, ValueError, KeyError) as e:
            errors.append("%s：%s" % (name, e))
            continue
        if best is None or df.index[-1] > best[1].index[-1]:
            best = (name, df)
        if df.index[-1].date() >= want:
            break
    if best is None:
        raise DataSourceError("所有行情来源都取不到 %s（%s）" % (sym, "；".join(errors)))
    if best[0] != "腾讯":
        log.warning("%s: bars from %s (%s)", sym, best[0], "；".join(errors) or "腾讯的最新一根更旧")
    return best[1]


def eastmoney_session_date(sym):
    """The date of the latest US session per Eastmoney's quote, or None: a second opinion when Tencent's quote is down."""
    for market in ("105", "106", "107"):
        url = "https://push2.eastmoney.com/api/qt/stock/get?secid=%s.%s&fltt=2&fields=f43,f60,f86" % (market, urllib.parse.quote(sym))
        try:
            d = json.loads(_get(url, "https://quote.eastmoney.com/")).get("data") or {}
        except (DataSourceError, NotFound, ValueError):
            continue
        if isinstance(d.get("f43"), (int, float)) and d["f43"] > 0 and d.get("f86"):
            return dt.datetime.fromtimestamp(d["f86"], ZoneInfo("America/New_York")).date().isoformat()
    return None


def sina_factors(sym):
    """[(ex_date, f, c)] newest first; an empty list for a stock without splits or dividends."""
    try:
        text = _get(FACTORS % sym.replace(".", "_"), "https://finance.sina.com.cn/")
    except NotFound:
        return []
    m = re.search(r"=\s*(\{.*\})", text, re.S)
    if not m:
        return []
    data = json.loads(m.group(1)).get("data") or []
    out = [(pd.Timestamp(d["d"]), float(d["f"]), float(d["c"])) for d in data]
    return sorted(out, key=lambda e: e[0], reverse=True)


def adjust(raw, factors):
    """Proportionally adjusted bars from unadjusted ones and Sina's factors (see the module docstring)."""
    if not factors:
        return raw.copy()
    dates = raw.index
    asc = sorted(factors, key=lambda e: e[0])
    starts = np.array([e[0] for e in asc], dtype="datetime64[ns]")
    # each row takes the factors of the newest entry dated on or before it (the oldest entry before any)
    seg = np.clip(np.searchsorted(starts, dates.values, side="right") - 1, 0, len(asc) - 1)
    f = np.array([asc[i][1] for i in seg])
    split_close = raw["close"].to_numpy() * f
    mult = f.copy()
    for k in range(1, len(asc)):  # a dividend on the ex-date of entry k: the drop in c from entry k-1 to k
        dividend = asc[k][2] - asc[k - 1][2]
        ex = asc[k][0]
        if dividend <= 0:
            continue
        before = np.flatnonzero(dates < ex)
        if not len(before):
            continue
        prev = split_close[before[-1]]
        if prev > dividend:
            mult[before] *= 1 - dividend / prev
    out = raw.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = raw[col].to_numpy() * mult
    out["volume"] = raw["volume"].to_numpy() / f
    return out


SPLIT_RATIOS = (1.5, 2, 3, 4, 5, 8, 10, 15, 20, 25, 30)


def missed_splits(bars, factors, tolerance=0.03, window=20):
    """Splits the factor list does not record: a day whose close and opening gap both moved by the same
    common split ratio (a 2-for-1 halves both), on a day with no recorded event, and whose volume moved the way
    the share count did. News can move a price by a split-like ratio too, but then trading swells: NBIS opened
    52% higher on 2025-09-09 on nine times its usual volume, which is no 1-for-1.5 reverse split. Returns
    [(date, ratio)], where ratio > 1 is a split and < 1 a reverse split."""
    recorded = {e[0] for e in factors}
    close, open_, volume = bars["close"].to_numpy(), bars["open"].to_numpy(), bars["volume"].to_numpy()
    found = []
    for i in range(1, len(bars)):
        day = bars.index[i]
        if day in recorded or close[i - 1] <= 0:
            continue
        move, gap = close[i - 1] / close[i], close[i - 1] / open_[i]
        for r in SPLIT_RATIOS:
            for ratio in (r, 1 / r):
                if abs(move / ratio - 1) < tolerance and abs(gap / ratio - 1) < tolerance and _volume_agrees(volume, i, ratio, window):
                    found.append((day, ratio))
    return found


def _volume_agrees(volume, i, ratio, window):
    """Whether the median volume from day i on moved from the one before it toward ratio, the change in the
    number of shares, rather than staying where it was. Without volume it cannot tell, and the prices decide."""
    before, after = np.median(volume[max(0, i - window):i]), np.median(volume[i:i + window])
    if not (before > 0 and after > 0):
        return True
    change = np.log(after / before)
    return abs(change - np.log(ratio)) < abs(change)


def after_long_halt(raw, days=365):
    """The bars from the end of the last pause in trading longer than days, or all of them.

    Before such a pause the ticker may have been another security (Tencent's QQQM starts with 15 bars of 2012,
    eight years before QQQM was launched) or the company another business (NBIS, halted as Yandex from 2022 to
    2024, sold its Russian business in between); either way the prices do not continue across it."""
    gaps = np.flatnonzero(np.diff(raw.index.values) > np.timedelta64(days, "D"))
    return raw.iloc[gaps[-1] + 1:] if len(gaps) else raw


def fetch(sym):
    factors = sina_factors(sym)
    raw = raw_bars(sym)
    kept = after_long_halt(raw)
    if len(kept) < len(raw):
        logging.getLogger(__name__).warning("%s: no trading for over a year before %s; the %d bars before it are left out",
                                            sym, kept.index[0].date(), len(raw) - len(kept))
    bars = adjust(kept, factors)
    for day, ratio in missed_splits(bars, factors):
        logging.getLogger(__name__).warning("%s: split of %g on %s missing from the factor list; adjusting", sym, ratio, day.date())
        before = bars.index < day
        for col in ("open", "high", "low", "close"):
            bars.loc[before, col] /= ratio
        bars.loc[before, "volume"] *= ratio
    return bars
