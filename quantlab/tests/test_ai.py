import datetime as dt

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from quantlab.ai import engine, ledger
from quantlab.ai.jobs import Worker
from quantlab.api.app import create_app
from quantlab.config import get_settings
from quantlab.core import data, paper
from quantlab.errors import BudgetExceeded, Conflict, Unavailable
from quantlab.scheduler import Scheduler, is_due, queue_due_analyses

AUTH = {"Authorization": "Bearer test-token"}
PRICES = {"deepseek-flash": [0.006, 0.30, 1.20], "deepseek-v4-pro": [0.044, 1.32, 3.96]}
NOW = dt.datetime(2026, 10, 20, 0, 0, tzinfo=dt.UTC)


def bars(n=300, end="2026-10-16", seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, n)))
    idx = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 0}, index=idx)


def fake_analyze(rating="Buy", tokens=(100_000, 40_000, 5_000), fail=False):
    def analyze(symbol, trade_date, usage, s=None):
        usage.add("deepseek-flash", *tokens)
        if fail:
            raise RuntimeError("provider down")
        return rating, {"final_trade_decision": {"title": "最终决定", "text": "**%s** %s" % (rating, symbol)}}
    return analyze


@pytest.fixture
def con():
    c = ledger.connect()
    yield c
    c.close()


# ---------------------------------------------------------------- cost

def test_price_lookup():
    assert engine.price_for("deepseek-flash", PRICES) == [0.006, 0.30, 1.20]
    assert engine.price_for("deepseek-v4.1-flash-1017", PRICES) == [0.006, 0.30, 1.20]  # by family
    assert engine.price_for("deepseek-v4-pro-0813", PRICES) == [0.044, 1.32, 3.96]
    assert engine.price_for("mystery-model", PRICES) == [0.044, 1.32, 3.96]  # unknown: the highest rates


def test_usage_cost_counts_cached_input_separately():
    u = engine.Usage()
    u.add("deepseek-flash", 1_000_000, 400_000, 100_000)
    u.add("deepseek-flash", 0, 0, 0)
    t = u.totals(PRICES)
    assert t["llm_calls"] == 2 and t["tokens_in"] == 1_000_000 and t["tokens_cached"] == 400_000
    assert t["cost_usd"] == pytest.approx(0.4 * 0.006 + 0.6 * 0.30 + 0.1 * 1.20)


# ---------------------------------------------------------------- ledger and budget

def test_one_run_per_symbol_and_day_unless_it_failed(con):
    rid = ledger.enqueue(con, "QQQM", "2026-10-16", now=NOW)
    with pytest.raises(Conflict):
        ledger.enqueue(con, "QQQM", "2026-10-16", now=NOW)
    ledger.claim_next(con, NOW)
    ledger.fail(con, rid, "boom")
    assert ledger.enqueue(con, "QQQM", "2026-10-16", now=NOW) != rid


def test_budget_counts_spending_and_queued_runs(con, monkeypatch):
    monkeypatch.setenv("QUANTLAB_AI_FIRST_ESTIMATE_CNY", "40")
    get_settings.cache_clear()
    ledger.enqueue(con, "QQQM", "2026-10-16", now=NOW)
    ledger.enqueue(con, "VOO", "2026-10-16", now=NOW)        # 40 + 40 <= 100
    with pytest.raises(BudgetExceeded):
        ledger.enqueue(con, "SPY", "2026-10-16", now=NOW)    # 40 + 40 + 40 > 100
    b = ledger.budget(con, NOW)
    assert b["queued"] == 2 and b["spent_cny"] == 0 and b["remaining_cny"] == pytest.approx(20)


def test_spending_is_per_calendar_month(con):
    usage = {"llm_calls": 1, "tokens_in": 0, "tokens_cached": 0, "tokens_out": 0, "cost_usd": 2.0}
    rid = ledger.enqueue(con, "QQQM", "2026-09-30", now=NOW - dt.timedelta(days=25))
    ledger.claim_next(con, NOW - dt.timedelta(days=25))
    ledger.finish(con, rid, "Buy", {}, usage)
    assert ledger.spent_usd(con, NOW - dt.timedelta(days=25)) == 2.0
    assert ledger.spent_usd(con, NOW) == 0.0
    assert ledger.estimate_usd(con) == 2.0  # measured runs replace the first guess


def test_recover_marks_interrupted_runs_failed(con):
    rid = ledger.enqueue(con, "QQQM", "2026-10-16", now=NOW)
    ledger.claim_next(con, NOW)
    assert ledger.recover(con) == [rid]
    assert ledger.get(con, rid)["status"] == "failed"


# ---------------------------------------------------------------- worker

def test_worker_records_result_and_cost(con):
    rid = ledger.enqueue(con, "QQQM", "2026-10-16", now=NOW)
    assert Worker(analyze=fake_analyze("Overweight")).run_next()
    run = ledger.get(con, rid)
    assert run["status"] == "done" and run["rating"] == "Overweight"
    assert run["reports"]["final_trade_decision"]["text"].startswith("**Overweight**")
    assert run["tokens_in"] == 100_000 and run["cost_usd"] == pytest.approx((40_000 * 0.006 + 60_000 * 0.30 + 5_000 * 1.20) / 1e6)
    assert not Worker(analyze=fake_analyze()).run_next()  # queue empty


def test_failed_run_keeps_the_tokens_it_used(con):
    rid = ledger.enqueue(con, "QQQM", "2026-10-16", now=NOW)
    Worker(analyze=fake_analyze(fail=True)).run_next()
    run = ledger.get(con, rid)
    assert run["status"] == "failed" and "provider down" in run["error"] and run["cost_usd"] > 0


def test_worker_rechecks_budget_before_running(con, monkeypatch):
    rid = ledger.enqueue(con, "QQQM", "2026-10-16", now=NOW)
    monkeypatch.setenv("QUANTLAB_AI_BUDGET_CNY", "0.5")  # lowered after the run was queued
    get_settings.cache_clear()
    called = []
    Worker(analyze=lambda *a: called.append(a)).run_next()
    assert not called and ledger.get(con, rid)["status"] == "failed"


# ---------------------------------------------------------------- AI paper accounts

def test_rating_to_position():
    b = bars(10, end="2026-10-16")
    ratings = [(b.index[2].date().isoformat(), "Buy"), (b.index[4].date().isoformat(), "Hold"),
               (b.index[6].date().isoformat(), "Sell"), (b.index[8].date().isoformat(), "REVIEW")]
    states = [paper.decide_ai(b.iloc[: i + 1], {}, {"ratings": ratings})[0] for i in range(10)]
    assert states == [0, 0, 1, 1, 1, 1, 0, 0, 0, 0]
    _, detail = paper.decide_ai(b.iloc[:1], {}, {"ratings": ratings})
    assert "还没有" in detail["note"]


class FakeAi:
    def __init__(self):
        self.r, self.p = [], set()

    def ratings(self, sym):
        return list(self.r)

    def pending(self, sym, day):
        return (sym, day) in self.p

    def close(self):
        pass


def test_ai_account_waits_for_pending_analysis_and_never_looks_ahead():
    market = bars(300, end="2026-10-16")
    load = lambda sym, refresh=False: market
    pc = paper.connect()
    paper.create(pc, "AI", ["QQQM"], {"every_days": 7}, kind="ai_rating", load=load)
    acct = paper.get_account(pc, "AI")
    ai = FakeAi()
    ai.p.add(("QQQM", "2026-10-16"))
    assert paper.record(pc, acct, load, ai=ai) == []            # waits for the analysis of the start day
    ai.p.clear()
    ai.r.append(("2026-10-16", "Buy"))
    new = paper.record(pc, acct, load, ai=ai)
    assert [(n["date"], n["target"], n["rating"]) for n in new] == [("2026-10-16", 1, "Buy")]
    # a later rating cannot change a decision already taken
    ai.r.append(("2026-10-16", "Sell"))
    assert paper.record(pc, acct, load, ai=ai) == []
    assert paper.signals(pc, "AI", "QQQM")["target"].tolist() == [1]
    pc.close()


def test_ai_account_validation():
    pc = paper.connect()
    load = lambda sym, refresh=False: bars()
    with pytest.raises(ValueError):
        paper.create(pc, "AI", ["QQQM"], {"every_days": 0}, kind="ai_rating", load=load)
    pc.close()


# ---------------------------------------------------------------- scheduler

@pytest.mark.parametrize("local,last,due", [
    ("2026-10-20 07:31", None, True),            # Tuesday after the time
    ("2026-10-20 07:29", None, False),           # too early
    ("2026-10-20 08:00", "2026-10-20", False),   # already ran today
    ("2026-10-19 09:00", None, False),           # Monday: no US close since Saturday's run
    ("2026-10-24 09:00", "2026-10-23", True),    # Saturday: Friday's close
])
def test_is_due(local, last, due):
    now = dt.datetime.fromisoformat(local).replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    assert is_due(now, last, "07:30") is due


def test_scheduler_queues_due_analyses_then_records(monkeypatch):
    monkeypatch.setattr(engine, "available", lambda: True)
    monkeypatch.setattr(engine, "configured", lambda: True)
    market = {"QQQM": bars(300, seed=1), "VOO": bars(300, seed=2)}
    load = lambda sym, refresh=False: market[data.normalize(sym)]
    pc = paper.connect()
    paper.create(pc, "AI", ["QQQM"], {"every_days": 7}, kind="ai_rating", load=load)
    paper.create(pc, "MA", ["VOO"], {"fast": 20, "slow": 60}, load=load)
    pc.close()

    worker = Worker(analyze=fake_analyze("Buy"))
    worker.wake = lambda: None
    worker.wait_idle = lambda timeout=None: (worker.run_next(), True)[1]  # run the queue synchronously
    result = Scheduler(worker, load=load).run_daily(NOW)
    assert len(result["queued"]) == 1 and result["recorded"] == {"AI": 1, "MA": 1}

    ac, pc = ledger.connect(), paper.connect()
    assert ledger.get_meta(ac, "daily_last") == "2026-10-20"
    assert paper.signals(pc, "AI", "QQQM")["target"].tolist() == [1]
    q, notes = queue_due_analyses(pc, ac, load, NOW)  # analysed today: not due again for 7 days
    assert q == [] and notes == []
    ac.close()
    pc.close()


# ---------------------------------------------------------------- API

@pytest.fixture
def ai_client(monkeypatch):
    market = bars(300)
    monkeypatch.setattr(data, "load", lambda sym, refresh=False: market)
    worker = Worker(analyze=fake_analyze("Underweight"))
    with TestClient(create_app(worker=worker)) as c:
        c.worker = worker
        yield c


def test_ai_status(ai_client):
    s = ai_client.get("/api/ai/status", headers=AUTH).json()
    assert s["provider"] == "deepseek" and s["budget"]["budget_cny"] == 100
    assert s["schedule"]["enabled"] is False and s["schedule"]["last_run"] is None


def test_ai_run_needs_installation_and_key(ai_client, monkeypatch):
    monkeypatch.setattr(engine, "available", lambda: False)
    r = ai_client.post("/api/ai/runs", json={"symbol": "QQQM"}, headers=AUTH)
    assert r.status_code == 503 and "安装" in r.json()["detail"]
    monkeypatch.setattr(engine, "available", lambda: True)
    monkeypatch.setattr(engine, "configured", lambda: False)
    r = ai_client.post("/api/ai/runs", json={"symbol": "QQQM"}, headers=AUTH)
    assert r.status_code == 503 and "密钥" in r.json()["detail"]
    with pytest.raises(Unavailable):
        engine.require_ready()


def test_ai_run_lifecycle(ai_client, monkeypatch):
    monkeypatch.setattr(engine, "available", lambda: True)
    monkeypatch.setattr(engine, "configured", lambda: True)
    r = ai_client.post("/api/ai/runs", json={"symbol": "qqqm"}, headers=AUTH)
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["symbol"] == "QQQM" and run["trade_date"] == "2026-10-16" and run["status"] in ("queued", "running", "done")
    assert ai_client.post("/api/ai/runs", json={"symbol": "QQQM"}, headers=AUTH).status_code == 409
    ai_client.worker.wait_idle(10)
    done = ai_client.get("/api/ai/runs/%d" % run["id"], headers=AUTH).json()
    assert done["status"] == "done" and done["rating"] == "Underweight" and done["cost_cny"] > 0
    assert done["reports"]["final_trade_decision"]["title"] == "最终决定"
    listed = ai_client.get("/api/ai/runs", headers=AUTH).json()
    assert listed[0]["id"] == run["id"] and listed[0]["reports"] is None
    assert ai_client.get("/api/ai/runs/999", headers=AUTH).status_code == 404


def test_create_ai_account(ai_client):
    body = {"name": "AI", "symbols": ["QQQM"], "strategy": "ai_rating", "every_days": 5}
    r = ai_client.post("/api/paper/accounts", json=body, headers=AUTH)
    assert r.status_code == 201, r.text
    assert r.json()["strategy"] == "ai_rating" and r.json()["params"] == {"every_days": 5}


def test_key_name_without_tradingagents(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_tradingagents(name, *args, **kwargs):
        if name.startswith("tradingagents"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_tradingagents)
    assert engine.key_env("deepseek") == "DEEPSEEK_API_KEY"
