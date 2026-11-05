import numpy as np
import pandas as pd
import pytest

from quantlab.holdings import ma_rules


def series(values, end="2026-11-03"):
    return pd.Series(values, index=pd.bdate_range(end=end, periods=len(values)), dtype=float)


def test_every_rule_has_a_definition_a_warm_up_and_historical_evidence():
    ids = [r[0] for r in ma_rules.RULES]
    assert set(ids) == set(ma_rules.EVIDENCE) == set(ma_rules.WARMUP) == set(ma_rules.targets(series(range(1, 300))).columns)
    for rid in ids:
        e = ma_rules.EVIDENCE[rid]
        assert e["verdict"] in ("通过", "只降回撤", "未通过") and e["mdd"] < 0 and e["bh_mdd"] < 0


def test_cross_and_above_follow_their_definitions():
    up = series(np.linspace(100, 200, 300))                      # a steady climb: every trend rule holds
    t = ma_rules.targets(up)
    assert t.iloc[-1].drop("granville_60").tolist() == [1] * 8   # Granville needs a cross from below, which a steady climb never makes
    assert t.iloc[-1]["granville_60"] == 0
    down = series(np.linspace(200, 100, 300))                    # a steady fall: none holds
    assert ma_rules.targets(down).iloc[-1].sum() == 0
    # not enough history for a 200-day line: that rule says 0, never a guess
    assert ma_rules.targets(series(np.linspace(100, 200, 150)))["above_200"].sum() == 0


def test_a_golden_cross_flips_the_state_on_the_day_the_average_crosses():
    close = series([100.0] * 60 + [90.0] * 30 + list(np.linspace(90, 130, 40)))
    s = ma_rules._cross(close, 5, 10)
    flips = s.index[(s != s.shift(1)) & s.shift(1).notna()]
    assert len(flips) >= 1
    first_up = flips[0]
    f, sl = close.rolling(5).mean(), close.rolling(10).mean()
    assert f[first_up] > sl[first_up] and f.shift(1)[first_up] <= sl.shift(1)[first_up]


def test_granville_enters_on_a_cross_above_a_rising_line_and_leaves_below_it():
    rising = list(np.linspace(100, 130, 120))                    # a rising 60-day line
    g = ma_rules._granville(series(rising + [120.0, 131.0, 132.0, 100.0]))
    assert g.iloc[-4] == 0                                       # a dip under the line: out
    assert g.iloc[-3] == 1 and g.iloc[-2] == 1                   # the close crosses back above it while it rises: in, and stays
    assert g.iloc[-1] == 0                                       # back under the line: out
    # no cross, no entry: a steady climb never dips below the line, so the rule never starts
    assert ma_rules._granville(series(np.linspace(100, 200, 200))).iloc[-1] == 0


def test_bull_alignment_needs_all_four_averages_in_order():
    rising = series(np.linspace(50, 200, 200))
    assert ma_rules._alignment(rising).iloc[-1] == 1
    bumpy = rising.copy()
    bumpy.iloc[-3:] = 60.0                                       # a sharp drop pulls the 5-day line under the 10-day line
    assert ma_rules._alignment(bumpy).iloc[-1] == 0


def test_state_now_reports_state_and_the_day_it_began():
    close = series(list(np.linspace(100, 60, 120)) + list(np.linspace(60, 140, 160)))
    states = {x["id"]: x for x in ma_rules.state_now(close)}
    a20 = states["above_20"]
    assert a20["on"] is True and a20["since"] is not None and a20["days"] >= 0
    assert pd.Timestamp(a20["since"]) <= close.index[-1]
    short = {x["id"]: x for x in ma_rules.state_now(series(np.linspace(1, 2, 50)))}
    assert short["above_200"]["on"] is None and short["above_200"]["since"] is None   # too little history: no guess
    assert short["above_20"]["on"] is True


def test_the_stock_analysis_carries_the_rules_and_never_an_order(monkeypatch):
    from quantlab.core import data
    from quantlab.holdings import analysis
    market = series(np.linspace(100, 160, 400))
    frame = pd.DataFrame({"open": market, "high": market + 1, "low": market - 1, "close": market, "volume": 1000})
    monkeypatch.setattr(data, "load", lambda sym, refresh=False: frame)
    a = analysis.analyze("QQQM", load=lambda sym, refresh=False: frame, with_fundamentals=False, check=False)
    ma = a["ma_rules"]
    assert ma["ready_count"] == 9 and 0 <= ma["on_count"] <= 9
    assert {r["id"] for r in ma["rules"]} == set(ma_rules.EVIDENCE)
    assert all(r["evidence"]["verdict"] in ("通过", "只降回撤", "未通过") for r in ma["rules"])
    assert "按你的规则" in a["verdict"] or "还没有同步持仓" in a["verdict"]   # the verdict still comes only from the rules


@pytest.mark.parametrize("rid", [r[0] for r in ma_rules.RULES])
def test_no_rule_passed_the_registered_test_except_by_standing_aside(rid):
    """The result of docs/research/2026-11-ma-rules.md: nothing beats buy-and-hold on risk-adjusted return, and the one
    rule that lowered drawdown did it by being in the market under a third of the time."""
    e = ma_rules.EVIDENCE[rid]
    assert e["verdict"] != "通过" and e["sharpe"] < e["bh_sharpe"]
