"""The daily trading step, run by the scheduler after the paper accounts are recorded.

1. Settle: fill orders whose opening price is now known.
2. Mark: record each account's value at the latest close; halt an account that fell past the drawdown limit.
3. Rebalance, once a month: when the latest close is the last trading day of its month (or the previous month's
   signal was missed), compute the machine-learning target portfolio and send each account its orders."""
import logging
import threading

import pandas as pd

from ..core import data
from . import engine
from .broker import connect

log = logging.getLogger(__name__)
_running = threading.Lock()  # the scheduler and a manual run never trade at the same time


def is_month_end(day):
    return (day + pd.offsets.BDay(1)).month != day.month


def needs_signal(con, day):
    """True on a month's last trading day, or when no signal has been taken for the month before this one."""
    last = con.execute("SELECT MAX(signal_date) FROM orders").fetchone()[0]
    if is_month_end(day):
        return last is None or last < day.date().isoformat()
    prev_month_end = (day.replace(day=1) - pd.Timedelta(days=1)).date().isoformat()[:7]
    return last is not None and last[:7] < prev_month_end  # a holiday hid the month end: catch up now


def busy():
    return _running.locked()


def run(load=None, signal_fn=None, force=False, today=None):
    """Runs the step for every account. Returns a summary per account, or None if a run is already in progress."""
    if not _running.acquire(blocking=False):
        return None
    try:
        return _run(load, signal_fn, force, today)
    finally:
        _running.release()


def _run(load, signal_fn, force, today):
    load = load or data.load
    con = connect()
    try:
        accts = engine.accounts(con)
        if not accts:
            return {}
        out = {a["name"]: {} for a in accts}
        for a in accts:
            out[a["name"]]["filled"] = len(engine.broker_for(con, a, load).settle(load))

        qqq = load("QQQ")
        day = qqq.index[-1]
        held = {s for (s,) in con.execute("SELECT DISTINCT symbol FROM positions WHERE qty != 0")}
        prices = {s: float(load(s)["close"].iloc[-1]) for s in held}
        for a in accts:
            a = engine.get_account(con, a["name"])
            out[a["name"]]["equity"] = round(engine.mark(con, a, engine.broker_for(con, a, load), prices, day.date()), 2)

        if not (force or needs_signal(con, day)):
            return out
        if signal_fn is None:
            from ..ml import signal as ml_signal
            signal_fn = ml_signal.latest
        sig = signal_fn()
        w = sig["weights"]
        frames = {s: load(s) for s in set(w.index) | held}
        closes = {s: float(f["close"].iloc[-1]) for s, f in frames.items()}
        previous = {s: float(f["close"].iloc[-2]) for s, f in frames.items() if len(f) > 1}
        from ..ml import universe
        allowed = set(universe.load()["symbols"])
        for a in accts:
            a = engine.get_account(con, a["name"])
            out[a["name"]]["rebalance"] = engine.rebalance(con, a, w, closes, sig["signal_date"], allowed, sig["data_date"],
                                                           previous, load, today)
        return out
    finally:
        con.close()
