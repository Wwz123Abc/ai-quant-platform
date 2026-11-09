"""The daily review of your real holdings: value them, apply your rules, propose orders, and list reminders.

Proposed orders come only from the rules in policy.py (targets with a rebalancing band, a cap per stock, and a
cash reserve). Trend, fundamentals and AI ratings never create orders; they appear as reminders. You approve or
reject each proposal; you place approved ones at your broker and record them in the ledger yourself."""
import contextlib
import datetime as dt
import json
import math
import re

from .. import storage
from ..config import get_settings
from ..core import data, price_check
from ..errors import NotFound, QuantLabError
from . import ledger, policy, signals

MIGRATIONS = [
    """CREATE TABLE reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, synced TEXT, data TEXT NOT NULL);
       CREATE TABLE proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, review_id INTEGER NOT NULL, symbol TEXT NOT NULL,
                               side TEXT NOT NULL, shares REAL NOT NULL, price REAL NOT NULL, value REAL NOT NULL,
                               reason TEXT NOT NULL, status TEXT NOT NULL, decided_at TEXT, note TEXT);
       CREATE INDEX proposals_status ON proposals (status)""",
    "ALTER TABLE proposals ADD COLUMN basis TEXT NOT NULL DEFAULT ''",
]
CASH_LEVEL = {"Sell": "warn", "Underweight": "warn", "Buy": "info", "Overweight": "info", "Hold": "info"}
DUE_DAYS, MATURED_DAYS = 14, 30  # remind of a Treasury or deposit this many days before and after its maturity


def connect():
    return storage.connect(get_settings().data_dir / "holdings" / "holdings.db", MIGRATIONS)


def now_iso():
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def valuation(snapshot, prices, day=None):
    """Positions at the latest prices, with weights of the investable money (stocks + cash). Treasuries and deposits
    are valued as the ledger values them on day (default today in New York)."""
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
    items = ledger.fixed_status(snapshot, day)
    fixed = sum(f["value"] or 0.0 for f in items)
    return {"positions": pos, "cash": cash, "stocks": stocks, "investable": investable, "fixed": fixed, "fixed_items": items,
            "total": investable + fixed, "cash_weight": cash / investable if investable > 0 else None}


def price_basis(sym, px, info):
    """'SYM 2026-11-03 收盘价 $693.54（腾讯日线，已和第二来源核对一致）': the price and where it came from."""
    info = info or {}
    date = (info.get("dates") or {}).get(sym)
    src = info.get("source") or "行情数据"
    check = (info.get("checked") or {}).get(sym)
    return "%s %s收盘价 $%.2f（%s%s）" % (sym, "%s " % date if date else "", px, src, "，%s" % check if check else "")


def take_profit_step(pol, gain, done=()):
    """The take-profit step a holding with this gain (price over average cost) is at, as (index, threshold, share to sell),
    or None: the highest step reached that is not in done. Needs pol["take_profit"]."""
    if not pol.get("take_profit") or gain is None:
        return None
    for i in (2, 1):
        if gain >= pol["tp_gain_%d" % i] - 1e-9:
            if done and max(done) >= i:  # this step, or a higher one, was already taken
                return None
            return i, pol["tp_gain_%d" % i], pol["tp_sell_%d" % i]
    return None


def tp_tag(i, pol):
    return "止盈第 %d 档（+%.0f%%）" % (i, pol["tp_gain_%d" % i] * 100)


def proposals(val, pol, prices, info=None, tp_done=None):
    """Orders that bring the holdings within your rules. Sells first; buys never take cash below the reserve.
    Each order carries its basis: the rule, the figures it used, the data date and source. info (optional) names
    the price dates, the source, the sync time and the cross-check results. tp_done maps a symbol to the take-profit steps
    already proposed and approved since its last purchase."""
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
        cap = policy.cap_for(pol, sym) * inv
        if desired > cap + 1e-6:
            desired, why = cap, "超过单只上限 %.0f%%（当前 %.1f%%）" % (pol["max_position"] * 100, value / inv * 100)
        if not why and sym in held and sym not in pol["uncapped"]:
            step = take_profit_step(pol, held[sym]["gain"], (tp_done or {}).get(sym, ()))
            if step:
                i, at, sell = step
                desired = value * (1 - sell)
                why = "%s：现价比平均成本高 %.0f%%，卖出约 %.0f%% 的股数锁定一部分利润" % (tp_tag(i, pol), held[sym]["gain"] * 100, sell * 100)
        if why:
            want[sym] = (desired - value, why, value, desired)
    sells = {s: d for s, d in want.items() if d[0] < 0}
    buys = {s: d for s, d in want.items() if d[0] > 0}
    available = val["cash"] + sum(-d[0] for d in sells.values()) - pol["cash_reserve"] * inv
    need = sum(d[0] for d in buys.values())
    scale = 1.0
    if need > 0 and available < need:
        scale = max(0.0, available / need)
        notes.append("现金（扣除 %.0f%% 储备后）不够，买入按 %.0f%% 比例缩减" % (pol["cash_reserve"] * 100, scale * 100))
    out = []
    for sym, (delta, why, cur, desired) in sorted(sells.items()) + sorted(buys.items()):
        px = prices.get(sym)
        if not px:
            notes.append("%s 没有价格，无法计算股数" % sym)
            continue
        value = abs(delta) * (scale if delta > 0 else 1.0)
        shares = value / px
        if pol["whole_shares"]:
            # buys round down to stay within cash; sells take the whole number of shares that lands nearest the
            # rule, so a holding a few dollars over its cap is not cut by a whole share far below it
            shares = math.floor(shares) if delta > 0 else min(math.floor(shares + 0.5), held[sym]["shares"] if sym in held else 0)
        shares = min(shares, held[sym]["shares"]) if delta < 0 and sym in held else shares
        if shares <= 0:
            if value >= 1:
                notes.append("%s 需要%s约 $%.0f，不到半股（每股约 $%.0f），按整股暂不建议" % (sym, "卖出" if delta < 0 else "买入", value, px)
                             if pol["whole_shares"] and value < px else "%s 需要调整约 $%.0f，暂不建议" % (sym, value))
            continue
        if shares * px < pol["min_trade_usd"]:
            notes.append("%s 需要调整约 $%.0f，不到最小下单金额 $%.0f，暂不建议" % (sym, shares * px, pol["min_trade_usd"]))
            continue
        synced = "（%s 同步）" % info["synced"] if info and info.get("synced") else ""
        cut = "，现金不足，按 %.0f%% 缩减" % (scale * 100) if delta > 0 and scale < 1 else ""
        rule = ("规则：止盈（涨到平均成本的 %.0f%% 以上分两档卖出，每档每次买入只提议一次）。" % (pol["tp_gain_1"] * 100)
                if why.startswith("止盈") else "规则：你设定的持仓规则（目标占比和偏离带、单只上限、现金储备）。")
        basis = (rule + "数据：%s；持仓来自账本%s；"
                 "可投资资金 $%.2f（股票 $%.2f + 现金 $%.2f），%s 现值 $%.2f。计算：应持有 $%.2f，差 $%.2f%s，"
                 "按 $%.2f 折合 %s 股%s。"
                 % (price_basis(sym, px, info), synced, inv, val["stocks"], val["cash"], sym, cur, desired,
                    abs(delta), cut, px, "%.4g" % shares, "（整股）" if pol["whole_shares"] else ""))
        out.append({"symbol": sym, "side": "sell" if delta < 0 else "buy", "shares": round(shares, 6), "price": px,
                    "value": round(shares * px, 2), "reason": why, "basis": basis})
    return out, notes


def reminders(val, pol, load=None, with_fundamentals=True, info=None):
    """Signals about each holding. Reminders only: none of these creates an order. Each says what it rests on."""
    out = []
    src = (info or {}).get("source") or "行情数据"
    synced = "，%s 同步" % info["synced"] if info and info.get("synced") else ""
    add = lambda sym, level, kind, text, basis="": out.append(
        {"symbol": sym, "level": level, "kind": kind, "text": text, "basis": basis})
    if val["cash_weight"] is not None and val["cash_weight"] < pol["cash_reserve"] - 1e-9:
        add("现金", "warn", "规则", "现金只占 %.1f%%，低于储备 %.0f%%" % (val["cash_weight"] * 100, pol["cash_reserve"] * 100),
            "规则：现金占可投资资金不低于 %.0f%%。数据：现金 $%.2f ÷ 可投资资金 $%.2f（账本%s）。"
            % (pol["cash_reserve"] * 100, val["cash"], val["investable"], synced))
    for f in val.get("fixed_items", []):
        if f["matured"] and f["days_left"] >= -MATURED_DAYS:
            add(f["name"], "info", "到期", "%s 已到期，本息回到储蓄账户，不再算在国债定存里；在账本里更新现金后，可投资资金才会算上这笔钱" % f["maturity"],
                "数据：账本里这笔的到期日 %s；到期后 %d 天内提醒。" % (f["maturity"], MATURED_DAYS))
        elif f["started"] and not f["matured"] and f["days_left"] <= DUE_DAYS:
            add(f["name"], "info", "到期", "%s 到期（还有 %d 天）" % (f["maturity"], f["days_left"]),
                "数据：账本里这笔的到期日 %s；到期前 %d 天内提醒。" % (f["maturity"], DUE_DAYS))
    for p in val["positions"]:
        sym = p["symbol"]
        if p["gain"] is not None and p["gain"] <= -pol["alert_drawdown_from_cost"]:
            add(sym, "warn", "亏损", "比平均成本 %.2f 低 %.0f%%" % (p["avg_cost"], -p["gain"] * 100),
                "规则：比平均成本低 %.0f%% 提醒。数据：平均成本 $%.2f（账本移动平均法），%s。"
                % (pol["alert_drawdown_from_cost"] * 100, p["avg_cost"], price_basis(sym, p["price"], info)))
        try:
            t = signals.technical(sym, load)
        except QuantLabError as e:
            add(sym, "warn", "数据", "取不到行情：%s" % e)
            continue
        if t.get("from_high") is not None and t["from_high"] <= -pol["alert_drawdown_from_high"]:
            add(sym, "warn", "回撤", "比一年内最高点低 %.0f%%" % (-t["from_high"] * 100),
                "规则：比一年内最高点低 %.0f%% 提醒。数据：%s 收盘 $%.2f，近一年最高 $%.2f（%s，近 252 个交易日）。"
                % (pol["alert_drawdown_from_high"] * 100, t["date"], t["close"], t["close"] / (1 + t["from_high"]), src))
        if t.get("uptrend") is False:
            add(sym, "info", "趋势", "50 日线在 200 日线下方%s" % ("（%s 死叉）" % t["last_cross"] if t.get("last_cross") else ""),
                "数据：%s 的 50 日线 $%.2f、200 日线 $%.2f（收盘价的简单移动平均，%s）。只是提醒，不生成订单。"
                % (t["date"], t["ma50"], t["ma200"], src))
        ai = signals.ai_rating(sym)
        if ai:
            add(sym, CASH_LEVEL.get(ai["rating"], "info"), "AI", "最近一次 AI 评级 %s（%s 收盘后，分析 #%d）" % (ai["rating"], ai["trade_date"], ai["id"]),
                "数据：AI 研报 #%d（%s 收盘后的分析，完整报告在“AI 研报”页）。AI 评级只是提醒，不生成订单。" % (ai["id"], ai["trade_date"]))
        if with_fundamentals:
            f = signals.fundamentals(sym, t.get("close"))
            if f:
                if f["net_income_ttm"] is not None and f["net_income_ttm"] < 0:
                    add(sym, "warn", "基本面", "最近 12 个月净利润为负",
                        "数据：SEC EDGAR 财报，最近 12 个月净利润 $%.0f（按披露日期取数，不用未来数据）。" % f["net_income_ttm"])
                if f["sue"] is not None and f["sue"] < -2:
                    add(sym, "warn", "基本面", "最近一季盈利明显低于去年同期（SUE %.1f）" % f["sue"],
                        "数据：SEC EDGAR 财报；SUE 是本季盈利相对去年同期的意外程度，低于 -2 提醒（当前 %.1f）。" % f["sue"])
                if f["rev_growth"] is not None and f["rev_growth"] < 0:
                    add(sym, "info", "基本面", "收入同比下降 %.0f%%" % (-f["rev_growth"] * 100),
                        "数据：SEC EDGAR 财报，最近 12 个月收入相对前一年 %.1f%%。" % (f["rev_growth"] * 100))
    return out


def check_prices(symbols):
    """Each US stock's recent closes checked against a second source by the ledger's rule (core/price_check.py).
    Only with the mainland price source, whose closes are Tencent's: Yahoo's are not what the check looks at."""
    if get_settings().price_source != "cn":
        return []
    return [price_check.check(s) for s in sorted(symbols) if re.fullmatch(r"[A-Z][A-Z.\-]*", s)]


def prices_for(symbols, load=None):
    load = load or data.load
    out = {}
    for s in symbols:
        with contextlib.suppress(QuantLabError):  # a symbol without data simply has no price
            out[s] = float(load(s)["close"].iloc[-1])
    return out


def _price_dates(symbols, load):
    out = {}
    for s in symbols:
        with contextlib.suppress(QuantLabError):
            out[s] = load(s).index[-1].date().isoformat()
    return out


def take_profit_done(snapshot, pol):
    """Per symbol, the take-profit steps already approved since the stock's last purchase in the ledger."""
    last_buy = {}
    for t in snapshot.get("trades", []):
        if t.get("side") == "buy":
            s = str(t.get("symbol", "")).upper()
            last_buy[s] = max(last_buy.get(s, ""), t.get("date", ""))
    out = {}
    con = connect()
    try:
        for sym, reason, decided in con.execute("SELECT symbol, reason, decided_at FROM proposals WHERE status = 'approved' AND reason LIKE '止盈第%'"):
            for i in (1, 2):
                if reason.startswith(tp_tag(i, pol)) and (decided or "")[:10] >= last_buy.get(sym, ""):
                    out.setdefault(sym, set()).add(i)
    finally:
        con.close()
    return out


def run(refresh=True, load=None, with_fundamentals=True, check=True):
    """Syncs (when a key is configured), reviews, and records the review and its proposals. check cross-checks the
    held stocks' recent closes with a second source.

    Pending proposals from earlier reviews are superseded: each review stands on the latest holdings."""
    load = load or data.load
    snap = ledger.load(refresh=refresh and ledger.configured())
    pol = policy.load()
    syms = {p["symbol"] for p in ledger.positions(snap)} | set(pol["targets"])
    prices = prices_for(syms, load)
    dates = []
    for s in syms:
        with contextlib.suppress(QuantLabError):
            dates.append(load(s).index[-1].date().isoformat())
    val = valuation(snap, prices)
    checks = check_prices({p["symbol"] for p in val["positions"]}) if check else []
    info = {"dates": _price_dates(syms, load), "synced": snap.get("synced"),
            "source": "腾讯日线" if get_settings().price_source == "cn" else "Yahoo 日线",
            "checked": {c["symbol"]: "已和第二来源核对一致" for c in checks if c["ok"]}}
    props, notes = proposals(val, pol, prices, info, take_profit_done(snap, pol))
    rem = reminders(val, pol, load, with_fundamentals, info)
    rem += [{"symbol": c["symbol"], "level": "info" if c["ok"] is None else "warn", "kind": "核对", "text": c["text"]} for c in checks if not c["ok"]]
    review = {"at": now_iso(), "synced": snap.get("synced"), "account": snap.get("account"), "policy": pol,
              "price_date": min(dates) if dates else None,  # the oldest close the review used
              "valuation": val, "notes": notes, "reminders": rem, "price_check": checks}
    con = connect()
    try:
        with con:
            con.execute("UPDATE proposals SET status = 'superseded', decided_at = ? WHERE status = 'pending'", (now_iso(),))
            rid = con.execute("INSERT INTO reviews (at, synced, data) VALUES (?, ?, ?)",
                              (review["at"], review["synced"], json.dumps(review, ensure_ascii=False, default=str))).lastrowid
            for o in props:
                con.execute("INSERT INTO proposals (review_id, symbol, side, shares, price, value, reason, basis, status) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                            (rid, o["symbol"], o["side"], o["shares"], o["price"], o["value"], o["reason"], o["basis"]))
        return latest(con)
    finally:
        con.close()


PCOLS = ["id", "review_id", "symbol", "side", "shares", "price", "value", "reason", "basis", "status", "decided_at", "note"]


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
