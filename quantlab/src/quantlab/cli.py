"""Command line: quantlab backtest QQQ / scan QQQ / signal QQQM VOO / paper run / serve"""
import argparse
import logging
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


def describe(acct):
    p = acct["params"]
    return "AI 评级（每 %d 天分析一次）" % p["every_days"] if acct["strategy"] == "ai_rating" else "均线 %d/%d" % (p["fast"], p["slow"])


def cmd_paper_new(a):
    kind = "ai_rating" if a.strategy == "ai" else "ma_cross"
    params = {"every_days": a.every} if kind == "ai_rating" else {"fast": a.fast, "slow": a.slow}
    con = paper.connect()
    try:
        start = paper.create(con, a.name, a.symbols.split(","), params, a.cash, a.fee, a.slippage, kind=kind)
        acct = paper.get_account(con, a.name)
        paper.record(con, acct)
    finally:
        con.close()
    print("\n已创建模拟账户 %s：%s，%s，初始资金 %s。" % (a.name, "、".join(acct["symbols"]), describe(acct), money(a.cash)))
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
    n = len(s["symbols"])
    print("\n模拟账户 %s · %s · %s 收盘时开始 · 初始资金 %s（%d 只，每只 %s）" % (
        s["name"], describe(s), s["start"], money(s["cash"]), n, money(s["cash"] / n)))
    if new:
        print("本次记录了 %d 条新的决定（到 %s）。" % (len(new), max(x["date"] for x in new)))
    rows = [("代码", "最新决定", "当前", "股数", "资产", "收益", "同期买入持有")]
    for q in s["positions"]:
        decided = "等待 AI 分析"
        if q["decision"] is not None:
            decided = "%s（%s）" % ("持有" if q["decision"] else "空仓", q["decision_date"].strftime("%m-%d"))
        rows.append((q["symbol"], decided,
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


def cny(v):
    return "¥%.2f" % v


def cmd_ai_status(a):
    from .ai import engine, ledger
    s = get_settings()
    con = ledger.connect()
    try:
        b = ledger.budget(con)
    finally:
        con.close()
    print("\nAI 组件：%s · 密钥：%s · 模型 %s / %s（%s）" % (
        "已安装" if engine.available() else "未安装（pip install -e \".[ai]\"）", "已配置" if engine.configured() else "未配置",
        s.ai_quick_model, s.ai_deep_model, s.ai_provider))
    print("%s 月预算 %s：已用 %s，排队中预计 %s，剩余 %s；每次分析预计 %s" % (
        b["month"], cny(b["budget_cny"]), cny(b["spent_cny"]), cny(b["committed_cny"] - b["spent_cny"]), cny(b["remaining_cny"]),
        cny(b["estimate_cny"])))


def cmd_ai_runs(a):
    from .ai import ledger
    con = ledger.connect()
    try:
        runs = ledger.runs(con, data.normalize(a.symbol) if a.symbol else None, a.limit)
    finally:
        con.close()
    if not runs:
        print("还没有 AI 分析记录。")
        return
    rows = [("编号", "代码", "收盘日", "状态", "评级", "调用", "输入 token", "输出 token", "费用")]
    names = {"queued": "排队中", "running": "运行中", "done": "完成", "failed": "失败"}
    for r in runs:
        rows.append(("%d" % r["id"], r["symbol"], r["trade_date"], names[r["status"]], r["rating"] or "—", "%d" % r["llm_calls"],
                     format(r["tokens_in"], ","), format(r["tokens_out"], ","), cny(r["cost_usd"] * get_settings().ai_usd_cny)))
    table(rows)


def _drain(worker):
    while worker.run_next():
        pass


def cmd_ai_analyze(a):
    from .ai import engine, ledger
    from .ai.jobs import Worker
    engine.require_ready()
    sym = data.normalize(a.symbol)
    trade_date = data.load(sym).index[-1].date().isoformat()
    con = ledger.connect()
    try:
        rid = ledger.enqueue(con, sym, trade_date, "cli")
        print("\n正在分析 %s（%s 收盘后），通常需要几分钟……" % (sym, trade_date))
        _drain(Worker())  # runs queued analyses in order, this one included
        run = ledger.get(con, rid)
    finally:
        con.close()
    usd_cny = get_settings().ai_usd_cny
    if run["status"] != "done":
        sys.exit("分析没有完成：%s（已花费 %s）" % (run["error"], cny(run["cost_usd"] * usd_cny)))
    print("评级：%s · 调用 %d 次 · 输入 %s token（缓存 %s）· 输出 %s token · 费用 %s" % (
        run["rating"], run["llm_calls"], format(run["tokens_in"], ","), format(run["tokens_cached"], ","),
        format(run["tokens_out"], ","), cny(run["cost_usd"] * usd_cny)))
    final = (run["reports"] or {}).get("final_trade_decision")
    if final:
        print("\n%s\n%s" % (final["title"], final["text"]))
    print("\n完整报告：在网页工作台的“AI 研报”里查看编号 %d。以上内容是 AI 生成的研究材料，不构成投资建议。" % rid)


def cmd_schedule_run(a):
    import json

    from .ai.jobs import Worker
    from .scheduler import Scheduler
    worker = Worker()
    worker.wake = lambda: None
    worker.wait_idle = lambda timeout=None: (_drain(worker), True)[1]  # no background thread here: run the queue now
    print(json.dumps(Scheduler(worker).run_daily(), ensure_ascii=False, indent=2))


def cmd_ml_backtest(a):
    from .ml import features, research, universe
    u = universe.load(a.refresh_universe)
    print("\n正在读取 %d 只股票的行情（第一次需要几分钟）……" % len(u["symbols"]))
    wide, skipped = features.load_panel(u["symbols"])
    print("正在滚动训练模型并回测……")
    st = research.run_study(wide, u["symbols"], skipped, a.start, a.end, a.top, a.max_weight, a.cash, a.slippage, a.fee, a.rf)
    years = (st.end - st.start).days / 365.25
    print("\n纳斯达克 100（%d 只，名单截至 %s）· %s → %s（%.1f 年）· 每月调仓 · 持有前 %d 只 · 单只上限 %.0f%% · 滑点 %g bp" % (
        len(st.universe), u["as_of"], st.start.date(), st.end.date(), years, a.top, a.max_weight * 100, a.slippage))
    if st.skipped:
        print("没有取到数据：%s" % "、".join(st.skipped))
    print("注意：用的是现在的成分股（幸存者偏差），所有策略的历史收益都偏高；请主要和“股票池等权”比较。\n")
    rows = [("", "年化", "波动率", "夏普", "最大回撤", "月均换手", "交易成本")]
    for key, v in st.strategies.items():
        m, r = v["summary"], v["result"]
        rows.append((research.NAMES[key], pct(m["cagr"]), pct(m["vol"]).lstrip("+"), "%.2f" % m["sharpe"], pct(m["mdd"]),
                     "%.0f%%" % (r.turnover.mean() * 100), money(r.costs)))
    table(rows)
    ic = st.ic
    print("\n预测能力（IC：每月预测排名和之后 21 个交易日实际收益排名的相关系数）")
    print("  平均 %.3f · 标准差 %.3f · 平均/标准差 %.2f · 为正的月份 %.0f%%（共 %d 个月）" % (
        ic.mean(), ic.std(), ic.mean() / ic.std(), (ic > 0).mean() * 100, len(ic)))
    print("  按预测分成 10 组，之后 21 天的平均收益：" + "  ".join("%d组 %+.2f%%" % (k, v * 100) for k, v in st.deciles.items()))
    print("  最重要的特征：" + "、".join("%s %.0f%%" % (features.FEATURES[k], v * 100) for k, v in st.importance.head(5).items()))
    out = reports_dir()
    eq = {research.NAMES[k]: v["result"].equity for k, v in st.strategies.items()}
    import pandas as pd
    pd.DataFrame(eq).to_csv(out / "ml_backtest.csv", index_label="date", float_format="%.2f", encoding="utf-8-sig")
    print("\n每日净值：%s" % (out / "ml_backtest.csv"))
    if not a.no_chart:
        from .core import charts
        charts.ml_chart(out / "ml_backtest.png", eq, ic)
        print("图表：%s" % (out / "ml_backtest.png"))
    print("\n提示：回测里的每个预测都来自当时之前的数据训练出的模型；但幸存者偏差无法消除，实盘表现通常比回测差。")


def cmd_ml_experiment(a):
    from .ml import experiment as E
    print("\n预登记实验 %s · %s 阶段。第一次运行要下载约 270 只股票的行情和 SEC 财报，需要十几分钟……" % (E.NAME, "开发期" if a.phase == "dev" else "考试期"))
    record, _ = E.run_dev() if a.phase == "dev" else E.run_holdout()
    rows = [("", "说明", "年化", "夏普", "最大回撤", "月均换手", "平均 IC")]
    for k, r in record["rows"].items():
        rows.append((k, r["label"], pct(r["cagr"]), "%.2f" % r["sharpe"], pct(r["mdd"]), "%.0f%%" % (r["turnover"] * 100),
                     "—" if r["ic_mean"] is None else "%.3f" % r["ic_mean"]))
    print()
    table(rows)
    if a.phase == "dev":
        print("\n按预登记规则选中：%s。成分股价格覆盖率平均 %.0f%%。" % (record["selected"], record["coverage_mean"] * 100))
        print("考试期只能运行一次：quantlab ml experiment holdout")
    else:
        act = record["active_vs_B2"]
        print("\n相对 B2：月度超额 %+.2f%%，t 值 %.2f（%d 个月）→ %s" % (act["monthly_mean"] * 100, act["t"], act["months"],
              "满足预登记标准" if record["passed"] else "不满足预登记标准：没有证据表明机器学习优于动量"))
    print("结果：docs/experiments/%s-results.md" % E.NAME)


def cmd_ml_signal(a):
    from .ml import signal
    capital = a.capital or get_settings().trading_capital
    print("\n正在读取行情、训练模型（需要一两分钟）……")
    sig = signal.latest(a.top, a.max_weight)
    w, sc, px = sig["weights"], sig["scores"], sig["prices"]
    print("\n%s 收盘后的目标组合（模型用到 %s 为止已知结果的 %s 个样本训练）" % (sig["signal_date"], sig["card"]["to"], format(sig["card"]["samples"], ",")))
    rows = [("排名", "代码", "预测分", "权重", "金额", "约合股数")]
    for i, sym in enumerate(sc.reindex(w.index).sort_values(ascending=False).index, 1):
        rows.append(("%d" % i, sym, "%.3f" % sc[sym], "%.1f%%" % (w[sym] * 100), money(w[sym] * capital), "%.4f" % (w[sym] * capital / px[sym])))
    table(rows)
    print("\n按资金 %s 计算；股数按收盘价估算，实际按下一个开盘价成交，需要券商支持碎股。" % money(capital))
    print("这是模型的计算结果，不构成投资建议。")


def _trade_con():
    from .trading.broker import connect
    return connect()


def cmd_trade_new(a):
    from .trading import engine
    con = _trade_con()
    try:
        engine.create_account(con, a.name, a.mode, a.capital)
    finally:
        con.close()
    print("已创建交易账户 %s（%s 模式）。每月最后一个交易日收盘后，每日任务会自动调仓；想立刻建仓可以运行：quantlab trade run --force" % (
        a.name, {"plan": "只出计划", "paper": "模拟券商"}[a.mode]))


def cmd_trade_status(a):
    from .trading import engine
    from .trading.broker import PaperBroker
    con = _trade_con()
    try:
        accts = [engine.get_account(con, a.name)] if a.name else engine.accounts(con)
        if not accts:
            print("还没有交易账户。创建一个：quantlab trade new 模拟 --mode paper")
            return
        for acct in accts:
            st = PaperBroker(con, acct["name"]).account()
            curve = engine.equity_curve(con, acct["name"])
            value = curve.iloc[-1] if len(curve) else acct["cash"]
            print("\n交易账户 %s · %s 模式 · 资金上限 %s · 最近净值 %s（%s）· 最高 %s%s" % (
                acct["name"], acct["mode"], money(acct["capital"]), money(value), curve.index[-1].date() if len(curve) else "—",
                money(acct["peak_equity"]), " · ⚠ 已暂停：%s" % acct["halted"] if acct["halted"] else ""))
            print("  现金 %s；持仓：%s" % (money(st["cash"]), "、".join("%s %.4f 股" % kv for kv in sorted(st["positions"].items())) or "无"))
            recent = engine.orders(con, acct["name"], 12)
            if recent:
                rows = [("信号日", "代码", "方向", "数量", "状态", "成交价", "说明")]
                names = {"pending": "待成交", "filled": "已成交", "rejected": "已拒绝", "planned": "计划"}
                for o in recent:
                    rows.append((o["signal_date"], o["symbol"], "买" if o["side"] == "buy" else "卖", "%.4f" % o["qty"], names.get(o["status"], o["status"]),
                                 "%.2f" % o["fill_price"] if o["fill_price"] else "—", o["reason"] or ""))
                table(rows)
    finally:
        con.close()


def cmd_trade_run(a):
    import json

    from .trading import daily
    print(json.dumps(daily.run(force=a.force), ensure_ascii=False, indent=2, default=str))


def cmd_trade_halt(a):
    from .trading import engine
    con = _trade_con()
    try:
        engine.get_account(con, a.name)
        engine.halt(con, a.name, a.reason or "手动暂停")
    finally:
        con.close()
    print("已暂停 %s：不会再生成或发送订单（已发送、待成交的订单仍会成交）。" % a.name)


def cmd_trade_resume(a):
    from .trading import engine
    con = _trade_con()
    try:
        engine.resume(con, a.name)
    finally:
        con.close()
    print("已恢复 %s。" % a.name)


def cmd_trade_mode(a):
    from .trading import engine
    con = _trade_con()
    try:
        engine.set_mode(con, a.name, a.mode)
    finally:
        con.close()
    print("%s 已切换为 %s 模式。" % (a.name, a.mode))


def big(v):
    """Large dollar amounts in 亿 (hundred millions), e.g. $3,030 亿."""
    return "—" if v is None else ("$%s 亿" % format(v / 1e8, ",.0f")) if abs(v) >= 1e8 else money(v)


def _p(v, digits=1):
    return "—" if v is None else "%+.*f%%" % (digits, v * 100)


def cmd_analyze(a):
    from .holdings import analysis
    r = analysis.analyze(a.symbol, a.amount)
    t, f, pf = r["technical"], r["fundamentals"], r["portfolio"]
    print("\n%s · %s 收盘 %.2f" % (r["symbol"], t["date"], t["close"] or 0))
    print("  趋势：1 个月 %s · 3 个月 %s · 1 年 %s · 12-1 月动量 %s · %s" % (
        _p(t.get("r1m")), _p(t.get("r3m")), _p(t.get("r1y")), _p(t.get("mom_12_1")),
        "50 日线在 200 日线上方" if t.get("uptrend") else "50 日线在 200 日线下方" if t.get("uptrend") is False else "均线数据不足"))
    print("  风险：年化波动率 %s · 距一年最高点 %s · 一年最大回撤 %s · 相对 QQQ 的 β %s" % (
        _p(t.get("vol"), 0).lstrip("+"), _p(t.get("from_high"), 0), _p(t.get("max_dd_1y"), 0), "%.2f" % t["beta"] if t.get("beta") else "—"))
    if f:
        print("  基本面（SEC，按公告日）：收入 %s · 净利润 %s · 营业利润率 %s · 收入同比 %s · 市盈率 %s · SUE %s" % (
            big(f["revenue_ttm"]), big(f["net_income_ttm"]),
            _p(f["op_margin"], 0).lstrip("+"), _p(f["rev_growth"], 0), "%.0f" % f["pe"] if f["pe"] else "—",
            "%.1f" % f["sue"] if f["sue"] is not None else "—"))
    else:
        print("  基本面：没有 SEC 数据（非美国公司或已退市）")
    if r["ai"]:
        print("  AI 评级：%s（%s 收盘后，分析 #%d）" % (r["ai"]["rating"], r["ai"]["trade_date"], r["ai"]["id"]))
    if r["model"]:
        print("  机器学习排名：第 %d / %d（%s；该模型未通过考试期检验）" % (r["model"]["rank"], r["model"]["of"], r["model"]["signal_date"]))
    if pf:
        corr = "、".join("%s %.2f" % kv for kv in sorted(pf["correlations"].items(), key=lambda kv: -kv[1]))
        print("  和持仓的相关性：%s" % (corr or "—"))
        for c in r["checks"]:
            print("  %s %s：%s" % ("✓" if c["ok"] else "✗", c["rule"], c["text"]))
    print("\n%s" % r["verdict"])
    for n in r["notes"]:
        print("  · [%s] %s" % (n["kind"], n["text"]))
    print("\n%s" % r["disclaimer"])


def cmd_holdings_status(a):
    from .holdings import ledger
    snap = ledger.load(refresh=False) if ledger.snapshot_path().exists() else None
    print("账本只读密钥：%s · 上次同步：%s" % ("已配置" if ledger.configured() else "未配置（在账本“账号”页面创建，填进 .env 的 LEDGER_TOKEN）",
                                       (snap or {}).get("synced") or "从未"))


def _print_review(r):
    v = r["valuation"]
    print("\n持仓检查 #%d（同步于 %s）· 可投资资金 %s（股票 %s + 现金 %s）· 国债定存 %s" % (
        r["id"], r.get("synced") or "—", money(v["investable"]), money(v["stocks"]), money(v["cash"]), money(v["fixed"])))
    rows = [("代码", "股数", "成本", "现价", "市值", "盈亏", "占比", "目标")]
    for p in v["positions"]:
        tgt = r["policy"]["targets"].get(p["symbol"])
        rows.append((p["symbol"], "%g" % p["shares"], "%.2f" % p["avg_cost"], "%.2f" % p["price"] if p["price"] else "—",
                     money(p["value"]) if p["value"] else "—", _p(p["gain"]), "%.1f%%" % (p["weight"] * 100) if p["weight"] else "—",
                     "%.0f%%" % (tgt * 100) if tgt else "—"))
    table(rows)
    if r["proposals"]:
        print("\n建议订单（按你的规则；确认后请自己去券商下单，成交后在账本记一笔）：")
        for o in r["proposals"]:
            print("  #%d %s %s %g 股 @ 约 %.2f（约 %s）· %s · %s" % (
                o["id"], "买入" if o["side"] == "buy" else "卖出", o["symbol"], o["shares"], o["price"], money(o["value"]), o["reason"],
                {"pending": "待确认", "approved": "已确认", "rejected": "已拒绝", "superseded": "已过期"}.get(o["status"], o["status"])))
    else:
        print("\n没有建议订单：持仓符合你的规则。")
    for n in r["notes"]:
        print("  · %s" % n)
    if r["reminders"]:
        print("\n提醒（只是信号，不会生成订单）：")
        for m in r["reminders"]:
            print("  %s %s [%s] %s" % ("⚠" if m["level"] == "warn" else "·", m["symbol"], m["kind"], m["text"]))


def cmd_holdings_review(a):
    from .holdings import review
    _print_review(review.run())


def cmd_holdings_policy(a):
    import json

    from .holdings import policy
    p = policy.load()
    for item in a.set or []:
        key, _, val = item.partition("=")
        if key == "target":
            sym, _, w = val.partition(":")
            p["targets"][sym.upper()] = float(w)
        elif key in p:
            p[key] = val.lower() in ("1", "true", "yes") if isinstance(p[key], bool) else float(val)
        else:
            sys.exit("没有这个规则：%s" % key)
    if a.set:
        p = policy.save(p)
    print(json.dumps(p, ensure_ascii=False, indent=2))


def cmd_holdings_decide(a):
    from .holdings import review
    o = review.decide(a.id, a.cmd_name == "approve", a.note or "")
    print("#%d %s %s %g 股：%s" % (o["id"], "买入" if o["side"] == "buy" else "卖出", o["symbol"], o["shares"],
                                  "已确认，请自己去券商下单，成交后在账本记一笔" if o["status"] == "approved" else "已拒绝"))


SECRETS = ("DEEPSEEK_API_KEY", "FRED_API_KEY", "SEC_EDGAR_USER_AGENT", "LEDGER_TOKEN")


def cmd_secrets_set(a):
    """Asks for a key without echoing it and writes it into .env in the working directory (owner-only)."""
    import getpass
    import os
    import stat
    from pathlib import Path

    value = getpass.getpass("%s（输入时不显示）：" % a.name).strip()
    if not value or any(c in value for c in "\r\n"):
        raise SystemExit("没有输入，未修改")
    path = Path(".env")
    lines = [ln for ln in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
             if not ln.startswith(a.name + "=")]
    path.write_text("\n".join(lines + ["%s=%s" % (a.name, value)]) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    print("已写入 %s 的 %s，重启服务后生效" % (path.resolve(), a.name))


def cmd_secrets_status(a):
    import os
    from pathlib import Path

    env = Path(".env")
    found = dict(ln.split("=", 1) for ln in env.read_text(encoding="utf-8").splitlines() if "=" in ln) if env.exists() else {}
    for name in SECRETS:
        print("%-22s %s" % (name, "已配置" if found.get(name) or os.environ.get(name) else "未配置"))


def cmd_serve(a):
    import uvicorn

    from .api.app import create_app
    from .api.security import api_token
    s = get_settings()
    host, port = a.host or s.host, a.port or s.port
    if s.auth_mode == "proxy":
        if host not in ("127.0.0.1", "localhost", "::1"):  # anyone who reaches the port could send the user header
            raise SystemExit("代理模式只能监听本机地址（127.0.0.1），由 nginx 转发")
        logging.getLogger("quantlab").info("QuantLab %s listening on http://%s:%d behind a proxy at %s/", __version__, host, port, s.base_path)
    elif a.log_file:  # running in the background: keep the token out of the log; `quantlab open` opens the page
        logging.getLogger("quantlab").info("QuantLab %s listening on http://%s:%d", __version__, host, port)
    else:
        print("QuantLab %s 已启动：http://%s:%d/?token=%s" % (__version__, host, port, api_token()))
        print("接口文档：http://%s:%d/api/docs" % (host, port))
    if host not in ("127.0.0.1", "localhost", "::1"):
        logging.getLogger("quantlab").warning("listening on %s: other devices on the network can reach it; keep the token safe", host)
    uvicorn.run(create_app(), host=host, port=port, log_config=None, root_path=s.base_path)


def cmd_open(a):
    """Opens the workbench in the default browser with the token, for a service already running in the background."""
    import urllib.request
    import webbrowser

    from .api.security import api_token
    s = get_settings()
    base = "http://%s:%d" % ("127.0.0.1" if s.host in ("0.0.0.0", "::") else s.host, s.port)
    try:
        urllib.request.urlopen(base + "/api/health", timeout=3)
    except OSError:
        sys.exit("服务没有在运行（%s）。先运行：python -m quantlab serve" % base)
    webbrowser.open(base + "/?token=" + api_token())


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
    pn.add_argument("--strategy", choices=("ma", "ai"), default="ma", help="ma：均线交叉（默认）；ai：按 AI 评级")
    pn.add_argument("--every", type=int, default=7, help="AI 账户每隔多少天重新分析一次（默认 7）")
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

    ai = sub.add_parser("ai", help="AI 研究：用 TradingAgents 分析股票（需要安装 AI 组件并配置密钥）")
    aisub = ai.add_subparsers(dest="ai_cmd", required=True)
    aisub.add_parser("status", help="安装、密钥和本月预算").set_defaults(fn=cmd_ai_status)
    an = aisub.add_parser("analyze", help="分析一只股票（按最近一个收盘日），会产生模型费用")
    an.add_argument("symbol")
    an.set_defaults(fn=cmd_ai_analyze)
    ar = aisub.add_parser("runs", help="分析记录和费用")
    ar.add_argument("--symbol")
    ar.add_argument("--limit", type=int, default=20)
    ar.set_defaults(fn=cmd_ai_runs)

    ml = sub.add_parser("ml", help="机器学习选股（纳斯达克 100）")
    mlsub = ml.add_subparsers(dest="ml_cmd", required=True)
    mb = mlsub.add_parser("backtest", help="滚动训练 + 组合回测，并和动量、等权、QQQ 比较")
    mb.add_argument("--start", default="2012-01-01", help="回测开始（默认 2012-01-01）")
    mb.add_argument("--end", help="回测结束")
    mb.add_argument("--top", type=int, default=10, help="持有前几只（默认 10）")
    mb.add_argument("--max-weight", type=float, default=0.2, help="单只上限（默认 0.2）")
    mb.add_argument("--cash", type=float, default=10_000, help="初始资金（默认 10000）")
    mb.add_argument("--slippage", type=float, default=5, help="滑点，基点（默认 5）")
    mb.add_argument("--fee", type=float, default=0, help="每笔手续费（默认 0）")
    mb.add_argument("--rf", type=float, default=0, help="无风险年利率（默认 0）")
    mb.add_argument("--refresh-universe", action="store_true", help="重新获取成分股名单")
    mb.add_argument("--no-chart", action="store_true", help="不画图")
    mb.set_defaults(fn=cmd_ml_backtest)
    me = mlsub.add_parser("experiment", help="预登记实验 v2：开发期比较所有版本并选出一个，考试期只运行一次")
    me.add_argument("phase", choices=("dev", "holdout"))
    me.set_defaults(fn=cmd_ml_experiment)
    ms = mlsub.add_parser("signal", help="按最新收盘价给出目标组合")
    ms.add_argument("--capital", type=float, help="按多少资金计算金额（默认交易资金上限）")
    ms.add_argument("--top", type=int, help="持有前几只")
    ms.add_argument("--max-weight", type=float, help="单只上限")
    ms.set_defaults(fn=cmd_ml_signal)

    tr = sub.add_parser("trade", help="自动交易：按机器学习组合每月调仓（模拟券商；实盘需要券商连接器）")
    trsub = tr.add_subparsers(dest="trade_cmd", required=True)
    tn = trsub.add_parser("new", help="创建交易账户")
    tn.add_argument("name")
    tn.add_argument("--mode", choices=("plan", "paper"), default="paper", help="plan：只出计划；paper：模拟券商（默认）")
    tn.add_argument("--capital", type=float, help="资金（默认并且最多为设置的上限）")
    tn.set_defaults(fn=cmd_trade_new)
    ts = trsub.add_parser("status", help="账户、持仓和最近的订单")
    ts.add_argument("name", nargs="?")
    ts.set_defaults(fn=cmd_trade_status)
    tru = trsub.add_parser("run", help="运行一次交易步骤：结算、估值、（到期时）调仓")
    tru.add_argument("--force", action="store_true", help="不等月末，按最新收盘价立刻调仓")
    tru.set_defaults(fn=cmd_trade_run)
    th = trsub.add_parser("halt", help="暂停一个账户")
    th.add_argument("name")
    th.add_argument("--reason")
    th.set_defaults(fn=cmd_trade_halt)
    trs = trsub.add_parser("resume", help="恢复一个账户")
    trs.add_argument("name")
    trs.set_defaults(fn=cmd_trade_resume)
    tm = trsub.add_parser("mode", help="切换模式（plan / paper / live）")
    tm.add_argument("name")
    tm.add_argument("mode", choices=("plan", "paper", "live"))
    tm.set_defaults(fn=cmd_trade_mode)

    az = sub.add_parser("analyze", help="个股分析：趋势、基本面、AI 评级、和持仓的关系、你的规则允许买多少")
    az.add_argument("symbol")
    az.add_argument("--amount", type=float, help="打算买多少美元（默认：规则允许的最大金额）")
    az.set_defaults(fn=cmd_analyze)

    ho = sub.add_parser("holdings", help="你的真实持仓：从账本同步，按你的规则检查，给出建议订单")
    hosub = ho.add_subparsers(dest="holdings_cmd", required=True)
    hosub.add_parser("status", help="账本密钥和上次同步时间").set_defaults(fn=cmd_holdings_status)
    hosub.add_parser("review", help="同步账本并检查持仓").set_defaults(fn=cmd_holdings_review)
    hp = hosub.add_parser("policy", help="查看或修改规则，例如 --set max_position=0.3 --set target=QQQM:0.4")
    hp.add_argument("--set", action="append", metavar="KEY=VALUE")
    hp.set_defaults(fn=cmd_holdings_policy)
    for name, text in (("approve", "确认一条建议订单"), ("reject", "拒绝一条建议订单")):
        hd = hosub.add_parser(name, help=text)
        hd.add_argument("id", type=int)
        hd.add_argument("--note")
        hd.set_defaults(fn=cmd_holdings_decide, cmd_name=name)

    sch = sub.add_parser("schedule", help="每日任务")
    schsub = sch.add_subparsers(dest="schedule_cmd", required=True)
    schsub.add_parser("run", help="立即运行一次：到期的 AI 分析 + 记录所有模拟账户").set_defaults(fn=cmd_schedule_run)

    se = sub.add_parser("secrets", help="密钥：只显示是否配置，输入时不回显")
    sesub = se.add_subparsers(dest="secrets_cmd", required=True)
    sesub.add_parser("status", help="哪些密钥已配置").set_defaults(fn=cmd_secrets_status)
    ss = sesub.add_parser("set", help="输入一个密钥，写进当前文件夹的 .env")
    ss.add_argument("name", choices=SECRETS)
    ss.set_defaults(fn=cmd_secrets_set)

    sv = sub.add_parser("serve", help="启动接口服务和网页工作台")
    sv.add_argument("--host", help="监听地址（默认 127.0.0.1，只允许本机访问）")
    sv.add_argument("--port", type=int, help="端口（默认 8000）")
    sv.add_argument("--log-file", help="日志写入这个文件（按 5 MB 轮换），用于后台运行")
    sv.set_defaults(fn=cmd_serve)
    sub.add_parser("open", help="用浏览器打开正在运行的工作台（自动带上令牌）").set_defaults(fn=cmd_open)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    logs.setup(getattr(a, "log_file", None))
    try:
        a.fn(a)
    except QuantLabError as e:
        sys.exit("出错了：%s" % e)


if __name__ == "__main__":
    main()
