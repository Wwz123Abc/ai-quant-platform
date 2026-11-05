"""Moving-average rules ("口诀") as exact definitions: for a series of closes, each rule says on every day whether it is in
its holding state or not.

The same definitions drive the historical test (scripts/ma_rules_research.py, written up in
docs/research/2026-11-ma-rules.md) and the rule panel on the stock analysis page, so what the page shows is what was
tested. A rule's state is a description of the trend, not a recommendation: nothing here creates an order."""
import pandas as pd

# (id, short name, what it says in words)
RULES = [
    ("cross_5_10", "5/10 金叉死叉", "5 日线在 10 日线上方"),
    ("cross_10_20", "10/20 金叉死叉", "10 日线在 20 日线上方"),
    ("cross_20_60", "20/60 金叉死叉", "20 日线在 60 日线上方"),
    ("cross_50_200", "50/200 金叉死叉（黄金交叉）", "50 日线在 200 日线上方"),
    ("above_20", "站上 20 日线（生命线）", "收盘价高于 20 日线"),
    ("above_60", "站上 60 日线", "收盘价高于 60 日线"),
    ("above_200", "站上 200 日线（牛熊线）", "收盘价高于 200 日线"),
    ("bull_alignment", "多头排列", "5 日线 > 10 日线 > 20 日线 > 60 日线"),
    ("granville_60", "葛兰威尔买入法则（趋势版）", "收盘价从下向上穿过 60 日线且该线 5 天来没有下降时进场，跌回线下出场"),
]
NAMES = {r[0]: r[1] for r in RULES}
WORDS = {r[0]: r[2] for r in RULES}


def _ma(close, n):
    return close.rolling(n).mean()


def _cross(close, fast, slow):
    f, s = _ma(close, fast), _ma(close, slow)
    return ((f > s) & s.notna()).astype(int)


def _above(close, n):
    m = _ma(close, n)
    return ((close > m) & m.notna()).astype(int)


def _alignment(close):
    m5, m10, m20, m60 = (_ma(close, n) for n in (5, 10, 20, 60))
    return ((m5 > m10) & (m10 > m20) & (m20 > m60) & m60.notna()).astype(int)


def _granville(close, n=60, slope_days=5):
    """In after a close from below to above the n-day average while the average has not fallen over slope_days; out once
    the close is back below it. The state carries on between: it is a position, not a one-day signal."""
    m = _ma(close, n)
    up = (close > m) & (close.shift(1) <= m.shift(1)) & (m >= m.shift(slope_days))
    below = (close <= m) | m.isna()
    state, held = [], 0
    for u, b in zip(up.to_numpy(), below.to_numpy(), strict=True):
        if b:
            held = 0
        elif u:
            held = 1
        state.append(held)
    return pd.Series(state, close.index)


def targets(close):
    """One column per rule: 1 on the days the rule is in its holding state, 0 otherwise (decided on that day's close)."""
    return pd.DataFrame({
        "cross_5_10": _cross(close, 5, 10), "cross_10_20": _cross(close, 10, 20), "cross_20_60": _cross(close, 20, 60),
        "cross_50_200": _cross(close, 50, 200), "above_20": _above(close, 20), "above_60": _above(close, 60),
        "above_200": _above(close, 200), "bull_alignment": _alignment(close), "granville_60": _granville(close),
    })


WARMUP = {"cross_5_10": 10, "cross_10_20": 20, "cross_20_60": 60, "cross_50_200": 200, "above_20": 20, "above_60": 60,
          "above_200": 200, "bull_alignment": 60, "granville_60": 65}


def state_now(close):
    """For each rule: whether it is in its holding state at the last close, and since when (the last day it changed).
    Returns [{id, name, says, on, since, days}] in RULES order. on is None while the history is shorter than the rule needs;
    since is None when the state has not changed within the history."""
    t = targets(close)
    out = []
    for rid, name, says in RULES:
        s = t[rid]
        if len(close) <= WARMUP[rid]:
            out.append({"id": rid, "name": name, "says": says, "on": None, "since": None, "days": None})
            continue
        changed = s.index[(s != s.shift(1)) & s.shift(1).notna()]
        since = changed[-1] if len(changed) else None
        out.append({"id": rid, "name": name, "says": says, "on": int(s.iloc[-1]) == 1,
                    "since": since.date().isoformat() if since is not None else None,
                    "days": int((s.index[-1] - since).days) if since is not None else None})
    return out


# What the historical test (docs/research/2026-11-ma-rules.md, 2026-11-05) found for each rule over 12 US stocks, full history:
# medians of the rule against buy-and-hold, with HSBC Trade25 costs. verdict: 通过 / 只降回撤 / 未通过 by the criteria registered before the run.
EVIDENCE = {
    "cross_5_10": {
        "verdict": "未通过",
        "cagr": 0.01,
        "bh_cagr": 0.119,
        "mdd": -0.379,
        "bh_mdd": -0.441,
        "sharpe": 0.19,
        "bh_sharpe": 0.69,
        "buys": 13.7,
        "exposure": 0.59,
    },
    "cross_10_20": {
        "verdict": "未通过",
        "cagr": 0.05,
        "bh_cagr": 0.119,
        "mdd": -0.376,
        "bh_mdd": -0.441,
        "sharpe": 0.47,
        "bh_sharpe": 0.69,
        "buys": 6.7,
        "exposure": 0.62,
    },
    "cross_20_60": {
        "verdict": "未通过",
        "cagr": 0.077,
        "bh_cagr": 0.119,
        "mdd": -0.324,
        "bh_mdd": -0.441,
        "sharpe": 0.54,
        "bh_sharpe": 0.69,
        "buys": 2.2,
        "exposure": 0.65,
    },
    "cross_50_200": {
        "verdict": "未通过",
        "cagr": 0.085,
        "bh_cagr": 0.119,
        "mdd": -0.406,
        "bh_mdd": -0.441,
        "sharpe": 0.53,
        "bh_sharpe": 0.69,
        "buys": 0.7,
        "exposure": 0.75,
    },
    "above_20": {
        "verdict": "未通过",
        "cagr": 0.009,
        "bh_cagr": 0.119,
        "mdd": -0.39,
        "bh_mdd": -0.441,
        "sharpe": 0.2,
        "bh_sharpe": 0.69,
        "buys": 14.6,
        "exposure": 0.61,
    },
    "above_60": {
        "verdict": "未通过",
        "cagr": 0.045,
        "bh_cagr": 0.119,
        "mdd": -0.327,
        "bh_mdd": -0.441,
        "sharpe": 0.46,
        "bh_sharpe": 0.69,
        "buys": 8.3,
        "exposure": 0.65,
    },
    "above_200": {
        "verdict": "未通过",
        "cagr": 0.092,
        "bh_cagr": 0.119,
        "mdd": -0.341,
        "bh_mdd": -0.441,
        "sharpe": 0.6,
        "bh_sharpe": 0.69,
        "buys": 3.9,
        "exposure": 0.74,
    },
    "bull_alignment": {
        "verdict": "未通过",
        "cagr": 0.011,
        "bh_cagr": 0.119,
        "mdd": -0.242,
        "bh_mdd": -0.441,
        "sharpe": 0.18,
        "bh_sharpe": 0.69,
        "buys": 8.7,
        "exposure": 0.34,
    },
    "granville_60": {
        "verdict": "只降回撤",
        "cagr": 0.012,
        "bh_cagr": 0.119,
        "mdd": -0.305,
        "bh_mdd": -0.441,
        "sharpe": 0.17,
        "bh_sharpe": 0.69,
        "buys": 4.5,
        "exposure": 0.28,
    },
}
