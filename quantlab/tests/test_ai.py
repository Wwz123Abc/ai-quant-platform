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
from quantlab.errors import BudgetExceeded, Conflict, QuantLabError, Unavailable
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
    ("2026-10-19 09:00", None, False),           # Monday
    ("2026-10-21 09:00", "2026-10-20", False),   # Wednesday: once a week, Tuesday only
    ("2026-10-24 09:00", "2026-10-20", False),   # Saturday
    ("2026-10-27 09:00", "2026-10-20", True),    # the next Tuesday
])
def test_is_due(local, last, due):
    now = dt.datetime.fromisoformat(local).replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    assert is_due(now, last, "07:30") is due


def test_scheduler_waits_for_the_latest_close(monkeypatch):
    """Tencent's bar for Friday's close arrives some hours after it; the job waits for it, then gives up at noon."""
    market = {"QQQ": bars(end="2026-10-23")}            # Friday's close only
    calls = []

    def load(sym, refresh=False):
        calls.append((sym, refresh))
        return market[data.normalize(sym)]

    beijing = lambda hm: dt.datetime.fromisoformat("2026-10-27 %s" % hm).replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    sched = Scheduler(Worker(analyze=fake_analyze()), load=load, market=lambda: "2026-10-26")
    sched.run_daily = lambda now=None: calls.append(("ran", now))
    get_settings.cache_clear()
    ledger.set_meta(ledger.connect(), "daily_last", "2026-10-20")

    sched.clock = lambda: beijing("09:00")
    sched.tick()
    assert ("QQQ", True) in calls and not [c for c in calls if c[0] == "ran"]   # downloaded afresh, still Friday
    calls.clear()
    sched.clock = lambda: beijing("09:20")
    sched.tick()
    assert calls == []                                    # checks at most every half hour
    market["QQQ"] = bars(end="2026-10-26")                # Monday's bar has arrived
    sched.clock = lambda: beijing("09:31")
    sched.tick()
    assert calls[-1][0] == "ran"

    market["QQQ"] = bars(end="2026-10-23")
    late = Scheduler(Worker(analyze=fake_analyze()), load=load, market=lambda: "2026-10-26")
    late.run_daily = lambda now=None: calls.append(("ran", now))
    late.clock = lambda: beijing("12:00")
    calls.clear()
    late.tick()
    assert calls[-1][0] == "ran"                          # noon: runs anyway


def test_daily_job_downloads_each_symbol_afresh_and_notes_old_bars(monkeypatch):
    monkeypatch.setattr(engine, "available", lambda: False)
    market = {"QQQ": bars(end="2026-10-22"), "VOO": bars(end="2026-10-22", seed=2)}
    calls = []

    def load(sym, refresh=False):
        calls.append((data.normalize(sym), refresh))
        return market[data.normalize(sym)]

    pc = paper.connect()
    paper.create(pc, "MA", ["VOO"], {"fast": 20, "slow": 60}, load=load)
    pc.close()
    calls.clear()
    sched = Scheduler(Worker(analyze=fake_analyze()), load=load, market=lambda: "2026-10-23")
    sched._probe = ("2026-10-22", "2026-10-23")           # what a noon check found
    result = sched.run_daily(NOW)
    assert [c for c in calls if c[0] == "VOO"][0] == ("VOO", True)          # the first use of a symbol refreshes it
    assert any("2026-10-23" in n and "2026-10-22" in n for n in result["notes"])


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
    assert result["trading"] is None  # the trading step is off unless QUANTLAB_TRADING_DAILY is set

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


def test_a_run_is_claimed_once_across_connections(con):
    rid = ledger.enqueue(con, "QQQM", "2026-10-16", now=NOW)
    other = ledger.connect()
    first, second = ledger.claim_next(con, NOW), ledger.claim_next(other, NOW)
    other.close()
    assert first["id"] == rid and second is None


def test_summary_while_first_ai_decision_waits():
    market = bars(300, end="2026-10-16")
    load = lambda sym, refresh=False: market
    pc = paper.connect()
    paper.create(pc, "AI", ["QQQM", "VOO"], {"every_days": 1}, kind="ai_rating", load=load)
    acct = paper.get_account(pc, "AI")
    ai = FakeAi()
    ai.r.append(("2026-10-16", "Buy"))
    ai.p.add(("VOO", "2026-10-16"))
    paper.record(pc, acct, load, ai=ai)
    s = paper.summarize(acct, paper.replay(pc, acct, load))
    by = {p["symbol"]: p for p in s["positions"]}
    assert by["QQQM"]["decision"] is True and by["VOO"]["decision"] is None and by["VOO"]["decision_date"] is None
    assert [o["symbol"] for o in s["pending"]] == ["QQQM"]
    pc.close()


def test_figures_missing_from_the_fetched_data_are_marked():
    from quantlab.ai import personal
    sources = ["Close 2026-10-16: 612.34  50 SMA: 598.71\nPE ratio 27.4  dividend yield 0.0123\nVolume 1,234,567"]
    reports = {"market_report": {"title": "技术", "text":
        "收盘价 $612.34，50 日均线 598.71；市盈率 27.4；股息率 1.23%；前五大权重 30.8%，盈利收益率约 4.5%；"
        "2026-10-16 共 3 个信号；成交 1,234,567 股；市值 $1.2万亿。"}}
    personal.mark_unsourced(reports, sources)
    text = reports["market_report"]["text"]
    assert "30.8%、4.5%" in text.split("找不到")[1]
    for ok in ("612.34", "598.71", "27.4", "1.23%"):
        assert ok not in text.split("找不到")[1]
    assert "1.2" not in text.split("找不到")[1]  # a figure with a scale unit is not checked
    # no recorded tool output: nothing can be said, so the report is left alone
    again = {"x": {"title": "t", "text": "约 4.5%"}}
    assert personal.mark_unsourced(again, [])["x"]["text"] == "约 4.5%"


def test_personal_summary_has_rating_conclusion_and_levels_from_our_data():
    import pandas as pd

    from quantlab.ai import personal
    close = pd.Series([100.0 + i * 0.1 for i in range(300)], index=pd.bdate_range(end="2026-10-16", periods=300))
    lv = personal.levels(close)
    assert lv["date"] == "2026-10-16" and lv["high"] == lv["close"] and "ma200" in lv
    text = personal.summary("Underweight", "## 最终决定\n\n评级：减持\n\n建议把仓位降到目标以下，因为估值偏高且趋势转弱，等待回调再看。\n", lv)
    assert "减持" in text and "估值偏高" in text and "200 日线" in text and "评级：减持" not in text.split("理由")[1]


def test_usage_collects_tool_outputs():
    u = engine.Usage()
    u.add_tool_output("Close 612.34")
    assert u.tool_outputs == ["Close 612.34"]


def test_vendor_outputs_are_recorded_only_while_an_analysis_runs():
    from quantlab.ai import cn_vendor
    u = engine.Usage()
    fetch = cn_vendor._recorded(lambda x: "Close 612.34 " + x)
    assert fetch("a") == "Close 612.34 a" and u.tool_outputs == []     # nothing is listening
    with cn_vendor.record_into(u):
        fetch("b")
    fetch("c")                                                          # listening stopped with the analysis
    assert u.tool_outputs == ["Close 612.34 b"]


def test_the_summary_conclusion_drops_the_markdown_label():
    from quantlab.ai import personal
    text = ("**Rating**: Underweight\n\n**Executive Summary**: 对 QQQM 给出 Underweight：把敞口调降至约 70%，分批、限价、不追跌，"
            "并且这一句要足够长。\n\n**Thesis**: 其他")
    assert personal._conclusion(text).startswith("对 QQQM 给出 Underweight")


def test_automatic_analysis_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(engine, "available", lambda: True)
    monkeypatch.setattr(engine, "configured", lambda: True)
    market = bars(300, seed=1)
    load = lambda sym, refresh=False: market
    pc, ac = paper.connect(), ledger.connect()
    paper.create(pc, "AI", ["QQQM"], {"every_days": 5}, kind="ai_rating", load=load)
    monkeypatch.setenv("QUANTLAB_AI_AUTO_ANALYSIS", "false")
    get_settings.cache_clear()
    try:
        queued, notes = queue_due_analyses(pc, ac, load, NOW)
        assert queued == [] and "已关闭" in notes[0]
        assert ledger.runs(ac) == []                                  # nothing was queued, nothing is spent
    finally:
        monkeypatch.delenv("QUANTLAB_AI_AUTO_ANALYSIS")
        get_settings.cache_clear()
        pc.close()
        ac.close()


# ---------------------------------------------------------------- the weekly run for the stocks you hold

class FakeHeld:
    """Stands in for holdings.ledger: a key is set and the ledger says what is held."""

    def __init__(self, monkeypatch, symbols, fail=False):
        from quantlab.holdings import ledger as hl
        monkeypatch.setattr(hl, "configured", lambda: True)

        def load(refresh=False):
            if fail:
                raise QuantLabError("账本连不上")
            return {"trades": [{"date": "2026-10-01", "side": "buy", "symbol": s, "shares": 1, "price": 10} for s in symbols]}
        monkeypatch.setattr(hl, "load", load)


def test_every_held_us_stock_is_queued_each_week_and_a_repeat_of_the_same_close_is_not(monkeypatch):
    monkeypatch.setattr(engine, "available", lambda: True)
    monkeypatch.setattr(engine, "configured", lambda: True)
    FakeHeld(monkeypatch, ["QQQM", "BAC", "0700"])
    market = {"QQQM": bars(300, seed=1), "BAC": bars(40, seed=2)}
    load = lambda sym, refresh=False: market[data.normalize(sym)]
    from quantlab.scheduler import queue_holdings
    ac = ledger.connect()
    q, notes = queue_holdings(ac, load, NOW)
    assert len(q) == 2                                              # QQQM and BAC; the Hong Kong code is skipped
    assert any("0700" in n and "不是美股代码" in n for n in notes)
    q2, notes2 = queue_holdings(ac, load, NOW)                      # same close again: nothing new is paid for
    assert q2 == [] and any("已经" in n for n in notes2)


def test_held_stock_analyses_have_their_own_switch_and_survive_a_broken_ledger(monkeypatch):
    monkeypatch.setattr(engine, "available", lambda: True)
    monkeypatch.setattr(engine, "configured", lambda: True)
    from quantlab.scheduler import queue_holdings
    ac = ledger.connect()
    load = lambda sym, refresh=False: bars(300, seed=1)
    FakeHeld(monkeypatch, ["QQQM"], fail=True)
    q, notes = queue_holdings(ac, load, NOW)
    assert q == [] and "读不到账本" in notes[0]
    FakeHeld(monkeypatch, ["QQQM"])
    monkeypatch.setenv("QUANTLAB_AI_WEEKLY_HOLDINGS", "false")
    get_settings.cache_clear()
    q, notes = queue_holdings(ac, load, NOW)
    assert q == [] and "已关闭" in notes[0]


def test_the_job_does_not_run_blind_when_the_data_source_is_down_and_runs_anyway_from_the_give_up_time():
    """2026-11-10: Tencent answered 501 at 10:25, the probe could not be read, the job ran at once and every symbol failed."""
    beijing = lambda hm: dt.datetime.fromisoformat("2026-11-10 %s" % hm).replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))

    def down(sym, refresh=False):
        raise QuantLabError("行情接口返回错误 501")
    sched = Scheduler(Worker(analyze=fake_analyze()), load=down, market=lambda: "2026-11-09")
    assert sched.data_ready(beijing("10:25")) is False                  # waits, as for an old bar
    assert sched.data_ready(beijing("10:40")) is False                  # and does not hammer the source: the next look is 30 minutes on
    assert sched.data_ready(beijing("12:00")) is True                   # the give-up time: run anyway


def test_the_cash_stock_gets_no_weekly_research_report(monkeypatch):
    monkeypatch.setattr(engine, "available", lambda: True)
    monkeypatch.setattr(engine, "configured", lambda: True)
    FakeHeld(monkeypatch, ["QQQM", "SGOV"])
    from quantlab.scheduler import queue_holdings
    ac = ledger.connect()
    q, notes = queue_holdings(ac, lambda sym, refresh=False: bars(300, seed=1), NOW)
    assert len(q) == 1 and not any("SGOV" in n for n in notes)
