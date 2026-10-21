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


def test_missed_split_found_only_when_close_and_gap_agree():
    bars = raw_bars([1000, 1010, 505, 510], opens=[1000, 1005, 503, 508])
    assert cn_data.missed_splits(bars, []) == [(bars.index[2], 2)]
    crash = raw_bars([1000, 1010, 505, 510], opens=[1000, 1005, 990, 508])  # fell 50% during the day: not a split
    assert cn_data.missed_splits(crash, []) == []
    assert cn_data.missed_splits(bars, [(bars.index[2], 1.0, 0.0)]) == []   # recorded: not "missed"
    reverse = raw_bars([10, 10, 100, 101], opens=[10, 10, 100, 100])
    assert cn_data.missed_splits(reverse, []) == [(reverse.index[2], pytest.approx(0.1))]


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
