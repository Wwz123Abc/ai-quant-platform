import json

import numpy as np
import pandas as pd
import pytest

from quantlab.errors import Conflict, InvalidInput
from quantlab.ml import experiment as E


def row(sharpe, turnover):
    return {"sharpe": sharpe, "turnover": turnover}


def test_selection_rule():
    rows = {"V0": row(1.00, 1.2), "V1": row(1.10, 0.6), "V2": row(1.09, 0.4), "V3": row(0.90, 0.3), "B1": row(5.0, 0.1)}
    assert E.select(rows) == "V2"            # within 0.02 of the best Sharpe, lower turnover; baselines never chosen
    rows["V2"] = row(1.07, 0.4)
    assert E.select(rows) == "V1"


def test_monthly_active_t_stat():
    idx = pd.bdate_range("2022-01-03", "2023-12-29")
    rng = np.random.default_rng(1)
    base = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, len(idx))), index=idx)
    better = base * np.linspace(1, 1.2, len(idx))
    mean, t, n = E.monthly_active(better, base)
    assert mean > 0 and t > 3 and n == 23


def test_holdout_needs_dev_and_runs_once(monkeypatch, tmp_path):
    monkeypatch.setattr(E, "results_dir", lambda: tmp_path)
    with pytest.raises(InvalidInput):
        E.run_holdout(data={})
    (tmp_path / ("%s-dev.json" % E.NAME)).write_text(json.dumps({"selected": "V1"}))
    (tmp_path / ("%s-holdout.json" % E.NAME)).write_text(json.dumps({"passed": False}))
    with pytest.raises(Conflict):
        E.run_holdout(data={})
