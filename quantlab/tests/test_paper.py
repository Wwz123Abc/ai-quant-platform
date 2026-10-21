import numpy as np
import pandas as pd
import pytest

from quantlab.core import paper

PARAMS = {"fast": 3, "slow": 10}


class Market:
    """Fake data source whose history can be revealed one day at a time."""

    def __init__(self, n=60, seed=0):
        rng = np.random.default_rng(seed)
        idx = pd.bdate_range("2026-01-01", periods=n)
        self.full = {}
        for i, sym in enumerate(("AAA", "BBB")):
            close = 50 * np.exp(np.cumsum(rng.normal(0.002 * (1 - 2 * i), 0.02, n)))
            self.full[sym] = pd.DataFrame({"open": close * 0.99, "high": close, "low": close, "close": close, "volume": 0}, index=idx)
        self.upto = 30

    def load(self, sym, refresh=False):
        return self.full[sym].iloc[:self.upto]


@pytest.fixture
def con(tmp_path):
    return paper.connect(tmp_path / "paper.db")


def make(con, m, name="t"):
    paper.create(con, name, ["aaa", "BBB", "AAA"], PARAMS, cash=10_000, slippage=0, load=m.load)
    return next(a for a in paper.accounts(con) if a["name"] == name)


def test_create_starts_at_latest_close(con):
    m = Market()
    acct = make(con, m)
    assert acct["symbols"] == ["AAA", "BBB"]
    assert acct["start"] == m.full["AAA"].index[29].date().isoformat()
    with pytest.raises(ValueError):
        make(con, m)


def test_one_decision_per_close_and_never_rewritten(con):
    m = Market()
    acct = make(con, m)
    assert len(paper.record(con, acct, m.load)) == 2
    assert paper.record(con, acct, m.load) == []
    before = paper.signals(con, "t", "AAA")
    m.full["AAA"].loc[:, ["open", "close"]] *= 0.5  # history revised later (e.g. a new dividend adjustment)
    m.upto = 33
    assert len(paper.record(con, acct, m.load)) == 6
    after = paper.signals(con, "t", "AAA")
    pd.testing.assert_frame_equal(after.loc[before.index, ["target", "detail"]], before[["target", "detail"]])


def test_late_decisions_match_on_time_ones(con):
    m = Market()
    on_time, late = make(con, m, "on_time"), make(con, m, "late")
    for upto in range(30, 61):
        m.upto = upto
        paper.record(con, on_time, m.load)
    paper.record(con, late, m.load)
    for sym in ("AAA", "BBB"):
        a, b = paper.signals(con, "on_time", sym), paper.signals(con, "late", sym)
        assert len(a) == 31
        pd.testing.assert_frame_equal(a[["target", "detail"]], b[["target", "detail"]])


def test_replay_trades_from_the_next_open(con):
    m = Market()
    acct = make(con, m)
    paper.record(con, acct, m.load)
    rep = paper.replay(con, acct, m.load)
    assert rep["AAA"]["result"] is None  # nothing to trade until the next day opens
    m.upto = 45
    paper.record(con, acct, m.load)
    rep = paper.replay(con, acct, m.load)
    for sym in ("AAA", "BBB"):
        r = rep[sym]
        assert r["cash"] == 5_000
        first = r["result"].equity.index[0]
        assert first == m.full[sym].index[30]
        sig = r["signals"]["target"]
        if sig.iloc[0] == 1:  # held at the start's close: bought on the next open
            t = r["result"].trades.iloc[0]
            assert t["entry_date"] == first and t["entry_price"] == pytest.approx(m.full[sym]["open"].iloc[30])
        assert r["bench"].trades.iloc[0]["entry_date"] == first


def test_delete(con):
    m = Market()
    acct = make(con, m)
    paper.record(con, acct, m.load)
    assert paper.delete(con, "t") == 1
    assert paper.accounts(con) == [] and paper.signals(con, "t", "AAA").empty
