"""Command line: quantlab backtest QQQ / scan QQQ / signal QQQM VOO / paper run / serve"""
import argparse
import sys
import unicodedata

import pandas as pd

from . import __version__, logs
from .config import get_settings
from .core import data, paper, research, scan
from .errors import QuantLabError


def width(s):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s, w):
    return s + " " * max(0, w - width(s))


def pct(x):
    return "—" if pd.isna(x) else "%+.1f%%" % (x * 100)


def money(x):
    return "$" + format(x, ",.0f")


def table(rows, gap=3):
    ws = [max(width(r[i]) for r in rows) + gap for i in range(len(rows[0]))]
    for r in rows:
        print("  " + "".join(pad(c, w) for c, w in zip(r, ws, strict=True)).rstrip())


def span(w):
    return "%s → %s（%.1f 年）" % (w.index[0].date(), w.index[-1].date(), (w.index[-1] - w.index[0]).days / 365.25)


def reports_dir():
    d = get_settings().reports_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_backtest(a):
    sym = data.normalize(a.symbol)
    run = research.backtest_ma(sym, data.load(sym, a.refresh), a.fast, a.slow, a.start, a.end, a.cash, a.fee, a.slippage, a.rf)
    s, b = run.summary, run.benchmark_summary
    print("\n%s  均线 %d/%d  %s → %s（%.1f 年）" % (sym, a.fast, a.slow, s["start"], s["end"], s["years"]))
    print("初始资金 %s · 每笔手续费 $%g · 滑点 %g bp · 信号按收盘价计算，下一个交易日开盘成交\n" % (money(a.cash), a.fee, a.slippage))
    dd = lambda m: "%s（%s → %s）" % (pct(m["mdd"]), m["mdd_peak"], m["mdd_trough"])
    table([("", "均线策略", "买入持有"),
           ("期末资产", money(s["final"]), money(b["final"])),
           ("总收益", pct(s["total"]), pct(b["total"])),
           ("年化收益", pct(s["cagr"]), pct(b["cagr"])),
           ("年化波动率", pct(s["vol"]).lstrip("+"), pct(b["vol"]).lstrip("+")),
           ("夏普比率", "%.2f" % s["sharpe"], "%.2f" % b["sharpe"]),
           ("最大回撤", dd(s), dd(b)),
           ("持仓时间占比", "%.0f%%" % (s["exposure"] * 100), "%.0f%%" % (b["exposure"] * 100)),
           ("交易次数", "%d" % s["trades"], "%d" % b["trades"]),
           ("胜率", "—" if pd.isna(s["win_rate"]) else "%.0f%%" % (s["win_rate"] * 100), "—"),
           ("平均每笔收益", pct(s["avg_trade"]), "—")])
    out, stem = reports_dir(), "%s_ma%d_%d" % (sym, a.fast, a.slow)
    run.strategy.trades.to_csv(out / (stem + "_trades.csv"), index=False, float_format="%.4f")
    print("\n交易明细：%s" % (out / (stem + "_trades.csv")))
    if not a.no_chart:
        from .core import charts
        charts.chart(out / (stem + ".png"), sym, run.signals, run.strategy, run.benchmark, a.fast, a.slow)
        print("图表：%s" % (out / (stem + ".png")))
    print("\n提示：回测是历史表现，不代表未来；反复调参数挑出来的最好结果，往往是过拟合。")


def cmd_scan(a):
    sym = data.normalize(a.symbol)
    run = research.scan_ma(sym, data.load(sym, a.refresh), a.fast, a.slow, a.split, a.start, a.end, a.by,
                           a.cash, a.fee, a.slippage, a.rf)
    df, n = run.table, len(run.table)
    name = {"sharpe": "夏普", "cagr": "年化"}[a.by]
    print("\n%s 均线参数扫描 · 短均线 %s · 长均线 %s · 共 %d 组 · 按样本内%s排名" % (
        sym, "/".join(map(str, a.fast)), "/".join(map(str, a.slow)), n, {"sharpe": "夏普比率", "cagr": "年化收益"}[a.by]))
    print("样本内（挑参数）：%s" % span(run.in_sample))
    print("样本外（检验）：  %s\n" % span(run.out_sample))
    rows = [("样本内排名", "参数", "样本内夏普", "样本内年化", "样本外夏普", "样本外年化", "样本外排名")]
    line = lambda r: ("%.2f" % r["is_sharpe"], pct(r["is_cagr"]), "%.2f" % r["oos_sharpe"], pct(r["oos_cagr"]))
    for _, r in df.head(a.top).iterrows():
        rows.append(("%d" % r["is_rank"], "%d/%d" % (r["fast"], r["slow"]), *line(r), "%d / %d" % (r["oos_rank"], n)))
    ref = df[(df["fast"] == 50) & (df["slow"] == 200)]
    if len(ref) and ref.index[0] >= a.top:
        r = ref.iloc[0]
        rows.append(("%d" % r["is_rank"], "50/200", *line(r), "%d / %d" % (r["oos_rank"], n)))
    bi, bo = run.benchmark_in, run.benchmark_out
    rows.append(("—", "买入持有", "%.2f" % bi["sharpe"], pct(bi["cagr"]), "%.2f" % bo["sharpe"], pct(bo["cagr"]), "—"))
    table(rows)

    best, rho = run.best, run.rank_corr
    fmt = (lambda v: "%.2f" % v) if a.by == "sharpe" else pct
    strength = "很弱" if abs(rho) < 0.3 else "中等" if abs(rho) < 0.7 else "较强"
    print("\n结论")
    print("  · 样本内第 1 名 %d/%d 到了样本外：%s %s，在 %d 组里排第 %d；所有组合样本外%s的中位数是 %s。" % (
        best["fast"], best["slow"], name, fmt(best["oos_" + a.by]), n, best["oos_rank"], name, fmt(run.median_out)))
    beat = best["oos_" + a.by] > bo[a.by]
    print("  · 同期买入持有的%s是 %s，样本内第 1 名%s它。" % (name, fmt(bo[a.by]), "跑赢了" if beat else "没有跑赢"))
    print("  · 样本内和样本外排名的相关系数（Spearman）是 %.2f，相关性%s。" % (rho, strength))
    print("    接近 1：在过去表现好的参数，之后大概率还好，可以参考样本内的排名；")
    print("    接近 0 或为负：过去的排名对未来几乎没有参考价值，挑出来的“最优参数”主要是在拟合噪声（过拟合）。")

    out = reports_dir()
    df.to_csv(out / (sym + "_scan.csv"), index=False, float_format="%.4f")
    print("\n全部结果：%s" % (out / (sym + "_scan.csv")))
    if not a.no_chart:
        from .core import charts
        spans = (span(run.in_sample), span(run.out_sample))
        charts.heatmap(out / (sym + "_scan.png"), sym, df, a.by, (best["fast"], best["slow"]), spans)
        print("热力图：%s" % (out / (sym + "_scan.png")))


def cmd_signal(a):
    print("\n均线 %d/%d · 按最近一个已收盘的交易日计算\n" % (a.fast, a.slow))
    for raw in a.symbols:
        try:
            sym = data.normalize(raw)
            s = research.latest_signal(sym, data.load(sym, a.refresh), a.fast, a.slow)
        except QuantLabError as e:
            print("  %-6s %s" % (str(raw).upper(), e))
            continue
        cross = ("金叉" if s["hold"] else "死叉") if s["since_is_cross"] else "自数据开始"
        print("  %-6s %s 收盘 %.2f · %d 日线 %.2f · %d 日线 %.2f（差 %+.1f%%）→ %s（%s于 %s，%d 个交易日前）" % (
            sym, s["date"], s["close"], a.fast, s["fast"], a.slow, s["slow"], s["gap"] * 100,
            "持有" if s["hold"] else "空仓", cross, s["since"], s["days"]))
        if s["flipped"]:
            cross, side = ("金叉", "买入") if s["hold"] else ("死叉", "卖出")
            print("         ↳ 今天刚出现%s：按策略，下一个交易日开盘%s" % (cross, side))
    print("\n信号只是策略的计算结果，是否下单由你自己决定。")


def cmd_paper_new(a):
    con = paper.connect()
    try:
        start = paper.create(con, a.name, a.symbols.split(","), {"fast": a.fast, "slow": a.slow}, a.cash, a.fee, a.slippage)
        acct = paper.get_account(con, a.name)
        paper.record(con, acct)
    finally:
        con.close()
    print("\n已创建模拟账户 %s：%s，均线 %d/%d，初始资金 %s。" % (a.name, "、".join(acct["symbols"]), a.fast, a.slow, money(a.cash)))
    print("第一批决定按 %s 的收盘价做出，下一个交易日开盘开始模拟成交。" % start)
    print("以后每个交易日收盘后运行：quantlab paper run")


def cmd_paper_delete(a):
    con = paper.connect()
    try:
        if not paper.delete(con, a.name):
            sys.exit("没有叫 %s 的模拟账户。" % a.name)
    finally:
        con.close()
    print("已删除模拟账户 %s 和它的全部决定记录。" % a.name)


def cmd_paper_run(a):
    con = paper.connect()
    try:
        accts = [paper.get_account(con, a.name)] if a.name else paper.accounts(con)
        if not accts:
            sys.exit("还没有模拟账户。先创建一个，例如：quantlab paper new 核心 --symbols QQQM,VOO")
        for acct in accts:
            new = paper.record(con, acct)
            report_paper(paper.summarize(acct, paper.replay(con, acct)), new)
    finally:
        con.close()


def report_paper(s, new):
    n, p = len(s["symbols"]), s["params"]
    print("\n模拟账户 %s · 均线 %d/%d · %s 收盘时开始 · 初始资金 %s（%d 只，每只 %s）" % (
        s["name"], p["fast"], p["slow"], s["start"], money(s["cash"]), n, money(s["cash"] / n)))
    if new:
        print("本次记录了 %d 条新的决定（到 %s）。" % (len(new), max(x["date"] for x in new)))
    rows = [("代码", "最新决定", "当前", "股数", "资产", "收益", "同期买入持有")]
    for q in s["positions"]:
        rows.append((q["symbol"], "%s（%s）" % ("持有" if q["decision"] else "空仓", q["decision_date"].strftime("%m-%d")),
                     "持有" if q["held"] else "空仓", "%d" % q["shares"], money(q["value"]), pct(q["return"]), pct(q["benchmark_return"])))
    rows.append(("合计", "", "", "", money(s["value"]), pct(s["return"]), pct(s["benchmark_return"])))
    table(rows)
    if not s["days"]:
        print("\n还没有成交日：下一个交易日开盘后开始模拟。")
    if s["pending"]:
        print("\n待执行：")
        for o in s["pending"]:
            print("  %s：下一个交易日开盘%s（%s 收盘后的决定%s）" % (
                o["symbol"], "买入" if o["side"] == "buy" else "卖出", o["decision_date"],
                "" if o["affordable"] else "，但这一份资金不够买 1 股，不会成交"))
    if s["fills"]:
        print("\n最近成交（价格为复权价）：")
        for f in s["fills"][:5]:
            print("  %s %s %s %d 股 @ %.2f" % (f["date"], "买入" if f["side"] == "buy" else "卖出", f["symbol"], f["shares"], f["price"]))
    if s["days"]:
        path = reports_dir() / ("paper_%s.csv" % s["name"])
        s["equity"].rename(columns={"strategy": "策略", "benchmark": "买入持有"}).to_csv(
            path, index_label="date", float_format="%.2f", encoding="utf-8-sig")
        print("\n每日资产：%s" % path)


def cmd_serve(a):
    import uvicorn

    from .api.app import create_app
    from .api.security import api_token
    s = get_settings()
    host, port = a.host or s.host, a.port or s.port
    print("QuantLab %s 已启动：http://%s:%d/?token=%s" % (__version__, host, port, api_token()))
    print("接口文档：http://%s:%d/api/docs" % (host, port))
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("注意：正在监听 %s，同一网络里的其他设备也能访问，请保管好上面的令牌。" % host)
    uvicorn.run(create_app(), host=host, port=port, log_config=None)


def build_parser():
    p = argparse.ArgumentParser(prog="quantlab", description="量化研究工作站：回测、参数扫描、每日信号、模拟盘")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def ma(sp):
        sp.add_argument("--fast", type=int, default=50, help="短均线天数（默认 50）")
        sp.add_argument("--slow", type=int, default=200, help="长均线天数（默认 200）")

    def costs(sp):
        sp.add_argument("--start", help="开始日期，如 2015-01-01")
        sp.add_argument("--end", help="结束日期（默认：最近一个交易日）")
        sp.add_argument("--cash", type=float, default=10_000, help="初始资金，美元（默认 10000）")
        sp.add_argument("--fee", type=float, default=0, help="每笔手续费，美元（默认 0）")
        sp.add_argument("--slippage", type=float, default=5, help="滑点，基点（默认 5，即 0.05%%）")
        sp.add_argument("--rf", type=float, default=0, help="无风险年利率，用于夏普比率，如 0.04（默认 0）")
        sp.add_argument("--refresh", action="store_true", help="忽略缓存，重新下载数据")
        sp.add_argument("--no-chart", action="store_true", help="不画图")

    bt = sub.add_parser("backtest", help="回测一只股票或 ETF")
    bt.add_argument("symbol")
    ma(bt)
    costs(bt)
    bt.set_defaults(fn=cmd_backtest)

    ints = lambda s: tuple(sorted({int(x) for x in s.split(",") if x.strip()}))
    sc = sub.add_parser("scan", help="参数扫描 + 样本外检验")
    sc.add_argument("symbol")
    sc.add_argument("--fast", type=ints, default=scan.FAST, help="短均线天数，逗号分隔（默认 %s）" % ",".join(map(str, scan.FAST)))
    sc.add_argument("--slow", type=ints, default=scan.SLOW, help="长均线天数，逗号分隔（默认 %s）" % ",".join(map(str, scan.SLOW)))
    sc.add_argument("--split", help="样本外从这天开始，如 2019-01-01（默认：最后 30%% 的交易日）")
    sc.add_argument("--by", choices=("sharpe", "cagr"), default="sharpe", help="按夏普比率还是年化收益排名（默认 sharpe）")
    sc.add_argument("--top", type=int, default=10, help="显示样本内前几名（默认 10）")
    costs(sc)
    sc.set_defaults(fn=cmd_scan)

    sg = sub.add_parser("signal", help="查看今天的信号")
    sg.add_argument("symbols", nargs="+")
    ma(sg)
    sg.add_argument("--refresh", action="store_true", help="忽略缓存，重新下载数据")
    sg.set_defaults(fn=cmd_signal)

    pp = sub.add_parser("paper", help="模拟盘：每天记录策略的决定，跟踪模拟账户")
    psub = pp.add_subparsers(dest="paper_cmd", required=True)
    pn = psub.add_parser("new", help="创建模拟账户")
    pn.add_argument("name", help="账户名称，如 核心")
    pn.add_argument("--symbols", required=True, help="股票代码，逗号分隔，如 QQQM,VOO")
    ma(pn)
    pn.add_argument("--cash", type=float, default=10_000, help="初始资金，美元，平均分给每只股票（默认 10000）")
    pn.add_argument("--fee", type=float, default=0, help="每笔手续费，美元（默认 0）")
    pn.add_argument("--slippage", type=float, default=5, help="滑点，基点（默认 5）")
    pn.set_defaults(fn=cmd_paper_new)
    pr = psub.add_parser("run", help="记录最新的决定并显示账户（每个交易日收盘后运行）")
    pr.add_argument("name", nargs="?", help="只看这个账户（默认全部）")
    pr.set_defaults(fn=cmd_paper_run)
    pdel = psub.add_parser("delete", help="删除模拟账户和它的全部记录")
    pdel.add_argument("name")
    pdel.set_defaults(fn=cmd_paper_delete)

    sv = sub.add_parser("serve", help="启动接口服务和网页工作台")
    sv.add_argument("--host", help="监听地址（默认 127.0.0.1，只允许本机访问）")
    sv.add_argument("--port", type=int, help="端口（默认 8000）")
    sv.set_defaults(fn=cmd_serve)
    return p


def main(argv=None):
    logs.setup()
    a = build_parser().parse_args(argv)
    try:
        a.fn(a)
    except QuantLabError as e:
        sys.exit("出错了：%s" % e)


if __name__ == "__main__":
    main()
