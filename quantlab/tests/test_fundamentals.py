import pandas as pd
import pytest

from quantlab.ml import fundamentals as FD


def fact(start, end, val, filed):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": "10-Q"}


def facts(entries, tag="Revenues", ns="us-gaap", unit="USD"):
    return {"facts": {ns: {tag: {"units": {unit: entries}}}}}


# fiscal year = calendar year; Q1-Q3 reported for three months, the year only as a whole (as most 10-Ks do)
REVENUE = [
    fact("2023-01-01", "2023-03-31", 10, "2023-05-01"), fact("2023-04-01", "2023-06-30", 11, "2023-08-01"),
    fact("2023-07-01", "2023-09-30", 12, "2023-11-01"), fact("2023-01-01", "2023-09-30", 33, "2023-11-01"),
    fact("2023-01-01", "2023-12-31", 46, "2024-02-15"),                       # Q4 = 46 - 33 = 13
    fact("2024-01-01", "2024-03-31", 14, "2024-05-01"),
    fact("2024-01-01", "2024-03-31", 99, "2025-05-01"),                       # a later restatement: ignored
]


def test_quarters_from_direct_and_year_to_date_values():
    q = FD.quarterly(FD._facts(facts(REVENUE), "us-gaap", ["Revenues"], "USD"))
    assert q["val"].tolist() == [10, 11, 12, 13, 14]
    assert q["avail"].iloc[3] == pd.Timestamp("2024-02-16")   # the day after the 10-K
    assert q["avail"].iloc[4] == pd.Timestamp("2024-05-02")   # first report, not the restatement


def test_cash_flow_quarters_from_year_to_date_only():
    cfo = [fact("2023-01-01", "2023-03-31", 5, "2023-05-01"), fact("2023-01-01", "2023-06-30", 12, "2023-08-01"),
           fact("2023-01-01", "2023-09-30", 20, "2023-11-01"), fact("2023-01-01", "2023-12-31", 30, "2024-02-15")]
    q = FD.quarterly(FD._facts(facts(cfo, "NetCashProvidedByUsedInOperatingActivities"), "us-gaap",
                               ["NetCashProvidedByUsedInOperatingActivities"], "USD"))
    assert q["val"].tolist() == [5, 7, 8, 10]


def test_ttm_needs_four_consecutive_quarters_and_is_point_in_time():
    q = FD.quarterly(FD._facts(facts(REVENUE), "us-gaap", ["Revenues"], "USD"))
    t = FD.ttm(q)
    assert t["val"].tolist() == [46, 50] and t["avail"].tolist() == [pd.Timestamp("2024-02-16"), pd.Timestamp("2024-05-02")]
    cal = pd.bdate_range("2024-02-12", "2024-05-06")
    s = FD.as_of(t, cal)
    assert pd.isna(s["2024-02-15"]) and s["2024-02-16"] == 46 and s["2024-05-01"] == 46 and s["2024-05-02"] == 50
    gaps = q.drop(index=1)  # a missing quarter: no TTM across the gap
    assert FD.ttm(gaps).empty


def test_stale_values_expire():
    events = pd.DataFrame({"end": [pd.Timestamp("2020-12-31")], "val": [1.0], "avail": [pd.Timestamp("2021-02-01")]})
    s = FD.as_of(events, pd.bdate_range("2021-02-01", "2022-06-30"))
    assert s["2021-02-01"] == 1.0 and pd.isna(s["2022-06-30"])


def test_growth_and_sue():
    ends = pd.date_range("2019-03-31", periods=16, freq="QE")
    q = pd.DataFrame({"end": ends, "val": [1.0 + 0.1 * i + (0.5 if i == 15 else 0) for i in range(16)],
                      "avail": ends + pd.Timedelta(days=40)})
    t = FD.ttm(q)
    g = FD.growth(t)
    assert g["val"].iloc[0] == pytest.approx(t["val"].iloc[4] / t["val"].iloc[0] - 1)
    s = FD.sue(q.assign(val=q["val"] + [0.01 * (i % 3) for i in range(16)]))
    assert s["val"].iloc[-1] > 10  # the last quarter's jump stands far outside the usual year-over-year changes


def test_features_use_market_cap():
    cal = pd.bdate_range("2024-01-01", periods=3)
    one = lambda v: pd.DataFrame({"A": [v] * 3}, index=cal, dtype=float)
    wide = {"shares": one(10), "net_income_ttm": one(50), "assets": one(500), "op_income_ttm": one(30), "revenue_ttm": one(100),
            "rev_growth": one(0.1), "equity": one(200), "cfo_ttm": one(400), "sue": one(1.5)}
    f = FD.features(wide, one(20))  # market cap 200
    assert f["earnings_yield"].iat[0, 0] == pytest.approx(0.25) and f["book_to_market"].iat[0, 0] == pytest.approx(1.0)
    assert f["roa"].iat[0, 0] == pytest.approx(0.1) and f["cfo_to_ni"].iat[0, 0] == 5  # 8, capped at 5
