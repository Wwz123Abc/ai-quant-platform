import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type HoldingsReview, type HoldingsStatus, post, type Proposal } from "../api/client";
import { ErrorBox, PageHead, Signed, Stat } from "../components/ui";
import { money, pctPlain, when } from "../lib/format";

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
            {status.data?.synced ? `上次同步 ${when(status.data.synced)}（北京时间，${status.data.account}）` : "还没有同步过"} · 每周二的任务和账本有变化时也会自动检查
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
            <h2>建议订单（检查 #{r.id}{r.price_date ? `，按 ${r.price_date} 收盘价` : ""}）</h2>
            {r.proposals.length ? (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>方向</th><th>代码</th><th className="num">股数</th><th className="num">约价</th><th className="num">约金额</th><th>原因</th><th>状态</th><th /></tr></thead>
                  <tbody>
                    {r.proposals.map((o) => (
                      <tr key={o.id}>
                        <td className={o.side === "buy" ? "up" : "down"}>{o.side === "buy" ? "买入" : "卖出"}</td>
                        <td>{o.symbol}</td><td className="num">{o.shares}</td><td className="num">{o.price.toFixed(2)}</td>
                        <td className="num">{money(o.value)}</td><td style={{ whiteSpace: "normal" }}>{o.reason}{o.basis ? <details><summary className="hint">依据</summary><span className="hint">{o.basis}</span></details> : null}</td>
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
                  {r.reminders.map((m, i) => <li key={i} className={m.level === "warn" ? "down" : undefined}>{m.symbol} [{m.kind}] {m.text}{m.basis ? <details><summary className="hint">依据</summary><span className="hint">{m.basis}</span></details> : null}</li>)}
                </ul>
              ) : <p className="empty">没有提醒。</p>}
              <PriceCheckLine checks={r.price_check ?? []} />
            </div>
          </div>
          {v.fixed_items?.length ? (
            <div className="card">
              <h2>国债和定期存款（{money(v.fixed)}，按账本的算法）</h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>名称</th><th>到期日</th><th className="num">价值</th><th>状态</th></tr></thead>
                  <tbody>
                    {v.fixed_items.map((f, i) => (
                      <tr key={i}>
                        <td>{f.name}</td><td>{f.maturity}</td>
                        <td className="num">{f.value == null ? "—" : money(f.value)}</td>
                        <td>{!f.started ? "未开始" : f.matured ? "已到期，回到储蓄账户" : `还有 ${f.days_left} 天`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="hint">持有到期：国债按买入净价逐日拉向面值再加应计利息，定存按天数平摊利息，和账本首页一样；港币按设定的汇率折成美元。已到期的不再计入，钱回到储蓄账户。</p>
            </div>
          ) : null}
        </>
      ) : review.isPending ? <p className="empty">正在读取…</p> : <p className="empty">还没有检查过持仓。</p>}
      <p className="hint">
        规则的说明、你现在和规则的对照、详细调仓计划，以及修改规则，都在单独的页面：<Link to="/rules">持仓规则</Link>。
      </p>
    </>
  );
}

/** One line on the cross-check of the held stocks' closes; the problems themselves are in the reminders. */
function PriceCheckLine({ checks }: { checks: HoldingsReview["price_check"] }) {
  if (!checks?.length) return null;
  const good = checks.filter((c) => c.ok);
  const against = good[0]?.against || "第二个来源";
  return (
    <p className="hint" style={{ marginTop: 12 }}>
      价格核对（规则和账本相同）：{good.length === checks.length
        ? `${good.map((c) => c.symbol).join("、")} 最近 5 个交易日的收盘价和${against}一致，差异不超过 0.2%。`
        : `${good.length} 只一致，${checks.length - good.length} 只有问题（见上面标“核对”的提醒）。`}
    </p>
  );
}
