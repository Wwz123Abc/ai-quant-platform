"""The trading engine: turns target weights into orders, checks every order against the risk limits, and sends
the approved ones to the account's broker. It never decides what to buy; the strategy's weights do.

Modes, per account:
  plan   orders are worked out and recorded, nothing is sent anywhere
  paper  orders go to the built-in simulated broker
  live   orders go to a real broker connector; refused unless live trading is enabled in the settings, a
         connector exists, and the paper account has run for the required number of weeks"""
import datetime as dt
import logging
import math

import pandas as pd

from ..config import get_settings
from ..errors import Conflict, InvalidInput, NotFound, Unavailable
from .broker import PaperBroker, event, now_iso

log = logging.getLogger(__name__)
MODES = ("plan", "paper", "live")


# ---------------------------------------------------------------- accounts

def create_account(con, name, mode="paper", capital=None):
    s = get_settings()
    capital = float(capital if capital is not None else s.trading_capital)
    if mode not in MODES:
        raise InvalidInput("模式只能是 plan、paper 或 live")
    if mode == "live":
        raise InvalidInput("不能直接创建实盘账户：先用模拟账户运行满 %d 周" % s.trading_paper_weeks_before_live)
    if not 0 < capital <= s.trading_capital:
        raise InvalidInput("资金要大于 0，且不能超过设置的上限 $%.0f" % s.trading_capital)
    if con.execute("SELECT 1 FROM accounts WHERE name = ?", (name,)).fetchone():
        raise Conflict("已经有一个叫 %s 的交易账户" % name)
    with con:
        con.execute("INSERT INTO accounts VALUES (?, 'paper', ?, ?, ?, ?, NULL, ?)", (name, mode, capital, capital, capital, now_iso()))
    event(con, name, "account", "创建账户：%s 模式，资金 $%.2f" % (mode, capital))


def get_account(con, name):
    cols = ["name", "broker", "mode", "capital", "cash", "peak_equity", "halted", "created"]
    r = con.execute("SELECT %s FROM accounts WHERE name = ?" % ", ".join(cols), (name,)).fetchone()
    if not r:
        raise NotFound("没有叫 %s 的交易账户" % name)
    return dict(zip(cols, r, strict=True))


def accounts(con):
    return [get_account(con, r[0]) for r in con.execute("SELECT name FROM accounts ORDER BY created")]


def broker_for(con, acct, load=None):
    if acct["mode"] == "live":
        raise Unavailable("还没有接入券商：实盘需要先为你的券商写连接器")
    return PaperBroker(con, acct["name"], load)


def halt(con, name, reason):
    with con:
        con.execute("UPDATE accounts SET halted = ? WHERE name = ?", (reason, name))
    event(con, name, "halt", "暂停交易：%s" % reason)
    log.warning("trading halted for %s: %s", name, reason)


def resume(con, name):
    get_account(con, name)
    with con:
        con.execute("UPDATE accounts SET halted = NULL WHERE name = ?", (name,))
    event(con, name, "resume", "恢复交易")


def set_mode(con, name, mode):
    s, acct = get_settings(), get_account(con, name)
    if mode not in MODES:
        raise InvalidInput("模式只能是 plan、paper 或 live")
    if mode == "live":
        weeks = (dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(acct["created"])).days / 7
        if not s.trading_live_enabled:
            raise Unavailable("实盘交易没有开启：需要在 .env 里设置 QUANTLAB_TRADING_LIVE_ENABLED=true")
        if weeks < s.trading_paper_weeks_before_live:
            raise InvalidInput("模拟账户只运行了 %.1f 周，至少要 %d 周才能切换实盘" % (weeks, s.trading_paper_weeks_before_live))
        raise Unavailable("还没有接入券商：实盘需要先为你的券商写连接器")
    with con:
        con.execute("UPDATE accounts SET mode = ? WHERE name = ?", (mode, name))
    event(con, name, "mode", "切换为 %s 模式" % mode)


# ---------------------------------------------------------------- valuation and safety

def valuation(account_state, prices):
    pos = account_state["positions"]
    held = sum(q * float(prices.get(s, 0) or 0) for s, q in pos.items())
    return account_state["cash"] + held


def mark(con, acct, broker, prices, day):
    """Records the day's equity, raises the peak, and halts the account if it fell too far below the peak."""
    s = get_settings()
    value = valuation(broker.account(), prices)
    peak = max(acct["peak_equity"], value)
    with con:
        con.execute("INSERT OR REPLACE INTO equity VALUES (?, ?, ?)", (acct["name"], day.isoformat(), value))
        con.execute("UPDATE accounts SET peak_equity = ? WHERE name = ?", (peak, acct["name"]))
    if not acct["halted"] and peak > 0 and value < peak * (1 - s.trading_max_drawdown):
        halt(con, acct["name"], "账户从最高点 $%.2f 回撤到 $%.2f，超过 %.0f%% 的熔断线" % (peak, value, s.trading_max_drawdown * 100))
    return value


def data_problems(prices, previous, data_date, today=None):
    """Reasons not to trade on this data: stale, missing, or prices that moved implausibly in a day."""
    s = get_settings()
    today = today or dt.date.today()
    problems = []
    if (today - data_date).days > s.trading_max_data_age_days:
        problems.append("行情停在 %s，已经超过 %d 天" % (data_date, s.trading_max_data_age_days))
    for sym, px in prices.items():
        if not px or not math.isfinite(px) or px <= 0:
            problems.append("%s 没有有效价格" % sym)
        elif sym in previous and previous[sym] and abs(px / previous[sym] - 1) > 0.5:
            problems.append("%s 一天变动超过 50%%（%.2f → %.2f），可能是数据错误" % (sym, previous[sym], px))
    return problems


# ---------------------------------------------------------------- orders

def orders_for(acct, account_state, weights, prices, signal_date):
    """The orders that move the account from what it holds to the target weights of its capital (or of its value,
    if lower). Quantities are fractional; prices are the latest closes, as estimates of the next open."""
    s = get_settings()
    value = valuation(account_state, prices)
    budget = min(acct["capital"], value)
    held = account_state["positions"]
    out = []
    for sym in sorted(set(held) | set(weights.index)):
        px = float(prices.get(sym, 0) or 0)
        if px <= 0:
            continue
        diff = float(weights.get(sym, 0.0)) * budget / px - held.get(sym, 0.0)
        if abs(diff) * px < s.trading_min_order_usd:
            continue
        side = "buy" if diff > 0 else "sell"
        out.append({"client_id": "%s-%s-%s-%s" % (acct["name"], signal_date, sym, side), "signal_date": str(signal_date),
                    "symbol": sym, "side": side, "qty": round(abs(diff), 6), "est_price": px,
                    "reason": "目标权重 %.1f%%" % (float(weights.get(sym, 0.0)) * 100)})
    return sorted(out, key=lambda o: o["side"] != "sell")  # sells first


def risk_check(acct, orders, weights, allowed, sent_today=0.0, count_today=0):
    """Splits orders into approved and rejected, each rejection with its reason. Every limit applies in every mode."""
    s = get_settings()
    approved, rejected = [], []
    total, count = sent_today, count_today
    for o in orders:
        value = o["qty"] * o["est_price"]
        why = None
        if acct["halted"]:
            why = "账户已暂停：%s" % acct["halted"]
        elif o["side"] == "buy" and o["symbol"] not in allowed:
            why = "%s 不在可交易名单里" % o["symbol"]
        elif o["side"] == "buy" and float(weights.get(o["symbol"], 0)) > s.trading_max_weight + 1e-9:
            why = "目标权重超过单只上限 %.0f%%" % (s.trading_max_weight * 100)
        elif value > s.trading_max_order_usd:
            why = "单笔 $%.2f 超过上限 $%.0f" % (value, s.trading_max_order_usd)
        elif total + value > s.trading_max_daily_usd:
            why = "当天成交额将超过上限 $%.0f" % s.trading_max_daily_usd
        elif count + 1 > s.trading_max_orders_per_day:
            why = "当天订单数将超过上限 %d" % s.trading_max_orders_per_day
        if why:
            rejected.append({**o, "reason": why})
        else:
            approved.append(o)
            total, count = total + value, count + 1
    return approved, rejected


def _store(con, acct, orders, status):
    """Keeps orders that are not sent to a broker (rejected or planned) in the order log."""
    with con:
        for o in orders:
            con.execute("INSERT OR IGNORE INTO orders (client_id, account, signal_date, symbol, side, qty, est_price, status, reason,"
                        " created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (o["client_id"], acct["name"], o["signal_date"], o["symbol"], o["side"], o["qty"], o["est_price"], status,
                         o["reason"], now_iso()))


def record_rejected(con, acct, rejected):
    _store(con, acct, rejected, "rejected")
    for o in rejected:
        event(con, acct["name"], "reject", "拒绝 %s %s %.4f：%s" % (o["side"], o["symbol"], o["qty"], o["reason"]))


def record_planned(con, acct, orders):
    _store(con, acct, orders, "planned")


def rebalance(con, acct, weights, prices, signal_date, allowed, data_date, previous=None, load=None, today=None):
    """Plans, checks and (in paper mode) sends the orders for one signal. Returns what happened."""
    name = acct["name"]
    if con.execute("SELECT 1 FROM orders WHERE account = ? AND signal_date = ? LIMIT 1", (name, str(signal_date))).fetchone():
        return {"status": "skipped", "reason": "%s 的信号已经处理过" % signal_date}
    problems = data_problems({k: prices.get(k) for k in set(weights.index)}, previous or {}, data_date, today)
    if problems:
        halt(con, name, "数据检查没通过：" + "；".join(problems[:3]))
        return {"status": "halted", "reason": problems}
    broker = broker_for(con, acct, load)
    state = broker.account()
    orders = orders_for(acct, state, weights, prices, signal_date)
    approved, rejected = risk_check(acct, orders, weights, allowed)
    record_rejected(con, acct, rejected)
    event(con, name, "signal", "%s 信号：目标 %d 只，生成 %d 笔订单，通过 %d 笔，拒绝 %d 笔" % (
        signal_date, int((weights > 0).sum()), len(orders), len(approved), len(rejected)))
    if acct["mode"] == "plan":
        record_planned(con, acct, approved)
        sent = []
    else:
        sent = [o for o in approved if broker.submit(o) == "pending"]
        for o in sent:
            event(con, name, "order", "下单 %s %s %.4f（预估 $%.2f，%s）" % (o["side"], o["symbol"], o["qty"], o["qty"] * o["est_price"], o["reason"]))
    return {"status": "ok", "orders": len(orders), "sent": len(sent), "planned": len(approved) if acct["mode"] == "plan" else 0,
            "rejected": len(rejected)}


def orders(con, name, limit=200):
    cols = ["client_id", "signal_date", "symbol", "side", "qty", "est_price", "status", "reason", "created", "filled_at", "fill_price", "fee"]
    rows = con.execute("SELECT %s FROM orders WHERE account = ? ORDER BY created DESC, client_id LIMIT ?" % ", ".join(cols),
                       (name, limit)).fetchall()
    return [dict(zip(cols, r, strict=True)) for r in rows]


def events(con, name, limit=100):
    return [dict(zip(["at", "kind", "message"], r, strict=True)) for r in con.execute(
        "SELECT at, kind, message FROM events WHERE account = ? ORDER BY id DESC LIMIT ?", (name, limit))]


def equity_curve(con, name):
    rows = con.execute("SELECT date, value FROM equity WHERE account = ? ORDER BY date", (name,)).fetchall()
    return pd.Series([v for _, v in rows], index=pd.to_datetime([d for d, _ in rows]), dtype=float)
