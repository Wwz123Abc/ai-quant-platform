"""Paper trading: a strategy records one decision per symbol after each close, and accounts are replayed from them.

Only the decisions are stored, and once written they never change, so the record is a genuine forward test: the
strategy cannot be re-tuned after the fact. Fills and account values are recomputed from the decisions on every
run with the backtest engine, which keeps them consistent when a new dividend rescales the adjusted history."""
import datetime as dt
import json
import re

import pandas as pd

from .. import storage
from ..config import get_settings
from ..errors import Conflict, InvalidInput, NotFound
from . import backtest, data, strategy

MIGRATIONS = [
    """CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, strategy TEXT, params TEXT, symbols TEXT,
                                            cash REAL, fee REAL, slippage REAL, start TEXT, created TEXT);
       CREATE TABLE IF NOT EXISTS signals (account TEXT, symbol TEXT, date TEXT, target INTEGER, detail TEXT,
                                           recorded TEXT, PRIMARY KEY (account, symbol, date))""",
]
NAME_RE = re.compile(r"^[\w\-]{1,40}$")  # letters (any script), digits, _ and -


def connect(path=None):
    return storage.connect(path or get_settings().data_dir / "paper.db", MIGRATIONS)


def decide_ma(bars, params, ctx=None):
    """The moving-average decision at the last bar given, from that bar and earlier ones only."""
    s = strategy.ma_cross(bars["close"], params["fast"], params["slow"]).iloc[-1]
    if pd.isna(s["slow"]):
        return 0, {"note": "数据不足 %d 天" % params["slow"]}
    return int(s["target"]), {"close": round(s["close"], 4), "fast": round(s["fast"], 4), "slow": round(s["slow"], 4)}


HOLD_RATINGS, CASH_RATINGS = {"Buy", "Overweight"}, {"Sell", "Underweight"}


def decide_ai(bars, params, ctx):
    """The AI-rating decision at the last bar given: the position implied by every rating made up to that close.

    Buy and Overweight mean hold, Sell and Underweight mean cash; Hold and REVIEW (no usable rating) keep the
    previous position. Before the first rating the account stays in cash."""
    day = bars.index[-1].date().isoformat()
    state, last = 0, None
    for date, rating in ctx["ratings"]:
        if date > day:
            break
        state = 1 if rating in HOLD_RATINGS else 0 if rating in CASH_RATINGS else state
        last = (date, rating)
    detail = {"close": round(float(bars["close"].iloc[-1]), 4)}
    detail.update({"rating": last[1], "rating_date": last[0]} if last else {"note": "还没有 AI 评级，先空仓"})
    return state, detail


STRATEGIES = {"ma_cross": decide_ma, "ai_rating": decide_ai}


def ai_ratings():
    """Ratings and in-flight analyses from the AI ledger, for AI accounts."""
    from ..ai import ledger

    con = ledger.connect()

    class Source:
        def ratings(self, sym):
            return ledger.ratings(con, sym)

        def pending(self, sym, day):
            return ledger.active(con, sym, day) > 0

        def close(self):
            con.close()

    return Source()


def accounts(con):
    cols = ["name", "strategy", "params", "symbols", "cash", "fee", "slippage", "start", "created"]
    out = []
    for row in con.execute("SELECT %s FROM accounts ORDER BY created" % ", ".join(cols)):
        a = dict(zip(cols, row, strict=True))
        a["params"], a["symbols"] = json.loads(a["params"]), json.loads(a["symbols"])
        out.append(a)
    return out


def get_account(con, name):
    for a in accounts(con):
        if a["name"] == name:
            return a
    raise NotFound("没有叫 %s 的模拟账户" % name)


def create(con, name, symbols, params, cash=10_000.0, fee=0.0, slippage=5.0, kind="ma_cross", load=None, now=None):
    """A new account. Its first decisions are taken at the latest close already available, and trading starts at
    the next open, so nothing before the account existed is ever traded."""
    load = load or data.load
    if not NAME_RE.match(name or ""):
        raise InvalidInput("账户名称只能用文字、数字、- 和 _，最长 40 个字")
    if kind not in STRATEGIES:
        raise InvalidInput("不支持的策略：%s" % kind)
    if kind == "ma_cross" and not 0 < params.get("fast", 0) < params.get("slow", 0):
        raise InvalidInput("短均线天数要小于长均线天数")
    if kind == "ai_rating" and not 1 <= params.get("every_days", 0) <= 60:
        raise InvalidInput("AI 分析间隔要在 1 到 60 天之间")
    if not cash > 0 or fee < 0 or slippage < 0:
        raise InvalidInput("初始资金要大于 0，手续费和滑点不能是负数")
    if con.execute("SELECT 1 FROM accounts WHERE name = ?", (name,)).fetchone():
        raise Conflict("已经有一个叫 %s 的模拟账户" % name)
    symbols = list(dict.fromkeys(data.normalize(s) for s in symbols if str(s).strip()))
    if not symbols:
        raise InvalidInput("至少要有一只股票")
    last = [load(s).index[-1] for s in symbols]
    start = max(last).date().isoformat()
    now = (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds")
    with con:
        con.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, kind, json.dumps(params), json.dumps(symbols), cash, fee, slippage, start, now))
    return start


def delete(con, name):
    with con:
        n = con.execute("DELETE FROM accounts WHERE name = ?", (name,)).rowcount
        con.execute("DELETE FROM signals WHERE account = ?", (name,))
    return n


def record(con, acct, load=None, now=None, ai=None):
    """Takes the decision for every completed bar on or after the account's start that has none yet.

    Each decision sees only the bars up to its own date, so one taken late is the same as one taken on time.
    Existing decisions are never rewritten. For AI accounts a day waits while an analysis of it is still queued
    or running, so its rating is not missed. Returns the new decisions."""
    load = load or data.load
    now = (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds")
    decide, new = STRATEGIES[acct["strategy"]], []
    own = ai is None and acct["strategy"] == "ai_rating"
    if own:
        ai = ai_ratings()
    try:
        for sym in acct["symbols"]:
            new += _record_symbol(con, acct, sym, load(sym), decide, now, ai)
    finally:
        if own:
            ai.close()
    return new


def _record_symbol(con, acct, sym, bars, decide, now, ai):
    new = []
    have = {r[0] for r in con.execute("SELECT date FROM signals WHERE account = ? AND symbol = ?", (acct["name"], sym))}
    ctx = {"ratings": ai.ratings(sym)} if ai else None
    for d in bars.index[bars.index >= pd.Timestamp(acct["start"])]:
        day = d.date().isoformat()
        if day in have:
            continue
        if ai and ai.pending(sym, day):
            break  # this and later days wait for the analysis
        target, detail = decide(bars.loc[:d], acct["params"], ctx)
        with con:
            con.execute("INSERT OR IGNORE INTO signals VALUES (?, ?, ?, ?, ?, ?)",
                        (acct["name"], sym, day, target, json.dumps(detail, ensure_ascii=False), now))
        new.append({"symbol": sym, "date": day, "target": target, **detail})
    return new


def signals(con, name, sym):
    rows = con.execute("SELECT date, target, detail, recorded FROM signals WHERE account = ? AND symbol = ? ORDER BY date",
                       (name, sym)).fetchall()
    df = pd.DataFrame(rows, columns=["date", "target", "detail", "recorded"])
    return df.set_index(pd.to_datetime(df.pop("date")))


def replay(con, acct, load=None):
    """Per symbol: the decisions, and the strategy and buy-and-hold results from the first open after the start.

    Cash is split equally between the symbols; each share of it trades only its own symbol."""
    load = load or data.load
    sleeve, out = acct["cash"] / len(acct["symbols"]), {}
    kw = dict(cash=sleeve, fee=acct["fee"], slippage_bps=acct["slippage"])
    for sym in acct["symbols"]:
        bars, sig = load(sym), signals(con, acct["name"], sym)
        window = bars[bars.index > pd.Timestamp(acct["start"])]
        res = bench = None
        if len(window):
            res, bench = backtest.run(window, sig["target"], **kw), backtest.buy_and_hold(window, **kw)
        out[sym] = {"bars": bars, "signals": sig, "result": res, "bench": bench, "cash": sleeve}
    return out


def summarize(acct, rep):
    """What an account holds and how it has done, as plain data for the command line and the API."""
    positions, pending, fills, curves = [], [], [], []
    for sym, r in rep.items():
        sig, res, bench, cash = r["signals"], r["result"], r["bench"], r["cash"]
        last = sig.iloc[-1]
        held = bool(res.in_market.iloc[-1]) if res else False
        value = float(res.equity.iloc[-1]) if res else cash
        bvalue = float(bench.equity.iloc[-1]) if bench else cash
        positions.append({
            "symbol": sym, "decision": bool(last["target"]), "decision_date": sig.index[-1].date(), "held": held,
            "shares": int(res.trades[res.trades["open"]]["shares"].sum()) if res else 0,
            "value": value, "cash": cash, "return": value / cash - 1, "benchmark_return": bvalue / cash - 1,
        })
        if bool(last["target"]) != held:
            pending.append({"symbol": sym, "side": "buy" if last["target"] else "sell", "decision_date": sig.index[-1].date(),
                            "affordable": not (last["target"] and cash < r["bars"]["close"].iloc[-1])})
        if res:
            for _, t in res.trades.iterrows():
                fills.append({"date": t["entry_date"].date(), "side": "buy", "symbol": sym, "shares": int(t["shares"]),
                              "price": float(t["entry_price"])})
                if not t["open"]:
                    fills.append({"date": t["exit_date"].date(), "side": "sell", "symbol": sym, "shares": int(t["shares"]),
                                  "price": float(t["exit_price"])})
            curves.append(pd.DataFrame({(sym, "strategy"): res.equity, (sym, "benchmark"): bench.equity}))
    equity = pd.DataFrame(columns=["strategy", "benchmark"], dtype=float)
    if curves:
        # US and Hong Kong trade on different days: carry each value forward; before its first open it is cash
        df = pd.concat(curves, axis=1).sort_index().ffill()
        for sym, r in rep.items():
            for kind in ("strategy", "benchmark"):
                col = (sym, kind)
                df[col] = df[col].fillna(r["cash"]) if col in df else r["cash"]
        equity = pd.DataFrame({k: df.xs(k, axis=1, level=1).sum(axis=1) for k in ("strategy", "benchmark")})
    total = sum(p["value"] for p in positions)
    bench_total = sum(p["cash"] * (1 + p["benchmark_return"]) for p in positions)
    return {
        "name": acct["name"], "strategy": acct["strategy"], "params": acct["params"], "symbols": acct["symbols"],
        "start": acct["start"], "cash": acct["cash"], "fee": acct["fee"], "slippage": acct["slippage"],
        "value": total, "return": total / acct["cash"] - 1, "benchmark_return": bench_total / acct["cash"] - 1,
        "days": len(equity), "positions": positions, "pending": pending,
        "fills": sorted(fills, key=lambda f: f["date"], reverse=True), "equity": equity,
    }
