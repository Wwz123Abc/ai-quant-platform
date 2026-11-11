// Bookkeeping rules used by the home page, tested on their own (tests/calc.test.mjs).
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

  // The dividends of the symbols in `symbols` that market data shows and the records do not have, as dividend records (gross,
  // no tax) flagged `auto`: the owner keeps cash in SGOV, whose monthly interest is paid out as dividends. A dividend the owner
  // has recorded by hand is not counted twice (the same matching as missingActions). Dated on the ex-date, the day the shares must be held.
  function autoDividends(records, actions, symbols, today) {
    const only = {};
    for (const s of symbols || []) if (actions && actions[s]) only[s] = actions[s];
    return missingActions(records, only, today).filter((h) => h.kind === "dividend" && h.gross > 0).map((h) => ({
      id: "auto|" + h.key, side: "dividend", symbol: h.symbol, date: h.date, amount: Math.round(h.gross * 100) / 100, tax: 0, auto: true,
      note: "自动计入：每股 $" + Number(h.perShare).toFixed(4) + " × " + h.shares + " 股",
    }));
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

  // ---------- dates: YYYY-MM-DD strings, compared as text ----------
  const parseD = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
  const fmtD = (dt) => dt.toISOString().slice(0, 10);
  const dayDiff = (a, b) => Math.round((parseD(b) - parseD(a)) / 864e5);
  // Month arithmetic that keeps month-end dates at month end (6/30 - 6 months = 12/31), as Treasury coupon dates do.
  function addMonths(s, n) {
    const [y, m, d] = s.split("-").map(Number);
    const eom = d === new Date(Date.UTC(y, m, 0)).getUTCDate();
    const last = new Date(Date.UTC(y, m - 1 + n + 1, 0)).getUTCDate();
    return fmtD(new Date(Date.UTC(y, m - 1 + n, eom ? last : Math.min(d, last))));
  }

  // ---------- Treasuries and time deposits (also implemented by the quant platform; see tests/fixtures/contract.json) ----------
  // Coupon dates counted back from maturity: the first one is on or before settlement, the last is maturity.
  function coupons(b) {
    const step = 12 / (b.freq || 2), out = [];
    for (let i = 0; i < 400; i++) { const c = addMonths(b.maturity, -i * step); out.unshift(c); if (c <= b.settle) break; }
    return out;
  }
  // Hold-to-maturity value: clean price pulls straight-line to par, plus accrued coupon; a reference price replaces the clean part.
  function bondAt(b, d, mark) {
    if (d < b.settle) return null;
    const face = b.face, cpn = (face * b.coupon) / 100 / (b.freq || 2), cs = coupons(b);
    const cleanCost = (face * b.price) / 100, cost = cleanCost + (b.accruedPaid || 0);
    if (d >= b.maturity) return { value: 0, cost: 0, income: cpn * (cs.length - 1) + face - cost, matured: true };
    let k = 0;
    while (k + 1 < cs.length && cs[k + 1] <= d) k++;
    const accr = (cpn * dayDiff(cs[k], d)) / dayDiff(cs[k], cs[k + 1]);
    const clean = mark != null ? (face * mark) / 100 : cleanCost + ((face - cleanCost) * dayDiff(b.settle, d)) / dayDiff(b.settle, b.maturity);
    return { value: clean + accr, cost, income: clean + accr - cost + cpn * k, accr, matured: false };
  }
  // Time deposit: the maturity interest spread evenly over the days of the term.
  function depositAt(t, d) {
    if (d < t.start) return null;
    if (d >= t.maturity) return { value: 0, cost: 0, income: t.interest, matured: true };
    const income = (t.interest * dayDiff(t.start, d)) / dayDiff(t.start, t.maturity);
    return { value: t.principal + income, cost: t.principal, income, matured: false };
  }
  const markOn = (f, d) => (f.kind === "bond" && Number(f.markPrice) > 0 && f.markDate && d >= f.markDate ? Number(f.markPrice) : null);
  const fixedStart = (f) => (f.kind === "bond" ? f.settle : f.start);
  // One Treasury or deposit on date d, in its own currency: null before it starts; value 0 once matured, when the
  // money is back in savings.
  const fixedOn = (f, d) => (f.kind === "bond" ? bondAt(f, d, markOn(f, d)) : depositAt(f, d));

  // What one stock gained or lost since its previous close, counting what was bought or sold since then: the shares it is
  // worth now, minus the shares held at the previous close at that close, minus the money put in (buys and their fees) plus
  // the money taken out (sells less their fees). A share bought today is measured from its buy price, not from yesterday's
  // close, and a share sold today from its sale price. records are the ledger records, prevDate the date of the previous
  // close, price and prev in the stock's own currency. Returns {amount, base, basis, held, now, traded}: base is what the
  // day's percentage is a share of (the value at the previous close plus the cost of what was bought), basis the value the
  // shares held now must reach to break even for the day (the value at the previous close plus the money put in net of
  // what was taken out: now x price - basis = amount), traded whether anything was bought or sold since. null when a split is on record (share counts would not line up) or a price is missing.
  function dayChange(records, symbol, prevDate, price, prev) {
    if (!(price > 0) || !(prev > 0) || !prevDate) return null;
    let held = 0, moved = 0, net = 0, bought = 0, traded = false;
    for (const r of records) {
      if (r.symbol !== symbol) continue;
      if (r.side === "split") return null;
      if (!isTrade(r)) continue;
      const n = num(r.shares), gross = n * num(r.price), fee = num(r.fees), buy = r.side === "buy";
      if (r.date <= prevDate) { held += buy ? n : -n; continue; }
      traded = true;
      moved += buy ? n : -n;
      net += buy ? gross + fee : -(gross - fee);
      if (buy) bought += gross;
    }
    const now = held + moved;
    return { amount: now * price - held * prev - net, base: held * prev + bought, basis: held * prev + net, held, now, traded };
  }

  // All held stocks as one: rows are {shares, ccy, price, prev} with price and the previous close in the stock's own currency;
  // toUSD converts. value is what the stocks are worth at these prices, change what they gained or lost against the previous
  // closes, pct that change as a share of the previous value. A stock without a price is left out (missing); one without a
  // previous close counts in the value but not in the change.
  // A row may carry day: {amount, base} from dayChange, which counts what was bought or sold since the previous close.
  function portfolioQuote(rows, toUSD) {
    let value = 0, count = 0, missing = 0, change = 0, base = 0, withPrev = 0;
    for (const r of rows) {
      if (!(r.shares > 0)) continue;
      if (!(r.price > 0)) { missing++; continue; }
      value += toUSD(r.price * r.shares, r.ccy);
      count++;
      if (r.prev > 0) {
        change += toUSD(r.day ? r.day.amount : (r.price - r.prev) * r.shares, r.ccy);
        base += toUSD(r.day ? r.day.base : r.prev * r.shares, r.ccy);
        withPrev++;
      }
    }
    return { value, count, missing, change, pct: base > 0 ? (change / base) * 100 : null, withPrev };
  }

  // The held stocks as one intraday line. stocks: [{shares, ccy, prev, points: [[ts, price], ...]}] with ts ascending; toUSD
  // converts. At every minute any stock has a price, the value is each stock's shares times its latest price so far (its
  // previous close before its first minute), converted to USD. base is the same sum at the previous closes. Stocks without
  // a previous close or without minutes are left out (skipped).
  function intradayPortfolio(stocks, toUSD) {
    const used = stocks.filter((s) => s.shares > 0 && s.prev > 0 && s.points && s.points.length);
    const times = [...new Set(used.flatMap((s) => s.points.map((p) => p[0])))].sort((a, b) => a - b);
    const at = used.map(() => 0), cur = used.map((s) => s.prev), v = [];
    // A stock may carry basis and pbase from dayChange (what was bought or sold since the previous close counted): the line
    // is then measured from that basis, and a percentage is a share of pbase. Without them both are shares x previous close.
    let base = 0, pbase = 0;
    used.forEach((s) => {
      base += toUSD(s.basis != null ? s.basis : s.shares * s.prev, s.ccy);
      pbase += toUSD(s.pbase != null ? s.pbase : s.shares * s.prev, s.ccy);
    });
    for (const t of times) {
      let total = 0;
      used.forEach((s, i) => {
        while (at[i] < s.points.length && s.points[at[i]][0] <= t) cur[i] = s.points[at[i]++][1];
        total += toUSD(s.shares * cur[i], s.ccy);
      });
      v.push(total);
    }
    return { t: times, v, base, pbase, used: used.length, skipped: stocks.length - used.length };
  }

  root.LedgerCalc = {
    intradayPortfolio,
    portfolioQuote,
    dayChange,
    SIDES, isTrade, ratio, sortRecords, apply, cashFlow, holdings, missingActions, autoDividends, splitList, factorAfter, splitAdjust,
    parseD, fmtD, dayDiff, addMonths, coupons, bondAt, depositAt, markOn, fixedStart, fixedOn,
  };
})(typeof window !== "undefined" ? window : globalThis);
