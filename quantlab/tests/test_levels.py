import pandas as pd
import pytest

from quantlab.holdings import levels


def make(highs, lows, closes, end="2026-11-03"):
    idx = pd.bdate_range(end=end, periods=len(closes))
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes, "volume": 1000}, index=idx)


def by(result, group, name):
    return next(x for x in result["levels"] if x["group"] == group and x["name"] == name)


def zigzag(cycles=12):
    """Price turning between 100 and 120 every 10 days, ending at 110 on the way down."""
    up = [100 + 2 * i for i in range(11)]
    down = [120 - 2 * i for i in range(1, 10)]
    closes = (up + down) * cycles
    closes = closes[: len(closes) - 6]  # ends mid-leg
    return make([c + 0.5 for c in closes], [c - 0.5 for c in closes], closes)


def test_too_short_history_has_no_levels():
    assert levels.compute(make([11] * 20, [9] * 20, [10] * 20)) is None


def test_prior_highs_and_lows_name_the_day():
    highs = [100 + i * 0.1 for i in range(300)]
    bars = make(highs, [h - 2 for h in highs], [h - 1 for h in highs])
    r = levels.compute(bars, "腾讯日线")
    top = by(r, "前高前低", "20 日最高")
    assert top["price"] == round(max(highs[-20:]), 2) and bars.index[-1].strftime("%Y-%m-%d") in top["basis"]
    assert by(r, "前高前低", "252 日最低")["price"] == round(min(h - 2 for h in highs[-252:]), 2)
    assert r["source"] == "腾讯日线" and r["date"] == "2026-11-03"


def test_pivot_points_follow_the_formula():
    n = 40
    bars = make([100] * (n - 1) + [110], [90] * (n - 1) + [100], [95] * (n - 1) + [105])  # last bar: H110 L100 C105
    r = levels.compute(bars)
    assert by(r, "枢轴点", "枢轴 P")["price"] == 105          # (110+100+105)/3
    assert by(r, "枢轴点", "压力 R1")["price"] == 110         # 2P-L
    assert by(r, "枢轴点", "支撑 S1")["price"] == 100         # 2P-H
    assert by(r, "枢轴点", "压力 R2")["price"] == 115         # P+(H-L)
    assert by(r, "枢轴点", "支撑 S2")["price"] == 95          # P-(H-L)


def test_atr_bands_on_constant_range():
    bars = make([102] * 60, [100] * 60, [101] * 60)             # every true range is 2
    r = levels.compute(bars)
    assert by(r, "ATR 通道", "上轨 +1×ATR")["price"] == 103.0 and by(r, "ATR 通道", "下轨 −2×ATR")["price"] == 97.0
    assert "ATR(14)=$2.00" in by(r, "ATR 通道", "上轨 +1×ATR")["basis"]


@pytest.mark.parametrize("close, below, above", [(123.4, 120, 130), (120.0, 110, 130), (9.5, 9, 10), (282.23, 275, 300), (1234.0, 1200, 1300)])
def test_round_numbers_either_side(close, below, above):
    r = levels.compute(make([close + 1] * 40, [close - 1] * 40, [close] * 40))
    assert by(r, "整数关口", "下方整数")["price"] == below and by(r, "整数关口", "上方整数")["price"] == above


def test_swing_turns_become_support_and_resistance():
    r = levels.compute(zigzag())
    res, sup = levels.nearest(r)
    assert 119 <= res["price"] <= 121 and 99 <= sup["price"] <= 101
    assert "转折" in res["basis"] and res["distance"] > 0 > sup["distance"]


def test_distance_is_relative_to_the_close():
    r = levels.compute(make([102] * 60, [100] * 60, [101] * 60))
    x = by(r, "整数关口", "上方整数")
    assert x["distance"] == pytest.approx(x["price"] / 101 - 1)
