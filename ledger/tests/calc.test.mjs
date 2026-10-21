// Bookkeeping rules (static/calc.js). Run: node --test ledger/tests/calc.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const ctx = {};
vm.runInNewContext(readFileSync(new URL("../static/calc.js", import.meta.url), "utf8"), ctx);
const C = ctx.LedgerCalc;
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

const buy = (date, shares, price, fees = 0) => ({ date, side: "buy", symbol: "X", shares, price, fees });
const sell = (date, shares, price, fees = 0) => ({ date, side: "sell", symbol: "X", shares, price, fees });

test("moving average cost and realized gain", () => {
  const h = C.holdings([buy("2026-01-02", 10, 100, 1), buy("2026-02-02", 10, 120, 1), sell("2026-03-02", 5, 130, 1)]).X;
  // average cost (1001 + 1201) / 20 = 110.1; selling 5 realizes 5 * 130 - 1 - 5 * 110.1
  near(h.realized, 5 * 130 - 1 - 5 * 110.1);
  near(h.shares, 15);
  near(h.cost, 2202 - 5 * 110.1);
  near(h.fees, 3);
});

test("selling more than held is flagged and capped", () => {
  const h = C.holdings([buy("2026-01-02", 2, 10), sell("2026-01-03", 3, 12)]).X;
  assert.equal(h.oversold.length, 1);
  near(h.shares, 0);
  near(h.realized, 2 * 12 - 2 * 10);
});

test("a dividend is income net of tax and leaves shares and cost alone", () => {
  const h = C.holdings([buy("2026-01-02", 10, 100), { date: "2026-03-31", side: "dividend", symbol: "X", amount: 8, tax: 0.8 }]).X;
  near(h.dividends, 7.2);
  near(h.tax, 0.8);
  near(h.shares, 10);
  near(h.cost, 1000);
});

test("a split changes the share count and the average cost, not the total cost", () => {
  const recs = [buy("2026-01-02", 10, 300), { date: "2026-06-10", side: "split", symbol: "X", from: 1, to: 3 }];
  const h = C.holdings(recs).X;
  near(h.shares, 30);
  near(h.cost, 3000);
  near(h.cost / h.shares, 100);
  // a later sale at the post-split price realizes against the post-split average cost
  const after = C.holdings([...recs, sell("2026-07-01", 30, 110)]).X;
  near(after.realized, 30 * 110 - 3000);
  near(after.shares, 0);
});

test("a reverse split", () => {
  const h = C.holdings([buy("2026-01-02", 100, 2), { date: "2026-05-01", side: "split", symbol: "X", from: 10, to: 1 }]).X;
  near(h.shares, 10);
  near(h.cost / h.shares, 20);
});

test("a split comes before the trades of its own day", () => {
  // bought 10 before the split, 5 more on the split day at the new price: 10 * 2 + 5 = 25 shares
  const h = C.holdings([
    buy("2026-06-10", 5, 50),
    { date: "2026-06-10", side: "split", symbol: "X", from: 1, to: 2 },
    buy("2026-06-01", 10, 100),
  ]).X;
  near(h.shares, 25);
  near(h.cost, 1000 + 250);
});

test("a split with a missing or zero ratio changes nothing", () => {
  const h = C.holdings([buy("2026-01-02", 10, 100), { date: "2026-06-10", side: "split", symbol: "X", from: 0, to: 2 }]).X;
  near(h.shares, 10);
});

test("cash flows: buys out, sales and net dividends in, splits none", () => {
  near(C.cashFlow(buy("d", 10, 100, 1)), -1001);
  near(C.cashFlow(sell("d", 10, 100, 1)), 999);
  near(C.cashFlow({ side: "dividend", amount: 8, tax: 0.8 }), 7.2);
  near(C.cashFlow({ side: "split", from: 1, to: 2 }), 0);
});

test("record kinds", () => {
  assert.ok(C.isTrade({ side: "buy" }) && C.isTrade({ side: "sell" }));
  assert.ok(!C.isTrade({ side: "dividend" }) && !C.isTrade({ side: "split" }));
  near(C.ratio({ from: 1, to: 4 }), 4);
  near(C.ratio({ from: 10, to: 1 }), 0.1);
});

const TODAY = "2026-10-21";
const voo = (extra = []) => [{ date: "2025-10-01", side: "buy", symbol: "VOO", shares: 2, price: 600 }, ...extra];
const VOO_ACTIONS = { VOO: { dividends: [["2025-09-29", "2025-10-01", 1.74], ["2025-12-22", "2025-12-24", 1.771], ["2026-03-27", "2026-03-31", 1.8724]], splits: [] } };

test("missing dividends: only while held, estimated from the shares held before the ex-date", () => {
  const m = C.missingActions(voo(), VOO_ACTIONS, TODAY);
  // 2025-09-29 was before the first buy; the other two are missing, newest first
  assert.equal(m.map((x) => x.date).join(), "2026-03-27,2025-12-22");
  near(m[0].gross, 1.8724 * 2);
  assert.equal(m[0].pay, "2026-03-31");
});

test("a dividend record near the pay date ticks one dividend off, and only one", () => {
  const m = C.missingActions(voo([{ date: "2025-12-24", side: "dividend", symbol: "VOO", amount: 3.54, tax: 0.35 }]), VOO_ACTIONS, TODAY);
  assert.equal(m.map((x) => x.date).join(), "2026-03-27");
  // monthly payer: one record must not answer for two dividends a month apart
  const monthly = { SGOV: { dividends: [["2026-08-01", "2026-08-05", 0.4], ["2026-09-01", "2026-09-05", 0.4]], splits: [] } };
  const recs = [{ date: "2026-07-01", side: "buy", symbol: "SGOV", shares: 10, price: 100 },
    { date: "2026-08-20", side: "dividend", symbol: "SGOV", amount: 4, tax: 0 }];
  assert.equal(C.missingActions(recs, monthly, TODAY).length, 1);
});

test("future dividends and stocks never bought are not suggested", () => {
  const a = { VOO: { dividends: [["2026-12-20", "2026-12-24", 1.9]], splits: [] }, KO: { dividends: [["2026-09-15", "2026-10-01", 0.53]], splits: [] } };
  assert.equal(C.missingActions(voo(), a, TODAY).length, 0);
});

test("missing splits, and a recorded one within five days counts", () => {
  const a = { NFLX: { dividends: [], splits: [["2015-07-15", 1, 7], ["2025-11-17", 1, 10]] } };
  const buy = { date: "2025-10-01", side: "buy", symbol: "NFLX", shares: 1, price: 1170.9 };
  const m = C.missingActions([buy], a, TODAY);
  assert.equal(m.length, 1);
  assert.equal(m[0].kind, "split");
  assert.equal(m[0].to, 10);
  assert.equal(C.missingActions([buy, { date: "2025-11-18", side: "split", symbol: "NFLX", from: 1, to: 10 }], a, TODAY).length, 0);
});

test("after a recorded split, a later dividend is estimated on the split share count", () => {
  const a = { NFLX: { dividends: [["2026-01-10", "2026-01-20", 0.1]], splits: [["2025-11-17", 1, 10]] } };
  const recs = [{ date: "2025-10-01", side: "buy", symbol: "NFLX", shares: 1, price: 1170.9 }, { date: "2025-11-17", side: "split", symbol: "NFLX", from: 1, to: 10 }];
  const m = C.missingActions(recs, a, TODAY);
  assert.equal(m.length, 1);
  near(m[0].shares, 10);
  near(m[0].gross, 1);
});
