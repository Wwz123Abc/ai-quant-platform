import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useState } from "react";
import { api, type HoldingsPolicy, type HoldingsReview, type HoldingsStatus, post, type Proposal } from "../api/client";
import { ErrorBox, PageHead } from "../components/ui";
import { money, pctPlain, when } from "../lib/format";

/** The single-stock cap is fixed by the owner: it is shown here, never edited (the server also forces it). */
export const MAX_POSITION = 0.25;

export type Anomaly = { level: "error" | "warn" | "info"; group: string; title: string; detail: string; basis?: string };

const LEVEL_TEXT = { error: "违反规则", warn: "需要留意", info: "信号提醒" } as const;

/** Everything in the latest check that conflicts with your rules or with itself: breaches, rules that clash with each other,
 *  data that disagrees or is missing, take-profit steps reached, and the signals that argue with a holding. */
export function anomalies(r: HoldingsReview): Anomaly[] {
  const v = r.valuation, pol = r.policy;
  const out: Anomaly[] = [];
  const uncapped = new Set(pol.uncapped ?? []);
  const targets = pol.targets ?? {};
  const order = (sym: string) => r.proposals.find((o) => o.symbol === sym && o.status === "pending");
  const orderText = (sym: string) => {
    const o = order(sym);
    return o ? `建议${o.side === "buy" ? "买入" : "卖出"} ${o.shares} 股（约 ${money(o.value)}），在上面的“建议订单”里确认。` : "按整股或最小下单金额，这次没有生成建议订单。";
  };
  for (const p of v.positions) {
    if (!uncapped.has(p.symbol) && p.weight != null && p.weight > MAX_POSITION + 1e-9) {
      out.push({ level: "error", group: "仓位", title: `${p.symbol} 占 ${pctPlain(p.weight)}，超过单只上限 ${pctPlain(MAX_POSITION, 0)}`, detail: orderText(p.symbol) });
    }
  }
  if (v.cash_weight != null && v.cash_weight < pol.cash_reserve - 1e-9) {
    out.push({ level: "error", group: "SGOV", title: `SGOV（现金）只占 ${pctPlain(v.cash_weight)}，低于储备 ${pctPlain(pol.cash_reserve, 0)}`, detail: `至少要有 ${money(pol.cash_reserve * v.investable)}，现在 ${money(v.cash)}。买入会被暂停，直到 SGOV 回到储备以上。` });
  }
  for (const [sym, t] of Object.entries(targets)) {
    const w = v.positions.find((p) => p.symbol === sym)?.weight ?? 0;
    if (Math.abs(w - t) > pol.band + 1e-9) {
      out.push({ level: "error", group: "仓位", title: `${sym} 占 ${pctPlain(w)}，目标 ${pctPlain(t, 0)}，偏离超过 ${pctPlain(pol.band, 0)} 的区间`, detail: orderText(sym) });
    }
    if (!uncapped.has(sym) && t > MAX_POSITION + 1e-9) {
      out.push({ level: "error", group: "规则冲突", title: `${sym} 的目标 ${pctPlain(t, 0)} 高于单只上限 ${pctPlain(MAX_POSITION, 0)}`, detail: "两条规则互相矛盾：这只股票永远买不到目标。把目标降到 25% 以内，或确认它是宽基 ETF 并加进“不设上限的代码”。" });
    }
  }
  const tsum = Object.values(targets).reduce((a, b) => a + b, 0);
  if (tsum + pol.cash_reserve > 1 + 1e-9) {
    out.push({ level: "error", group: "规则冲突", title: `目标合计 ${pctPlain(tsum, 0)} 加 SGOV 储备 ${pctPlain(pol.cash_reserve, 0)} 超过 100%`, detail: "目标不可能同时满足：减少目标或降低 SGOV 储备。" });
  }
  for (const o of r.proposals.filter((x) => x.status === "pending" && x.reason.startsWith("止盈"))) {
    out.push({ level: "warn", group: "止盈", title: `${o.symbol} 到了止盈档：${o.reason.split("：")[0]}`, detail: `${o.reason.split("：").slice(1).join("：")}。建议卖出 ${o.shares} 股（约 ${money(o.value)}），在上面的“建议订单”里确认。`, basis: o.basis });
  }
  if (pol.take_profit) {
    for (const p of v.positions) {
      if (!uncapped.has(p.symbol) && p.gain != null && p.gain >= pol.tp_gain_1 && !order(p.symbol)) {
        out.push({ level: "info", group: "止盈", title: `${p.symbol} 比成本高 ${pctPlain(p.gain, 0)}，已过止盈线，这次没有止盈订单`, detail: "这一档你已经确认过，或者按整股、最小下单金额算出来不到 1 股。再买入这只股票后，止盈会重新计。" });
      }
    }
  }
  for (const m of r.reminders) {
    if (m.kind === "核对") out.push({ level: "warn", group: "数据冲突", title: `${m.symbol} 两个行情来源对不上`, detail: m.text, basis: m.basis });
    else if (m.kind === "数据") out.push({ level: "warn", group: "数据缺失", title: `${m.symbol} 取不到行情`, detail: m.text, basis: m.basis });
    else if (m.kind === "到期") out.push({ level: "warn", group: "账本", title: `${m.symbol} ${m.text.includes("已到期") ? "已到期" : "快到期"}`, detail: m.text, basis: m.basis });
    else if (m.kind === "亏损" || m.kind === "回撤") out.push({ level: "warn", group: "提醒线", title: `${m.symbol} ${m.text}`, detail: m.kind === "亏损" ? "触发了“低于成本提醒”。只提醒，不生成订单。" : "触发了“离一年高点提醒”。只提醒，不生成订单。", basis: m.basis });
    else if (m.kind === "AI" && m.level === "warn") out.push({ level: "info", group: "信号", title: `${m.symbol} ${m.text}`, detail: "AI 评级偏谨慎，但你在持有：只是提醒，是否调整由你决定。", basis: m.basis });
    else if (m.kind === "基本面" && m.level === "warn") out.push({ level: "info", group: "信号", title: `${m.symbol} ${m.text}`, detail: "财报信号偏弱，只是提醒。", basis: m.basis });
    else if (m.kind === "规则" && m.symbol === "SGOV") continue;   // the SGOV reserve is already reported above with its figures
    else out.push({ level: "info", group: "信号", title: `${m.symbol} ${m.text}`, detail: "只是信号，不生成订单。", basis: m.basis });
  }
  for (const n of r.notes) {
    out.push({ level: "warn", group: "规则和数据冲突", title: n, detail: "规则要求调整，但价格、SGOV 或下单限制让它没法照做。" });
  }
  return out;
}

const STATUS: Record<string, string> = { pending: "待确认", approved: "已确认（请去券商下单）", rejected: "已拒绝", superseded: "已过期" };

export default function Rules() {
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
  return (
    <>
      <PageHead title="持仓规则">
        你的持仓规则放在这一页：从账本同步真实持仓，按规则检查，给出建议订单，列出异常和冲突，最后是规则设置。只有这些规则会生成建议订单；
        趋势、基本面、AI 评级只做提醒。你逐条确认后自己去券商下单，成交后在账本记一笔，系统不会替你下单。持仓本身（股数、成本、盈亏）看<a href="/">持仓账本</a>。
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
        {r ? (
          <p className="hint" style={{ marginTop: 8 }}>
            可投资资金 {money(r.valuation.investable)}（股票 {money(r.valuation.stocks)} + SGOV {money(r.valuation.cash)}）· SGOV 占 {pctPlain(r.valuation.cash_weight)}，储备下限 {pctPlain(r.policy.cash_reserve, 0)}
          </p>
        ) : null}
        <ErrorBox error={run.error ?? decide.error} />
      </div>
      {r ? (
        <>
          <Orders r={r} onDecide={(id, ok) => decide.mutate({ id, ok })} />
          <Anomalies r={r} />
        </>
      ) : review.isPending ? <p className="empty">正在读取…</p> : <div className="card"><h2>建议订单和异常</h2><p className="empty">还没有检查过持仓：点上面的“同步账本并检查”。</p></div>}
      <PolicyCard investable={r?.valuation.investable} />
    </>
  );
}

export function Orders({ r, onDecide }: { r: HoldingsReview; onDecide: (id: number, ok: boolean) => void }) {
  return (
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
                  <td className="num">{money(o.value)}</td>
                  <td style={{ whiteSpace: "normal" }}>{o.reason}{o.basis ? <details><summary className="hint">依据</summary><span className="hint">{o.basis}</span></details> : null}</td>
                  <td>{STATUS[o.status] ?? o.status}</td>
                  <td>
                    {o.status === "pending" ? (
                      <span style={{ display: "flex", gap: 6 }}>
                        <button onClick={() => onDecide(o.id, true)}>确认</button>
                        <button onClick={() => onDecide(o.id, false)}>拒绝</button>
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
  );
}

/** One line on the cross-check of the held stocks' closes; the problems themselves are in the anomalies. */
function PriceCheckLine({ checks }: { checks: HoldingsReview["price_check"] }) {
  if (!checks?.length) return null;
  const good = checks.filter((c) => c.ok);
  const against = good[0]?.against || "第二个来源";
  return (
    <p className="hint" style={{ marginTop: 12 }}>
      价格核对：{good.length === checks.length
        ? `${good.map((c) => c.symbol).join("、")} 最近 5 个交易日的收盘价和${against}一致，差异不超过 0.2%。`
        : `${good.length} 只一致，${checks.length - good.length} 只有问题（见上面标“数据冲突”的项）。`}
    </p>
  );
}

export function Anomalies({ r }: { r: HoldingsReview }) {
  const items = anomalies(r);
  const levels = (["error", "warn", "info"] as const).filter((l) => items.some((i) => i.level === l));
  return (
    <div className="card">
      <h2>异常与冲突（检查 #{r.id}{r.price_date ? `，${r.price_date} 收盘价` : ""}）</h2>
      {items.length ? (
        <p className="hint">{levels.map((l) => `${LEVEL_TEXT[l]} ${items.filter((i) => i.level === l).length} 项`).join(" · ")}</p>
      ) : <p className="empty">没有异常：持仓符合规则，规则之间没有矛盾，行情来源一致。</p>}
      <PriceCheckLine checks={r.price_check ?? []} />
      {levels.map((l) => (
        <div key={l} className={"anom " + l}>
          <h3>{LEVEL_TEXT[l]}</h3>
          <ul>
            {items.filter((i) => i.level === l).map((i, k) => (
              <li key={k}>
                <span className="tag">{i.group}</span> <b>{i.title}</b>
                <div className="hint">{i.detail}</div>
                {i.basis ? <details><summary className="hint">依据</summary><span className="hint">{i.basis}</span></details> : null}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function toForm(p: HoldingsPolicy): Record<string, string> {
  const pct = (v: number | undefined) => String(Math.round((v ?? 0) * 1000) / 10);
  return {
    targets: Object.entries(p.targets ?? {}).map(([s, w]) => `${s}:${pct(w)}`).join(", "),
    band: pct(p.band), cash_reserve: pct(p.cash_reserve),
    uncapped: (p.uncapped ?? []).join(", "), min_trade_usd: String(p.min_trade_usd ?? 50), whole_shares: p.whole_shares === false ? "0" : "1",
    alert_drawdown_from_cost: pct(p.alert_drawdown_from_cost), alert_drawdown_from_high: pct(p.alert_drawdown_from_high),
    take_profit: p.take_profit === false ? "0" : "1",
    tp_gain_1: pct(p.tp_gain_1), tp_sell_1: pct(p.tp_sell_1), tp_gain_2: pct(p.tp_gain_2), tp_sell_2: pct(p.tp_sell_2),
  };
}

function PolicyCard({ investable }: { investable?: number }) {
  const pol = useQuery({ queryKey: ["hpolicy"], queryFn: () => api<HoldingsPolicy>("/api/holdings/policy") });
  if (!pol.data) return <div className="card"><h2>规则设置</h2><ErrorBox error={pol.error} /><p className="empty">正在读取…</p></div>;
  return <PolicyForm key={JSON.stringify(pol.data)} initial={pol.data} investable={investable} />;
}

export function parseTargets(text: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const part of text.split(/[,，\s]+/).filter(Boolean)) {
    const [sym, w] = part.split(/[:：]/);
    if (sym && w && Number.isFinite(Number(w))) out[sym.toUpperCase()] = Number(w) / 100;
  }
  return out;
}

export function PolicyForm({ initial, investable }: { initial: HoldingsPolicy; investable?: number }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<Record<string, string>>(() => toForm(initial));
  const save = useMutation({
    mutationFn: (p: HoldingsPolicy) => api<HoldingsPolicy>("/api/holdings/policy", { method: "PUT", body: JSON.stringify(p) }),
    onSuccess: (p) => qc.setQueryData(["hpolicy"], p),
  });
  const set = (k: string) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const n = (k: string) => Number(form[k]);
  const pct = (k: string) => n(k) / 100;
  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate({
      targets: parseTargets(form.targets ?? ""), band: pct("band"), max_position: MAX_POSITION, cash_reserve: pct("cash_reserve"),
      uncapped: (form.uncapped ?? "").split(/[,，\s]+/).filter(Boolean).map((s) => s.toUpperCase()),
      min_trade_usd: n("min_trade_usd"), whole_shares: form.whole_shares === "1",
      alert_drawdown_from_cost: pct("alert_drawdown_from_cost"), alert_drawdown_from_high: pct("alert_drawdown_from_high"),
      take_profit: form.take_profit === "1", tp_gain_1: pct("tp_gain_1"), tp_sell_1: pct("tp_sell_1"), tp_gain_2: pct("tp_gain_2"), tp_sell_2: pct("tp_sell_2"),
    });
  }
  const inv = investable ?? 0;
  const parsed = parseTargets(form.targets ?? "");
  const input = (k: string, unit: string) => (
    <label className="inl"><input type="number" step="any" min={0} value={form[k] ?? ""} onChange={set(k)} /> <span>{unit}</span></label>
  );
  const row = (name: string, term: string, control: ReactNode, text: ReactNode) => (
    <div className="rule">
      <div className="rule-in"><div className="rule-name">{name}<small>{term}</small></div>{control}</div>
      <div className="rule-text">{text}</div>
    </div>
  );
  // the take-profit example: 10 shares bought at $100
  const s1 = Math.round(10 * pct("tp_sell_1")), s2 = Math.round((10 - s1) * pct("tp_sell_2"));
  const p1 = 100 * (1 + pct("tp_gain_1")), p2 = 100 * (1 + pct("tp_gain_2"));
  return (
    <div className="card">
      <h2>规则设置</h2>
      <p className="hint">占比都按“可投资资金”（所有股票，含 SGOV；国债和定存持有到期，不参与）算。账本不记现金，SGOV 就是你的现金。保存后点上面的“同步账本并检查”，异常和建议订单就按新规则重算。</p>
      <h3 className="rule-group">固定规则（不能改）</h3>
      <div className="rule">
        <div className="rule-in"><div className="rule-name">单只上限<small>position cap</small></div><b className="fixed">{pctPlain(MAX_POSITION, 0)}</b></div>
        <div className="rule-text">任何一只个股占可投资资金不超过 25%，超出的部分会提议卖出。宽基指数 ETF 例外（见下面的“不设上限的代码”）。{inv > 0 ? <> 按现在的资金 {money(inv)}，单只最多约 <b>{money(MAX_POSITION * inv)}</b>。</> : null}</div>
      </div>
      <form onSubmit={submit}>
        <h3 className="rule-group">止盈</h3>
        {row("止盈开关", "take profit", <select value={form.take_profit} onChange={set("take_profit")}><option value="1">开</option><option value="0">关</option></select>,
          <>分两档：现价涨到<b>平均成本</b>的某个幅度，就卖掉一部分股数，把利润落袋，剩下的继续拿着。只对个股，宽基 ETF 不止盈；每一档每次买入只提议一次，再买入这只股票后重新计。</>)}
        {row("第一档", "step 1", <div className="two">{input("tp_gain_1", "% 涨幅")}{input("tp_sell_1", "% 股数")}</div>,
          <>涨到成本的 +{n("tp_gain_1") || 0}% 时，卖掉当时股数的 {n("tp_sell_1") || 0}%。</>)}
        {row("第二档", "step 2", <div className="two">{input("tp_gain_2", "% 涨幅")}{input("tp_sell_2", "% 股数")}</div>,
          <>涨到成本的 +{n("tp_gain_2") || 0}% 时，再卖掉当时股数的 {n("tp_sell_2") || 0}%（必须比第一档高）。例：10 股、成本 $100，涨到 ${p1.toFixed(0)} 卖 {s1} 股，涨到 ${p2.toFixed(0)} 再卖 {s2} 股，剩 {10 - s1 - s2} 股继续持有。</>)}
        <p className="hint">为什么这样设计：不预测顶部，只在大幅盈利后分批落袋，和 25% 上限互补（上限管“太集中”，止盈管“赚了不落袋”）。我们测过的均线卖出规则没有一条胜过一直持有，所以没有用“跌破某条线就清仓”这类规则。分批止盈会在大涨的股票上少赚一些，换的是把一部分利润锁住；不想要就把开关关掉。</p>
        <h3 className="rule-group">仓位和 SGOV 储备</h3>
        {row("目标仓位", "target weight", <input className="wide" value={form.targets ?? ""} onChange={set("targets")} placeholder="例如 QQQM:40, VOO:30" />,
          <>可不填。格式“代码:百分比”，逗号隔开；不填的股票只受上限约束。{Object.keys(parsed).length ? <> 现在合计 {(Object.values(parsed).reduce((a, b) => a + b, 0) * 100).toFixed(0)}%{Object.entries(parsed).map(([s, w]) => `，${s} ≈ ${inv > 0 ? money(w * inv) : "—"}`)}。</> : null}</>)}
        {row("再平衡区间", "rebalancing band", input("band", "个百分点"), <>只对设了目标的股票：偏离目标超过这个距离才买卖回目标，免得为小偏差反复下单。</>)}
        {row("不设上限的代码", "uncapped", <input className="wide" value={form.uncapped ?? ""} onChange={set("uncapped")} placeholder="例如 VOO, QQQM" />,
          <>宽基指数 ETF，不受 25% 上限限制，也不止盈（逗号隔开）。个股、行业或杠杆 ETF 不要放进来。</>)}
        {row("SGOV 储备", "cash reserve", input("cash_reserve", "%"), <>SGOV（你的现金）至少占这么多，买入不会把 SGOV 用到这条线以下；买入要先卖出相应的 SGOV。{inv > 0 ? <> 现在至少留 <b>{money(pct("cash_reserve") * inv)}</b>。</> : null}</>)}
        {row("最小下单金额", "minimum trade", input("min_trade_usd", "美元"), <>算出来的调整不到这个金额就不提议。</>)}
        {row("按整股", "whole shares", <select value={form.whole_shares} onChange={set("whole_shares")}><option value="1">是</option><option value="0">否（碎股）</option></select>,
          <>买入向下取整，卖出取最接近规则的整数股。</>)}
        <h3 className="rule-group">提醒线（只提醒，不生成订单）</h3>
        {row("低于成本", "drawdown from cost", input("alert_drawdown_from_cost", "%"), <>现价比平均成本低这么多就提醒。成本 $100 的股票，跌到 <b>${(100 * (1 - pct("alert_drawdown_from_cost"))).toFixed(0)}</b> 以下。</>)}
        {row("离一年高点", "drawdown from 52-week high", input("alert_drawdown_from_high", "%"), <>现价比近一年最高点低这么多就提醒。高点 $100 的股票，跌到 <b>${(100 * (1 - pct("alert_drawdown_from_high"))).toFixed(0)}</b> 以下。</>)}
        <button type="submit" disabled={save.isPending}>保存规则</button>
      </form>
      <ErrorBox error={save.error} />
      {save.isSuccess ? <p className="hint">已保存。点上面的“同步账本并检查”后生效。</p> : null}
    </div>
  );
}
