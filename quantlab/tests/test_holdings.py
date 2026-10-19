import json

import numpy as np
import pandas as pd
import pytest

from quantlab.errors import InvalidInput, NotFound
from quantlab.holdings import analysis, ledger, policy, review, signals

SNAP = {
    "account": "me",
    "trades": [
        {"date": "2026-09-10", "side": "buy", "symbol": "AAA", "shares": 10, "price": 100, "fees": 1, "currency": "USD"},
        {"date": "2026-09-20", "side": "buy", "symbol": "AAA", "shares": 10, "price": 120, "fees": 0, "currency": "USD"},
        {"date": "2026-10-01", "side": "sell", "symbol": "AAA", "shares": 5, "price": 130, "fees": 0, "currency": "USD"},
        {"date": "2026-09-15", "side": "buy", "symbol": "BBB", "shares": 4, "price": 50, "fees": 0, "currency": "USD"},
    ],
    "cash": [{"name": "储蓄", "currency": "USD", "amount": 1000}],
    "fixed": [{"kind": "deposit", "principal": 2000, "currency": "USD"}, {"kind": "bond", "face": 1000, "price": 98, "accruedPaid": 5}],
}


def bars(level, n=300, seed=0, end="2026-10-16"):
    rng = np.random.default_rng(seed)
    c = level * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    c = c / c[-1] * level  # end exactly at `level`
    idx = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx)


MARKET = {"AAA": bars(150, seed=1), "BBB": bars(25, seed=2), "CCC": bars(40, seed=3), "QQQ": bars(400, seed=4)}
load = lambda s, refresh=False: MARKET[s]


def test_positions_average_cost():
    pos = {p["symbol"]: p for p in ledger.positions(SNAP)}
    a = pos["AAA"]
    assert a["shares"] == 15 and a["avg_cost"] == pytest.approx((1000 + 1 + 1200) / 20)  # selling keeps the average cost
    assert pos["BBB"]["shares"] == 4
    assert ledger.cash_usd(SNAP) == 1000 and ledger.fixed_usd(SNAP) == pytest.approx(2000 + 980 + 5)


def test_valuation():
    v = review.valuation(SNAP, {"AAA": 150.0, "BBB": 25.0})
    assert v["stocks"] == 15 * 150 + 4 * 25 and v["investable"] == 2350 + 1000
    w = {p["symbol"]: p["weight"] for p in v["positions"]}
    assert w["AAA"] == pytest.approx(2250 / 3350) and v["total"] == pytest.approx(3350 + 2985)


def test_policy_validation():
    assert policy.validate({"targets": {"aaa": 0.3}})["targets"] == {"AAA": 0.3}
    with pytest.raises(InvalidInput):
        policy.validate({"targets": {"AAA": 0.5}})                      # above the 30% cap
    with pytest.raises(InvalidInput):
        policy.validate({"targets": {"AAA": 0.3, "BBB": 0.3, "CCC": 0.3}, "cash_reserve": 0.2})  # 90% > 80%
    with pytest.raises(InvalidInput):
        policy.validate({"band": 0.0})


def test_cap_trims_and_target_buys_within_the_cash_reserve():
    prices = {"AAA": 150.0, "BBB": 25.0, "CCC": 40.0}
    v = review.valuation(SNAP, prices)                       # investable 3350; AAA is 67%
    pol = policy.validate({"targets": {"CCC": 0.2}, "max_position": 0.4, "cash_reserve": 0.1, "min_trade_usd": 10})
    props, notes = review.proposals(v, pol, prices)
    by = {p["symbol"]: p for p in props}
    assert by["AAA"]["side"] == "sell" and by["AAA"]["shares"] == 7          # 2250 - 1340 = 910 -> ceil(6.07) = 7 shares
    assert "单只上限" in by["AAA"]["reason"]
    assert by["CCC"]["side"] == "buy" and by["CCC"]["shares"] == 16           # 20% of 3350 = 670 -> 16 whole shares
    assert [p["side"] for p in props] == ["sell", "buy"]


def test_buys_scaled_to_cash_after_reserve():
    prices = {"AAA": 150.0, "BBB": 25.0, "CCC": 40.0}
    snap = {**SNAP, "cash": [{"currency": "USD", "amount": 500}]}
    v = review.valuation(snap, prices)                       # investable 2850; reserve 285; available 215
    pol = policy.validate({"targets": {"CCC": 0.25}, "max_position": 1.0, "cash_reserve": 0.1, "min_trade_usd": 10})
    props, notes = review.proposals(v, pol, prices)
    assert props == [{"symbol": "CCC", "side": "buy", "shares": 5, "price": 40.0, "value": 200.0, "reason": props[0]["reason"]}]
    assert any("缩减" in n for n in notes)


def test_no_order_inside_the_band_or_below_minimum():
    prices = {"AAA": 150.0, "BBB": 25.0}
    v = review.valuation(SNAP, prices)
    w = v["positions"][0]["weight"]
    pol = policy.validate({"targets": {"AAA": min(1.0, round(w, 2))}, "max_position": 1.0, "cash_reserve": 0.0, "band": 0.05})
    assert review.proposals(v, pol, prices)[0] == []
    pol = policy.validate({"targets": {"BBB": 0.05}, "max_position": 1.0, "band": 0.01, "min_trade_usd": 500})
    props, notes = review.proposals(v, pol, prices)
    assert props == [] and any("最小下单金额" in n for n in notes)


def test_reminders(monkeypatch):
    monkeypatch.setattr(signals, "ai_rating", lambda s: {"id": 3, "rating": "Sell", "trade_date": "2026-10-16"} if s == "BBB" else None)
    v = review.valuation({**SNAP, "cash": []}, {"AAA": 150.0, "BBB": 25.0})
    pol = policy.validate({"cash_reserve": 0.1})
    r = review.reminders(v, pol, load, with_fundamentals=False)
    kinds = {(x["symbol"], x["kind"]) for x in r}
    assert ("现金", "规则") in kinds          # no cash at all
    assert ("BBB", "亏损") in kinds           # 25 vs average cost 50
    assert ("BBB", "AI") in kinds and all(x["level"] == "warn" for x in r if x["kind"] == "AI")


def test_review_and_decisions(monkeypatch):
    monkeypatch.setattr(ledger, "load", lambda refresh=False: {**SNAP, "synced": "2026-10-19T00:00:00+00:00"})
    monkeypatch.setattr(signals, "ai_rating", lambda s: None)
    policy.save({"targets": {"CCC": 0.2}, "max_position": 0.4})
    first = review.run(refresh=False, load=load, with_fundamentals=False)
    assert {p["symbol"] for p in first["proposals"]} == {"AAA", "CCC"} and all(p["status"] == "pending" for p in first["proposals"])
    pid = first["proposals"][0]["id"]
    assert review.decide(pid, True, "下周下单")["status"] == "approved"
    with pytest.raises(NotFound):
        review.decide(pid, False)
    second = review.run(refresh=False, load=load, with_fundamentals=False)
    con = review.connect()
    statuses = dict(con.execute("SELECT id, status FROM proposals WHERE review_id = ?", (first["id"],)).fetchall())
    con.close()
    assert statuses[pid] == "approved" and "superseded" in statuses.values()   # older pending ones are replaced
    assert second["id"] == first["id"] + 1 and json.dumps(second, default=str)


def test_analysis_verdict(monkeypatch):
    monkeypatch.setattr(ledger, "load", lambda refresh=False: SNAP)
    monkeypatch.setattr(signals, "ai_rating", lambda s: None)
    policy.save({"max_position": 0.3, "cash_reserve": 0.1})
    a = analysis.analyze("ccc", load=load, with_fundamentals=False)
    p = a["portfolio"]
    # investable 3350: cap room 1005, cash room 1000 - 335 = 665
    assert a["allowed"] and p["max_buy"] == pytest.approx(665) and "665" in a["verdict"]
    assert set(p["correlations"]) == {"AAA", "BBB"}
    too_much = analysis.analyze("CCC", amount=2000, load=load, with_fundamentals=False)
    assert not too_much["allowed"] and [c["ok"] for c in too_much["checks"]] == [False, False]
    held = analysis.analyze("AAA", load=load, with_fundamentals=False)   # already 67% of the money
    assert not held["allowed"] and held["portfolio"]["max_buy"] == 0


def test_api(monkeypatch):
    from fastapi.testclient import TestClient

    from quantlab.api.app import create_app
    from quantlab.core import data

    monkeypatch.setattr(data, "load", load)
    monkeypatch.setattr(ledger, "load", lambda refresh=False: {**SNAP, "synced": "2026-10-19T00:00:00+00:00"})
    monkeypatch.setattr(signals, "ai_rating", lambda s: None)
    monkeypatch.setattr(signals, "fundamentals", lambda s, close=None: None)
    c = TestClient(create_app())
    auth = {"Authorization": "Bearer test-token"}
    assert c.get("/api/holdings/review", headers=auth).status_code == 404
    assert c.put("/api/holdings/policy", json={"targets": {"CCC": 0.5}}, headers=auth).status_code == 400
    assert c.put("/api/holdings/policy", json={"targets": {"CCC": 0.2}, "max_position": 0.4}, headers=auth).json()["targets"] == {"CCC": 0.2}
    r = c.post("/api/holdings/review", headers=auth)
    assert r.status_code == 200, r.text
    pid = r.json()["proposals"][0]["id"]
    assert c.post("/api/holdings/proposals/%d/reject" % pid, json={"note": "不想卖"}, headers=auth).json()["status"] == "rejected"
    a = c.get("/api/analyze/CCC?amount=100", headers=auth)
    assert a.status_code == 200, a.text
    assert a.json()["portfolio"]["amount"] == 100 and a.json()["technical"]["symbol"] == "CCC"
