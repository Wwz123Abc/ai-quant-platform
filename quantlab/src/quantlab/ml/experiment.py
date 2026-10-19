"""The pre-registered v2 experiment (docs/experiments/2026-10-ml-v2-preregistration.md).

Two phases. `dev` runs every variant and baseline on the development period and picks one by the registered
rule. `holdout` runs the picked variant and the baselines once on the holdout period and applies the registered
pass criterion. The holdout phase refuses to run before a development result exists, and refuses to run a
second time: a holdout that is looked at repeatedly stops being a test."""
import datetime as dt
import json
import logging
import math

import pandas as pd

from ..config import get_settings
from ..core import metrics
from ..errors import Conflict, InvalidInput
from . import features as F
from . import fundamentals as FD
from . import model as M
from . import portfolio as P
from . import universe as U

log = logging.getLogger(__name__)
NAME = "2026-10-ml-v2"
DEV = ("2012-01-01", "2021-12-31")
HOLDOUT_START = "2022-01-01"
TOP, BUFFER, MAX_WEIGHT, CASH, SLIPPAGE = 10, 20, 0.20, 10_000.0, 5.0
VARIANTS = {
    "V0": {"model": "lgbm", "horizon": 21, "fund": False, "turnover": False, "blend": False},
    "V1": {"model": "lgbm", "horizon": 21, "fund": False, "turnover": True, "blend": False},
    "V2": {"model": "lgbm", "horizon": 21, "fund": False, "turnover": True, "blend": True},
    "V3": {"model": "lgbm", "horizon": 63, "fund": False, "turnover": True, "blend": False},
    "V4": {"model": "lgbm", "horizon": 63, "fund": True, "turnover": True, "blend": False},
    "V5": {"model": "ridge", "horizon": 63, "fund": True, "turnover": True, "blend": False},
}
LABELS = {
    "V0": "LightGBM · 21 天 · 价格因子", "V1": "V0 + 换手控制", "V2": "V1 与动量排名平均", "V3": "LightGBM · 63 天 · 价格因子 · 换手控制",
    "V4": "V3 + 时点财务因子", "V5": "岭回归 · 63 天 · 价格 + 财务因子（对照）",
    "B1": "12-1 月动量", "B2": "12-1 月动量 + 换手控制", "B3": "当月成分股等权", "B4": "QQQ 买入持有",
}


def results_dir():
    return get_settings().data_dir.parent / "docs" / "experiments"


def prepare():
    """Everything the variants share: point-in-time membership, prices, features and labels."""
    current = U.load()["symbols"]
    hist = U.load_history()
    changes = hist["changes"]
    ever = U.ever_members(current, changes, "2007-01-01")
    wide, skipped = F.load_panel(ever, pause=0.1)
    close, open_, volume = wide["close"], wide["open"], wide["volume"]
    syms = [s for s in ever if s in close.columns]
    cal = close.index
    mask = U.membership_mask(cal, syms, current, changes) & close[syms].notna()
    raw = F.raw_features(close[syms], volume[syms])
    fwide, no_sec = FD.load_wide(syms, cal)
    raw.update(FD.features(fwide, close[syms]))
    names_price, names_all = list(F.FEATURES), list(F.FEATURES) + list(FD.FEATURES)
    X_all = F.feature_matrix(raw, mask, names_all)
    coverage = {}
    for d in M.month_ends(cal, "2008-01-01"):
        members = U.members_at(d.date(), current, changes)
        coverage[d] = sum(1 for s in members if s in close.columns and pd.notna(close.at[d, s])) / len(members)
    return {
        "cal": cal, "close": close, "open": open_, "mask": mask, "syms": syms, "raw": raw,
        "X_price": X_all[names_price].dropna(how="all"), "X_all": X_all,
        "y": {h: F.labels(open_[syms], h, mask) for h in (21, 63)},
        "skipped": sorted(skipped), "no_sec": sorted(no_sec), "coverage": pd.Series(coverage),
        "wiki_fetched": hist["fetched"], "universe_as_of": U.load()["as_of"],
    }


def _ranks(s):
    return s.rank(pct=True)


def _smooth(scores_by_date, d, dates, n=3):
    """Average of the last n months' cross-sectional ranks (only past and current predictions)."""
    i = dates.index(d)
    recent = [_ranks(scores_by_date[x]) for x in dates[max(0, i - n + 1): i + 1] if x in scores_by_date]
    return pd.concat(recent, axis=1).mean(axis=1)


def _members_with_price(data, d):
    m = data["mask"].loc[d]
    return list(m.index[m])


def model_scores(data, cfg, dates):
    """Walk-forward predictions at each signal date, as {date: Series(symbol -> score)}."""
    X = data["X_all"] if cfg["fund"] else data["X_price"]
    fit = M.fit_ridge if cfg["model"] == "ridge" else None
    wf = M.walk_forward(X, data["y"][cfg["horizon"]], data["cal"], dates, horizon=cfg["horizon"], fit=fit)
    return {d: s.droplevel(0) for d, s in wf.scores.groupby(level=0)}, len(wf.fits)


def portfolio_weights(score_fn, data, dates, turnover):
    """Signal date -> target weights, holding TOP names (with the buffer when turnover control is on)."""
    out, held = {}, []
    vol = data["raw"]["vol63"]
    for d in dates:
        scores = score_fn(d)
        if scores is None or scores.empty:
            continue
        eligible = scores.reindex(_members_with_price(data, d)).dropna()
        names = P.select(eligible, held, TOP, BUFFER if turnover else None)
        w = P.weights_for(names, vol.loc[d], MAX_WEIGHT)
        out[d], held = w, list(w.index)
    return out


def run_phase(data, start, end, keys):
    """Runs the given variants and baselines between start and end. Returns {key: result dict}."""
    cal = data["cal"]
    all_dates = M.month_ends(cal, "2010-01-01", end)
    all_dates = [d for d in all_dates if d != cal[-1]]
    dates = [d for d in all_dates if d >= pd.Timestamp(start)]
    warm = all_dates[max(0, all_dates.index(dates[0]) - 2):]  # two earlier months feed the 3-month average
    mom = data["raw"]["mom_12_1"]
    mom_scores = {d: mom.loc[d].reindex(_members_with_price(data, d)).dropna() for d in warm}
    out = {}
    for key in keys:
        fits, ic = 0, None
        if key in VARIANTS:
            cfg = VARIANTS[key]
            scores, fits = model_scores(data, cfg, warm)
            fwd = F.forward_returns(data["open"][data["syms"]], cfg["horizon"])
            ic = M.information_coefficients(pd.concat({d: s for d, s in scores.items() if d in dates}), fwd)
            if cfg["blend"]:
                fn = lambda d, sc=scores: (pd.concat([_smooth(sc, d, warm), _ranks(mom_scores[d])], axis=1).mean(axis=1)
                                           if d in sc else None)
            elif cfg["turnover"]:
                fn = lambda d, sc=scores: _smooth(sc, d, warm) if d in sc else None
            else:
                fn = lambda d, sc=scores: sc.get(d)
            weights = portfolio_weights(fn, data, dates, cfg["turnover"])
        elif key == "B1":
            weights = portfolio_weights(lambda d: mom_scores[d], data, dates, False)
        elif key == "B2":
            weights = portfolio_weights(lambda d: _smooth(mom_scores, d, warm), data, dates, True)
        elif key == "B3":
            weights = {d: pd.Series(1 / len(m), index=m) for d in dates for m in [_members_with_price(data, d)]}
        elif key == "B4":
            weights = {d: pd.Series({"QQQ": 1.0}) for d in dates}
        else:
            raise InvalidInput(key)
        res = P.run(data["open"], data["close"], weights, start=dates[0], end=end, cash=CASH, slippage_bps=SLIPPAGE)
        res.equity = res.equity[res.equity.index > dates[0]]
        summ = metrics.summary(P.as_backtest_result(res, CASH))
        out[key] = {"label": LABELS[key], "summary": summ, "turnover": float(res.turnover.mean()), "costs": res.costs,
                    "equity": res.equity, "fits": fits, "ic": ic}
    return out


def monthly_active(a, b):
    ra = a.resample("ME").last().pct_change().dropna()
    rb = b.resample("ME").last().pct_change().dropna()
    diff = (ra - rb).dropna()
    t = diff.mean() / diff.std() * math.sqrt(len(diff)) if len(diff) > 1 and diff.std() > 0 else float("nan")
    return float(diff.mean()), float(t), len(diff)


def _row(r):
    s = r["summary"]
    ic = r["ic"]
    return {"label": r["label"], "cagr": s["cagr"], "vol": s["vol"], "sharpe": s["sharpe"], "mdd": s["mdd"],
            "turnover": r["turnover"], "costs": r["costs"], "fits": r["fits"],
            "ic_mean": None if ic is None or ic.empty else float(ic.mean()),
            "ic_positive": None if ic is None or ic.empty else float((ic > 0).mean())}


def select(dev_rows):
    """The registered rule: highest Sharpe among V0-V5; within 0.02 of it, the lowest turnover."""
    vs = {k: v for k, v in dev_rows.items() if k in VARIANTS}
    best = max(v["sharpe"] for v in vs.values())
    near = [k for k, v in vs.items() if v["sharpe"] >= best - 0.02]
    return min(near, key=lambda k: vs[k]["turnover"])


def run_dev(data=None):
    data = data or prepare()
    res = run_phase(data, *DEV, list(VARIANTS) + ["B1", "B2", "B3", "B4"])
    rows = {k: _row(v) for k, v in res.items()}
    chosen = select(rows)
    cov = data["coverage"]
    dev_cov = cov[(cov.index >= DEV[0]) & (cov.index <= DEV[1])]
    record = {"experiment": NAME, "phase": "dev", "run_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
              "period": DEV, "rows": rows, "selected": chosen, "coverage_mean": float(dev_cov.mean()),
              "coverage_by_year": {str(y): float(v) for y, v in dev_cov.groupby(dev_cov.index.year).mean().items()},
              "price_missing": len(data["skipped"]), "sec_missing": len(data["no_sec"]), "members_with_prices": len(data["syms"]),
              "wiki_fetched": data["wiki_fetched"], "universe_as_of": data["universe_as_of"]}
    _write(record, "dev")
    return record, res


def run_holdout(data=None, end=None):
    dev = _read("dev")
    if dev is None:
        raise InvalidInput("还没有开发期结果：先运行开发期")
    if _read("holdout") is not None:
        raise Conflict("考试期已经运行过一次。按预登记规则，不再重复运行（重复查看会让考试期失去意义）")
    data = data or prepare()
    chosen = dev["selected"]
    res = run_phase(data, HOLDOUT_START, end, [chosen, "B1", "B2", "B3", "B4"])
    rows = {k: _row(v) for k, v in res.items()}
    mean, t, n = monthly_active(res[chosen]["equity"], res["B2"]["equity"])
    passed = rows[chosen]["sharpe"] > rows["B2"]["sharpe"] and t > 1.5
    cov = data["coverage"]
    record = {"experiment": NAME, "phase": "holdout", "run_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
              "period": [HOLDOUT_START, str(res[chosen]["equity"].index[-1].date())], "selected": chosen, "rows": rows,
              "active_vs_B2": {"monthly_mean": mean, "t": t, "months": n}, "passed": bool(passed),
              "coverage_mean": float(cov[cov.index >= HOLDOUT_START].mean())}
    _write(record, "holdout")
    return record, res


def _path(phase):
    return results_dir() / ("%s-%s.json" % (NAME, phase))


def _read(phase):
    p = _path(phase)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _write(record, phase):
    p = _path(phase)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    report_markdown()


def _fmt_rows(rows):
    pct = lambda v: "—" if v is None or v != v else "%+.1f%%" % (v * 100)
    lines = ["| 版本 | 说明 | 年化 | 波动率 | 夏普 | 最大回撤 | 月均换手 | 平均 IC |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for k, r in rows.items():
        lines.append("| %s | %s | %s | %.1f%% | %.2f | %s | %.0f%% | %s |" % (
            k, r["label"], pct(r["cagr"]), r["vol"] * 100, r["sharpe"], pct(r["mdd"]), r["turnover"] * 100,
            "—" if r["ic_mean"] is None else "%.3f" % r["ic_mean"]))
    return "\n".join(lines)


PASS_TEXT = "满足预登记标准：机器学习相对动量有增益的弱证据。"
FAIL_TEXT = "不满足预登记标准：没有证据表明机器学习模型优于动量规则。"


def report_markdown():
    """docs/experiments/<name>-results.md from whatever phases have run."""
    dev, hold = _read("dev"), _read("holdout")
    if dev is None:
        return
    out = ["# 实验结果：机器学习选股 v2", "", "预登记：[%s-preregistration.md](%s-preregistration.md)。本文件由程序生成。" % (NAME, NAME), "",
           "## 开发期 %s 至 %s" % tuple(dev["period"]), "",
           "运行于 %s · 有价格的历史成分股 %d 只（另有 %d 只查不到价格）· 成分股价格覆盖率平均 %.0f%% · 维基百科调整记录抓取于 %s" % (
               dev["run_at"], dev["members_with_prices"], dev["price_missing"], dev["coverage_mean"] * 100, dev["wiki_fetched"]), "",
           _fmt_rows(dev["rows"]), "", "**按预登记规则选中：%s**（开发期夏普最高；相差 0.02 以内时取换手更低者）。" % dev["selected"], ""]
    if hold:
        a = hold["active_vs_B2"]
        out += ["## 考试期 %s 至 %s（只运行一次）" % tuple(hold["period"]), "", "运行于 %s · 成分股价格覆盖率平均 %.0f%%" % (
            hold["run_at"], hold["coverage_mean"] * 100), "", _fmt_rows(hold["rows"]), "",
            "相对 B2 的月度超额收益：平均 %+.2f%%，t 值 %.2f（%d 个月）。" % (a["monthly_mean"] * 100, a["t"], a["months"]), "",
            "**判定：%s**" % (PASS_TEXT if hold["passed"] else FAIL_TEXT), ""]
    else:
        out += ["## 考试期", "", "尚未运行。", ""]
    (results_dir() / ("%s-results.md" % NAME)).write_text("\n".join(out), encoding="utf-8")
