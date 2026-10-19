import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { type Analysis, api, type AiRun, post } from "../api/client";
import { ErrorBox, Field, PageHead, Signed, Stat } from "../components/ui";
import { big, money, num, pctPlain } from "../lib/format";

export default function Analyze() {
  const [symbol, setSymbol] = useState("");
  const [amount, setAmount] = useState("");
  const run = useMutation({
    mutationFn: () => api<Analysis>(`/api/analyze/${encodeURIComponent(symbol.trim())}${amount ? `?amount=${Number(amount)}` : ""}`),
  });
  const ai = useMutation({ mutationFn: (sym: string) => post<AiRun>("/api/ai/runs", { symbol: sym }) });
  function submit(e: FormEvent) {
    e.preventDefault();
    run.mutate();
  }
  const r = run.data;
  return (
    <>
      <PageHead title="个股分析">
        想买一只股票时，先看它的趋势和风险、基本面、AI 评级，以及和你现有持仓的关系。结论只来自你在“我的持仓”里设定的规则（单只上限、现金储备）：
        能不能买、最多买多少。其余信息供你参考，不是买卖建议。
      </PageHead>
      <div className="card">
        <form className="params" onSubmit={submit}>
          <Field label="代码"><input value={symbol} onChange={(e) => setSymbol(e.target.value)} required maxLength={10} placeholder="例如 NVDA" /></Field>
          <Field label="打算买多少（$，可不填）"><input type="number" min={1} value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="默认：规则允许的最大金额" /></Field>
          <button className="primary" type="submit" disabled={run.isPending}>{run.isPending ? "分析中…" : "分析"}</button>
        </form>
        <ErrorBox error={run.error} />
      </div>
      {r ? (
        <>
          <div className={r.allowed ? "notice" : r.portfolio ? "error" : "notice"} role="status">
            <b>{r.verdict}</b>
            {r.checks.map((c) => <div key={c.rule}>{c.ok ? "✓" : "✗"} {c.rule}：{c.text}</div>)}
            {!r.portfolio ? <div>到“<Link to="/holdings">我的持仓</Link>”同步账本后，这里会按你的规则判断。</div> : null}
          </div>
          <div className="grid-2">
            <div className="card">
              <h2>趋势和风险（{r.technical.date} 收盘 {num(r.technical.close)}）</h2>
              <div className="stats">
                <Stat k="1 个月" v={<Signed v={r.technical.r1m} />} />
                <Stat k="1 年" v={<Signed v={r.technical.r1y} />} s={`12-1 月动量 ${r.technical.mom_12_1 == null ? "—" : pctPlain(r.technical.mom_12_1)}`} />
                <Stat k="年化波动率" v={pctPlain(r.technical.vol, 0)} s={`相对 QQQ 的 β ${num(r.technical.beta)}`} />
                <Stat k="距一年最高点" v={<Signed v={r.technical.from_high} digits={0} />} s={`一年最大回撤 ${pctPlain(r.technical.max_dd_1y, 0)}`} />
              </div>
              <p className="hint" style={{ marginTop: 10 }}>
                {r.technical.uptrend == null ? "均线数据不足" : r.technical.uptrend ? "50 日线在 200 日线上方（多头排列）" : "50 日线在 200 日线下方（空头排列）"}
                {r.technical.last_cross ? `，最近一次交叉在 ${r.technical.last_cross}` : ""}
              </p>
            </div>
            <div className="card">
              <h2>基本面（SEC 财报，按公告日）</h2>
              {r.fundamentals ? (
                <div className="stats">
                  <Stat k="最近 12 个月收入" v={big(r.fundamentals.revenue_ttm)} s={`同比 ${r.fundamentals.rev_growth == null ? "—" : pctPlain(r.fundamentals.rev_growth, 0)}`} />
                  <Stat k="净利润" v={big(r.fundamentals.net_income_ttm)} s={`营业利润率 ${pctPlain(r.fundamentals.op_margin, 0)}`} />
                  <Stat k="市盈率" v={r.fundamentals.pe ? r.fundamentals.pe.toFixed(0) : "—"} s={`市净率 ${r.fundamentals.pb ? r.fundamentals.pb.toFixed(1) : "—"} · 市值 ${big(r.fundamentals.market_cap)}`} />
                  <Stat k="SUE" v={num(r.fundamentals.sue, 1)} s="最近一季相对去年同季的盈利变化（标准化）" />
                </div>
              ) : <p className="empty">没有 SEC 数据（非美国公司或已退市）。</p>}
            </div>
          </div>
          <div className="grid-2">
            <div className="card">
              <h2>和你现有持仓的关系</h2>
              {r.portfolio ? (
                <>
                  <div className="stats">
                    <Stat k="规则允许最多买" v={money(r.portfolio.max_buy)} s={`可投资资金 ${money(r.portfolio.investable)}，现金 ${money(r.portfolio.cash)}`} />
                    <Stat k="按此金额买入后占比" v={pctPlain(r.portfolio.weight_after)} s={`约 ${num(r.portfolio.shares_at_close, 2)} 股（按收盘价）`} />
                    <Stat k="和持仓的平均相关" v={num(r.portfolio.avg_correlation)} s="按持仓市值加权，越接近 1 分散效果越差" />
                  </div>
                  <table style={{ marginTop: 10 }}>
                    <tbody>
                      {Object.entries(r.portfolio.correlations).sort((a, b) => b[1] - a[1]).map(([s, c]) => (
                        <tr key={s}><td>{s}</td><td className="num">{c.toFixed(2)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </>
              ) : <p className="empty">还没有同步持仓。</p>}
            </div>
            <div className="card">
              <h2>参考信号（不构成建议）</h2>
              {r.notes.length ? <ul style={{ margin: 0, paddingLeft: 18 }}>{r.notes.map((n, i) => <li key={i}>[{n.kind}] {n.text}</li>)}</ul> : <p className="empty">没有特别的信号。</p>}
              {r.model ? <p className="hint">机器学习排名：第 {r.model.rank} / {r.model.of}（{r.model.signal_date}）。该模型未通过考试期检验，只作参考。</p> : null}
              <div style={{ marginTop: 12 }}>
                {r.ai ? <p>最近一次 AI 评级：<Link to={`/ai/runs/${r.ai.id}`}>{r.ai.rating}（{r.ai.trade_date} 收盘后）</Link></p> : <p className="hint">还没有 AI 研报。</p>}
                <button onClick={() => ai.mutate(r.symbol)} disabled={ai.isPending || ai.isSuccess}>
                  {ai.isSuccess ? "已提交，到“AI 研报”查看" : "生成 AI 研报（约 ¥0.9，几分钟）"}
                </button>
                <ErrorBox error={ai.error} />
              </div>
            </div>
          </div>
          <p className="hint">{r.disclaimer}</p>
        </>
      ) : null}
    </>
  );
}
