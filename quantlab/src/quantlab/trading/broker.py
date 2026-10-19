"""Brokers. The trading engine only talks to the Broker interface; a connector for a real broker implements the
same four methods. PaperBroker is the built-in simulated broker.

Orders are market-on-open: sent before a session, filled at that session's opening price. PaperBroker fills a
pending order once the opening price of the first session after it was sent is known, at that price plus or
minus slippage, sells before buys, and never lets cash go below zero."""
import datetime as dt
from typing import Protocol

from .. import storage
from ..config import get_settings
from ..core import data

MIGRATIONS = [
    """CREATE TABLE accounts (name TEXT PRIMARY KEY, broker TEXT NOT NULL, mode TEXT NOT NULL, capital REAL NOT NULL,
                              cash REAL NOT NULL, peak_equity REAL NOT NULL, halted TEXT, created TEXT NOT NULL);
       CREATE TABLE positions (account TEXT NOT NULL, symbol TEXT NOT NULL, qty REAL NOT NULL, PRIMARY KEY (account, symbol));
       CREATE TABLE orders (client_id TEXT PRIMARY KEY, account TEXT NOT NULL, signal_date TEXT NOT NULL, symbol TEXT NOT NULL,
                            side TEXT NOT NULL, qty REAL NOT NULL, est_price REAL NOT NULL, status TEXT NOT NULL, reason TEXT,
                            created TEXT NOT NULL, filled_at TEXT, fill_price REAL, fee REAL NOT NULL DEFAULT 0);
       CREATE INDEX orders_account ON orders (account, status);
       CREATE TABLE equity (account TEXT NOT NULL, date TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY (account, date));
       CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL,
                            message TEXT NOT NULL)""",
]


def connect(path=None):
    return storage.connect(path or get_settings().data_dir / "trading.db", MIGRATIONS)


def now_iso():
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def event(con, account, kind, message):
    """The audit log: every decision, order, fill, rejection and halt, in order."""
    with con:
        con.execute("INSERT INTO events (account, at, kind, message) VALUES (?, ?, ?, ?)", (account, now_iso(), kind, message))


class Broker(Protocol):
    name: str

    def account(self) -> dict: ...                  # {"cash": float, "positions": {symbol: qty}}
    def submit(self, order: dict) -> str: ...       # returns the order's status after submission
    def open_orders(self) -> list: ...
    def settle(self, load=None) -> list: ...        # fills that happened since the last call


class PaperBroker:
    name = "paper"

    def __init__(self, con, account, load=None):
        self.con, self.acct, self.load = con, account, load or data.load

    def account(self):
        cash = self.con.execute("SELECT cash FROM accounts WHERE name = ?", (self.acct,)).fetchone()[0]
        pos = dict(self.con.execute("SELECT symbol, qty FROM positions WHERE account = ? AND qty != 0", (self.acct,)).fetchall())
        return {"cash": cash, "positions": pos}

    def submit(self, order):
        with self.con:
            cur = self.con.execute(
                "INSERT OR IGNORE INTO orders (client_id, account, signal_date, symbol, side, qty, est_price, status, reason, created)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (order["client_id"], self.acct, order["signal_date"], order["symbol"], order["side"], order["qty"],
                 order["est_price"], order.get("reason", ""), now_iso()))
        return "pending" if cur.rowcount else "duplicate"  # the same client id is never sent twice

    def open_orders(self):
        cols = ["client_id", "signal_date", "symbol", "side", "qty", "est_price"]
        rows = self.con.execute("SELECT %s FROM orders WHERE account = ? AND status = 'pending' ORDER BY side DESC, client_id"
                                % ", ".join(cols), (self.acct,)).fetchall()
        return [dict(zip(cols, r, strict=True)) for r in rows]

    def settle(self, load=None):
        """Fills pending orders at the open of the first session after their signal date, once it is known."""
        s, load = get_settings(), load or self.load
        slip, fills = s.trading_slippage_bps / 10_000, []
        for o in self.open_orders():  # sells first ('sell' > 'buy'), so their cash pays for the buys
            bars = load(o["symbol"])
            after = bars[bars.index > o["signal_date"]]
            if after.empty:
                continue  # that session has not opened yet
            day, px = after.index[0], float(after["open"].iloc[0])
            price = px * (1 + slip) if o["side"] == "buy" else px * (1 - slip)
            acct = self.account()
            qty, note = o["qty"], ""
            if o["side"] == "sell":
                qty = min(qty, acct["positions"].get(o["symbol"], 0.0))
            else:
                affordable = max(0.0, (acct["cash"] - s.trading_fee) / price)
                if qty > affordable:
                    qty, note = affordable, "现金不足，按可用现金部分成交"
            cash_delta = (-qty * price if o["side"] == "buy" else qty * price) - s.trading_fee
            signed = qty if o["side"] == "buy" else -qty
            with self.con:
                self.con.execute("UPDATE accounts SET cash = cash + ? WHERE name = ?", (cash_delta, self.acct))
                self.con.execute("INSERT INTO positions VALUES (?, ?, ?) ON CONFLICT (account, symbol) DO UPDATE SET qty = qty + ?",
                                 (self.acct, o["symbol"], signed, signed))
                self.con.execute("UPDATE orders SET status = 'filled', qty = ?, fill_price = ?, fee = ?, filled_at = ?, reason = TRIM(reason || ' ' || ?)"
                                 " WHERE client_id = ?", (qty, price, s.trading_fee, day.date().isoformat(), note, o["client_id"]))
            event(self.con, self.acct, "fill", "%s %s %.4f @ %.2f（%s 开盘）%s" % (o["side"], o["symbol"], qty, price, day.date(), note))
            fills.append({**o, "qty": qty, "fill_price": price, "date": day.date()})
        return fills
