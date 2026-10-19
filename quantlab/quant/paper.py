"""Paper trading: a strategy records one decision per symbol after each close, and accounts are replayed from them.

Only the decisions are stored, and once written they never change, so the record is a genuine forward test: the
strategy cannot be re-tuned after the fact. Fills and account values are recomputed from the decisions on every
run with the backtest engine, which keeps them consistent when a new dividend rescales the adjusted history."""
import datetime as dt
import json
import sqlite3
from pathlib import Path

import pandas as pd

from . import backtest, data, strategy

DB = data.DB.parent / "paper.db"


def connect(path=None):
    path = Path(path or DB)
    path.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, strategy TEXT, params TEXT, symbols TEXT,"
                " cash REAL, fee REAL, slippage REAL, start TEXT, created TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS signals (account TEXT, symbol TEXT, date TEXT, target INTEGER, detail TEXT,"
                " recorded TEXT, PRIMARY KEY (account, symbol, date))")
    return con


def decide_ma(bars, params):
    """The moving-average decision at the last bar given, from that bar and earlier ones only."""
    s = strategy.ma_cross(bars["close"], params["fast"], params["slow"]).iloc[-1]
    if pd.isna(s["slow"]):
        return 0, {"note": "数据不足 %d 天" % params["slow"]}
    return int(s["target"]), {"close": round(s["close"], 4), "fast": round(s["fast"], 4), "slow": round(s["slow"], 4)}


STRATEGIES = {"ma_cross": decide_ma}


def accounts(con):
    cols = ["name", "strategy", "params", "symbols", "cash", "fee", "slippage", "start", "created"]
    out = []
    for row in con.execute("SELECT %s FROM accounts ORDER BY created" % ", ".join(cols)):
        a = dict(zip(cols, row))
        a["params"], a["symbols"] = json.loads(a["params"]), json.loads(a["symbols"])
        out.append(a)
    return out


def create(con, name, symbols, params, cash=10_000.0, fee=0.0, slippage=5.0, kind="ma_cross", load=data.load, now=None):
    """A new account. Its first decisions are taken at the latest close already available, and trading starts at
    the next open, so nothing before the account existed is ever traded."""
    if con.execute("SELECT 1 FROM accounts WHERE name = ?", (name,)).fetchone():
        raise ValueError("已经有一个叫 %s 的模拟账户" % name)
    symbols = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
    if not symbols:
        raise ValueError("至少要有一只股票")
    last = [load(s).index[-1] for s in symbols]
    start = max(last).date().isoformat()
    now = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
    with con:
        con.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, kind, json.dumps(params), json.dumps(symbols), cash, fee, slippage, start, now))
    return start


def delete(con, name):
    with con:
        n = con.execute("DELETE FROM accounts WHERE name = ?", (name,)).rowcount
        con.execute("DELETE FROM signals WHERE account = ?", (name,))
    return n


def record(con, acct, load=data.load, now=None):
    """Takes the decision for every completed bar on or after the account's start that has none yet.

    Each decision sees only the bars up to its own date, so one taken late is the same as one taken on time.
    Existing decisions are never rewritten. Returns the new decisions."""
    now = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
    decide, new = STRATEGIES[acct["strategy"]], []
    for sym in acct["symbols"]:
        bars = load(sym)
        have = {r[0] for r in con.execute("SELECT date FROM signals WHERE account = ? AND symbol = ?", (acct["name"], sym))}
        for d in bars.index[bars.index >= pd.Timestamp(acct["start"])]:
            day = d.date().isoformat()
            if day in have:
                continue
            target, detail = decide(bars.loc[:d], acct["params"])
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


def replay(con, acct, load=data.load):
    """Per symbol: the decisions, and the strategy and buy-and-hold results from the first open after the start.

    Cash is split equally between the symbols; each share of it trades only its own symbol."""
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
