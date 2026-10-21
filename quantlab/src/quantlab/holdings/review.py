"""The daily review of your real holdings: value them, apply your rules, propose orders, and list reminders.

Proposed orders come only from the rules in policy.py (targets with a rebalancing band, a cap per stock, and a
cash reserve). Trend, fundamentals and AI ratings never create orders; they appear as reminders. You approve or
reject each proposal; you place approved ones at your broker and record them in the ledger yourself."""
import contextlib
import datetime as dt
import json
import math

from .. import storage
from ..config import get_settings
from ..core import data
from ..errors import NotFound, QuantLabError
from . import ledger, policy, signals

MIGRATIONS = [
    """CREATE TABLE reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, synced TEXT, data TEXT NOT NULL);
       CREATE TABLE proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, review_id INTEGER NOT NULL, symbol TEXT NOT NULL,
                               side TEXT NOT NULL, shares REAL NOT NULL, price REAL NOT NULL, value REAL NOT NULL,
                               reason TEXT NOT NULL, status TEXT NOT NULL, decided_at TEXT, note TEXT);
       CREATE INDEX proposals_status ON proposals (status)""",
]
CASH_LEVEL = {"Sell": "warn", "Underweight": "warn", "Buy": "info", "Overweight": "info", "Hold": "info"}


def connect():
    return storage.connect(get_settings().data_dir / "holdings" / "holdings.db", MIGRATIONS)


def now_iso():
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def valuation(snapshot, prices):
    """Positions at the latest prices, with weights of the investable money (stocks + cash)."""
    pos = ledger.positions(snapshot)
    cash = ledger.cash_usd(snapshot)
    for p in pos:
        px = prices.get(p["symbol"])
        p["price"] = px
        p["value"] = ledger.usd(p["shares"] * px, p["currency"]) if px else None
        p["gain"] = (px / p["avg_cost"] - 1) if px and p["avg_cost"] else None
    stocks = sum(p["value"] or 0 for p in pos)
    investable = stocks + cash
    for p in pos:
        p["weight"] = (p["value"] / investable) if p["value"] and investable > 0 else None
    fixed = ledger.fixed_usd(snapshot)
    return {"positions": pos, "cash": cash, "stocks": stocks, "investable": investable, "fixed": fixed,
            "total": investable + fixed, "cash_weight": cash / investable if investable > 0 else None}


def proposals(val, pol, prices):
    """Orders that bring the holdings within your rules. Sells first; buys never take cash below the reserve."""
    inv = val["investable"]
    if inv <= 0:
        return [], []
    held = {p["symbol"]: p for p in val["positions"]}
    targets, notes = pol["targets"], []
    want = {}
    for sym in set(held) | set(targets):
        value = held[sym]["value"] if sym in held and held[sym]["value"] is not None else 0.0
        if sym in held and held[sym]["value"] is None:
            notes.append("%s 没有最新价格，不做调整" % sym)
            continue
        desired, why = value, None
        if sym in targets:
            t = targets[sym]
            w = value / inv
            if sym not in held or abs(w - t) > pol["band"]:
                desired, why = t * inv, "目标 %.0f%%，当前 %.1f%%（偏离超过 %.0f 个百分点）" % (t * 100, w * 100, pol["band"] * 100)
        cap = pol["max_position"] * inv
        if desired > cap + 1e-6:
            desired, why = cap, "超过单只上限 %.0f%%（当前 %.1f%%）" % (pol["max_position"] * 100, value / inv * 100)
        if why:
            want[sym] = (desired - value, why)
    sells = {s: d for s, d in want.items() if d[0] < 0}
    buys = {s: d for s, d in want.items() if d[0] > 0}
    available = val["cash"] + sum(-d[0] for d in sells.values()) - pol["cash_reserve"] * inv
    need = sum(d[0] for d in buys.values())
    scale = 1.0
    if need > 0 and available < need:
        scale = max(0.0, available / need)
        notes.append("现金（扣除 %.0f%% 储备后）不够，买入按 %.0f%% 比例缩减" % (pol["cash_reserve"] * 100, scale * 100))
    out = []
    for sym, (delta, why) in sorted(sells.items()) + sorted(buys.items()):
        px = prices.get(sym)
        if not px:
            notes.append("%s 没有价格，无法计算股数" % sym)
            continue
        value = abs(delta) * (scale if delta > 0 else 1.0)
        shares = value / px
        if pol["whole_shares"]:
            shares = math.floor(shares) if delta > 0 else min(math.ceil(shares - 1e-9), held[sym]["shares"] if sym in held else 0)
        shares = min(shares, held[sym]["shares"]) if delta < 0 and sym in held else shares
        if shares <= 0 or shares * px < pol["min_trade_usd"]:
            if value >= 1:
                notes.append("%s 需要调整 $%.0f，不到最小下单金额或不足 1 股，暂不建议" % (sym, value))
            continue
        out.append({"symbol": sym, "side": "sell" if delta < 0 else "buy", "shares": round(shares, 6), "price": px,
                    "value": round(shares * px, 2), "reason": why})
    return out, notes


def reminders(val, pol, load=None, with_fundamentals=True):
    """Signals about each holding. Reminders only: none of these creates an order."""
    out = []
    add = lambda sym, level, kind, text: out.append({"symbol": sym, "level": level, "kind": kind, "text": text})
    if val["cash_weight"] is not None and val["cash_weight"] < pol["cash_reserve"] - 1e-9:
        add("现金", "warn", "规则", "现金只占 %.1f%%，低于储备 %.0f%%" % (val["cash_weight"] * 100, pol["cash_reserve"] * 100))
    for p in val["positions"]:
        sym = p["symbol"]
        if p["gain"] is not None and p["gain"] <= -pol["alert_drawdown_from_cost"]:
            add(sym, "warn", "亏损", "比平均成本 %.2f 低 %.0f%%" % (p["avg_cost"], -p["gain"] * 100))
        try:
            t = signals.technical(sym, load)
        except QuantLabError as e:
            add(sym, "warn", "数据", "取不到行情：%s" % e)
            continue
        if t.get("from_high") is not None and t["from_high"] <= -pol["alert_drawdown_from_high"]:
            add(sym, "warn", "回撤", "比一年内最高点低 %.0f%%" % (-t["from_high"] * 100))
        if t.get("uptrend") is False:
            add(sym, "info", "趋势", "50 日线在 200 日线下方%s" % ("（%s 死叉）" % t["last_cross"] if t.get("last_cross") else ""))
        ai = signals.ai_rating(sym)
        if ai:
            add(sym, CASH_LEVEL.get(ai["rating"], "info"), "AI", "最近一次 AI 评级 %s（%s 收盘后，分析 #%d）" % (ai["rating"], ai["trade_date"], ai["id"]))
        if with_fundamentals:
            f = signals.fundamentals(sym, t.get("close"))
            if f:
                if f["net_income_ttm"] is not None and f["net_income_ttm"] < 0:
                    add(sym, "warn", "基本面", "最近 12 个月净利润为负")
                if f["sue"] is not None and f["sue"] < -2:
                    add(sym, "warn", "基本面", "最近一季盈利明显低于去年同期（SUE %.1f）" % f["sue"])
                if f["rev_growth"] is not None and f["rev_growth"] < 0:
                    add(sym, "info", "基本面", "收入同比下降 %.0f%%" % (-f["rev_growth"] * 100))
    return out


def prices_for(symbols, load=None):
    load = load or data.load
    out = {}
    for s in symbols:
        with contextlib.suppress(QuantLabError):  # a symbol without data simply has no price
            out[s] = float(load(s)["close"].iloc[-1])
    return out


def run(refresh=True, load=None, with_fundamentals=True):
    """Syncs (when a key is configured), reviews, and records the review and its proposals.

    Pending proposals from earlier reviews are superseded: each review stands on the latest holdings."""
    snap = ledger.load(refresh=refresh and ledger.configured())
    pol = policy.load()
    syms = {p["symbol"] for p in ledger.positions(snap)} | set(pol["targets"])
    prices = prices_for(syms, load)
    val = valuation(snap, prices)
    props, notes = proposals(val, pol, prices)
    rem = reminders(val, pol, load, with_fundamentals)
    review = {"at": now_iso(), "synced": snap.get("synced"), "account": snap.get("account"), "policy": pol,
              "valuation": val, "notes": notes, "reminders": rem}
    con = connect()
    try:
        with con:
            con.execute("UPDATE proposals SET status = 'superseded', decided_at = ? WHERE status = 'pending'", (now_iso(),))
            rid = con.execute("INSERT INTO reviews (at, synced, data) VALUES (?, ?, ?)",
                              (review["at"], review["synced"], json.dumps(review, ensure_ascii=False, default=str))).lastrowid
            for o in props:
                con.execute("INSERT INTO proposals (review_id, symbol, side, shares, price, value, reason, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
                            (rid, o["symbol"], o["side"], o["shares"], o["price"], o["value"], o["reason"]))
        return latest(con)
    finally:
        con.close()


PCOLS = ["id", "review_id", "symbol", "side", "shares", "price", "value", "reason", "status", "decided_at", "note"]


def latest(con=None):
    own = con is None
    con = con or connect()
    try:
        r = con.execute("SELECT id, data FROM reviews ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        review = json.loads(r[1])
        review["id"] = r[0]
        review["proposals"] = [dict(zip(PCOLS, x, strict=True)) for x in con.execute(
            "SELECT %s FROM proposals WHERE review_id = ? ORDER BY side DESC, symbol" % ", ".join(PCOLS), (r[0],))]
        return review
    finally:
        if own:
            con.close()


def decide(proposal_id, approve, note=""):
    """Approve (you will place it at your broker) or reject a pending proposal."""
    con = connect()
    try:
        r = con.execute("SELECT status FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if not r:
            raise NotFound("没有编号为 %s 的建议订单" % proposal_id)
        if r[0] != "pending":
            raise NotFound("这条建议已经处理过（%s）" % r[0])
        with con:
            con.execute("UPDATE proposals SET status = ?, decided_at = ?, note = ? WHERE id = ?",
                        ("approved" if approve else "rejected", now_iso(), note[:200], proposal_id))
        return dict(zip(PCOLS, con.execute("SELECT %s FROM proposals WHERE id = ?" % ", ".join(PCOLS), (proposal_id,)).fetchone(), strict=True))
    finally:
        con.close()
