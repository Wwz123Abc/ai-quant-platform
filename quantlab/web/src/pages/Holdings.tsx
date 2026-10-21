import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { api, type HoldingsPolicy, type HoldingsReview, type HoldingsStatus, post, type Proposal } from "../api/client";
import { ErrorBox, PageHead, Signed, Stat } from "../components/ui";
import { money, pctPlain } from "../lib/format";

const STATUS: Record<string, string> = { pending: "待确认", approved: "已确认（请去券商下单）", rejected: "已拒绝", superseded: "已过期" };

export default function Holdings() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["hstatus"], queryFn: () => api<HoldingsStatus>("/api/holdings/status") });
  const review = useQuery({ queryKey: ["hreview"], queryFn: () => api<HoldingsReview>("/api/holdings/review"), retry: false });
  const run = useMutation({
    mutationFn: () => post<HoldingsReview>("/api/holdings/review", {}),
    onSuccess: (r) => { qc.setQueryData(["hreview"], r); qc.invalidateQueries({ queryKey: ["hstatus"] }); },
  });
  const decide = useMutation({
    mutationFn: (p: { id: number; ok: boolean }) => post<Proposal>(`/api/holdings/proposals/${p.id}/${p.ok ? "approve" : "reject"}`, { note: "" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hreview"] }),
  });
  const r = review.data;
  const v = r?.valuation;
  return (
    <>
      <PageHead title="我的持仓">
        从账本同步你的真实持仓，按你设定的规则检查（目标仓位、单只上限、现金储备），给出建议订单。你逐条确认后，自己去券商下单，成交后在账本记一笔。
        趋势、基本面、AI 评级只出现在“提醒”里，不会生成订单。
      </PageHead>
      {status.data && !status.data.configured ? (
        <div className="notice">
          还没有配置账本的只读访问密钥：在账本网站的“账号”页面创建密钥，然后在 quant-lab 文件夹的 <code>.env</code> 里加一行 <code>LEDGER_TOKEN=密钥</code>，重启服务。
        </div>
      ) : null}
      <div className="card">
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <button className="primary" onClick={() => run.mutate()} disabled={run.isPending || !status.data?.configured}>
            {run.isPending ? "同步和检查中…" : "同步账本并检查"}
          </button>
          <span className="hint">
            {status.data?.synced ? `上次同步 ${status.data.synced.replace("T", " ").slice(0, 16)}（${status.data.account}）` : "还没有同步过"} · 每日任务也会自动检查
          </span>
        </div>
        <ErrorBox error={run.error ?? decide.error} />
      </div>
      {r && v ? (
        <>
          <div className="card">
            <div className="stats">
              <Stat k="总资产" v={money(v.total)} s={`其中国债定存 ${money(v.fixed)}（持有到期，不参与调整）`} />
              <Stat k="可投资资金" v={money(v.investable)} s={`股票 ${money(v.stocks)} + 现金 ${money(v.cash)}`} />
              <Stat k="现金占比" v={pctPlain(v.cash_weight)} s={`储备下限 ${pctPlain(r.policy.cash_reserve, 0)}`} />
            </div>
          </div>
          <div className="card">
            <h2>建议订单（检查 #{r.id}）</h2>
            {r.proposals.length ? (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>方向</th><th>代码</th><th className="num">股数</th><th className="num">约价</th><th className="num">约金额</th><th>原因</th><th>状态</th><th /></tr></thead>
                  <tbody>
                    {r.proposals.map((o) => (
                      <tr key={o.id}>
                        <td className={o.side === "buy" ? "up" : "down"}>{o.side === "buy" ? "买入" : "卖出"}</td>
                        <td>{o.symbol}</td><td className="num">{o.shares}</td><td className="num">{o.price.toFixed(2)}</td>
                        <td className="num">{money(o.value)}</td><td style={{ whiteSpace: "normal" }}>{o.reason}</td>
                        <td>{STATUS[o.status] ?? o.status}</td>
                        <td>
                          {o.status === "pending" ? (
                            <span style={{ display: "flex", gap: 6 }}>
                              <button onClick={() => decide.mutate({ id: o.id, ok: true })}>确认</button>
                              <button onClick={() => decide.mutate({ id: o.id, ok: false })}>拒绝</button>
                            </span>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="empty">没有建议订单：持仓符合你的规则。</p>}
            {r.notes.map((n, i) => <p key={i} className="hint">· {n}</p>)}
            <p className="hint">“确认”只是记录你同意这笔调整；系统不会替你下单。</p>
          </div>
          <div className="grid-2">
            <div className="card">
              <h2>持仓</h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>代码</th><th className="num">股数</th><th className="num">成本</th><th className="num">现价</th><th className="num">市值</th><th className="num">盈亏</th><th className="num">占比</th><th className="num">目标</th></tr></thead>
                  <tbody>
                    {v.positions.map((p) => (
                      <tr key={p.symbol}>
                        <td>{p.symbol}</td><td className="num">{p.shares}</td><td className="num">{p.avg_cost.toFixed(2)}</td>
                        <td className="num">{p.price?.toFixed(2) ?? "—"}</td><td className="num">{money(p.value)}</td>
                        <td className="num"><Signed v={p.gain} /></td><td className="num">{pctPlain(p.weight)}</td>
                        <td className="num">{r.policy.targets?.[p.symbol] != null ? pctPlain(r.policy.targets[p.symbol], 0) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="card">
              <h2>提醒（只是信号，不生成订单）</h2>
              {r.reminders.length ? (
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {r.reminders.map((m, i) => <li key={i} className={m.level === "warn" ? "down" : undefined}>{m.symbol} [{m.kind}] {m.text}</li>)}
                </ul>
              ) : <p className="empty">没有提醒。</p>}
            </div>
          </div>
        </>
      ) : review.isPending ? <p className="empty">正在读取…</p> : <p className="empty">还没有检查过持仓。</p>}
      <PolicyCard />
    </>
  );
}

function toForm(p: HoldingsPolicy): Record<string, string> {
  const pct = (v: number | undefined) => String(Math.round((v ?? 0) * 1000) / 10);
  return {
    targets: Object.entries(p.targets ?? {}).map(([s, w]) => `${s}:${pct(w)}`).join(", "),
    band: pct(p.band), max_position: pct(p.max_position), cash_reserve: pct(p.cash_reserve),
    min_trade_usd: String(p.min_trade_usd ?? 50), whole_shares: p.whole_shares === false ? "0" : "1",
    alert_drawdown_from_cost: pct(p.alert_drawdown_from_cost), alert_drawdown_from_high: pct(p.alert_drawdown_from_high),
  };
}

function PolicyCard() {
  const pol = useQuery({ queryKey: ["hpolicy"], queryFn: () => api<HoldingsPolicy>("/api/holdings/policy") });
  if (!pol.data) return <div className="card"><h2>你的规则</h2><ErrorBox error={pol.error} /><p className="empty">正在读取…</p></div>;
  return <PolicyForm key={JSON.stringify(pol.data)} initial={pol.data} />;
}

function PolicyForm({ initial }: { initial: HoldingsPolicy }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<Record<string, string>>(() => toForm(initial));
  const save = useMutation({
    mutationFn: (p: HoldingsPolicy) => api<HoldingsPolicy>("/api/holdings/policy", { method: "PUT", body: JSON.stringify(p) }),
    onSuccess: (p) => qc.setQueryData(["hpolicy"], p),
  });
  const set = (k: string) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  function submit(e: FormEvent) {
    e.preventDefault();
    const targets: Record<string, number> = {};
    for (const part of (form.targets ?? "").split(/[,，\s]+/).filter(Boolean)) {
      const [s, w] = part.split(/[:：]/);
      if (s && w) targets[s.toUpperCase()] = Number(w) / 100;
    }
    const pct = (k: string) => Number(form[k]) / 100;
    save.mutate({
      targets, band: pct("band"), max_position: pct("max_position"), cash_reserve: pct("cash_reserve"),
      min_trade_usd: Number(form.min_trade_usd), whole_shares: form.whole_shares === "1",
      alert_drawdown_from_cost: pct("alert_drawdown_from_cost"), alert_drawdown_from_high: pct("alert_drawdown_from_high"),
    });
  }
  const f = (k: string, label: string) => (
    <div className="field"><label>{label}<input type="number" step="any" min={0} value={form[k] ?? ""} onChange={set(k)} /></label></div>
  );
  return (
    <div className="card">
      <h2>你的规则</h2>
      <p className="hint">占比都按“可投资资金”（股票 + 现金）计算。只有这些规则会生成建议订单。改完后点“同步账本并检查”生效。</p>
      <form className="params" onSubmit={submit}>
        <div className="field wide"><label>目标仓位（代码:百分比，逗号分隔，可不填）<input value={form.targets ?? ""} onChange={set("targets")} placeholder="例如 QQQM:40, VOO:30" /></label></div>
        {f("band", "再平衡区间（%）")}
        {f("max_position", "单只上限（%）")}
        {f("cash_reserve", "现金储备（%）")}
        {f("min_trade_usd", "最小下单金额（$）")}
        <div className="field"><label>按整股
          <select value={form.whole_shares ?? "1"} onChange={set("whole_shares")}><option value="1">是</option><option value="0">否（碎股）</option></select>
        </label></div>
        {f("alert_drawdown_from_cost", "低于成本提醒（%）")}
        {f("alert_drawdown_from_high", "离一年高点提醒（%）")}
        <button type="submit" disabled={save.isPending}>保存规则</button>
      </form>
      <ErrorBox error={save.error} />
      {save.isSuccess ? <p className="hint">已保存。</p> : null}
    </div>
  );
}
