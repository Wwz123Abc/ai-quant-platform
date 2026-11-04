"""Tencent's recent closes checked against a second source by the stock ledger's rule (ledger/app.py cross_check;
both are checked against ledger/tests/fixtures/contract.json).

The last five closes must agree within 0.2%. A date one source has and the other lacks is missing, except the
newest days the second source has not published yet while it trails by at most four days. A latest close older
than six calendar days is stale. Sina is the second source, Eastmoney stands in when Sina fails."""
import datetime as dt
import json
import re
import urllib.parse
from zoneinfo import ZoneInfo

from ..errors import QuantLabError
from . import cn_data

CHECK_DAYS, CHECK_TOLERANCE, STALE_DAYS = 5, 0.002, 6
LAG_DAYS = 4  # how far the second source may trail on its newest days before they count as missing
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EM_MARKET = {"OQ": "105", "N": "106", "AM": "107", "PS": "107"}  # Eastmoney's market for a Tencent exchange suffix


def today_us():
    return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def tencent_recent(sym, days=CHECK_DAYS + 5):
    """Tencent's last unadjusted daily bars as [date, open, high, low, close, volume]."""
    text = cn_data._get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=us%s,day,,,%d," % (cn_data.tencent_code(sym), days),
                        "https://gu.qq.com/")
    try:
        rows = next(iter(json.loads(text)["data"].values())).get("day") or []
    except (ValueError, KeyError, StopIteration, AttributeError) as e:
        raise QuantLabError("腾讯日线的格式不对：%s" % sym) from e
    out = []
    for r in rows:
        o, c, hi, lo, v = (float(x) for x in r[1:6])  # Tencent order: date, open, close, high, low, volume
        if min(o, c, hi, lo) > 0:
            out.append([r[0], round(o, 4), round(hi, 4), round(lo, 4), round(c, 4), int(v)])
    return out


def sina_bars(sym, days):
    """Unadjusted daily bars of a US stock from Sina, in the same form."""
    url = "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%%20_%s=/US_MinKService.getDailyK?symbol=%s&type=daily" % (
        re.sub(r"[^A-Za-z0-9]", "_", sym), urllib.parse.quote(sym))
    text = cn_data._get(url, "https://finance.sina.com.cn/")
    start, end = text.find("("), text.rfind(")")
    out = []
    for r in (json.loads(text[start + 1:end]) if 0 <= start < end else None) or []:
        try:
            o, hi, lo, c, v = (float(r[k]) for k in ("o", "h", "l", "c", "v"))
        except (KeyError, TypeError, ValueError):
            continue
        if DATE_RE.match(str(r.get("d", ""))) and min(o, hi, lo, c) > 0:
            out.append([r["d"], round(o, 4), round(hi, 4), round(lo, 4), round(c, 4), int(v)])
    return out[-days:]


def em_bars(sym, days):
    """Unadjusted daily bars of a US stock from Eastmoney, in the same form."""
    tail = cn_data.tencent_code(sym).rsplit(".", 1)[-1]
    for m in [EM_MARKET[tail]] if tail in EM_MARKET else ["105", "106", "107"]:
        url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s.%s&fields1=f1,f2,f3"
               "&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=0&lmt=%d&end=20500101") % (m, urllib.parse.quote(sym), days)
        rows = (json.loads(cn_data._get(url, "https://quote.eastmoney.com/")).get("data") or {}).get("klines") or []
        out = []
        for r in rows:
            day, o, c, hi, lo, v = r.split(",")[:6]  # Eastmoney order: date, open, close, high, low, volume
            if min(float(o), float(c), float(hi), float(lo)) > 0:
                out.append([day, round(float(o), 4), round(float(hi), 4), round(float(lo), 4), round(float(c), 4), int(float(v))])
        if out:
            return out
    return []


def second_source(sym, days):
    """(name, bars, problems) from Sina, else Eastmoney; bars is [] when neither answered."""
    problems = []
    for name, get in (("新浪财经", lambda: sina_bars(sym, days)), ("东方财富", lambda: em_bars(sym, days))):
        try:
            bars = get()
        except (QuantLabError, ValueError, KeyError) as e:
            problems.append("%s：%s" % (name, e))
            continue
        if bars:
            return name, bars, problems
        problems.append("%s：没有数据" % name)
    return "", [], problems


def cross_check(bars, other, today, other_name="第二来源"):
    """Tencent's last CHECK_DAYS closes against a second source's: {days, mismatches: [[date, tencent, other]],
    missing: [[date, "腾讯" or other_name]], latest, stale}, exactly as the ledger computes it."""
    a = {b[0]: b[4] for b in bars[-CHECK_DAYS:]}
    b = {r[0]: r[4] for r in other[-CHECK_DAYS:]}
    lo = max(min(a) if a else "", min(b) if b else "")
    behind = bool(a and b) and (dt.date.fromisoformat(max(a)) - dt.date.fromisoformat(max(b))).days <= LAG_DAYS
    days, mismatches, missing = [], [], []
    for d in sorted(d for d in set(a) | set(b) if d >= lo):
        if d not in a:
            missing.append([d, "腾讯"])
        elif d not in b:
            if behind and d > max(b):
                continue
            missing.append([d, other_name])
        elif abs(a[d] / b[d] - 1) > CHECK_TOLERANCE:
            mismatches.append([d, a[d], b[d]])
        days.append(d)
    latest = max(a) if a else ""
    stale = not latest or (dt.date.fromisoformat(today) - dt.date.fromisoformat(latest)).days > STALE_DAYS
    return {"days": days, "mismatches": mismatches, "missing": missing, "latest": latest, "stale": stale}


def check(sym, today=None):
    """One stock's recent closes checked: {symbol, against, ok, text}. ok is None when there was nothing to check
    against; text says what was found, in words."""
    today = today or today_us()
    try:
        bars = tencent_recent(sym)
    except QuantLabError as e:
        return {"symbol": sym, "against": "", "ok": False, "text": "取不到腾讯日线：%s" % e}
    name, other, problems = second_source(sym, CHECK_DAYS + 5)
    if not other:
        return {"symbol": sym, "against": "", "ok": None, "text": "没能核对：%s" % "；".join(problems)}
    r = cross_check(bars, other, today, name)
    issues = ["%s 腾讯 %.2f、%s %.2f（差 %.1f%%）" % (d[5:], x, name, y, abs(x / y - 1) * 100) for d, x, y in r["mismatches"]]
    issues += ["%s %s没有数据" % (d[5:], who) for d, who in r["missing"]]
    if r["stale"]:
        issues.append("最新收盘价是 %s 的，已经超过 %d 天" % (r["latest"] or "—", STALE_DAYS))
    if issues:
        return {"symbol": sym, "against": name, "ok": False, "text": "最近 %d 个交易日的收盘价和%s对不上：%s" % (CHECK_DAYS, name, "；".join(issues))}
    return {"symbol": sym, "against": name, "ok": True,
            "text": "最近 %d 个交易日（到 %s）的收盘价和%s一致，差异不超过 %.1f%%" % (len(r["days"]), r["latest"], name, CHECK_TOLERANCE * 100)}
