"""Command line: python -m quant backtest MCD  /  scan QQQ  /  signal MCD QQQM VOO"""
import argparse
import sys
import unicodedata
from pathlib import Path

import pandas as pd

from . import backtest, data, metrics, paper, scan, strategy

REPORTS = Path(__file__).resolve().parent.parent / "reports"


def width(s):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s, w):
    return s + " " * max(0, w - width(s))


def pct(x):
    return "—" if pd.isna(x) else "%+.1f%%" % (x * 100)


def money(x):
    return "$" + format(x, ",.0f")


def cmd_backtest(a):
    bars = data.load(a.symbol, a.refresh)
    sig = strategy.ma_cross(bars["close"], a.fast, a.slow)
    first = sig["slow"].first_valid_index()
    if first is None:
        sys.exit("%s 只有 %d 个交易日的数据，不够算 %d 日均线。" % (a.symbol, len(bars), a.slow))
    start = max(first, pd.Timestamp(a.start)) if a.start else first
    window = bars.loc[start:a.end] if a.end else bars.loc[start:]
    if len(window) < 2:
        sys.exit("这个时间段内没有足够的数据。")
    kw = dict(cash=a.cash, fee=a.fee, slippage_bps=a.slippage)
    strat, bench = backtest.run(window, sig["target"], **kw), backtest.buy_and_hold(window, **kw)
    s, b = metrics.summary(strat, a.rf), metrics.summary(bench, a.rf)

    print("\n%s  均线 %d/%d  %s → %s（%.1f 年）" % (a.symbol.upper(), a.fast, a.slow, s["start"], s["end"], s["years"]))
    print("初始资金 %s · 每笔手续费 $%g · 滑点 %g bp · 信号按收盘价计算，下一个交易日开盘成交\n" % (money(a.cash), a.fee, a.slippage))
    dd = lambda m: "%s（%s → %s）" % (pct(m["mdd"]), m["mdd_peak"], m["mdd_trough"])
    rows = [("", "均线策略", "买入持有"),
            ("期末资产", money(s["final"]), money(b["final"])),
            ("总收益", pct(s["total"]), pct(b["total"])),
            ("年化收益", pct(s["cagr"]), pct(b["cagr"])),
            ("年化波动率", pct(s["vol"]).lstrip("+"), pct(b["vol"]).lstrip("+")),
            ("夏普比率", "%.2f" % s["sharpe"], "%.2f" % b["sharpe"]),
            ("最大回撤", dd(s), dd(b)),
            ("持仓时间占比", "%.0f%%" % (s["exposure"] * 100), "%.0f%%" % (b["exposure"] * 100)),
            ("交易次数", "%d" % s["trades"], "%d" % b["trades"]),
            ("胜率", "—" if pd.isna(s["win_rate"]) else "%.0f%%" % (s["win_rate"] * 100), "—"),
            ("平均每笔收益", pct(s["avg_trade"]), "—")]
    w2 = max(width(r[1]) for r in rows) + 3
    for r in rows:
        print("  " + pad(r[0], 14) + pad(r[1], w2) + r[2])

    REPORTS.mkdir(exist_ok=True)
    stem = "%s_ma%d_%d" % (a.symbol.upper(), a.fast, a.slow)
    strat.trades.to_csv(REPORTS / (stem + "_trades.csv"), index=False, float_format="%.4f")
    print("\n交易明细：reports/%s_trades.csv" % stem)
    if not a.no_chart:
        from . import report
        report.chart(REPORTS / (stem + ".png"), a.symbol.upper(), sig, strat, bench, a.fast, a.slow)
        print("图表：reports/%s.png" % stem)
    print("\n提示：回测是历史表现，不代表未来；反复调参数挑出来的最好结果，往往是过拟合。")


def table(rows, gap=3):
    ws = [max(width(r[i]) for r in rows) + gap for i in range(len(rows[0]))]
    for r in rows:
        print("  " + "".join(pad(c, w) for c, w in zip(r, ws)).rstrip())


def span(w):
    return "%s → %s（%.1f 年）" % (w.index[0].date(), w.index[-1].date(), (w.index[-1] - w.index[0]).days / 365.25)


def cmd_scan(a):
    bars = data.load(a.symbol, a.refresh)
    try:
        ins, oos = scan.windows(bars, max(a.slow), a.start, a.end, a.split)
    except ValueError as e:
        sys.exit(str(e))
    kw = dict(cash=a.cash, fee=a.fee, slippage_bps=a.slippage)
    df = scan.scan(bars, ins, oos, a.fast, a.slow, a.rf, **kw)
    if len(df) < 3:
        sys.exit("参数组合太少（短均线要小于长均线），至少要 3 组才有比较意义。")
    df = scan.rank(df, a.by)
    bh = metrics.summary(backtest.buy_and_hold(ins, **kw), a.rf), metrics.summary(backtest.buy_and_hold(oos, **kw), a.rf)
    name = {"sharpe": "夏普", "cagr": "年化"}[a.by]
    sym, n = a.symbol.upper(), len(df)

    print("\n%s 均线参数扫描 · 短均线 %s · 长均线 %s · 共 %d 组 · 按样本内%s排名" % (
        sym, "/".join(map(str, a.fast)), "/".join(map(str, a.slow)), n, {"sharpe": "夏普比率", "cagr": "年化收益"}[a.by]))
    print("样本内（挑参数）：%s" % span(ins))
    print("样本外（检验）：  %s\n" % span(oos))
    rows = [("样本内排名", "参数", "样本内夏普", "样本内年化", "样本外夏普", "样本外年化", "样本外排名")]
    line = lambda r: ("%.2f" % r["is_sharpe"], pct(r["is_cagr"]), "%.2f" % r["oos_sharpe"], pct(r["oos_cagr"]))
    for _, r in df.head(a.top).iterrows():
        rows.append(("%d" % r["is_rank"], "%d/%d" % (r["fast"], r["slow"]), *line(r), "%d / %d" % (r["oos_rank"], n)))
    ref = df[(df["fast"] == 50) & (df["slow"] == 200)]
    if len(ref) and ref.index[0] >= a.top:
        r = ref.iloc[0]
        rows.append(("%d" % r["is_rank"], "50/200", *line(r), "%d / %d" % (r["oos_rank"], n)))
    rows.append(("—", "买入持有", "%.2f" % bh[0]["sharpe"], pct(bh[0]["cagr"]), "%.2f" % bh[1]["sharpe"], pct(bh[1]["cagr"]), "—"))
    table(rows)

    best, rho = df.iloc[0], scan.rank_corr(df, a.by)
    med = df["oos_" + a.by].median()
    fmt = (lambda v: "%.2f" % v) if a.by == "sharpe" else pct
    strength = "很弱" if abs(rho) < 0.3 else "中等" if abs(rho) < 0.7 else "较强"
    print("\n结论")
    print("  · 样本内第 1 名 %d/%d 到了样本外：%s %s，在 %d 组里排第 %d；所有组合样本外%s的中位数是 %s。" % (
        best["fast"], best["slow"], name, fmt(best["oos_" + a.by]), n, best["oos_rank"], name, fmt(med)))
    beat = best["oos_" + a.by] > bh[1][a.by]
    print("  · 同期买入持有的%s是 %s，样本内第 1 名%s它。" % (name, fmt(bh[1][a.by]), "跑赢了" if beat else "没有跑赢"))
    print("  · 样本内和样本外排名的相关系数（Spearman）是 %.2f，相关性%s。" % (rho, strength))
    print("    接近 1：在过去表现好的参数，之后大概率还好，可以参考样本内的排名；")
    print("    接近 0 或为负：过去的排名对未来几乎没有参考价值，挑出来的“最优参数”主要是在拟合噪声（过拟合）。")

    REPORTS.mkdir(exist_ok=True)
    df.to_csv(REPORTS / (sym + "_scan.csv"), index=False, float_format="%.4f")
    print("\n全部结果：reports/%s_scan.csv" % sym)
    if not a.no_chart:
        from . import report
        report.heatmap(REPORTS / (sym + "_scan.png"), sym, df, a.by, (best["fast"], best["slow"]), (span(ins), span(oos)))
        print("热力图：reports/%s_scan.png" % sym)


def cmd_signal(a):
    print("\n均线 %d/%d · 按最近一个已收盘的交易日计算\n" % (a.fast, a.slow))
    for sym in a.symbols:
        try:
            bars = data.load(sym, a.refresh)
        except Exception as e:
            print("  %-6s 读取失败：%s" % (sym.upper(), e))
            continue
        sig = strategy.ma_cross(bars["close"], a.fast, a.slow).dropna()
        if len(sig) < 2:
            print("  %-6s 数据不足 %d 个交易日，算不出 %d 日均线" % (sym.upper(), a.slow, a.slow))
            continue
        last, prev = sig.iloc[-1], sig.iloc[-2]
        flips = sig.index[sig["target"].diff().fillna(0) != 0]
        since = flips[-1] if len(flips) else sig.index[0]
        days = len(sig.loc[since:]) - 1
        state = "持有" if last["target"] else "空仓"
        cross = ("金叉" if last["target"] else "死叉") if len(flips) else "自数据开始"
        print("  %-6s %s 收盘 %.2f · %d 日线 %.2f · %d 日线 %.2f（差 %+.1f%%）→ %s（%s于 %s，%d 个交易日前）" % (
            sym.upper(), sig.index[-1].date(), last["close"], a.fast, last["fast"], a.slow, last["slow"],
            (last["fast"] / last["slow"] - 1) * 100, state, cross, since.date(), days))
        if last["target"] != prev["target"]:
            print("         ↳ 今天刚出现%s：按策略，下一个交易日开盘%s" % ("金叉" if last["target"] else "死叉", "买入" if last["target"] else "卖出"))
    print("\n信号只是策略的计算结果，是否下单由你自己决定。")


def cmd_paper_new(a):
    con = paper.connect()
    try:
        start = paper.create(con, a.name, a.symbols.split(","), {"fast": a.fast, "slow": a.slow}, a.cash, a.fee, a.slippage)
    except ValueError as e:
        sys.exit(str(e))
    acct = next(x for x in paper.accounts(con) if x["name"] == a.name)
    paper.record(con, acct)
    print("\n已创建模拟账户 %s：%s，均线 %d/%d，初始资金 %s。" % (a.name, "、".join(acct["symbols"]), a.fast, a.slow, money(a.cash)))
    print("第一批决定按 %s 的收盘价做出，下一个交易日开盘开始模拟成交。" % start)
    print("以后每个交易日收盘后运行：python -m quant paper run")


def cmd_paper_delete(a):
    con = paper.connect()
    if not paper.delete(con, a.name):
        sys.exit("没有叫 %s 的模拟账户。" % a.name)
    print("已删除模拟账户 %s 和它的全部决定记录。" % a.name)


def cmd_paper_run(a):
    con = paper.connect()
    accts = [x for x in paper.accounts(con) if not a.name or x["name"] == a.name]
    if not accts:
        sys.exit("还没有模拟账户。先创建一个，例如：python -m quant paper new 核心 --symbols QQQM,VOO,MCD,PEP")
    REPORTS.mkdir(exist_ok=True)
    for acct in accts:
        new = paper.record(con, acct)
        report_paper(acct, new, paper.replay(con, acct))


def report_paper(acct, new, rep):
    n, p = len(acct["symbols"]), acct["params"]
    print("\n模拟账户 %s · 均线 %d/%d · %s 收盘时开始 · 初始资金 %s（%d 只，每只 %s）" % (
        acct["name"], p["fast"], p["slow"], acct["start"], money(acct["cash"]), n, money(acct["cash"] / n)))
    if new:
        print("本次记录了 %d 条新的决定（到 %s）。" % (len(new), max(x["date"] for x in new)))
    rows = [("代码", "最新决定", "当前", "股数", "资产", "收益", "同期买入持有")]
    total = bench_total = 0.0
    pending, fills, curves = [], [], []
    for sym, r in rep.items():
        sig, res, bench, cash = r["signals"], r["result"], r["bench"], r["cash"]
        last = sig.iloc[-1]
        held = int(res.in_market.iloc[-1]) if res else 0
        shares = int(res.trades[res.trades["open"]]["shares"].sum()) if res else 0
        value = res.equity.iloc[-1] if res else cash
        bvalue = bench.equity.iloc[-1] if bench else cash
        total, bench_total = total + value, bench_total + bvalue
        rows.append((sym, "%s（%s）" % ("持有" if last["target"] else "空仓", sig.index[-1].strftime("%m-%d")),
                     "持有" if held else "空仓", "%d" % shares, money(value), pct(value / cash - 1), pct(bvalue / cash - 1)))
        if last["target"] != held:
            note = ""
            if last["target"] and cash < r["bars"]["close"].iloc[-1]:
                note = "，但这一份资金不够买 1 股，不会成交"
            pending.append("  %s：下一个交易日开盘%s（%s 收盘后的决定%s）" % (
                sym, "买入" if last["target"] else "卖出", sig.index[-1].date(), note))
        if res:
            for _, t in res.trades.iterrows():
                fills.append((t["entry_date"], "买入", sym, t["shares"], t["entry_price"]))
                if not t["open"]:
                    fills.append((t["exit_date"], "卖出", sym, t["shares"], t["exit_price"]))
            curves.append(pd.DataFrame({sym + "_策略": res.equity, sym + "_买入持有": bench.equity}))
    rows.append(("合计", "", "", "", money(total), pct(total / acct["cash"] - 1), pct(bench_total / acct["cash"] - 1)))
    table(rows)
    if not curves:
        print("\n还没有成交日：下一个交易日开盘后开始模拟。")
    if pending:
        print("\n待执行：")
        print("\n".join(pending))
    if fills:
        print("\n最近成交（价格为复权价）：")
        for d, side, sym, sh, px in sorted(fills, key=lambda f: f[0], reverse=True)[:5]:
            print("  %s %s %s %d 股 @ %.2f" % (d.date(), side, sym, sh, px))
    if curves:
        # US and Hong Kong trade on different days: carry each value forward, before the first open it is cash
        df = pd.concat(curves, axis=1).sort_index().ffill()
        for sym, r in rep.items():
            for col in (sym + "_策略", sym + "_买入持有"):
                if col not in df:
                    df[col] = r["cash"]
        df = df.fillna({c: rep[c.rsplit("_", 1)[0]]["cash"] for c in df.columns})
        df["策略合计"] = df[[c for c in df.columns if c.endswith("_策略")]].sum(axis=1)
        df["买入持有合计"] = df[[c for c in df.columns if c.endswith("_买入持有")]].sum(axis=1)
        path = REPORTS / ("paper_%s.csv" % acct["name"])
        df.to_csv(path, index_label="date", float_format="%.2f", encoding="utf-8-sig")
        print("\n每日资产：reports/%s" % path.name)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m quant", description="均线交叉策略：回测和每日信号")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--fast", type=int, default=50, help="短均线天数（默认 50）")
        sp.add_argument("--slow", type=int, default=200, help="长均线天数（默认 200）")
        sp.add_argument("--refresh", action="store_true", help="忽略缓存，重新下载数据")

    bt = sub.add_parser("backtest", help="回测一只股票或 ETF")
    bt.add_argument("symbol")
    common(bt)
    bt.add_argument("--start", help="开始日期，如 2015-01-01（默认：长均线算得出来的第一天）")
    bt.add_argument("--end", help="结束日期（默认：最近一个交易日）")
    bt.add_argument("--cash", type=float, default=10_000, help="初始资金，美元（默认 10000）")
    bt.add_argument("--fee", type=float, default=0, help="每笔手续费，美元（默认 0）")
    bt.add_argument("--slippage", type=float, default=5, help="滑点，基点（默认 5，即 0.05%%）")
    bt.add_argument("--rf", type=float, default=0, help="无风险年利率，用于夏普比率，如 0.04（默认 0）")
    bt.add_argument("--no-chart", action="store_true", help="不画图")
    bt.set_defaults(fn=cmd_backtest)

    ints = lambda s: tuple(sorted({int(x) for x in s.split(",") if x.strip()}))
    sc = sub.add_parser("scan", help="参数扫描 + 样本外检验")
    sc.add_argument("symbol")
    sc.add_argument("--fast", type=ints, default=scan.FAST, help="短均线天数，逗号分隔（默认 %s）" % ",".join(map(str, scan.FAST)))
    sc.add_argument("--slow", type=ints, default=scan.SLOW, help="长均线天数，逗号分隔（默认 %s）" % ",".join(map(str, scan.SLOW)))
    sc.add_argument("--split", help="样本外从这天开始，如 2019-01-01（默认：最后 30%% 的交易日）")
    sc.add_argument("--by", choices=("sharpe", "cagr"), default="sharpe", help="按夏普比率还是年化收益排名（默认 sharpe）")
    sc.add_argument("--top", type=int, default=10, help="显示样本内前几名（默认 10）")
    sc.add_argument("--start", help="开始日期（默认：最长的均线算得出来的第一天）")
    sc.add_argument("--end", help="结束日期（默认：最近一个交易日）")
    sc.add_argument("--cash", type=float, default=10_000, help="初始资金，美元（默认 10000）")
    sc.add_argument("--fee", type=float, default=0, help="每笔手续费，美元（默认 0）")
    sc.add_argument("--slippage", type=float, default=5, help="滑点，基点（默认 5）")
    sc.add_argument("--rf", type=float, default=0, help="无风险年利率（默认 0）")
    sc.add_argument("--refresh", action="store_true", help="忽略缓存，重新下载数据")
    sc.add_argument("--no-chart", action="store_true", help="不画图")
    sc.set_defaults(fn=cmd_scan)

    sg = sub.add_parser("signal", help="查看今天的信号")
    sg.add_argument("symbols", nargs="+")
    common(sg)
    sg.set_defaults(fn=cmd_signal)

    pp = sub.add_parser("paper", help="模拟盘：每天记录策略的决定，跟踪模拟账户")
    psub = pp.add_subparsers(dest="paper_cmd", required=True)
    pn = psub.add_parser("new", help="创建模拟账户")
    pn.add_argument("name", help="账户名称，如 核心")
    pn.add_argument("--symbols", required=True, help="股票代码，逗号分隔，如 QQQM,VOO,MCD,PEP")
    pn.add_argument("--fast", type=int, default=50, help="短均线天数（默认 50）")
    pn.add_argument("--slow", type=int, default=200, help="长均线天数（默认 200）")
    pn.add_argument("--cash", type=float, default=10_000, help="初始资金，美元，平均分给每只股票（默认 10000）")
    pn.add_argument("--fee", type=float, default=0, help="每笔手续费，美元（默认 0）")
    pn.add_argument("--slippage", type=float, default=5, help="滑点，基点（默认 5）")
    pn.set_defaults(fn=cmd_paper_new)
    pr = psub.add_parser("run", help="记录最新的决定并显示账户（每个交易日收盘后运行）")
    pr.add_argument("name", nargs="?", help="只看这个账户（默认全部）")
    pr.set_defaults(fn=cmd_paper_run)
    pd_ = psub.add_parser("delete", help="删除模拟账户和它的全部记录")
    pd_.add_argument("name")
    pd_.set_defaults(fn=cmd_paper_delete)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
