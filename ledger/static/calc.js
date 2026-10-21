// Bookkeeping rules shared by the home page and the stock dashboard, tested on their own (tests/calc.test.mjs).
//
// A record is one of:
//   buy / sell   shares, price, fees            moving average cost; a sale realizes price - average cost
//   dividend     amount (before tax), tax        cash paid out by the company; the net amount is income
//   split        from, to (from shares become to shares, e.g. 1 -> 2, or 10 -> 1 for a reverse split)
//                the share count changes and the cost does not, so the average cost per share changes
(function (root) {
  "use strict";
  const num = (v) => Number(v) || 0;
  const SIDES = ["buy", "sell", "dividend", "split"];
  // A split takes effect at the open of its date, so it comes before that day's trades.
  const RANK = { split: 0, buy: 1, sell: 1, dividend: 2 };

  const isTrade = (r) => r.side === "buy" || r.side === "sell";
  const ratio = (r) => (num(r.from) > 0 && num(r.to) > 0 ? num(r.to) / num(r.from) : 0);

  function sortRecords(list) {
    return list.slice().sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0) ||
      (RANK[a.side] ?? 1) - (RANK[b.side] ?? 1) || String(a.createdAt || "").localeCompare(String(b.createdAt || "")));
  }

  // Applies one record to a holding { shares, cost } in the record's currency and returns what it changed:
  // realized gain of a sale (after fees), net dividend, tax withheld, fees, and "oversold" when a sale exceeds
  // the shares held at the time.
  function apply(pos, r) {
    const out = { realized: 0, dividend: 0, tax: 0, fees: 0, oversold: false };
    if (r.side === "split") {
      const k = ratio(r);
      if (k > 0) pos.shares *= k;
      return out;
    }
    if (r.side === "dividend") {
      out.tax = num(r.tax);
      out.dividend = num(r.amount) - out.tax;
      return out;
    }
    const q = num(r.shares), p = num(r.price), f = num(r.fees);
    out.fees = f;
    if (r.side === "buy") {
      pos.shares += q;
      pos.cost += q * p + f;
      return out;
    }
    out.oversold = q > pos.shares + 1e-9;
    const sold = Math.min(q, pos.shares), avg = pos.shares > 0 ? pos.cost / pos.shares : 0;
    out.realized = sold * p - f - avg * sold;
    pos.cost -= avg * sold;
    pos.shares -= sold;
    if (pos.shares < 1e-9) { pos.shares = 0; pos.cost = 0; }
    return out;
  }

  // Money into (+) or out of (-) the portfolio for one record, in its currency; splits move no money.
  function cashFlow(r) {
    if (r.side === "buy") return -(num(r.shares) * num(r.price) + num(r.fees));
    if (r.side === "sell") return num(r.shares) * num(r.price) - num(r.fees);
    if (r.side === "dividend") return num(r.amount) - num(r.tax);
    return 0;
  }

  // Holdings after all records, per symbol: shares, cost, realized gain, net dividends, tax withheld, fees, and the
  // dates of any oversold sales.
  function holdings(list) {
    const out = {};
    for (const r of sortRecords(list)) {
      const s = out[r.symbol] = out[r.symbol] || { symbol: r.symbol, shares: 0, cost: 0, realized: 0, dividends: 0, tax: 0, fees: 0, oversold: [] };
      const d = apply(s, r);
      s.realized += d.realized; s.dividends += d.dividend; s.tax += d.tax; s.fees += d.fees;
      if (d.oversold) s.oversold.push(r);
    }
    return out;
  }

  const days = (a, b) => (Date.parse(a) - Date.parse(b)) / 864e5;

  // Dividends and splits that market data shows for a stock while it was held, but the records do not have.
  // actions: { SYMBOL: { dividends: [[exDate, payDate, perShare]], splits: [[date, from, to]] } }.
  // A dividend counts as recorded when a dividend record of the symbol is dated from 5 days before its ex-date to
  // 30 days after its pay date; each record answers for one dividend only, the nearest, so monthly payers are not
  // ticked off twice by one record. A split counts as recorded when a split record is within 5 days of its date.
  // Estimates use the shares held just before the ex-date or split date, as the records give them.
  function missingActions(records, actions, today) {
    const out = [];
    for (const sym of Object.keys(actions || {})) {
      const mine = records.filter((r) => r.symbol === sym);
      if (!mine.some(isTrade)) continue;
      const heldBefore = (date) => (holdings(mine.filter((r) => r.date < date))[sym] || { shares: 0 }).shares;
      const unused = mine.filter((r) => r.side === "dividend").map((r) => r.date);
      for (const [ex, pay, perShare] of (actions[sym].dividends || []).slice().sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
        if (ex > today) continue;
        const shares = heldBefore(ex);
        if (!(shares > 1e-9)) continue;
        const ref = pay && pay >= ex ? pay : ex, late = days(ref, ex) + 30;
        let best = -1;
        unused.forEach((d, i) => {
          const k = days(d, ex);
          if (k >= -5 && k <= late && (best < 0 || Math.abs(days(d, ref)) < Math.abs(days(unused[best], ref)))) best = i;
        });
        if (best >= 0) { unused.splice(best, 1); continue; }
        out.push({ kind: "dividend", key: "d|" + sym + "|" + ex, symbol: sym, date: ex, pay: pay || "", perShare, shares, gross: perShare * shares });
      }
      for (const [date, from, to] of actions[sym].splits || []) {
        if (date > today) continue;
        const shares = heldBefore(date);
        if (!(shares > 1e-9)) continue;
        if (mine.some((r) => r.side === "split" && Math.abs(days(r.date, date)) <= 5)) continue;
        out.push({ kind: "split", key: "s|" + sym + "|" + date, symbol: sym, date, from, to, shares });
      }
    }
    return out.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  }

  // Splits of one symbol as [date, ratio] oldest first, from market data and from the records; a record within
  // 5 days of a market split is the same split.
  function splitList(symbol, actions, records) {
    const out = ((actions && actions[symbol] && actions[symbol].splits) || [])
      .map(([d, from, to]) => [d, num(to) / num(from)]).filter(([, k]) => k > 0 && Number.isFinite(k));
    for (const r of records || []) {
      if (r.symbol === symbol && r.side === "split" && ratio(r) > 0 && !out.some(([d]) => Math.abs(days(d, r.date)) <= 5)) out.push([r.date, ratio(r)]);
    }
    return out.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  }

  // How many of today's shares one share held on `date` has become: the product of the ratios of later splits.
  // A split dated on `date` has already happened, as for records.
  const factorAfter = (date, splits) => splits.reduce((k, [d, r]) => (d > date ? k * r : k), 1);

  // Daily bars { date, open, high, low, close, volume } as they read after every later split, so a split is not a
  // cliff in a chart: prices divided by the factor, volume multiplied by it.
  function splitAdjust(bars, splits) {
    if (!splits.length) return bars;
    return bars.map((b) => {
      const k = factorAfter(b.date, splits);
      return k === 1 ? b : Object.assign({}, b, { open: b.open / k, high: b.high / k, low: b.low / k, close: b.close / k, volume: b.volume * k });
    });
  }

  root.LedgerCalc = { SIDES, isTrade, ratio, sortRecords, apply, cashFlow, holdings, missingActions, splitList, factorAfter, splitAdjust };
})(typeof window !== "undefined" ? window : globalThis);
