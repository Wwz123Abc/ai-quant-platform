"""Point-in-time fundamentals from SEC company facts.

Every value is the one first reported for its period, usable from the day after the filing that reported it;
later restatements are ignored. Flows (revenue, earnings, cash flow) become quarterly values, either reported
directly for three months or derived from year-to-date figures (the half year minus the first quarter, the
year minus nine months), and trailing-twelve-month sums need four consecutive quarters. Values whose period
ended more than STALE_DAYS before a date are treated as missing on that date."""
import logging

import numpy as np
import pandas as pd

from ..errors import QuantLabError
from . import sec

log = logging.getLogger(__name__)
STALE_DAYS = 450
FLOWS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["NetIncomeLoss"],
    "op_income": ["OperatingIncomeLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
}
INSTANTS = {"assets": ["Assets"], "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]}
EPS = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]
FEATURES = {
    "roa": "总资产回报率（TTM）", "op_margin": "营业利润率（TTM）", "rev_growth": "收入同比增长（TTM）",
    "earnings_yield": "盈利收益率（TTM 净利润/市值）", "book_to_market": "账面市值比", "cfo_to_ni": "经营现金流/净利润",
    "sue": "标准化未预期盈利（SUE）",
}


def _facts(facts, ns, tags, unit):
    """Entries of the first tag (in priority order) that has each period, keeping the earliest filing per period."""
    out = {}
    for tag in tags:
        for e in facts.get("facts", {}).get(ns, {}).get(tag, {}).get("units", {}).get(unit, []):
            key = (e.get("start"), e["end"])
            if key in out and out[key]["tag"] != tag:
                continue  # a higher-priority tag already covers this period
            if key not in out or e["filed"] < out[key]["filed"]:
                out[key] = {"start": e.get("start"), "end": e["end"], "val": float(e["val"]), "filed": e["filed"], "tag": tag}
    return list(out.values())


def _months(start, end):
    return round((pd.Timestamp(end) - pd.Timestamp(start)).days / 30.44)


def quarterly(entries):
    """DataFrame of quarterly flow values: end, val, avail (the first day the value was public)."""
    q = {}
    for e in entries:  # quarters reported directly
        if e["start"] and _months(e["start"], e["end"]) == 3:
            q[e["end"]] = (e["val"], e["filed"])
    by_start = {}
    for e in entries:
        if e["start"] and _months(e["start"], e["end"]) in (3, 6, 9, 12):
            by_start.setdefault(e["start"], {})[_months(e["start"], e["end"])] = e
    for ytd in by_start.values():  # quarters derived from year-to-date figures with the same start
        for m in (6, 9, 12):
            if m in ytd and m - 3 in ytd and ytd[m]["end"] not in q:
                a, b = ytd[m], ytd[m - 3]
                q[a["end"]] = (a["val"] - b["val"], max(a["filed"], b["filed"]))
    if not q:
        return pd.DataFrame(columns=["end", "val", "avail"])
    df = pd.DataFrame([(pd.Timestamp(k), v, pd.Timestamp(f) + pd.Timedelta(days=1)) for k, (v, f) in q.items()],
                      columns=["end", "val", "avail"])
    return df.sort_values("end").reset_index(drop=True)


def _consecutive(ends):
    gaps = np.diff(ends.values).astype("timedelta64[D]").astype(int)
    return bool(len(gaps)) and bool(((gaps > 75) & (gaps < 105)).all())


def ttm(q):
    """Events (end, val, avail) of trailing-twelve-month sums over four consecutive quarters."""
    rows = []
    for i in range(3, len(q)):
        w = q.iloc[i - 3:i + 1]
        if _consecutive(w["end"]):
            rows.append((w["end"].iloc[-1], w["val"].sum(), w["avail"].max()))
    return pd.DataFrame(rows, columns=["end", "val", "avail"])


def growth(t):
    """Year-over-year growth of a TTM series: the TTM four quarters later over this one."""
    rows = []
    idx = {e: i for i, e in enumerate(t["end"])}
    for _, r in t.iterrows():
        prev = [j for e, j in idx.items() if 350 <= (r["end"] - e).days <= 380]
        if prev and t["val"].iloc[prev[0]] > 0:
            p = t.iloc[prev[0]]
            rows.append((r["end"], r["val"] / p["val"] - 1, max(r["avail"], p["avail"])))
    return pd.DataFrame(rows, columns=["end", "val", "avail"])


def sue(q, min_history=4):
    """Standardised unexpected earnings: this quarter's EPS minus the same quarter a year earlier, over the
    standard deviation of the previous eight such changes."""
    rows, diffs = [], []
    by_end = dict(zip(q["end"], zip(q["val"], q["avail"], strict=True), strict=True))
    for end, (val, avail) in sorted(by_end.items()):
        prev = [e for e in by_end if 350 <= (end - e).days <= 380]
        if not prev:
            continue
        d = val - by_end[prev[0]][0]
        past = diffs[-8:]
        if len(past) >= min_history and np.std(past) > 0:
            rows.append((end, d / np.std(past), max(avail, by_end[prev[0]][1])))
        diffs.append(d)
    return pd.DataFrame(rows, columns=["end", "val", "avail"])


def instants(entries):
    if not entries:
        return pd.DataFrame(columns=["end", "val", "avail"])
    df = pd.DataFrame([(pd.Timestamp(e["end"]), e["val"], pd.Timestamp(e["filed"]) + pd.Timedelta(days=1)) for e in entries],
                      columns=["end", "val", "avail"])
    return df.sort_values("end").reset_index(drop=True)


def as_of(events, calendar):
    """Each date's value: the event with the latest period end among those already public, unless stale."""
    if events.empty:
        return pd.Series(np.nan, index=calendar)
    best, best_end, rows = None, None, []
    for _, e in events.sort_values(["avail", "end"]).iterrows():
        if best_end is None or e["end"] >= best_end:
            best, best_end = e["val"], e["end"]
        rows.append((e["avail"], best, best_end))
    s = pd.DataFrame(rows, columns=["avail", "val", "end"]).drop_duplicates("avail", keep="last").set_index("avail")
    s = s.reindex(s.index.union(calendar)).ffill().reindex(calendar)
    stale = (calendar.to_series() - s["end"]).dt.days > STALE_DAYS
    return s["val"].where(~stale.values)


def company_series(facts, calendar):
    """Wide inputs for one company: one daily (as-of) series per quantity."""
    out = {}
    q = {k: quarterly(_facts(facts, "us-gaap", tags, "USD")) for k, tags in FLOWS.items()}
    t = {k: ttm(v) for k, v in q.items()}
    for k in FLOWS:
        out[k + "_ttm"] = as_of(t[k], calendar)
    out["rev_growth"] = as_of(growth(t["revenue"]), calendar)
    for k, tags in INSTANTS.items():
        out[k] = as_of(instants(_facts(facts, "us-gaap", tags, "USD")), calendar)
    out["shares"] = as_of(instants(_facts(facts, "dei", ["EntityCommonStockSharesOutstanding"], "shares")), calendar)
    out["sue"] = as_of(sue(quarterly(_facts(facts, "us-gaap", EPS, "USD/shares"))), calendar)
    return out


def load_wide(symbols, calendar):
    """Wide tables (date x symbol) of the raw quantities, for every symbol SEC has facts for."""
    ciks = sec.cik_map()
    per, missing = {}, []
    for sym in symbols:
        cik = ciks.get(sym)
        if cik is None:
            missing.append(sym)
            continue
        try:
            per[sym] = company_series(sec.company_facts(cik), calendar)
        except QuantLabError as e:
            log.warning("no SEC facts for %s: %s", sym, e)
            missing.append(sym)
    keys = next(iter(per.values())).keys() if per else []
    wide = {k: pd.DataFrame({s: v[k] for s, v in per.items()}, index=calendar).reindex(columns=list(symbols)) for k in keys}
    return wide, missing


def features(wide, close):
    """The seven fundamental features, as wide tables aligned with `close`."""
    cap = wide["shares"] * close
    ni = wide["net_income_ttm"]
    return {
        "roa": ni / wide["assets"].where(wide["assets"] > 0),
        "op_margin": wide["op_income_ttm"] / wide["revenue_ttm"].where(wide["revenue_ttm"] > 0),
        "rev_growth": wide["rev_growth"],
        "earnings_yield": ni / cap.where(cap > 0),
        "book_to_market": wide["equity"] / cap.where(cap > 0),
        "cfo_to_ni": (wide["cfo_ttm"] / ni.where(ni.abs() > 0)).clip(-5, 5),
        "sue": wide["sue"],
    }
