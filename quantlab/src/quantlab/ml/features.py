"""Features and labels for cross-sectional stock selection.

Every feature at date t uses closes and volumes up to t only. Features are ranked across the stocks on each date
(0 to 1), so the model learns which stocks do better than the others, not where the whole market goes.
The label is the return from the next open to the open HORIZON trading days later, the period a trade made on
that signal would actually hold, also ranked across stocks. Labels look ahead by design: they are used only to
train on past dates whose outcome is already known, and to score predictions afterwards."""
import logging
import time

import numpy as np
import pandas as pd

from ..core import data
from ..errors import QuantLabError

log = logging.getLogger(__name__)
HORIZON = 21          # trading days held between rebalances (about a month)
MIN_HISTORY = 253     # a stock needs a year of closes before it gets features
FEATURES = {
    "r5": "1 周收益", "r21": "1 个月收益", "r63": "3 个月收益", "r126": "6 个月收益", "mom_12_1": "12-1 月动量",
    "vol21": "1 个月波动率", "vol63": "3 个月波动率", "ma50_gap": "距 50 日均线", "ma200_gap": "距 200 日均线",
    "rsi14": "RSI(14)", "dd252": "距一年最高点", "dv_ratio": "成交额变化（20 日/120 日）",
}


def load_panel(symbols, extra=("QQQ",), pause=0.2):
    """Adjusted daily bars as wide tables (date x symbol) of open, close and volume, on the US trading calendar.

    Symbols that fail to load are skipped and reported; `extra` symbols (benchmarks) are loaded but are not part
    of the universe."""
    frames, skipped = {}, {}
    for sym in list(symbols) + [s for s in extra if s not in symbols]:
        try:
            frames[sym] = data.load(sym)
        except QuantLabError as e:
            skipped[sym] = str(e)
            log.warning("skipping %s: %s", sym, e)
        time.sleep(pause)  # be gentle with the data source on a first download
    if not frames:
        raise QuantLabError("一只股票的数据都没有取到")
    calendar = frames["QQQ"].index if "QQQ" in frames else sorted(set().union(*(f.index for f in frames.values())))
    wide = {col: pd.DataFrame({s: f[col] for s, f in frames.items()}).reindex(calendar) for col in ("open", "close", "volume")}
    return wide, skipped


def _rsi(close, n=14):
    diff = close.diff()
    gain = diff.clip(lower=0).rolling(n).mean()
    loss = (-diff.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def raw_features(close, volume):
    """Wide tables of each feature, NaN where a stock lacks the history."""
    ret = close.pct_change(fill_method=None)
    dv = close * volume
    f = {
        "r5": close / close.shift(5) - 1, "r21": close / close.shift(21) - 1, "r63": close / close.shift(63) - 1,
        "r126": close / close.shift(126) - 1, "mom_12_1": close.shift(21) / close.shift(252) - 1,
        "vol21": ret.rolling(21).std(), "vol63": ret.rolling(63).std(),
        "ma50_gap": close / close.rolling(50).mean() - 1, "ma200_gap": close / close.rolling(200).mean() - 1,
        "rsi14": _rsi(close), "dd252": close / close.rolling(252).max() - 1,
        "dv_ratio": dv.rolling(20).mean() / dv.rolling(120).mean(),
    }
    has_history = close.notna() & close.shift(MIN_HISTORY - 1).notna()
    return {k: v.where(has_history) for k, v in f.items()}


def stack(wide):
    """A wide table as a (date, symbol) series without the missing cells."""
    s = wide.stack(future_stack=True)
    return s[s.notna()]


def feature_matrix(raw):
    """Long table (date, symbol) of features ranked across stocks on each date."""
    cols = {k: stack(v).groupby(level=0).rank(pct=True) for k, v in raw.items()}
    return pd.DataFrame(cols)[list(FEATURES)].dropna(how="all")


def forward_returns(open_, horizon=HORIZON):
    """Wide table: return from the open after date t to the open horizon days after that."""
    return open_.shift(-1 - horizon) / open_.shift(-1) - 1


def labels(open_, horizon=HORIZON):
    """Long series (date, symbol): the forward return's rank across stocks on each date."""
    return stack(forward_returns(open_, horizon)).groupby(level=0).rank(pct=True)
