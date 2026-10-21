"""Facts about one stock for analysis and reminders: price trend and risk, point-in-time fundamentals, and the
latest AI rating. Nothing here decides a trade."""
import datetime as dt
import logging
import math

import numpy as np
import pandas as pd

from ..core import data
from ..errors import QuantLabError

log = logging.getLogger(__name__)


def _f(v):
    v = float(v)
    return v if math.isfinite(v) else None


def technical(sym, load=None, bench="QQQ"):
    load = load or data.load
    bars = load(sym)
    c = bars["close"]
    if len(c) < 30:
        return {"symbol": sym, "date": c.index[-1].date() if len(c) else None, "close": _f(c.iloc[-1]) if len(c) else None, "short_history": True}
    ret = lambda n: _f(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None
    daily = c.pct_change(fill_method=None).dropna()
    year = c.iloc[-252:]
    ma50, ma200 = c.rolling(50).mean().iloc[-1], c.rolling(200).mean().iloc[-1]
    cross = None
    if len(c) >= 210:
        state = (c.rolling(50).mean() > c.rolling(200).mean()).dropna()
        flips = state.index[state.astype(int).diff().fillna(0) != 0]
        cross = flips[-1].date() if len(flips) else None
    beta = None
    try:
        b = load(bench)["close"].pct_change(fill_method=None)
        both = pd.concat([daily, b], axis=1, join="inner").dropna().iloc[-252:]
        if len(both) > 60 and both.iloc[:, 1].var() > 0:
            beta = _f(both.cov().iloc[0, 1] / both.iloc[:, 1].var())
    except QuantLabError:
        pass
    return {
        "symbol": sym, "date": c.index[-1].date(), "close": _f(c.iloc[-1]),
        "r1m": ret(21), "r3m": ret(63), "r6m": ret(126), "r1y": ret(252),
        "mom_12_1": _f(c.iloc[-22] / c.iloc[-253] - 1) if len(c) > 253 else None,
        "vol": _f(daily.iloc[-63:].std() * np.sqrt(252)),
        "from_high": _f(c.iloc[-1] / year.max() - 1), "max_dd_1y": _f((year / year.cummax() - 1).min()),
        "ma50": _f(ma50) if not pd.isna(ma50) else None, "ma200": _f(ma200) if not pd.isna(ma200) else None,
        "uptrend": bool(ma50 > ma200) if not (pd.isna(ma50) or pd.isna(ma200)) else None, "last_cross": cross,
        "beta": beta,
    }


def fundamentals(sym, close=None):
    """Latest point-in-time fundamentals from SEC (US filers only), or None."""
    from ..ml import fundamentals as FD
    from ..ml import sec
    try:
        cik = sec.cik_map().get(sym)
        if cik is None:
            return None
        cal = pd.bdate_range(end=pd.Timestamp(dt.date.today()), periods=5)
        s = {k: v.iloc[-1] for k, v in FD.company_series(sec.company_facts(cik), cal).items()}
    except QuantLabError as e:
        log.warning("fundamentals for %s: %s", sym, e)
        return None
    cap = s["shares"] * close if close and s.get("shares") == s.get("shares") else None
    ni, rev = s.get("net_income_ttm"), s.get("revenue_ttm")
    ok = lambda v: v is not None and v == v
    return {
        "revenue_ttm": _f(rev) if ok(rev) else None, "net_income_ttm": _f(ni) if ok(ni) else None,
        "op_margin": _f(s["op_income_ttm"] / rev) if ok(rev) and rev and ok(s.get("op_income_ttm")) else None,
        "rev_growth": _f(s["rev_growth"]) if ok(s.get("rev_growth")) else None,
        "roa": _f(ni / s["assets"]) if ok(ni) and ok(s.get("assets")) and s["assets"] > 0 else None,
        "pe": _f(cap / ni) if cap and ok(ni) and ni > 0 else None,
        "pb": _f(cap / s["equity"]) if cap and ok(s.get("equity")) and s["equity"] > 0 else None,
        "market_cap": _f(cap) if cap else None, "sue": _f(s["sue"]) if ok(s.get("sue")) else None,
        "cfo_to_ni": _f(s["cfo_ttm"] / ni) if ok(ni) and ni and ok(s.get("cfo_ttm")) else None,
    }


def ai_rating(sym):
    """The latest completed AI analysis of sym, or None."""
    from ..ai import ledger
    con = ledger.connect()
    try:
        runs = [r for r in ledger.runs(con, sym, 20) if r["status"] == "done"]
    finally:
        con.close()
    if not runs:
        return None
    r = runs[0]
    return {"id": r["id"], "rating": r["rating"], "trade_date": r["trade_date"]}
