import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { type Analysis, api, type AiRun, post } from "../api/client";
import { ErrorBox, Field, PageHead, Signed, Stat } from "../components/ui";
import { big, money, num, pct, pctPlain } from "../lib/format";

type Fit = NonNullable<Analysis["portfolio"]>;

/** "约 6 股（按收盘价 296.96）", whole shares when your rules trade whole shares. */
function lots(p: Fit, n: number | null): string {
  if (n == null || p.price == null) return "没有收盘价，算不出股数";
  if (p.whole_shares && n < 1) return `按收盘价 ${p.price.toFixed(2)} 不到 1 股`;
  return `约 ${p.whole_shares ? n.toFixed(0) : n.toFixed(2)} 股（按收盘价 ${p.price.toFixed(2)}）`;
}

/** The weight after the purchase you asked about, after the most the rules allow, or now when nothing can be bought. */
function AfterBuy({ p }: { p: Fit }) {
  if (p.amount_given) return <Stat k={`买入 ${money(p.amount)} 后占比`} v={pctPlain(p.weight_after)} s={lots(p, p.shares)} />;
  if ((p.shares ?? 0) > 0) return <Stat k="买到上限后占比" v={pctPlain(p.weight_after)} s={`${lots(p, p.shares)}，约 ${money(p.amount)}`} />;
  return <Stat k="现在占比" v={pctPlain(p.current_weight)} s="按你的规则现在不能再买" />;
}

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
          {r.levels ? (
            <div className="card">
              <h2>关键价位（{r.levels.date} 收盘 {num(r.levels.close)}，{r.levels.source}）</h2>
              <ul className="levels">
                {[...r.levels.levels].sort((x, y) => y.price - x.price).map((l, i) => (
                  <li key={i}>
                    <div className="top">
                      <span className="name"><small>{l.group}</small>{l.name}</span>
                      <span className="val">{num(l.price)} <Signed v={l.distance} digits={1} /></span>
                    </div>
                    <div className="hint">{l.basis}</div>
                  </li>
                ))}
              </ul>
              <p className="hint">价位只是参考，不生成订单；行情是复权后的，较早的价位可能比当时的报价略低。</p>
            </div>
          ) : null}
          {r.ma_rules ? (
            <div className="card">
              <h2>均线规则（{r.ma_rules.date} 收盘）</h2>
              <p className="hint">
                9 条常见的均线“口诀”里，现在有 <b>{r.ma_rules.on_count}</b> 条处于“满足”状态（共 {r.ma_rules.ready_count} 条能判断）。
                这只是在描述走势，<b>不是买卖建议</b>：我们用 12 只美股、2008 年以来的数据逐条检验过，没有一条在风险调整后胜过买入持有，
                短周期的明显更差；长周期的（200 日线）只是回撤小一些、收益也低一些。详情见使用手册和 docs/research/2026-11-ma-rules.md。
              </p>
              <ul className="levels">
                {r.ma_rules.rules.map((m) => (
                  <li key={m.id}>
                    <div className="top">
                      <span className="name">{m.name}</span>
                      <span className="val">
                        {m.on == null ? "历史不够，无法判断" : m.on ? "满足" : "不满足"}
                        {m.since ? <small className="hint">（自 {m.since}，{m.days} 天）</small> : null}
                      </span>
                    </div>
                    <div className="hint">规则：{m.says}。</div>
                    <div className="hint">
                      历史检验：<b>{m.evidence.verdict}</b> · 年化 {(m.evidence.cagr * 100).toFixed(0)}%（按它操作每年约赚多少），一直拿着不动（买入持有）是 {(m.evidence.bh_cagr * 100).toFixed(0)}%
                      · 最大回撤 {(m.evidence.mdd * 100).toFixed(0)}%（最惨时账面跌多少；一直拿着是 {(m.evidence.bh_mdd * 100).toFixed(0)}%）· 夏普 {m.evidence.sharpe.toFixed(2)} 对 {m.evidence.bh_sharpe.toFixed(2)}（每承担一份风险赚多少，越高越好）
                    </div>
                  </li>
                ))}
              </ul>
              <p className="hint">检验按汇丰 Trade25 收费、收盘出信号次日开盘成交，数字是 12 只股票的中位数；过去的表现不代表以后。均线规则不会生成建议订单。</p>
            </div>
          ) : null}
          <div className="grid-2">
            <div className="card">
              <h2>趋势和风险（{r.technical.date} 收盘 {num(r.technical.close)}）</h2>
              <div className="stats">
                <Stat k="近 1 个月涨跌" v={<Signed v={r.technical.r1m} />} s="和一个月前的收盘价比" />
                <Stat k="近 1 年涨跌（1 年收益）" v={<Signed v={r.technical.r1y} />} s={`12-1 月动量 ${pct(r.technical.mom_12_1)}：一年前到一个月前这段的涨幅，不算最近一个月`} />
                <Stat k="价格波动有多大（年化波动率）" v={pctPlain(r.technical.vol, 0)} s={`一年里价格大致上下摆动这么多，越大越颠簸。相对 QQQ 的 β ${num(r.technical.beta)}：${r.technical.beta == null ? "—" : r.technical.beta > 1.1 ? `大盘涨跌 1% 它约 ${num(r.technical.beta, 1)}%，比大盘更颠簸` : r.technical.beta < 0.9 ? `大盘涨跌 1% 它约 ${num(r.technical.beta, 1)}%，比大盘稳` : "和大盘差不多"}`} />
                <Stat k="比一年最高价低多少（距 52 周高点）" v={<Signed v={r.technical.from_high} digits={0} />} s={`一年最大回撤 ${pct(r.technical.max_dd_1y, 0)}：这一年里最深的一次，从高点跌到低点跌了这么多`} />
              </div>
              <p className="hint" style={{ marginTop: 10 }}>
                {r.technical.uptrend == null ? "均线数据不足" : r.technical.uptrend ? "50 日线在 200 日线上方（多头排列）：中期趋势向上" : "50 日线在 200 日线下方（空头排列）：中期趋势向下"}
                {r.technical.last_cross ? `（两条线最近一次交叉在 ${r.technical.last_cross}）` : ""}
              </p>
            </div>
            <div className="card">
              <h2>基本面（SEC 财报，按公告日）</h2>
              {r.fundamentals ? (
                <div className="stats">
                  <Stat k="最近 12 个月收入（TTM）" v={big(r.fundamentals.revenue_ttm)} s={`同比 ${pct(r.fundamentals.rev_growth, 0)}，即比去年同期的变化`} />
                  <Stat k="净利润" v={big(r.fundamentals.net_income_ttm)} s={`营业利润率 ${pctPlain(r.fundamentals.op_margin, 0)}：每 100 元收入里，主业赚 ${r.fundamentals.op_margin == null ? "—" : (r.fundamentals.op_margin * 100).toFixed(0)} 元`} />
                  <Stat k="市盈率 PE（贵不贵）" v={r.fundamentals.pe ? r.fundamentals.pe.toFixed(0) : "—"} s={`股价是一年盈利的这么多倍，越大越贵（美股常见 15–30）· 市净率 ${r.fundamentals.pb ? r.fundamentals.pb.toFixed(1) : "—"} · 市值 ${big(r.fundamentals.market_cap)}`} />
                  <Stat k="最近一季盈利变化（SUE）" v={num(r.fundamentals.sue, 1)} s="标准化盈利意外：正数：比去年同季赚得多；负数：赚得少；绝对值超过 2 算明显" />
                </div>
              ) : <p className="empty">{r.fundamentals_note ?? "没有基本面数据。"}</p>}
            </div>
          </div>
          <div className="grid-2">
            <div className="card">
              <h2>和你现有持仓的关系</h2>
              {r.portfolio ? (
                <>
                  <div className="stats">
                    <Stat k="规则允许最多买" v={money(r.portfolio.max_buy)}
                      s={`${r.portfolio.max_buy > 0 ? lots(r.portfolio, r.portfolio.max_shares) + " · " : ""}可投资资金 ${money(r.portfolio.investable)}，现金 ${money(r.portfolio.cash)}`} />
                    <AfterBuy p={r.portfolio} />
                    <Stat k="和你持仓的相似度（平均相关系数）" v={num(r.portfolio.avg_correlation)} s="按持仓市值加权；1 表示涨跌几乎同步，买了也不分散风险；0.3 以下才算分散" />
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
