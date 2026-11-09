"""The platform syncs by itself when the ledger changes (a new buy): holdings/ledger.changed and Scheduler.watch."""
import datetime as dt
import json

import pytest

from quantlab.holdings import ledger, review
from quantlab.scheduler import WATCH_EVERY, Scheduler

TRADE = {"date": "2026-11-06", "side": "buy", "symbol": "BAC", "shares": 1, "price": 50, "fees": 0, "currency": "USD"}
SNAP = {"account": "me", "generated": "2026-11-06T00:00:00Z", "trades": [TRADE], "cash": [], "fixed": []}


def test_the_fingerprint_follows_the_records_not_the_time_they_were_read():
    a = ledger.fingerprint(SNAP)
    assert a == ledger.fingerprint({**SNAP, "generated": "2030-01-01T00:00:00Z", "synced": "x", "account": "other"})
    assert a != ledger.fingerprint({**SNAP, "trades": [TRADE, {**TRADE, "symbol": "KO"}]})
    assert a != ledger.fingerprint({**SNAP, "cash": [{"currency": "USD", "amount": 5}]})


def test_changed_saves_a_new_copy_only_when_the_ledger_differs(monkeypatch):
    current = {"snap": SNAP}
    monkeypatch.setattr(ledger, "fetch", lambda: dict(current["snap"]))
    assert ledger.changed() is True                                  # nothing saved yet: a first sync
    saved = json.loads(ledger.snapshot_path().read_text(encoding="utf-8"))
    assert saved["trades"] == [TRADE] and saved["synced"]
    assert ledger.changed() is False                                 # the same records: nothing to do
    current["snap"] = {**SNAP, "trades": [TRADE, {**TRADE, "symbol": "KO"}]}
    assert ledger.changed() is True                                  # a new buy
    assert len(json.loads(ledger.snapshot_path().read_text(encoding="utf-8"))["trades"]) == 2


class Clock:
    def __init__(self):
        self.now = dt.datetime(2026, 11, 9, 3, 0, tzinfo=dt.UTC)

    def __call__(self):
        return self.now


@pytest.fixture
def sched(monkeypatch):
    clock = Clock()
    s = Scheduler(worker=None, load=lambda sym, refresh=False: None, clock=clock)
    s.clock_ = clock
    monkeypatch.setattr(ledger, "configured", lambda: True)
    return s


def test_a_new_buy_triggers_a_sync_and_a_recheck_at_once(sched, monkeypatch):
    runs, state = [], {"changed": True}
    monkeypatch.setattr(ledger, "changed", lambda: state["changed"])
    monkeypatch.setattr(review, "run", lambda refresh=True, load=None, **kw: runs.append(refresh) or {"id": 21, "proposals": []})
    assert sched.watch() == 21 and runs == [False]                   # re-checks the copy it has just saved: no second read
    state["changed"] = False
    sched.clock_.now += WATCH_EVERY + dt.timedelta(seconds=1)
    assert sched.watch() is None and runs == [False]                 # looked, nothing new: no review


def test_it_looks_only_every_few_minutes(sched, monkeypatch):
    looks = []
    monkeypatch.setattr(ledger, "changed", lambda: looks.append(1) or False)
    sched.watch()
    sched.clock_.now += dt.timedelta(minutes=1)
    sched.watch()
    assert len(looks) == 1                                           # the ledger's read key allows 120 reads an hour
    sched.clock_.now += WATCH_EVERY
    sched.watch()
    assert len(looks) == 2


def test_it_stays_out_of_the_way_of_the_daily_job_and_of_a_missing_key(sched, monkeypatch):
    monkeypatch.setattr(ledger, "changed", lambda: pytest.fail("should not look"))
    sched._busy.acquire()
    assert sched.watch() is None                                     # the daily job is running: it syncs itself
    sched._busy.release()
    sched._next_watch = None
    monkeypatch.setattr(ledger, "configured", lambda: False)
    assert sched.watch() is None                                     # no ledger key: nothing to watch


def test_an_unreachable_ledger_is_tried_again_not_fatal(sched, monkeypatch):
    from quantlab.errors import DataSourceError

    def down():
        raise DataSourceError("连不上账本")
    monkeypatch.setattr(ledger, "changed", down)
    assert sched.watch() is None                                     # logged, and the next look is three minutes later
    assert sched._next_watch is not None and not sched._busy.locked()
