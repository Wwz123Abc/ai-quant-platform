"""The moving-average rule test registered in docs/research/2026-11-ma-rules.md. Run: python scripts/ma_rules_research.py

Writes docs/research/data/ma-rules-results.json and prints the table. Nothing here is tuned: the rules, the stocks, the
costs and the criteria are exactly the ones written down before the run."""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quantlab.core import backtest, data, metrics  # noqa: E402
from quantlab.holdings import ma_rules  # noqa: E402

SYMBOLS = ["SPY", "QQQ", "VOO", "AAPL", "MSFT", "NVDA", "KO", "JNJ", "PEP", "MCD", "ORCL", "NIO"]
CASH, FEE, SLIP, WARMUP, MIN_YEARS = 10_000.0, "trade25", 5.0, 250, 5
OUT = Path(__file__).resolve().parents[2] / "docs" / "research" / "data" / "ma-rules-results.json"


def one(bars, target):
    kw = {"cash": CASH, "fee": FEE, "slippage_bps": SLIP}
    r = metrics.summary(backtest.run(bars, target, **kw))
    b = metrics.summary(backtest.buy_and_hold(bars, **kw))
    return {
        "cagr": r["cagr"], "bh_cagr": b["cagr"], "mdd": r["mdd"], "bh_mdd": b["mdd"], "sharpe": r["sharpe"], "bh_sharpe": b["sharpe"],
        "buys_per_year": r["trades"] / r["years"], "exposure": r["exposure"], "fees": r["fees"], "years": r["years"],
    }


def run():
    rows = []  # one per stock x rule x period
    used = {}
    for sym in SYMBOLS:
        try:
            full = data.load(sym)
        except Exception as e:  # a symbol that cannot be fetched is reported, not silently dropped
            print("skipped", sym, e, file=sys.stderr)
            continue
        bars = full.iloc[WARMUP:]
        if len(bars) / 252 < MIN_YEARS:
            print("skipped (history under %d years): %s" % (MIN_YEARS, sym), file=sys.stderr)
            continue
        used[sym] = [bars.index[0].date().isoformat(), bars.index[-1].date().isoformat()]
        tg = ma_rules.targets(full["close"])
        mid = len(bars) // 2
        for period, b in (("full", bars), ("oos", bars.iloc[mid:])):
            for rid in tg.columns:
                rows.append({"symbol": sym, "rule": rid, "period": period, **one(b, tg[rid].reindex(b.index.union(tg.index)).ffill().fillna(0))})
    return rows, used


def judge(rows):
    out = {}
    for rid, name, _ in ma_rules.RULES:
        res = {"name": name}
        for period in ("full", "oos"):
            rs = [r for r in rows if r["rule"] == rid and r["period"] == period]
            n = len(rs)
            res[period] = {
                "n": n,
                "dd_ok": sum(abs(r["mdd"]) <= 0.8 * abs(r["bh_mdd"]) for r in rs) / n,
                "sharpe_ok": sum(r["sharpe"] >= r["bh_sharpe"] for r in rs) / n,
                "med_cagr": statistics.median(r["cagr"] for r in rs), "med_bh_cagr": statistics.median(r["bh_cagr"] for r in rs),
                "med_mdd": statistics.median(r["mdd"] for r in rs), "med_bh_mdd": statistics.median(r["bh_mdd"] for r in rs),
                "med_sharpe": statistics.median(r["sharpe"] for r in rs), "med_bh_sharpe": statistics.median(r["bh_sharpe"] for r in rs),
                "med_buys_per_year": statistics.median(r["buys_per_year"] for r in rs), "med_exposure": statistics.median(r["exposure"] for r in rs),
            }
        f, o = res["full"], res["oos"]
        c1 = f["dd_ok"] >= 2 / 3
        c2 = f["sharpe_ok"] >= 0.6
        c3 = f["med_buys_per_year"] <= 6
        c4 = o["dd_ok"] >= 2 / 3 and o["sharpe_ok"] >= 0.6
        res["criteria"] = {"drawdown": c1, "sharpe": c2, "frequency": c3, "out_of_sample": c4}
        res["verdict"] = "通过" if c1 and c2 and c3 and c4 else "只降回撤" if c1 and o["dd_ok"] >= 2 / 3 and c3 else "未通过"
        out[rid] = res
    return out


if __name__ == "__main__":
    rows, used = run()
    verdicts = judge(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"stocks": used, "verdicts": verdicts, "rows": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("stocks:", dict(used))
    pc = lambda x: "%.0f%%" % (x * 100)
    print("| 规则 | 回撤达标 | 夏普达标 | 年买入次数 | 年化 规则/买入持有 | 最大回撤 规则/买入持有 | 夏普 规则/买入持有 | 持有时间 "
          "| 样本外 回撤/夏普达标 | 结论 |")
    for v in verdicts.values():
        f, o = v["full"], v["oos"]
        print("| %s | %s | %s | %.1f | %s / %s | %s / %s | %.2f / %.2f | %s | %s / %s | **%s** |" % (
            v["name"], pc(f["dd_ok"]), pc(f["sharpe_ok"]), f["med_buys_per_year"], pc(f["med_cagr"]), pc(f["med_bh_cagr"]),
            pc(f["med_mdd"]), pc(f["med_bh_mdd"]), f["med_sharpe"], f["med_bh_sharpe"], pc(f["med_exposure"]),
            pc(o["dd_ok"]), pc(o["sharpe_ok"]), v["verdict"]))
