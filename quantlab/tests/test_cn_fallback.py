"""Raw daily bars come from Tencent, then Sina, then Eastmoney: one source failing or lagging does not leave the platform without prices."""
import datetime as dt
import json
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from quantlab.core import cn_data
from quantlab.errors import DataSourceError

NY = ZoneInfo("America/New_York")


def frame(last, n=3):
    days = pd.bdate_range(end=last, periods=n)
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=days)


@pytest.fixture
def tuesday(monkeypatch):
    """It is Tuesday 2026-11-10 11:00 in New York: the latest finished session is Monday 2026-11-09."""
    monkeypatch.setattr(cn_data, "last_session", lambda now=None: dt.date(2026, 11, 9))


def sources(monkeypatch, tencent, sina, em):
    for name, src in (("tencent_bars", tencent), ("sina_raw_bars", sina), ("eastmoney_raw_bars", em)):
        monkeypatch.setattr(cn_data, name, src if callable(src) else (lambda sym, v=src: v))


def boom(sym):
    raise DataSourceError("行情接口返回错误 501")


def test_tencent_is_used_when_it_has_the_latest_session(tuesday, monkeypatch):
    sources(monkeypatch, frame("2026-11-09"), boom, boom)
    assert cn_data.raw_bars("VOO").index[-1] == pd.Timestamp("2026-11-09")


def test_a_failing_tencent_falls_back_to_sina_then_eastmoney(tuesday, monkeypatch):
    sources(monkeypatch, boom, frame("2026-11-09"), boom)
    assert cn_data.raw_bars("VOO").index[-1] == pd.Timestamp("2026-11-09")
    sources(monkeypatch, boom, boom, frame("2026-11-09"))
    assert cn_data.raw_bars("VOO").index[-1] == pd.Timestamp("2026-11-09")


def test_a_lagging_source_is_not_trusted_when_another_has_the_newer_bar(tuesday, monkeypatch):
    sources(monkeypatch, frame("2026-11-06"), frame("2026-11-09"), boom)      # Tencent is a session behind
    assert cn_data.raw_bars("VOO").index[-1] == pd.Timestamp("2026-11-09")
    sources(monkeypatch, frame("2026-11-06"), frame("2026-11-06"), frame("2026-11-05"))   # nobody is current: the newest bar wins
    assert cn_data.raw_bars("VOO").index[-1] == pd.Timestamp("2026-11-06")


def test_every_source_down_is_one_clear_error(tuesday, monkeypatch):
    sources(monkeypatch, boom, boom, boom)
    with pytest.raises(DataSourceError, match="所有行情来源都取不到 VOO"):
        cn_data.raw_bars("VOO")


def test_the_latest_finished_session():
    at = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=NY)
    assert cn_data.last_session(at("2026-11-10 11:00")) == dt.date(2026, 11, 9)    # Tuesday morning: Monday
    assert cn_data.last_session(at("2026-11-10 17:00")) == dt.date(2026, 11, 10)   # Tuesday after the close: Tuesday
    assert cn_data.last_session(at("2026-11-14 12:00")) == dt.date(2026, 11, 13)   # Saturday: Friday
    assert cn_data.last_session(at("2026-11-09 10:00")) == dt.date(2026, 11, 6)    # Monday morning: Friday


def test_sina_and_eastmoney_rows_become_unadjusted_bars(monkeypatch):
    rows = [{"d": "2026-11-06", "o": "690", "h": "695", "l": "688", "c": "693.54", "v": "100"},
            {"d": "2026-11-09", "o": "682", "h": "694.39", "l": "681.18", "c": "691.87", "v": "200"},
            {"d": "bad", "o": "1", "h": "1", "l": "1", "c": "1", "v": "1"}]
    sina = "var _VOO=(" + json.dumps(rows) + ");"
    monkeypatch.setattr(cn_data, "_get", lambda url, ref, enc="utf-8": sina)
    df = cn_data.sina_raw_bars("VOO")
    assert list(df.index.strftime("%Y-%m-%d")) == ["2026-11-06", "2026-11-09"] and df["close"].iloc[-1] == 691.87
    em = json.dumps({"data": {"klines": ["2026-11-09,682,691.87,694.39,681.18,200,0"]}})
    monkeypatch.setattr(cn_data, "_get", lambda url, ref, enc="utf-8": em)
    df = cn_data.eastmoney_raw_bars("VOO")
    assert df["open"].iloc[0] == 682 and df["high"].iloc[0] == 694.39 and df["close"].iloc[0] == 691.87     # open, close, high, low are mapped by name


def test_the_probe_date_comes_from_eastmoney_when_tencents_quote_is_down(monkeypatch):
    from quantlab import scheduler
    monkeypatch.setenv("QUANTLAB_PRICE_SOURCE", "cn")
    from quantlab.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr(cn_data, "tencent_quote", boom)
    monkeypatch.setattr(cn_data, "eastmoney_session_date", lambda sym: "2026-11-09")
    assert scheduler.market_date() == "2026-11-09"
