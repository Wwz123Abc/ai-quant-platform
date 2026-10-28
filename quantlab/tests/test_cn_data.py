import pandas as pd
import pytest

from quantlab.core import cn_data


def raw_bars(closes, opens=None, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    opens = opens or closes
    return pd.DataFrame({"open": opens, "high": closes, "low": closes, "close": closes, "volume": 100.0}, index=idx, dtype=float)


def test_split_and_dividend_become_proportional():
    # raw: 100, 100, then a 2-for-1 split (50, 50), then a $1 dividend (in today's shares) going ex on day 4
    raw = raw_bars([100, 100, 50, 50, 49, 49])
    idx = raw.index
    factors = [(idx[4], 1.0, 0.0), (idx[2], 1.0, -1.0), (idx[0], 0.5, -1.0)]  # Sina: newest first
    adj = cn_data.adjust(raw, factors)
    # split: earlier prices halved; dividend: prices before the ex-date scaled by (1 - 1/50)
    assert adj["close"].iloc[0] == pytest.approx(50 * 0.98)
    assert adj["close"].iloc[3] == pytest.approx(50 * 0.98) and adj["close"].iloc[4] == 49
    assert adj["volume"].iloc[0] == 200  # share counts follow the split
    r = adj["close"].pct_change().dropna().round(10)
    assert (r == 0).all()  # neither the split nor the dividend (which explains the drop to 49) shows up as a return


def test_no_factors_leaves_prices_alone():
    raw = raw_bars([10, 11, 12])
    pd.testing.assert_frame_equal(cn_data.adjust(raw, []), raw)


def with_volume(bars, volumes):
    return bars.assign(volume=[float(v) for v in volumes])


def test_missed_split_found_only_when_close_and_gap_agree():
    bars = with_volume(raw_bars([1000, 1010, 505, 510], opens=[1000, 1005, 503, 508]), [100, 100, 210, 190])
    assert cn_data.missed_splits(bars, []) == [(bars.index[2], 2)]
    crash = raw_bars([1000, 1010, 505, 510], opens=[1000, 1005, 990, 508])  # fell 50% during the day: not a split
    assert cn_data.missed_splits(crash, []) == []
    assert cn_data.missed_splits(bars, [(bars.index[2], 1.0, 0.0)]) == []   # recorded: not "missed"
    reverse = with_volume(raw_bars([10, 10, 100, 101], opens=[10, 10, 100, 100]), [100, 100, 12, 9])
    assert cn_data.missed_splits(reverse, []) == [(reverse.index[2], pytest.approx(0.1))]


def test_news_jump_with_swelling_volume_is_no_split():
    # NBIS on 2025-09-09: from 64.06 it opened at 97.20 and closed at 95.72, a 1.5 ratio, on nine times the volume
    jump = with_volume(raw_bars([64, 64.06, 95.72, 96], opens=[64, 64, 97.2, 95]), [100, 100, 950, 300])
    assert cn_data.missed_splits(jump, []) == []
    # a 2-for-1 split on which trading barely changed is not confirmed either
    flat = with_volume(raw_bars([1000, 1010, 505, 510], opens=[1000, 1005, 503, 508]), [100, 100, 110, 100])
    assert cn_data.missed_splits(flat, []) == []


def test_missed_split_without_volume_decided_by_prices():
    bars = with_volume(raw_bars([1000, 1010, 505, 510], opens=[1000, 1005, 503, 508]), [0, 0, 0, 0])
    assert cn_data.missed_splits(bars, []) == [(bars.index[2], 2)]


def test_source_switch(monkeypatch):
    from quantlab.config import get_settings
    from quantlab.core import data
    from quantlab.errors import NotFound

    monkeypatch.setenv("QUANTLAB_PRICE_SOURCE", "cn")
    get_settings.cache_clear()
    monkeypatch.setattr(cn_data, "fetch", lambda s: "cn:" + s)
    assert data.fetch("MCD") == "cn:MCD"
    with pytest.raises(NotFound):
        data.fetch("700")


def test_bars_before_a_halt_of_over_a_year_are_left_out():
    # QQQM at Tencent: 15 bars of 2012 from an earlier security, then QQQM itself from 2020-10-13
    old = pd.bdate_range("2012-07-25", periods=15)
    new = pd.bdate_range("2020-10-13", periods=5)
    raw = pd.DataFrame({"close": range(20)}, index=old.append(new), dtype=float)
    assert list(cn_data.after_long_halt(raw).index) == list(new)
    assert len(cn_data.after_long_halt(raw.iloc[15:])) == 5        # no long pause: nothing left out
    month = raw.iloc[15:].copy()
    month.index = pd.DatetimeIndex(["2020-10-13", "2020-10-14", "2020-12-01", "2020-12-02", "2020-12-03"])
    assert len(cn_data.after_long_halt(month)) == 5                 # a seven-week halt is not long enough


def test_is_etf_reads_the_quote_type(monkeypatch):
    fields = ["200"] * 73
    fields[56] = "GP-ETF"
    monkeypatch.setattr(cn_data, "tencent_quote", lambda s: fields if s == "QQQM" else fields[:56] + ["GP"] + fields[57:])
    assert cn_data.is_etf("QQQM") and not cn_data.is_etf("AAPL")
