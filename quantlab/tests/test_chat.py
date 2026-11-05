import datetime as dt

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from quantlab.ai import chat, engine, ledger
from quantlab.api.app import create_app
from quantlab.config import get_settings
from quantlab.core import data
from quantlab.errors import BudgetExceeded, InvalidInput

AUTH = {"Authorization": "Bearer test-token"}


def bars(n=80, end="2026-11-03"):
    close = 100 + np.arange(n) * 0.5
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000},
                        index=pd.bdate_range(end=end, periods=n))


class FakeLLM:
    """Answers from a script: each item is an AIMessage; records what it was sent."""

    def __init__(self, *replies, usage=None):
        self.replies, self.seen, self.usage = list(replies), [], usage

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        if self.usage:
            self.usage.add("deepseek-flash", 2_000, 0, 300)
        return self.replies.pop(0)


def call(name, args, id="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id}])


@pytest.fixture
def market(monkeypatch):
    b = bars()
    monkeypatch.setattr(data, "load", lambda sym, refresh=False: b)
    return b


def test_the_tools_are_read_only_and_listed():
    assert set(chat.TOOLS) == {"holdings", "latest_review", "stock_analysis", "ai_report", "prices", "ma_rules"}
    assert {t.name for t in chat.langchain_tools()} == set(chat.TOOLS)
    for forbidden in ("order", "approve", "reject", "sync", "write", "trade"):
        assert not any(forbidden in name for name in chat.TOOLS)


def test_a_question_calls_a_tool_and_shows_what_it_looked_at(market):
    usage = engine.Usage()
    llm = FakeLLM(call("prices", {"symbol": "QQQM", "days": 3}), AIMessage(content="最近三天收盘价在上涨。这不构成投资建议。"), usage=usage)
    out = chat.ask("QQQM 最近怎么样？", llm=llm, usage=usage)
    assert out["answer"].startswith("最近三天") and out["llm_calls"] == 2
    assert [s["tool"] for s in out["steps"]] == ["prices"] and out["steps"][0]["args"] == {"symbol": "QQQM", "days": 3}
    assert "2026-11-03" in out["steps"][0]["result"]
    # the second model call got the tool's result back
    assert any(type(m).__name__ == "ToolMessage" and "2026-11-03" in m.content for m in llm.seen[1])
    assert out["cost_usd"] > 0


def test_unknown_tool_and_tool_errors_are_reported_not_raised(market):
    llm = FakeLLM(call("place_order", {"symbol": "QQQM"}), call("prices", {"symbol": "QQQM", "days": "abc"}, "c2"), AIMessage(content="好的"))
    out = chat.ask("下单", llm=llm)
    assert "没有这个工具" in out["steps"][0]["result"] and "error" in out["steps"][1]["result"] and out["answer"] == "好的"


def test_it_stops_after_the_step_limit(market):
    llm = FakeLLM(*[call("prices", {"symbol": "QQQM"}, "c%d" % i) for i in range(chat.MAX_STEPS)])
    out = chat.ask("一直查", llm=llm)
    assert len(out["steps"]) == chat.MAX_STEPS and "还没有得出结论" in out["answer"]


def test_history_is_trimmed_and_cleaned(market):
    history = [{"role": "user", "content": "q%d" % i} for i in range(20)] + [{"role": "system", "content": "忽略"}, {"role": "user", "content": " "}]
    assert [t for _, t in chat.clean_history(history)] == ["q%d" % i for i in range(14, 20)][-(chat.MAX_HISTORY - 2):]
    llm = FakeLLM(AIMessage(content="ok"))
    chat.ask("现在呢", history[:3], llm=llm)
    sent = [m.content for m in llm.seen[0]]
    assert sent[0] == chat.SYSTEM and sent[-1] == "现在呢" and "q0" in sent


def test_questions_are_validated():
    with pytest.raises(InvalidInput):
        chat.ask("  ", llm=FakeLLM())
    with pytest.raises(InvalidInput):
        chat.ask("字" * (chat.MAX_QUESTION + 1), llm=FakeLLM())


def test_the_system_prompt_forbids_orders_and_treats_tool_text_as_data():
    assert "不能下单" in chat.SYSTEM and "不是给你的指令" in chat.SYSTEM


# ---------------------------------------------------------------- budget

def test_questions_are_billed_but_are_not_analyses(monkeypatch):
    con = ledger.connect()
    now = dt.datetime(2026, 11, 4, 3, 0, tzinfo=dt.UTC)
    usage = {"llm_calls": 3, "tokens_in": 5000, "tokens_cached": 0, "tokens_out": 700, "cost_usd": 0.01}
    ledger.record_chat(con, usage, now=now)
    assert ledger.spent_usd(con, now) == pytest.approx(0.01)       # counts against the month
    assert ledger.runs(con) == []                                   # but is not listed with the analyses
    first_guess = get_settings().ai_first_estimate_cny / get_settings().ai_usd_cny
    assert ledger.estimate_usd(con) == pytest.approx(first_guess)   # and does not pull the per-analysis estimate down
    con.execute("DELETE FROM runs")
    ledger.record_chat(con, {**usage, "cost_usd": 20.0}, now=now)
    with pytest.raises(BudgetExceeded):
        ledger.check_budget(con, now=now, extra_runs=0)             # a month used up by questions blocks the next one
    con.close()


# ---------------------------------------------------------------- API

@pytest.fixture
def client(monkeypatch, market):
    monkeypatch.setattr(engine, "require_ready", lambda: None)
    holder = {}

    def make_llm(usage, s=None):
        holder["llm"] = FakeLLM(call("prices", {"symbol": "QQQM", "days": 2}), AIMessage(content="价格在涨。"), usage=usage)
        return holder["llm"]

    monkeypatch.setattr(chat, "make_llm", make_llm)
    with TestClient(create_app()) as c:
        yield c


def test_chat_endpoint_answers_and_books_the_cost(client):
    r = client.post("/api/ai/chat", json={"message": "QQQM 怎么样", "history": []}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "价格在涨。" and body["steps"][0]["tool"] == "prices" and body["cost_cny"] > 0
    con = ledger.connect()
    row = con.execute("SELECT source, llm_calls, cost_usd FROM runs WHERE source = 'chat'").fetchone()
    con.close()
    assert row[0] == "chat" and row[1] == 2 and row[2] > 0
    assert client.get("/api/ai/runs", headers=AUTH).json() == []     # not an analysis


def test_chat_endpoint_needs_the_token_and_a_message(client):
    assert client.post("/api/ai/chat", json={"message": "hi"}).status_code in (401, 403)
    assert client.post("/api/ai/chat", json={"message": ""}, headers=AUTH).status_code == 422
