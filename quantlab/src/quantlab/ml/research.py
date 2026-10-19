"""The machine-learning stock selection study: walk-forward model versus simple baselines on the same universe,
all through the same portfolio rules and costs."""
from dataclasses import dataclass

import pandas as pd

from ..core import metrics
from ..errors import InvalidInput
from . import features as F
from . import model as M
from . import portfolio as P

NAMES = {"model": "机器学习模型", "momentum": "12-1 月动量", "equal": "股票池等权", "qqq": "QQQ 买入持有"}


@dataclass
class Study:
    start: pd.Timestamp
    end: pd.Timestamp
    universe: list
    skipped: dict
    strategies: dict          # name -> {"result": PortfolioResult, "summary": dict}
    ic: pd.Series             # per rebalance date
    deciles: pd.Series        # mean forward return by score decile (1 = lowest)
    fits: list
    importance: pd.Series


def strategies_weights(scores, X_raw, close, rebalance_dates, universe, top, max_weight):
    """Target weights per signal date for the model and the baselines."""
    vol = X_raw["vol63"]
    mom = X_raw["mom_12_1"]
    out = {"model": {}, "momentum": {}, "equal": {}, "qqq": {}}
    for d in rebalance_dates:
        v = vol.loc[d].reindex(universe)
        if d in scores.index.get_level_values(0):
            out["model"][d] = P.target_weights(scores.loc[d], v, top, max_weight)
        out["momentum"][d] = P.target_weights(mom.loc[d].reindex(universe), v, top, max_weight)
        alive = close.loc[d].reindex(universe).dropna().index
        out["equal"][d] = pd.Series(1 / len(alive), index=alive)
        out["qqq"][d] = pd.Series({"QQQ": 1.0})
    return out


def run_study(wide, universe, skipped, start="2012-01-01", end=None, top=P.TOP_N, max_weight=P.MAX_WEIGHT,
              cash=10_000.0, slippage_bps=5.0, fee=0.0, rf=0.0):
    universe = [s for s in universe if s in wide["close"].columns]
    if len(universe) < top * 2:
        raise InvalidInput("股票池只有 %d 只，太少了" % len(universe))
    close, open_, volume = wide["close"], wide["open"], wide["volume"]
    raw = F.raw_features(close[universe], volume[universe])
    X = F.feature_matrix(raw)[list(F.FEATURES)]
    y = F.labels(open_[universe])
    cal = close.index
    rebal = M.month_ends(cal, start, end)
    rebal = [d for d in rebal if d != cal[-1]]  # the last day has no next open to trade at yet
    wf = M.walk_forward(X, y, cal, rebal)
    if wf.scores.empty:
        raise InvalidInput("训练数据不够：模型至少需要 %d 年的历史" % M.MIN_TRAIN_YEARS)
    first_pred = wf.scores.index.get_level_values(0).min()
    rebal = [d for d in rebal if d >= first_pred]
    weights = strategies_weights(wf.scores, raw, close, rebal, universe, top, max_weight)
    strategies = {}
    for key, w in weights.items():
        res = P.run(open_, close, w, start=first_pred, end=end, cash=cash, slippage_bps=slippage_bps, fee=fee)
        res.equity = res.equity[res.equity.index > first_pred]
        strategies[key] = {"name": NAMES[key], "result": res, "summary": metrics.summary(P.as_backtest_result(res, cash), rf)}

    fwd = F.forward_returns(open_[universe])
    ic = M.information_coefficients(wf.scores, fwd)
    rows = []
    for d, s in wf.scores.groupby(level=0):
        if d in fwd.index:
            r = fwd.loc[d].reindex(s.index.get_level_values(1)).values
            q = pd.qcut(s.rank(method="first"), 10, labels=False) + 1
            rows.append(pd.DataFrame({"decile": q.values, "ret": r}))
    deciles = pd.concat(rows).dropna().groupby("decile")["ret"].mean() if rows else pd.Series(dtype=float)
    final, _ = M.fit_final(X, y, cal)
    importance = pd.Series(final.feature_importance("gain"), index=list(F.FEATURES))
    importance = (importance / importance.sum()).sort_values(ascending=False)
    eq = strategies["model"]["result"].equity
    return Study(eq.index[0], eq.index[-1], universe, skipped, strategies, ic, deciles, wf.fits, importance)
