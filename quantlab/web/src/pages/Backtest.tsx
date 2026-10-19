import { useMutation } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useMemo, useState } from "react";
import { type BacktestRequest, type BacktestResponse, post, type Summary } from "../api/client";
import TimeChart, { type Line, type TradeMark } from "../components/TimeChart";
import { ErrorBox, Field, PageHead, Signed, Stat } from "../components/ui";
import { drawdown, money, num, pct, pctPlain } from "../lib/format";

const fmtPrice = (v: number) => v.toFixed(2);
const fmtMoney = (v: number) => money(v);
const fmtPct = (v: number) => pct(v);

export default function Backtest() {
  const [form, setForm] = useState({ symbol: "QQQ", fast: "50", slow: "200", start: "2005-01-01", end: "", cash: "10000", fee: "0", slippage: "5" });
  const run = useMutation({ mutationFn: (req: BacktestRequest) => post<BacktestResponse>("/api/backtests", req) });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  function submit(e: FormEvent) {
    e.preventDefault();
    run.mutate({
      symbol: form.symbol.trim(), fast: Number(form.fast), slow: Number(form.slow),
      start: form.start || null, end: form.end || null,
      cash: Number(form.cash), fee: Number(form.fee), slippage: Number(form.slippage), rf: 0, refresh: false,
    });
  }

  return (
    <>
      <PageHead title="均线回测">
        短均线在长均线上方时持有，否则空仓。信号按收盘价计算，下一个交易日开盘成交，计入手续费和滑点，并和同期买入持有比较。
      </PageHead>
      <div className="card">
        <form className="params" onSubmit={submit}>
          <Field label="代码"><input value={form.symbol} onChange={set("symbol")} required maxLength={10} /></Field>
          <Field label="短均线（天）"><input type="number" min={2} value={form.fast} onChange={set("fast")} /></Field>
          <Field label="长均线（天）"><input type="number" min={3} value={form.slow} onChange={set("slow")} /></Field>
          <Field label="开始日期"><input type="date" value={form.start} onChange={set("start")} /></Field>
          <Field label="结束日期"><input type="date" value={form.end} onChange={set("end")} /></Field>
          <Field label="初始资金（$）"><input type="number" min={1} value={form.cash} onChange={set("cash")} /></Field>
          <Field label="每笔手续费（$）"><input type="number" min={0} step="any" value={form.fee} onChange={set("fee")} /></Field>
          <Field label="滑点（bp）"><input type="number" min={0} step="any" value={form.slippage} onChange={set("slippage")} /></Field>
          <button className="primary" type="submit" disabled={run.isPending}>{run.isPending ? "回测中…" : "开始回测"}</button>
        </form>
        <ErrorBox error={run.error} />
      </div>
      {run.data ? <Result r={run.data} /> : null}
    </>
  );
}

function Result({ r }: { r: BacktestResponse }) {
  const s = r.series;
  const price: Line[] = useMemo(() => [
    { name: "复权收盘价", color: "--series-3", values: s.close },
    { name: `${r.fast} 日线`, color: "--series-1", values: s.fast },
    { name: `${r.slow} 日线`, color: "--series-2", values: s.slow },
  ], [s, r.fast, r.slow]);
  const equity: Line[] = useMemo(() => [
    { name: "均线策略", color: "--series-1", values: s.equity },
    { name: "买入持有", color: "--series-2", values: s.benchmark },
  ], [s]);
  const dd: Line[] = useMemo(() => [
    { name: "均线策略", color: "--series-1", values: drawdown(s.equity) },
    { name: "买入持有", color: "--series-2", values: drawdown(s.benchmark) },
  ], [s]);
  // Price markers go on the close line, which is drawn first.
  const marks: TradeMark[] = useMemo(() => [
    ...r.trades.map((t) => ({ date: t.entry_date, side: "buy" as const })),
    ...r.trades.filter((t) => !t.open).map((t) => ({ date: t.exit_date, side: "sell" as const })),
  ].sort((a, b) => a.date.localeCompare(b.date)), [r.trades]);
  const a = r.summary;
  const b = r.benchmark;
  const years = a.years.toFixed(1);

  return (
    <>
      <div className="card">
        <h2>{r.symbol} · 均线 {r.fast}/{r.slow} · {a.start} → {a.end}（{years} 年）</h2>
        <div className="stats">
          <Stat k="均线策略年化" v={<Signed v={a.cagr} />} s={`期末 ${money(a.final)}`} />
          <Stat k="买入持有年化" v={<Signed v={b.cagr} />} s={`期末 ${money(b.final)}`} />
          <Stat k="策略最大回撤" v={<Signed v={a.mdd} />} s={`买入持有 ${pct(b.mdd)}`} />
          <Stat k="夏普比率" v={num(a.sharpe)} s={`买入持有 ${num(b.sharpe)}`} />
        </div>
      </div>
      <div className="card">
        <TimeChart label="价格与均线（对数坐标）" dates={s.dates} lines={price} format={fmtPrice} log marks={marks} size="tall" />
      </div>
      <div className="grid-2">
        <div className="card">
          <TimeChart label="账户价值（对数坐标）" dates={s.dates} lines={equity} format={fmtMoney} log />
        </div>
        <div className="card">
          <TimeChart label="回撤" dates={s.dates} lines={dd} format={fmtPct} />
        </div>
      </div>
      <div className="card">
        <h2>指标对比</h2>
        <div className="table-wrap">
          <Compare a={a} b={b} />
        </div>
      </div>
      <div className="card">
        <h2>交易明细（{r.trades.length} 笔，价格为复权价）</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>买入日</th><th className="num">买入价</th><th>卖出日</th><th className="num">卖出价</th><th className="num">股数</th><th className="num">盈亏</th><th className="num">收益率</th></tr>
            </thead>
            <tbody>
              {[...r.trades].reverse().map((t) => (
                <tr key={t.entry_date}>
                  <td>{t.entry_date}</td>
                  <td className="num">{t.entry_price.toFixed(2)}</td>
                  <td>{t.open ? "持有中" : t.exit_date}</td>
                  <td className="num">{t.exit_price.toFixed(2)}</td>
                  <td className="num">{t.shares}</td>
                  <td className="num">{money(t.pnl)}</td>
                  <td className="num"><Signed v={t.return} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="hint">回测是历史表现，不代表未来；反复调参数挑出来的最好结果往往是过拟合，请用“参数扫描”做样本外检验。</p>
      </div>
    </>
  );
}

function Compare({ a, b }: { a: Summary; b: Summary }) {
  const rows: [string, (m: Summary) => ReactNode][] = [
    ["期末资产", (m) => money(m.final)],
    ["总收益", (m) => <Signed v={m.total} />],
    ["年化收益", (m) => <Signed v={m.cagr} />],
    ["年化波动率", (m) => pctPlain(m.vol)],
    ["夏普比率", (m) => num(m.sharpe)],
    ["最大回撤", (m) => <><Signed v={m.mdd} /> <span className="hint">{m.mdd_peak} → {m.mdd_trough}</span></>],
    ["持仓时间占比", (m) => pctPlain(m.exposure, 0)],
    ["交易次数", (m) => m.trades],
    ["胜率", (m) => pctPlain(m.win_rate, 0)],
    ["平均每笔收益", (m) => <Signed v={m.avg_trade} />],
  ];
  return (
    <table>
      <thead><tr><th /><th className="num">均线策略</th><th className="num">买入持有</th></tr></thead>
      <tbody>
        {rows.map(([k, f]) => (
          <tr key={k}><td>{k}</td><td className="num">{f(a)}</td><td className="num">{f(b)}</td></tr>
        ))}
      </tbody>
    </table>
  );
}
