import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { HoldingsReview } from "../api/client";
import { Status } from "./Rules";

const pos = (symbol: string, shares: number, price: number, avg: number, weight: number) =>
  ({ symbol, currency: "USD", shares, avg_cost: avg, price, value: shares * price, gain: price / avg - 1, weight });

function review(over: Partial<HoldingsReview> = {}): HoldingsReview {
  return {
    id: 7, at: "2026-11-09T00:00:00Z", synced: null, price_date: "2026-11-06", account: "a",
    policy: { targets: { QQQM: 0.5 }, band: 0.05, max_position: 0.3, uncapped: ["QQQM", "VOO"], cash_reserve: 0.1, min_trade_usd: 50, whole_shares: true, alert_drawdown_from_cost: 0.2, alert_drawdown_from_high: 0.25 },
    valuation: {
      positions: [pos("QQQM", 10, 100, 90, 0.4), pos("TSLA", 5, 200, 150, 0.4)], cash: 500, stocks: 2000, investable: 2500, fixed: 0, fixed_items: [], total: 2500, cash_weight: 0.2,
    },
    proposals: [{ id: 1, symbol: "TSLA", side: "sell", shares: 2, price: 200, value: 400, reason: "超过单只上限 30%", basis: "", status: "pending", decided_at: null, note: null }],
    notes: [], reminders: [], price_check: [], ...over,
  } as HoldingsReview;
}

describe("Rules page", () => {
  it("compares each rule with the holdings and plans the orders with the cash left", () => {
    const html = renderToStaticMarkup(<Status r={review()} />);
    expect(html).toContain("最大 TSLA 40.0%");            // the cap is looked at for TSLA, not the uncapped ETF
    expect(html).toContain("✗ 不符合");                    // over the cap, and QQQM is below its target band
    expect(html).toContain("超上限");
    expect(html).toContain("卖出 2 股");
    expect(html).toContain("执行顺序（先卖后买）");
    expect(html).toContain("调整后 <b>$900</b>");          // 500 cash + 400 from the sale
  });

  it("says nothing needs doing when the holdings follow the rules", () => {
    const r = review({ proposals: [] });
    r.valuation.positions = [pos("QQQM", 10, 100, 90, 0.4)]; r.policy.targets = { QQQM: 0.4 };
    r.valuation.investable = 2500; r.valuation.stocks = 1000; r.valuation.cash = 1500; r.valuation.cash_weight = 0.6;
    const html = renderToStaticMarkup(<Status r={r} />);
    expect(html).toContain("现在不需要调整");
  });
});
