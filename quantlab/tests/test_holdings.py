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
        {"date": "2026-09-01", "side": "buy", "symbol": "SGOV", "shares": 10, "price": 100, "fees": 0, "currency": "USD"},   # the cash: 1000
    ],
    "fixed": [{"kind": "deposit", "principal": 2000, "interest": 40, "start": "2026-07-01", "maturity": "2027-07-01", "currency": "USD"},
              {"kind": "bond", "face": 1000, "price": 98, "coupon": 4, "freq": 2, "settle": "2026-01-01", "maturity": "2027-01-01",
               "accruedPaid": 0, "currency": "USD"}],
}
def with_cash(snap, amount):
    """The ledger keeps no cash: the owner's cash is SGOV (100 a share). A copy of snap holding exactly `amount` of it."""
    trades = [t for t in snap["trades"] if t["symbol"] != "SGOV"]
    if amount:
        trades.append({"date": "2026-09-01", "side": "buy", "symbol": "SGOV", "shares": amount / 100, "price": 100, "currency": "USD"})
    return {**{k: v for k, v in snap.items() if k != "cash"}, "trades": trades}


SG = {"SGOV": 100.0}


DAY = "2026-07-01"   # the deposit starts; the bond is on a coupon date, its clean price halfway to par
FIXED = 2000 + 980 + 20 * 181 / 365


def bars(level, n=300, seed=0, end="2026-10-16"):
    rng = np.random.default_rng(seed)
    c = level * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    c = c / c[-1] * level  # end exactly at `level`
    idx = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx)


MARKET = {"AAA": bars(150, seed=1), "BBB": bars(25, seed=2), "CCC": bars(40, seed=3), "QQQ": bars(400, seed=4), "DDD": bars(700, seed=5),
          "SGOV": bars(100, seed=6)}
load = lambda s, refresh=False: MARKET[s]


def test_positions_average_cost():
    pos = {p["symbol"]: p for p in ledger.positions(SNAP)}
    a = pos["AAA"]
    assert a["shares"] == 15 and a["avg_cost"] == pytest.approx((1000 + 1 + 1200) / 20)  # selling keeps the average cost
    assert pos["BBB"]["shares"] == 4
    assert pos["SGOV"]["shares"] == 10 and ledger.fixed_usd(SNAP, DAY) == pytest.approx(FIXED)


def test_valuation():
    v = review.valuation(SNAP, {"AAA": 150.0, "BBB": 25.0, **SG}, DAY)
    assert v["stocks"] == 15 * 150 + 4 * 25 and v["investable"] == 2350 + 1000
    w = {p["symbol"]: p["weight"] for p in v["positions"]}
    assert w["AAA"] == pytest.approx(2250 / 3350) and v["total"] == pytest.approx(3350 + FIXED)


def test_policy_validation():
    assert policy.validate({"targets": {"aaa": 0.25}})["targets"] == {"AAA": 0.25}
    with pytest.raises(InvalidInput):
        policy.validate({"targets": {"AAA": 0.5}})                      # above the fixed 25% cap
    with pytest.raises(InvalidInput):
        policy.validate({"targets": {"AAA": 0.25, "BBB": 0.25, "CCC": 0.25, "DDD": 0.25}, "cash_reserve": 0.2})  # 100% > 80%
    with pytest.raises(InvalidInput):
        policy.validate({"band": 0.0})


def test_cap_trims_and_target_buys_within_the_cash_reserve():
    prices = {"AAA": 150.0, "BBB": 25.0, "CCC": 40.0, **SG}
    v = review.valuation(SNAP, prices)                       # investable 3350; AAA is 67%
    pol = policy.validate({"targets": {"CCC": 0.2}, "cash_reserve": 0.1, "min_trade_usd": 10})
    props, notes = review.proposals(v, pol, prices)
    by = {p["symbol"]: p for p in props}
    assert by["AAA"]["side"] == "sell" and by["AAA"]["shares"] == 9          # 2250 - 837.5 = 1412.5 -> 9.4, nearest is 9 shares
    assert "单只上限" in by["AAA"]["reason"]
    assert by["CCC"]["side"] == "buy" and by["CCC"]["shares"] == 16           # 20% of 3350 = 670 -> 16 whole shares
    assert [p["side"] for p in props] == ["sell", "buy"]


def test_selling_rounds_to_the_nearest_whole_share():
    pol = policy.validate({"uncapped": [], "min_trade_usd": 10})
    snap = with_cash({**SNAP, "trades": [{"date": "2026-09-10", "side": "buy", "symbol": "AAA", "shares": 3, "price": 700, "currency": "USD"}]}, 6280)
    prices = {"AAA": 702.0, **SG}
    v = review.valuation(snap, prices)                       # investable 8386; AAA 2106 = 25.1%, $9.5 over the 25% cap
    props, notes = review.proposals(v, pol, prices)
    assert props == [] and any("不到半股" in n for n in notes)
    prices = {"AAA": 850.0, **SG}
    v = review.valuation(snap, prices)                       # investable 8830; AAA 2550 = 28.9%, $342 over: 0.40 share
    assert review.proposals(v, pol, prices)[0] == []
    prices = {"AAA": 1000.0, **SG}
    v = review.valuation(snap, prices)                       # investable 9280; AAA 3000 = 32.3%, $680 over: 0.68 share
    props, _ = review.proposals(v, pol, prices)
    assert [(p["symbol"], p["side"], p["shares"]) for p in props] == [("AAA", "sell", 1)]


def test_broad_etfs_are_not_capped():
    prices = {"AAA": 150.0, "BBB": 25.0, **SG}
    v = review.valuation(SNAP, prices)                       # AAA is 67%
    pol = policy.validate({"uncapped": ["aaa"]})
    assert pol["uncapped"] == ["AAA", "SGOV"] and review.proposals(v, pol, prices)[0] == []
    assert policy.validate({"targets": {"AAA": 0.6}, "uncapped": ["AAA"], "cash_reserve": 0.1})["targets"] == {"AAA": 0.6}
    assert "QQQM" in policy.load()["uncapped"] and "VOO" in policy.load()["uncapped"]   # the defaults


def test_buys_scaled_to_cash_after_reserve():
    prices = {"AAA": 150.0, "BBB": 25.0, "CCC": 40.0, **SG}
    snap = with_cash(SNAP, 500)
    v = review.valuation(snap, prices)                       # investable 2850; reserve 285; available 215
    pol = policy.validate({"targets": {"CCC": 0.25}, "uncapped": ["AAA"], "cash_reserve": 0.1, "min_trade_usd": 10})
    props, notes = review.proposals(v, pol, prices)
    assert props == [{"symbol": "CCC", "side": "buy", "shares": 5, "price": 40.0, "value": 200.0, "reason": props[0]["reason"],
                      "basis": props[0]["basis"]}]
    assert any("缩减" in n for n in notes)


def test_every_order_and_reminder_says_what_it_rests_on(monkeypatch):
    prices = {"AAA": 150.0, "BBB": 25.0, "CCC": 40.0, **SG}
    snap = with_cash(SNAP, 500)
    v = review.valuation(snap, prices)
    pol = policy.validate({"targets": {"CCC": 0.25}, "uncapped": ["AAA"], "cash_reserve": 0.1, "min_trade_usd": 10})
    info = {"dates": {"CCC": "2026-11-03"}, "source": "腾讯日线", "synced": "2026-11-04T03:00:00+00:00",
            "checked": {"CCC": "已和第二来源核对一致"}}
    basis = review.proposals(v, pol, prices, info)[0][0]["basis"]
    # the rule, the figures that went into it, the data date and source, and the arithmetic
    for part in ("规则", "CCC 2026-11-03 收盘价 $40.00", "腾讯日线", "已和第二来源核对一致", "2026-11-04T03:00:00",
                 "可投资资金 $2850.00", "应持有 $712.50", "缩减", "折合 5 股"):
        assert part in basis, part
    monkeypatch.setattr(signals, "ai_rating", lambda s: {"id": 3, "rating": "Sell", "trade_date": "2026-10-16"} if s == "BBB" else None)
    v = review.valuation(with_cash(SNAP, 0), {"AAA": 150.0, "BBB": 25.0})
    rem = review.reminders(v, policy.validate({"cash_reserve": 0.1}), load, with_fundamentals=False, info=info)
    assert rem and all(x["basis"] for x in rem if x["kind"] in ("规则", "亏损", "AI", "回撤", "趋势"))
    assert "#3" in next(x["basis"] for x in rem if x["kind"] == "AI")


def test_no_order_inside_the_band_or_below_minimum():
    prices = {"AAA": 150.0, "BBB": 25.0, **SG}
    v = review.valuation(SNAP, prices)
    w = v["positions"][0]["weight"]
    pol = policy.validate({"targets": {"AAA": min(1.0, round(w, 2))}, "uncapped": ["AAA", "BBB"], "cash_reserve": 0.0, "band": 0.05})
    assert review.proposals(v, pol, prices)[0] == []
    pol = policy.validate({"targets": {"BBB": 0.05}, "uncapped": ["AAA", "BBB"], "band": 0.01, "min_trade_usd": 500})
    props, notes = review.proposals(v, pol, prices)
    assert props == [] and any("最小下单金额" in n for n in notes)


def test_reminders(monkeypatch):
    monkeypatch.setattr(signals, "ai_rating", lambda s: {"id": 3, "rating": "Sell", "trade_date": "2026-10-16"} if s == "BBB" else None)
    v = review.valuation(with_cash(SNAP, 0), {"AAA": 150.0, "BBB": 25.0})
    pol = policy.validate({"cash_reserve": 0.1})
    r = review.reminders(v, pol, load, with_fundamentals=False)
    kinds = {(x["symbol"], x["kind"]) for x in r}
    assert ("SGOV", "规则") in kinds          # no SGOV (cash) at all
    assert ("BBB", "亏损") in kinds           # 25 vs average cost 50
    assert ("BBB", "AI") in kinds and all(x["level"] == "warn" for x in r if x["kind"] == "AI")


def test_review_and_decisions(monkeypatch):
    monkeypatch.setattr(ledger, "load", lambda refresh=False: {**SNAP, "synced": "2026-10-19T00:00:00+00:00"})
    monkeypatch.setattr(signals, "ai_rating", lambda s: None)
    policy.save({"targets": {"CCC": 0.2}, "max_position": 0.4})
    first = review.run(refresh=False, load=load, with_fundamentals=False)
    assert {p["symbol"] for p in first["proposals"]} == {"AAA", "CCC"} and all(p["status"] == "pending" for p in first["proposals"])
    assert first["price_date"] == "2026-10-16"                         # the close the review priced at
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
    # investable 3350: cap room 1005, cash room 1000 - 335 = 665, which buys 16 whole shares at 40
    assert a["allowed"] and p["max_buy"] == pytest.approx(665) and "665" in a["verdict"] and "16 股" in a["verdict"]
    assert p["max_shares"] == 16 and p["shares"] == 16 and p["amount"] == pytest.approx(640)
    assert set(p["correlations"]) == {"AAA", "BBB"}
    too_much = analysis.analyze("CCC", amount=2000, load=load, with_fundamentals=False)
    assert not too_much["allowed"] and [c["ok"] for c in too_much["checks"]] == [False, False]
    held = analysis.analyze("AAA", load=load, with_fundamentals=False)   # already 67% of the money
    # the rule that blocks the purchase is the one marked as failed (the verdict never contradicts the checks)
    assert not held["allowed"] and held["portfolio"]["max_buy"] == 0 and [c["ok"] for c in held["checks"]] == [False, True]
    pricey = analysis.analyze("DDD", load=load, with_fundamentals=False)  # $665 of room, but one share costs 700
    assert not pricey["allowed"] and pricey["checks"][-1]["rule"] == "整股" and pricey["portfolio"]["max_shares"] == 0
    policy.save({"max_position": 0.3, "cash_reserve": 0.1, "uncapped": ["AAA"]})
    etf = analysis.analyze("AAA", load=load, with_fundamentals=False)    # a broad ETF: only the cash rule applies
    assert etf["allowed"] and etf["portfolio"]["max_buy"] == pytest.approx(665) and "不设上限" in etf["checks"][0]["text"]


def test_fundamentals_note(monkeypatch):
    from quantlab.core import cn_data
    from quantlab.errors import DataSourceError
    from quantlab.ml import sec

    monkeypatch.setattr(cn_data, "is_etf", lambda s: s == "QQQM")
    monkeypatch.setattr(sec, "cik_map", lambda refresh=False: {"AAPL": 320193})
    assert "ETF 没有公司财报" in signals.fundamentals_note("QQQM")
    assert "名单里没有 NBIS" in signals.fundamentals_note("NBIS")

    def down(refresh=False):
        raise DataSourceError("SEC 返回错误 403")
    monkeypatch.setattr(sec, "cik_map", down)
    assert "暂时取不到 SEC 数据" in signals.fundamentals_note("AAPL")


def test_api(monkeypatch):
    from fastapi.testclient import TestClient

    from quantlab.api.app import create_app
    from quantlab.core import data

    monkeypatch.setattr(data, "load", load)
    monkeypatch.setattr(ledger, "load", lambda refresh=False: {**SNAP, "synced": "2026-10-19T00:00:00+00:00"})
    monkeypatch.setattr(signals, "ai_rating", lambda s: None)
    monkeypatch.setattr(signals, "fundamentals", lambda s, close=None: None)
    monkeypatch.setattr(signals, "fundamentals_note", lambda s: "ETF 没有公司财报")
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
    j = a.json()
    assert j["portfolio"]["shares"] == 2 and j["portfolio"]["amount"] == 80 and j["technical"]["symbol"] == "CCC"   # $100 buys 2 whole shares
    assert j["fundamentals"] is None and j["fundamentals_note"] == "ETF 没有公司财报"


def test_fixed_income_by_the_ledgers_rules():
    status = {f["kind"]: f for f in ledger.fixed_status(SNAP, "2026-12-25")}
    assert status["bond"]["days_left"] == 7 and not status["bond"]["matured"]
    after = {f["kind"]: f for f in ledger.fixed_status(SNAP, "2027-01-05")}
    assert after["bond"]["matured"] and after["bond"]["value"] == 0      # back in savings: no longer counted
    assert ledger.fixed_usd(SNAP, "2026-06-30") == pytest.approx(980 + 20 * 180 / 365 + 20 * 180 / 181)  # the deposit has not started
    broken = {**SNAP, "fixed": [{"kind": "deposit", "principal": 100},
                                {"kind": "bond", "face": 1000, "price": 0, "settle": "2026-01-01", "maturity": "2027-01-01"}]}
    assert ledger.fixed_items(broken) == []                               # records the ledger cannot value are left out
    hkd = {"fixed": [{"kind": "deposit", "currency": "HKD", "principal": "7800", "interest": "0", "start": "2026-01-01", "maturity": "2027-01-01"}]}
    assert ledger.fixed_usd(hkd, "2026-06-01") == pytest.approx(7800 / 7.8)


def test_maturity_reminders():
    pol = policy.validate({})
    due = review.reminders(review.valuation(SNAP, {"AAA": 150.0, "BBB": 25.0, **SG}, "2026-12-25"), pol, load, with_fundamentals=False)
    assert any(r["kind"] == "到期" and "还有 7 天" in r["text"] for r in due)
    gone = review.reminders(review.valuation(SNAP, {"AAA": 150.0, "BBB": 25.0, **SG}, "2027-01-05"), pol, load, with_fundamentals=False)
    assert any(r["kind"] == "到期" and "已到期" in r["text"] for r in gone)


def test_price_checks_become_reminders(monkeypatch):
    from quantlab.config import get_settings
    from quantlab.core import price_check

    monkeypatch.setattr(ledger, "load", lambda refresh=False: {**SNAP, "synced": "2026-10-19T00:00:00+00:00"})
    monkeypatch.setattr(signals, "ai_rating", lambda s: None)
    monkeypatch.setattr(price_check, "check", lambda s: {"symbol": s, "against": "新浪财经", "ok": s != "BBB", "text": "%s 核对结果" % s})
    first = review.run(refresh=False, load=load, with_fundamentals=False)
    assert first["price_check"] == []                                       # Yahoo prices: Tencent's are not what we used
    monkeypatch.setenv("QUANTLAB_PRICE_SOURCE", "cn")
    get_settings.cache_clear()
    r = review.run(refresh=False, load=load, with_fundamentals=False)
    assert [c["symbol"] for c in r["price_check"]] == ["AAA", "BBB", "SGOV"]
    assert [(m["symbol"], m["level"]) for m in r["reminders"] if m["kind"] == "核对"] == [("BBB", "warn")]
    a = analysis.analyze("CCC", load=load, with_fundamentals=False)
    assert {"kind": "核对", "text": "CCC 核对结果"} in a["notes"]


# ---- take profit: two steps, once per purchase, never for the broad ETFs
TP_SNAP = {"account": "me", "trades": [{"date": "2026-09-10", "side": "buy", "symbol": "AAA", "shares": 10, "price": 100, "currency": "USD"}],
           "fixed": []}
TP_SNAP = with_cash(TP_SNAP, 9000)


def tp_props(price, done=None, **rules):
    pol = policy.validate({"min_trade_usd": 10, **rules})
    v = review.valuation(TP_SNAP, {"AAA": price, **SG})
    return review.proposals(v, pol, {"AAA": price, **SG}, None, done)[0]


def test_the_position_cap_is_fixed_at_25_percent():
    assert policy.load()["max_position"] == 0.25
    assert policy.validate({"max_position": 0.9})["max_position"] == 0.25      # not a setting: whatever is sent, 25%
    pol = {**policy.DEFAULT, "max_position": 0.9}
    policy.path().parent.mkdir(parents=True, exist_ok=True)
    policy.path().write_text(json.dumps(pol), encoding="utf-8")                 # an old file with a looser cap is overruled
    assert policy.load()["max_position"] == 0.25


def test_take_profit_first_step_sells_a_third_at_plus_50_percent():
    assert tp_props(140.0) == []                                                # +40%: not yet
    (o,) = tp_props(160.0)                                                      # +60%
    assert (o["side"], o["shares"]) == ("sell", 3) and o["reason"].startswith("止盈第 1 档（+50%）")
    assert "止盈" in o["basis"] and "规则：止盈" in o["basis"]


def test_take_profit_second_step_and_each_step_only_once():
    (o,) = tp_props(220.0)                                                      # +120%: the second step
    assert o["reason"].startswith("止盈第 2 档（+100%）")
    assert tp_props(160.0, {"AAA": {1}}) == []                                  # step 1 already taken
    assert tp_props(220.0, {"AAA": {1}})[0]["reason"].startswith("止盈第 2 档")  # step 2 still open
    assert tp_props(220.0, {"AAA": {2}}) == []                                  # taking step 2 covers step 1 as well
    assert tp_props(160.0, {"AAA": {2}}) == []                                  # and a fall back does not re-open them


def test_take_profit_leaves_broad_etfs_alone_and_can_be_switched_off():
    assert tp_props(220.0, uncapped=["AAA"]) == []
    assert tp_props(220.0, take_profit=False) == []


def test_take_profit_settings_are_checked():
    with pytest.raises(InvalidInput):
        policy.validate({"tp_gain_1": 1.0, "tp_gain_2": 0.5})                   # the second step must be higher
    with pytest.raises(InvalidInput):
        policy.validate({"tp_sell_1": 0.0})


def test_steps_taken_are_remembered_until_the_stock_is_bought_again():
    pol = policy.load()
    con = review.connect()
    with con:
        con.execute("INSERT INTO reviews (at, synced, data) VALUES ('t', NULL, '{}')")
        for status, day in (("approved", "2026-10-05T01:00:00+00:00"), ("rejected", "2026-10-06T01:00:00+00:00")):
            con.execute("INSERT INTO proposals (review_id, symbol, side, shares, price, value, reason, basis, status, decided_at) "
                        "VALUES (1, 'AAA', 'sell', 3, 160, 480, ?, '', ?, ?)", ("%s：现价比平均成本高 60%%" % review.tp_tag(1, pol), status, day))
    con.close()
    assert review.take_profit_done(TP_SNAP, pol) == {"AAA": {1}}                # only the approved one counts
    later = {**TP_SNAP, "trades": TP_SNAP["trades"] + [{"date": "2026-10-20", "side": "buy", "symbol": "AAA", "shares": 5, "price": 150}]}
    assert review.take_profit_done(later, pol) == {}                            # bought again after: the steps start over


# ---- the ledger keeps no cash: SGOV is the cash
def test_cash_is_the_sgov_position_and_cash_records_are_ignored():
    snap = {**with_cash(SNAP, 2000), "cash": [{"currency": "USD", "amount": 99999}]}    # an old cash record is not read
    v = review.valuation(snap, {"AAA": 150.0, "BBB": 25.0, **SG})
    assert v["cash"] == 2000 and v["stocks"] == 15 * 150 + 4 * 25                       # stocks leave SGOV out
    assert v["investable"] == pytest.approx(2350 + 2000) and v["cash_weight"] == pytest.approx(2000 / 4350)
    assert [p["symbol"] for p in v["positions"]].count("SGOV") == 1                       # and SGOV is still a listed holding


def test_sgov_is_never_capped_trimmed_or_taken_profit_on_and_is_always_in_the_uncapped_list():
    assert "SGOV" in policy.load()["uncapped"] and "SGOV" in policy.validate({"uncapped": ["VOO"]})["uncapped"]
    snap = with_cash({**SNAP, "trades": [{"date": "2026-09-10", "side": "buy", "symbol": "AAA", "shares": 1, "price": 100, "currency": "USD"}]}, 9000)
    prices = {"AAA": 100.0, "SGOV": 300.0}                                               # SGOV is 99% of the money and up 200%
    v = review.valuation(snap, prices)
    assert review.proposals(v, policy.validate({"min_trade_usd": 1}), prices)[0] == []
    rem = review.reminders(v, policy.validate({}), load, with_fundamentals=False)
    assert not [r for r in rem if r["symbol"] == "SGOV"]                                  # no trend, rating or loss reminders for it


def test_a_thin_sgov_reserve_is_flagged_by_the_cash_rule():
    v = review.valuation(with_cash(SNAP, 100), {"AAA": 150.0, "BBB": 25.0, **SG})        # 100 of ~2450
    rem = review.reminders(v, policy.validate({"cash_reserve": 0.1}), load, with_fundamentals=False)
    assert any(r["symbol"] == "SGOV" and r["kind"] == "规则" and "SGOV 只占" in r["text"] for r in rem)
