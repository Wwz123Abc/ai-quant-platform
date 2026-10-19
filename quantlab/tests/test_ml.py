import numpy as np
import pandas as pd
import pytest

from quantlab.ml import features as F
from quantlab.ml import model as M
from quantlab.ml import portfolio as P


def market(n_days=900, n_syms=30, seed=0):
    rng = np.random.default_rng(seed)
    cal = pd.bdate_range("2015-01-01", periods=n_days)
    syms = ["S%02d" % i for i in range(n_syms)]
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, (n_days, n_syms)), axis=0)), index=cal, columns=syms)
    open_ = close.shift(1).fillna(close) * (1 + rng.normal(0, 0.002, close.shape))
    volume = pd.DataFrame(rng.uniform(1e6, 2e6, close.shape), index=cal, columns=syms)
    return open_, close, volume


def test_features_use_only_the_past():
    open_, close, volume = market()
    raw = F.raw_features(close, volume)
    cut = close.index[600]
    raw_cut = F.raw_features(close.loc[:cut], volume.loc[:cut])
    for k in raw:  # changing later prices must not change any feature up to `cut`
        pd.testing.assert_frame_equal(raw[k].loc[:cut], raw_cut[k], check_freq=False)
    assert raw["r21"].iloc[: F.MIN_HISTORY - 1].isna().all().all()  # no features before a year of history


def test_features_ranked_per_date():
    open_, close, volume = market()
    X = F.feature_matrix(F.raw_features(close, volume))
    day = X.loc[X.index.get_level_values(0)[-1]]
    assert day["r21"].between(0, 1).all() and day["r21"].max() == 1.0


def test_label_is_next_open_to_open():
    open_, close, volume = market()
    fwd = F.forward_returns(open_, horizon=3)
    d = 10
    assert fwd.iloc[d, 0] == pytest.approx(open_.iloc[d + 4, 0] / open_.iloc[d + 1, 0] - 1)


def test_walk_forward_never_trains_on_unfinished_labels(monkeypatch):
    open_, close, volume = market()
    X = F.feature_matrix(F.raw_features(close, volume))
    y = F.labels(open_)
    cal = close.index
    seen = []
    real_fit = M._fit
    monkeypatch.setattr(M, "_fit", lambda Xs, ys: (seen.append(Xs.index.get_level_values(0).max()), real_fit(Xs, ys))[1])
    rebal = M.month_ends(cal, start=cal[700])
    wf = M.walk_forward(X, y, cal, rebal, min_train_years=1, retrain_months=1)
    pos = {d: i for i, d in enumerate(cal)}
    fit_dates = [pd.Timestamp(f["at"]) for f in wf.fits]
    assert len(fit_dates) == len(seen) > 0
    for at, last_sample in zip(fit_dates, seen, strict=True):
        assert pos[last_sample] + 1 + F.HORIZON <= pos[at]  # the newest label closed by the rebalance open
    assert set(wf.scores.index.get_level_values(0)) <= set(rebal)


def test_month_ends():
    cal = pd.bdate_range("2024-01-01", "2024-03-31")
    assert [d.day for d in M.month_ends(cal)] == [31, 29, 29]  # 2024-03-29 is the last weekday in March


def test_capped_weights():
    w = P.capped(pd.Series([10.0, 1, 1, 1, 1, 1, 1, 1]), 0.2)
    assert w.sum() == pytest.approx(1) and w.max() <= 0.2 + 1e-9
    with pytest.raises(ValueError):
        P.capped(pd.Series([1.0, 1.0]), 0.2)


def test_target_weights_inverse_volatility():
    scores = pd.Series({"A": 0.9, "B": 0.8, "C": 0.1, "D": 0.7})
    vol = pd.Series({"A": 0.02, "B": 0.01, "C": 0.01, "D": np.nan})
    w = P.target_weights(scores, vol, top=2, max_weight=1.0)
    assert list(w.index) == ["A", "B"] and w["B"] == pytest.approx(2 * w["A"])  # D has no volatility: skipped


def test_portfolio_trades_next_open_with_costs():
    cal = pd.bdate_range("2024-01-01", periods=5)
    open_ = pd.DataFrame({"A": [10, 10, 20, 20, 20], "B": [5, 5, 5, 5, 5]}, index=cal, dtype=float)
    close = pd.DataFrame({"A": [10, 10, 20, 22, 22], "B": [5, 5, 5, 5, 6]}, index=cal, dtype=float)
    w = {cal[1]: pd.Series({"A": 1.0})}  # decided on day 1's close
    r = P.run(open_, close, w, start=cal[0], cash=1000, slippage_bps=100, fee=1)
    # traded at day 2's open (20), not day 1's close (10): 50 shares, 1% slippage + $1 fee
    assert r.equity[cal[1]] == 1000
    assert r.equity[cal[3]] == pytest.approx(50 * 22 - 10 - 1)
    assert r.turnover[cal[2]] == pytest.approx(1.0) and r.costs == pytest.approx(11)


def test_ic():
    cal = pd.bdate_range("2024-01-01", periods=2)
    syms = ["S%d" % i for i in range(12)]
    scores = pd.Series(range(12), index=pd.MultiIndex.from_product([[cal[0]], syms]), dtype=float)
    fwd = pd.DataFrame([np.arange(12) * 0.01, np.zeros(12)], index=cal, columns=syms)
    assert M.information_coefficients(scores, fwd)[cal[0]] == pytest.approx(1.0)
