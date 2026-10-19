"""Walk-forward LightGBM: at each rebalance date, a model trained only on outcomes already known by then.

A training sample from date t has a label that ends at the open HORIZON + 1 trading days later. At rebalance
date d (decided at d's close), a sample is usable only if that open is no later than d's, so no label overlaps
the period being predicted (purging). Hyper-parameters are fixed in advance and never tuned on backtest
results, which would be a second, quieter way of fitting the past."""
import json
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .features import FEATURES, HORIZON

log = logging.getLogger(__name__)
PARAMS = {
    "objective": "regression", "learning_rate": 0.03, "num_leaves": 15, "min_data_in_leaf": 200,
    "bagging_fraction": 0.8, "bagging_freq": 1, "feature_fraction": 0.8, "lambda_l2": 1.0,
    "seed": 7, "deterministic": True, "force_row_wise": True, "num_threads": 4, "verbose": -1,
}
ROUNDS = 300
SAMPLE_EVERY = 5        # train on every 5th trading day: overlapping monthly labels add little beyond that
MIN_TRAIN_YEARS = 3
RETRAIN_MONTHS = 3


def month_ends(calendar, start=None, end=None):
    """The last trading day of each month in the calendar: the rebalance signal dates."""
    s = pd.Series(calendar, index=calendar)
    ends = s.groupby([calendar.year, calendar.month]).max().tolist()
    return [d for d in ends if (start is None or d >= pd.Timestamp(start)) and (end is None or d <= pd.Timestamp(end))]


def _fit(X, y):
    import lightgbm as lgb
    return lgb.train(PARAMS, lgb.Dataset(X[list(FEATURES)].to_numpy(), y.to_numpy(), feature_name=list(FEATURES)), ROUNDS)


@dataclass
class WalkForward:
    scores: pd.Series                     # (date, symbol) -> predicted rank score, at rebalance dates only
    fits: list = field(default_factory=list)  # one entry per training: when, on which samples


def walk_forward(X, y, calendar, rebalance_dates, sample_every=SAMPLE_EVERY, min_train_years=MIN_TRAIN_YEARS,
                 retrain_months=RETRAIN_MONTHS):
    pos = {d: i for i, d in enumerate(calendar)}
    sample_dates = set(calendar[::sample_every])
    train_index = X.index[X.index.get_level_values(0).isin(sample_dates)]
    Xs, ys = X.loc[train_index], y.reindex(train_index)
    ok = ys.notna()
    Xs, ys = Xs[ok], ys[ok]
    sample_pos = np.array([pos[d] for d in Xs.index.get_level_values(0)])

    model, last_fit, out, fits = None, None, [], []
    for d in rebalance_dates:
        if model is None or (d.year - last_fit.year) * 12 + d.month - last_fit.month >= retrain_months:
            usable = sample_pos + 1 + HORIZON <= pos[d]  # purge: the label window has closed by d's open
            if not usable.any():
                continue
            first = Xs.index[usable][0][0]
            if (d - first).days < 365 * min_train_years:
                continue
            model, last_fit = _fit(Xs[usable], ys[usable]), d
            last_sample = Xs.index[usable][-1][0]
            fits.append({"at": d.date().isoformat(), "samples": int(usable.sum()), "from": first.date().isoformat(),
                         "to": last_sample.date().isoformat()})
            log.info("fit at %s on %d samples (%s .. %s)", d.date(), usable.sum(), first.date(), last_sample.date())
        if d not in X.index.get_level_values(0):
            continue
        Xd = X.loc[d]
        out.append(pd.Series(model.predict(Xd[list(FEATURES)].to_numpy()), index=pd.MultiIndex.from_product([[d], Xd.index])))
    scores = pd.concat(out) if out else pd.Series(dtype=float)
    return WalkForward(scores.rename("score"), fits)


def information_coefficients(scores, fwd_returns_wide):
    """Spearman rank correlation, per rebalance date, between scores and the returns that followed."""
    rows = {}
    for d, s in scores.groupby(level=0):
        if d not in fwd_returns_wide.index:
            continue
        realized = fwd_returns_wide.loc[d].reindex(s.index.get_level_values(1))
        both = pd.DataFrame({"s": s.values, "r": realized.values}).dropna()
        if len(both) >= 10:
            rows[d] = both["s"].rank().corr(both["r"].rank())
    return pd.Series(rows, dtype=float)


def fit_final(X, y, calendar):
    """A model on every sample whose outcome is known at the last date, for today's signal."""
    pos = {d: i for i, d in enumerate(calendar)}
    sample_dates = set(calendar[::SAMPLE_EVERY])
    idx = X.index[X.index.get_level_values(0).isin(sample_dates)]
    Xs, ys = X.loc[idx], y.reindex(idx)
    usable = ys.notna().values & (np.array([pos[d] for d in idx.get_level_values(0)]) + 1 + HORIZON <= len(calendar) - 1)
    model = _fit(Xs[usable], ys[usable])
    info = {"samples": int(usable.sum()), "from": idx[usable][0][0].date().isoformat(), "to": idx[usable][-1][0].date().isoformat(),
            "params": PARAMS, "rounds": ROUNDS, "features": list(FEATURES), "horizon": HORIZON}
    return model, info


def save(model, info, directory):
    """The model as LightGBM text plus a JSON card describing what it was trained on."""
    directory.mkdir(parents=True, exist_ok=True)
    model.save_model(str(directory / "model.txt"))
    (directory / "card.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
