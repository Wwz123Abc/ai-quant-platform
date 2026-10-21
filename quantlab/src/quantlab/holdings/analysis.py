"""Analysis of a stock you are considering: price trend and risk, point-in-time fundamentals, the latest AI
rating, how it moves with what you already hold, and what your rules allow you to buy.

The verdict comes only from your rules (cap per stock, cash reserve): whether a purchase fits and at most how
much. Trend, fundamentals, AI rating and the model's ranking are information for you to weigh; none of them
is turned into a recommendation to buy or sell."""
import json

import pandas as pd

from ..config import get_settings
from ..core import data
from ..errors import QuantLabError
from . import ledger, policy, review, signals


def correlations(sym, others, load=None, days=252):
    """One-year correlation of daily returns between sym and each holding."""
    load = load or data.load
    base = load(sym)["close"].pct_change(fill_method=None).iloc[-days:]
    out = {}
    for o in others:
        if o == sym:
            continue
        try:
            r = load(o)["close"].pct_change(fill_method=None).iloc[-days:]
        except QuantLabError:
            continue
        both = pd.concat([base, r], axis=1, join="inner").dropna()
        if len(both) > 60:
            out[o] = float(both.corr().iloc[0, 1])
    return out


def model_rank(sym):
    """The machine-learning model's latest rank for sym among the Nasdaq-100 (if a signal was computed)."""
    folders = sorted((get_settings().data_dir / "models").glob("*/scores.json"))
    if not folders:
        return None
    scores = pd.Series(json.loads(folders[-1].read_text(encoding="utf-8")))
    if sym not in scores.index:
        return None
    order = scores.rank(ascending=False)
    return {"rank": int(order[sym]), "of": len(scores), "signal_date": folders[-1].parent.name}


def analyze(sym, amount=None, load=None, with_fundamentals=True):
    sym = data.normalize(sym)
    load = load or data.load
    tech = signals.technical(sym, load)
    fund = signals.fundamentals(sym, tech.get("close")) if with_fundamentals else None
    ai = signals.ai_rating(sym)
    pol = policy.load()
    try:
        snap = ledger.load(refresh=False)  # the last sync; fetched once if there is none and a key is set
    except QuantLabError:
        snap = None  # no key and nothing synced yet: analysis without your holdings
    portfolio, checks = None, []
    if snap:
        held_syms = [p["symbol"] for p in ledger.positions(snap)]
        prices = review.prices_for(set(held_syms) | {sym}, load)
        val = review.valuation(snap, prices)
        inv = val["investable"]
        current = next((p["value"] or 0 for p in val["positions"] if p["symbol"] == sym), 0.0)
        room_cap = max(0.0, pol["max_position"] * inv - current)
        room_cash = max(0.0, val["cash"] - pol["cash_reserve"] * inv)
        max_buy = min(room_cap, room_cash)
        corr = correlations(sym, held_syms, load)
        weights = {p["symbol"]: p["weight"] for p in val["positions"] if p["weight"]}
        weight_sum = sum(weights.get(s, 0) for s in corr)
        avg_corr = sum(corr[s] * weights.get(s, 0) for s in corr) / weight_sum if weight_sum > 0 else None  # weighted by holding size
        amt = float(amount) if amount else max_buy
        after = (current + amt) / inv if inv > 0 else None
        checks.append({"rule": "单只上限", "ok": after is not None and after <= pol["max_position"] + 1e-9,
                       "text": "买入 $%.0f 后占可投资资金 %.1f%%（上限 %.0f%%）" % (amt, (after or 0) * 100, pol["max_position"] * 100)})
        checks.append({"rule": "现金储备", "ok": amt <= room_cash + 1e-6,
                       "text": "可用现金（扣除 %.0f%% 储备后）$%.0f" % (pol["cash_reserve"] * 100, room_cash)})
        portfolio = {"investable": inv, "cash": val["cash"], "held_value": current, "max_buy": max_buy, "amount": amt,
                     "weight_after": after, "correlations": corr, "avg_correlation": avg_corr,
                     "shares_at_close": amt / tech["close"] if tech.get("close") else None}
    allowed = bool(checks) and all(c["ok"] for c in checks) and (portfolio or {}).get("amount", 0) > 0
    notes = []
    if tech.get("uptrend") is False:
        notes.append({"kind": "趋势", "text": "50 日线在 200 日线下方（空头排列）"})
    elif tech.get("uptrend"):
        notes.append({"kind": "趋势", "text": "50 日线在 200 日线上方（多头排列）"})
    if tech.get("vol") and tech["vol"] > 0.5:
        notes.append({"kind": "风险", "text": "波动很大：年化波动率 %.0f%%" % (tech["vol"] * 100)})
    if fund:
        if fund["net_income_ttm"] is not None and fund["net_income_ttm"] < 0:
            notes.append({"kind": "基本面", "text": "最近 12 个月净利润为负"})
        if fund["pe"]:
            notes.append({"kind": "估值", "text": "市盈率约 %.0f 倍（按 SEC 最近 12 个月净利润和最新股本）" % fund["pe"]})
    if portfolio and portfolio["avg_correlation"] is not None and portfolio["avg_correlation"] > 0.8:
        notes.append({"kind": "分散", "text": "和你现有持仓高度相关（平均相关系数 %.2f），分散效果有限" % portfolio["avg_correlation"]})
    if ai:
        notes.append({"kind": "AI", "text": "最近一次 AI 评级 %s（%s 收盘后）" % (ai["rating"], ai["trade_date"])})
    verdict = ("按你的规则：可以买入，最多约 $%.0f" % portfolio["max_buy"]) if allowed and portfolio else (
        "按你的规则：现在不能买入（见下面未通过的规则）" if portfolio else "还没有同步持仓，无法按你的规则判断")
    return {"symbol": sym, "technical": tech, "fundamentals": fund, "ai": ai, "model": model_rank(sym), "portfolio": portfolio,
            "checks": checks, "allowed": allowed, "verdict": verdict, "notes": notes,
            "disclaimer": "结论只来自你设定的规则；其余是供你参考的信息，不是买卖建议。"}

