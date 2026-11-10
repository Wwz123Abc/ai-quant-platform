"""What the AI report adds for one person holding a few thousand dollars: a short summary built from our own data,
and a mark on every number in the report that the data the agents actually fetched does not contain.

Nothing here calls a model, so it costs nothing and cannot invent a number."""
import re

RATING_CN = {"Buy": "买入", "Overweight": "增持", "Hold": "持有", "Underweight": "减持", "Sell": "卖出", "REVIEW": "无法判断"}

_DATE = re.compile(r"\b\d{4}[-/年]\d{1,2}([-/月]\d{1,2}日?)?")
# A figure worth checking: a percentage, a dollar amount, or a decimal. Plain integers (counts, years) are skipped.
_CLAIM = re.compile(r"(?P<dollar>\$)?(?P<num>\d[\d,]*(?:\.\d+)?)(?P<pct>\s?%)?(?P<unit>\s?(?:亿|万|[KMBT]\b|million|billion|trillion))?")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_FOOT = "\n\n> ⚠ 下列数字在这次分析取得的行情、财务和新闻数据里找不到：%s。可能是模型自己推算的，或没有来源，用之前请自己核对。"


def _value(s):
    return float(s.replace(",", ""))


def source_numbers(sources):
    """Every number in the text the data tools returned, as floats."""
    out = set()
    for text in sources:
        for m in _NUM.findall(_DATE.sub(" ", str(text))):
            out.add(_value(m))
    return out


def _found(value, decimals, pct, known):
    tol = 0.5 * 10 ** -decimals + 1e-9
    for k in known:
        if abs(k - value) <= tol:
            return True
        if pct and abs(k * 100 - value) <= tol:  # the source gives the ratio as a fraction, e.g. 0.0123 for 1.23%
            return True
    return False


def unsourced(text, known):
    """The figures in text (as written) that no number in known matches. Figures with a scale unit (亿, M, B...)
    are not checked: the data may state them in a different scale."""
    found = []
    for m in _CLAIM.finditer(_DATE.sub(" ", text)):
        raw = m.group("num")
        has_decimal = "." in raw
        if not (m.group("dollar") or m.group("pct") or has_decimal) or m.group("unit"):
            continue
        decimals = len(raw.split(".")[1]) if has_decimal else 0
        if not _found(_value(raw), decimals, bool(m.group("pct")), known):
            shown = ("$" if m.group("dollar") else "") + raw + ("%" if m.group("pct") else "")
            if shown not in found:
                found.append(shown)
    return found


def mark_unsourced(reports, sources):
    """Appends a warning under each report listing its figures that the fetched data does not contain."""
    known = source_numbers(sources)
    if not known:  # nothing was recorded, so nothing can be said either way
        return reports
    for sec in reports.values():
        bad = unsourced(sec["text"], known)
        if bad:
            sec["text"] = sec["text"].rstrip() + _FOOT % "、".join(bad[:12])
    return reports


def _conclusion(text):
    """The first real paragraph of the portfolio manager's decision, without headings and the rating line."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    paras = [p for p in paras if not p.lstrip().startswith("#") and len(p) > 30 and "评级" not in p[:12]
             and "Rating" not in p[:12]]
    if not paras:
        return ""
    p = re.sub(r"\s+", " ", paras[0])
    p = re.sub(r"^[\s*_#>-]*[^\s*:：]{1,40}(?: [A-Za-z]+)*\s*[*_]*\s*[:：]\s*", "", p)  # a leading "**Executive Summary**:" label
    p = p.strip(" *_")
    return p if len(p) <= 260 else p[:260].rstrip() + "…"


def levels(close):
    """Close, 50 and 200 day averages and the 52 week range, from a series of closes ending at the analysis date."""
    if close is None or len(close) == 0:
        return None
    last = float(close.iloc[-1])
    out = {"date": str(close.index[-1])[:10], "close": last}
    if len(close) >= 50:
        out["ma50"] = float(close.iloc[-50:].mean())
    if len(close) >= 200:
        out["ma200"] = float(close.iloc[-200:].mean())
    year = close.iloc[-252:]
    out["high"], out["low"] = float(year.max()), float(year.min())
    return out


def holding_line(mine):
    """What the report did and did not know about the owner's position, in plain words."""
    if not mine:
        return "**你的持仓**：这份报告**不知道**你有没有这只股票、买了多少（没读到账本），评级和仓位说法只能当通用参考。"
    cap = "宽基 ETF，不设单只上限" if mine["uncapped"] else "单只上限 %.0f%%" % (mine["cap"] * 100)
    room = "按你的规则，现在最多还能买入约 $%.0f（%s；SGOV 扣掉 %.0f%% 储备后可用 $%.0f）。" % (
        mine["max_buy"], cap, mine["reserve"] * 100, mine["room_cash"])
    warn = "（注意：%s 没取到价格，没算进总额，下面的占比会偏高）" % "、".join(mine["unpriced"]) if mine.get("unpriced") else ""
    room += warn
    if mine["held"]:
        gain = "，浮动盈亏 %+.1f%%" % (mine["gain"] * 100) if mine["gain"] is not None else ""
        return "**你的持仓**：持有 %s 股，平均成本 $%.2f，现值 $%.2f（占可投资资金 %.1f%%%s）。%s" % (
            format(mine["shares"], ",.4g"), mine["avg_cost"] or 0, mine["value"], (mine["weight"] or 0) * 100, gain, room)
    return ("**你的持仓**：你现在**没有持有**这只股票。报告里的“减持”“卖出”对没持有的股票只能理解成“不买”。%s" % room)


def summary(rating, final_text, lv, near=None, mine=None):
    """The personal summary as markdown: rating, the conclusion in the report's own words, key prices from our data."""
    lines = ["**评级：%s**" % RATING_CN.get(rating, rating), holding_line(mine)]
    why = _conclusion(final_text)
    if why:
        lines.append("理由（摘自最终决定）：" + why)
    if lv:
        parts = ["%s 收盘 $%.2f" % (lv["date"], lv["close"])]
        for key, name in (("ma50", "50 日线"), ("ma200", "200 日线")):
            if key in lv:
                parts.append("%s $%.2f（现价%s %.1f%%）" % (name, lv[key], "高于" if lv["close"] >= lv[key] else "低于",
                                                           abs(lv["close"] / lv[key] - 1) * 100))
        parts.append("近一年收盘价区间 $%.2f – $%.2f" % (lv["low"], lv["high"]))
        res, sup = near or (None, None)
        if res:
            parts.append("最近压力 $%.2f（%+.1f%%）" % (res["price"], res["distance"] * 100))
        if sup:
            parts.append("最近支撑 $%.2f（%+.1f%%）" % (sup["price"], sup["distance"] * 100))
        lines.append("关键价位（本平台行情数据）：" + "；".join(parts))
    lines.append("*这只是研究参考。买不买、买多少，以你设定的持仓规则为准（见“个股分析”）；下面各节是模型的完整报告，"
                 "里面按机构操作写的期权、领口等建议不适用于个人持仓。*")
    return "\n\n".join(lines)
