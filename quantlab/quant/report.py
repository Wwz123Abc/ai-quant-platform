"""Chart of a backtest: price with the moving averages and trades, account value, and drawdown."""
import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "PingFang SC", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

UP, DOWN, INK, MUTED = "#1a8f4c", "#c0392b", "#1f2933", "#8a94a0"  # green up, red down


def chart(path, sym, sig, strat, bench, fast, slow):
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(11, 9), sharex=True, gridspec_kw={"height_ratios": [3, 2, 1.2]})
    idx = strat.equity.index
    s = sig.loc[idx]
    ax1.plot(idx, s["close"], color=INK, lw=1, label="复权收盘价")
    ax1.plot(idx, s["fast"], color="#d9822b", lw=1, label="%d 日均线" % fast)
    ax1.plot(idx, s["slow"], color="#2b6cb0", lw=1, label="%d 日均线" % slow)
    t = strat.trades
    ax1.scatter(t["entry_date"], t["entry_price"], marker="^", color=UP, s=40, zorder=3, label="买入")
    shut = t[~t["open"]]
    ax1.scatter(shut["exit_date"], shut["exit_price"], marker="v", color=DOWN, s=40, zorder=3, label="卖出")
    ax1.set_title("%s 均线交叉 %d/%d 回测" % (sym, fast, slow), loc="left")
    ax1.legend(loc="upper left", fontsize=8, frameon=False, ncol=5)

    ax2.plot(idx, strat.equity, color="#2b6cb0", lw=1.3, label="均线策略")
    ax2.plot(idx, bench.equity, color=MUTED, lw=1.1, label="买入持有")
    ax2.set_ylabel("账户价值（美元，对数坐标）")
    for ax in (ax1, ax2):  # log scale: equal heights mean equal percentage moves
        ax.set_yscale("log")
        label = matplotlib.ticker.FuncFormatter(lambda v, _: format(v, ",.0f") if v >= 10 else format(v, ".3g"))
        ax.yaxis.set_major_locator(matplotlib.ticker.LogLocator(subs=(1, 2, 5)))
        ax.yaxis.set_major_formatter(label)
        ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax2.legend(loc="upper left", fontsize=8, frameon=False)

    for res, color, name in ((strat, "#2b6cb0", "均线策略"), (bench, MUTED, "买入持有")):
        dd = res.equity / res.equity.cummax() - 1
        ax3.plot(idx, dd * 100, color=color, lw=1, label=name)
    ax3.set_ylabel("回撤 %")
    for ax in (ax1, ax2, ax3):
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
