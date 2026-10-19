"""Daily bars from sources reachable in mainland China: Tencent prices with Sina's adjustment factors.

Tencent serves unadjusted US daily bars from 2007 (2,000 bars per request, so history comes in date windows).
Sina lists, for each ex-date, a split factor f and a cumulative dividend c in today's share terms, meant as
"adjusted = raw * f + c". Subtracting dividends that way turns old prices negative for steady payers (McDonald's
2007 price is below the dividends paid since), so the factors are turned into Yahoo-style proportional
adjustment instead: prices are split-adjusted with f, and every dividend multiplies the prices before its
ex-date by (1 - dividend / previous close). Returns then match a total-return series."""
import datetime as dt
import json
import re
import time
import urllib.error
import urllib.request

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


def tencent_code(sym):
    """Tencent's code with the exchange suffix, e.g. MCD -> MCD.N, NVDA -> NVDA.OQ."""
    if sym not in _codes:
        text = _get(QUOTE % sym, "https://gu.qq.com/", "gbk")
        m = re.search(r'="([^"]*)"', text)
        fields = m.group(1).split("~") if m else []
        if len(fields) < 3 or not fields[2]:
            raise NotFound("腾讯行情找不到 %s" % sym)
        _codes[sym] = fields[2]
    return _codes[sym]


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


def missed_splits(bars, factors, tolerance=0.03):
    """Splits the factor list does not record: a day whose close and opening gap both moved by the same
    common split ratio (a 2-for-1 halves both), on a day with no recorded event. Returns [(date, ratio)], where
    ratio > 1 is a split and < 1 a reverse split."""
    recorded = {e[0] for e in factors}
    close, open_ = bars["close"].to_numpy(), bars["open"].to_numpy()
    found = []
    for i in range(1, len(bars)):
        day = bars.index[i]
        if day in recorded or close[i - 1] <= 0:
            continue
        move, gap = close[i - 1] / close[i], close[i - 1] / open_[i]
        for r in SPLIT_RATIOS:
            for ratio in (r, 1 / r):
                if abs(move / ratio - 1) < tolerance and abs(gap / ratio - 1) < tolerance:
                    found.append((day, ratio))
    return found


def fetch(sym):
    import logging

    factors = sina_factors(sym)
    bars = adjust(tencent_bars(sym), factors)
    for day, ratio in missed_splits(bars, factors):
        logging.getLogger(__name__).warning("%s: split of %g on %s missing from the factor list; adjusting", sym, ratio, day.date())
        before = bars.index < day
        for col in ("open", "high", "low", "close"):
            bars.loc[before, col] /= ratio
        bars.loc[before, "volume"] *= ratio
    return bars
