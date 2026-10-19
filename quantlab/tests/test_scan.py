import numpy as np
import pandas as pd
import pytest

from quant import scan


def walk(n=1500, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    idx = pd.bdate_range("2010-01-01", periods=n)
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 0}, index=idx)


def test_windows_split_after_warm_up_without_overlap():
    b = walk()
    ins, oos = scan.windows(b, 300, split="2014-01-01")
    assert ins.index[0] == b.index[299]  # first day the 300-day average exists
    assert ins.index[-1] < pd.Timestamp("2014-01-01") <= oos.index[0]
    assert len(ins) + len(oos) == len(b) - 299
    ins, oos = scan.windows(b, 300)  # default: last 30% held out
    assert len(oos) == pytest.approx(0.3 * (len(b) - 299), abs=1)


def test_windows_too_short():
    with pytest.raises(ValueError):
        scan.windows(walk(600), 300)
    with pytest.raises(ValueError):
        scan.windows(walk(), 300, split="2010-06-01")


def test_scan_skips_pairs_with_fast_not_below_slow():
    b = walk()
    ins, oos = scan.windows(b, 100)
    df = scan.scan(b, ins, oos, fasts=(10, 50, 100), slows=(50, 100), slippage_bps=0)
    assert sorted(zip(df["fast"], df["slow"])) == [(10, 50), (10, 100), (50, 100)]
    assert {"is_sharpe", "oos_sharpe", "is_cagr", "oos_cagr"} <= set(df.columns)


def test_rank_and_correlation():
    df = pd.DataFrame({"fast": [1, 2, 3], "slow": [9, 9, 9], "is_sharpe": [0.3, 0.9, 0.5], "oos_sharpe": [0.1, 0.8, 0.6]})
    r = scan.rank(df)
    assert r[["fast", "is_rank", "oos_rank"]].values.tolist() == [[2, 1, 1], [3, 2, 2], [1, 3, 3]]
    assert scan.rank_corr(df) == pytest.approx(1.0)
    df["oos_sharpe"] = [0.8, 0.1, 0.5]
    assert scan.rank_corr(df) == pytest.approx(-1.0)
