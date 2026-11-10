import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { HoldingsReview } from "../api/client";
import { anomalies, Anomalies, PolicyForm } from "./Rules";

const pos = (symbol: string, shares: number, price: number, avg: number, weight: number) =>
  ({ symbol, currency: "USD", shares, avg_cost: avg, price, value: shares * price, gain: price / avg - 1, weight });

const POLICY = {
  targets: { QQQM: 0.5 }, band: 0.05, max_position: 0.25, uncapped: ["QQQM", "VOO"], cash_reserve: 0.1, min_trade_usd: 50, whole_shares: true,
  alert_drawdown_from_cost: 0.2, alert_drawdown_from_high: 0.25, take_profit: true, tp_gain_1: 0.5, tp_sell_1: 0.33, tp_gain_2: 1, tp_sell_2: 0.33,
};

function review(over: Partial<HoldingsReview> = {}): HoldingsReview {
  return {
    id: 7, at: "2026-11-09T00:00:00Z", synced: null, price_date: "2026-11-06", account: "a", policy: POLICY,
    valuation: {
      positions: [pos("QQQM", 10, 100, 90, 0.4), pos("TSLA", 5, 200, 100, 0.4)], cash: 500, stocks: 2000, investable: 2500, fixed: 0, fixed_items: [], total: 2500, cash_weight: 0.2,
    },
    proposals: [{ id: 1, symbol: "TSLA", side: "sell", shares: 2, price: 200, value: 400, reason: "超过单只上限 25%（当前 40.0%）", basis: "", status: "pending", decided_at: null, note: null }],
    notes: [], reminders: [], price_check: [], ...over,
  } as HoldingsReview;
}

describe("异常与冲突", () => {
  it("flags a stock over the fixed 25% cap, a target off its band and the order that fixes it", () => {
    const html = renderToStaticMarkup(<Anomalies r={review()} />);
    expect(html).toContain("TSLA 占 40.0%，超过单只上限 25%");
    expect(html).toContain("建议卖出 2 股（约 $400）");
    expect(html).toContain("QQQM 占 40.0%，目标 50%，偏离超过 5% 的区间");   // QQQM is uncapped but still has a target
    expect(html).toContain("违反规则");
  });

  it("says nothing is wrong when the holdings follow the rules", () => {
    const r = review({ proposals: [] });
    r.valuation.positions = [pos("QQQM", 10, 100, 90, 0.5)];
    r.policy = { ...POLICY, targets: { QQQM: 0.5 } };
    r.valuation.cash_weight = 0.5;
    expect(anomalies(r)).toEqual([]);
    expect(renderToStaticMarkup(<Anomalies r={r} />)).toContain("没有异常");
  });

  it("shows rules that contradict each other", () => {
    const r = review({ proposals: [] });
    r.policy = { ...POLICY, targets: { TSLA: 0.3, QQQM: 0.7 }, uncapped: ["QQQM"] };
    const titles = anomalies(r).map((a) => a.title);
    expect(titles).toContain("TSLA 的目标 30% 高于单只上限 25%");
    expect(titles.some((t) => t.startsWith("目标合计 100% 加 SGOV 储备 10% 超过 100%"))).toBe(true);
  });

  it("shows a take-profit order, and data that disagrees or is missing", () => {
    const r = review({
      proposals: [{ id: 2, symbol: "TSLA", side: "sell", shares: 1, price: 200, value: 200, reason: "止盈第 1 档（+50%）：现价比平均成本高 100%，卖出约 33% 的股数锁定一部分利润", basis: "规则：止盈", status: "pending", decided_at: null, note: null }],
      reminders: [{ symbol: "TSLA", level: "warn", kind: "核对", text: "和新浪差 0.9%", basis: "" }, { symbol: "BAC", level: "warn", kind: "数据", text: "取不到行情：超时", basis: "" }],
      notes: ["BAC 没有价格，无法计算股数"],
    });
    r.valuation.positions = [pos("QQQM", 10, 100, 90, 0.5), pos("TSLA", 5, 200, 100, 0.2)];
    const html = renderToStaticMarkup(<Anomalies r={r} />);
    expect(html).toContain("TSLA 到了止盈档：止盈第 1 档（+50%）");
    expect(html).toContain("TSLA 两个行情来源对不上");
    expect(html).toContain("BAC 取不到行情");
    expect(html).toContain("BAC 没有价格，无法计算股数");
  });

  it("notes a holding past the take-profit line that has no order this time, but never for a broad ETF", () => {
    const r = review({ proposals: [] });
    r.valuation.positions = [pos("QQQM", 10, 300, 90, 0.5), pos("TSLA", 1, 200, 100, 0.1)];
    const titles = anomalies(r).map((a) => a.title);
    expect(titles.some((t) => t.startsWith("TSLA 比成本高 100%，已过止盈线"))).toBe(true);
    expect(titles.some((t) => t.startsWith("QQQM 比成本高"))).toBe(false);
  });
});

describe("规则设置", () => {
  it("shows the cap as fixed and explains take profit with figures", () => {
    const html = renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}><PolicyForm initial={POLICY} investable={2500} /></QueryClientProvider>);
    expect(html).toContain("固定规则（不能改）");
    expect(html).toContain("<b class=\"fixed\">25%</b>");
    expect(html).toContain("<b>$625</b>");                                  // 25% of $2,500
    expect(html).toContain("涨到 $150 卖 3 股，涨到 $200 再卖 2 股，剩 5 股继续持有");
    expect(html).not.toContain("单只上限（%）");                            // no input for the cap
  });
});
