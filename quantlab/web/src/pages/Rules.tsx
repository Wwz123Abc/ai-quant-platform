import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { api, type HoldingsPolicy, type HoldingsReview } from "../api/client";
import { ErrorBox, PageHead, Stat } from "../components/ui";
import { money, pctPlain } from "../lib/format";

const pp = (v: number) => `${v >= 0 ? "+" : "−"}${(Math.abs(v) * 100).toFixed(1)} 个百分点`;
const ok = (good: boolean | null) => (good == null ? "—" : good ? "✓ 符合" : "✗ 不符合");

export default function Rules() {
  const review = useQuery({ queryKey: ["hreview"], queryFn: () => api<HoldingsReview>("/api/holdings/review"), retry: false });
  const r = review.data;
  return (
    <>
      <PageHead title="持仓规则">
        你的持仓规则放在这一页：每条规则是什么意思、你现在的持仓符不符合，以及按规则算出的详细调仓计划。只有这些规则会生成建议订单；
        趋势、基本面、AI 评级只做提醒。占比都按“可投资资金”（股票 + 现金）计算，国债和定存持有到期，不参与。
        这里的计划只是计划，系统不会替你下单；去<Link to="/holdings">我的持仓</Link>逐条确认。
      </PageHead>
      {r ? <Status r={r} /> : review.isPending ? <p className="empty">正在读取…</p> : <p className="empty">还没有检查过持仓：先到“我的持仓”点“同步账本并检查”，这里才有对照和计划。</p>}
      <PolicyCard />
    </>
  );
}

export function Status({ r }: { r: HoldingsReview }) {
  const v = r.valuation, pol = r.policy;
  const inv = v.investable;
  const uncapped = new Set(pol.uncapped ?? []);
  const targets = pol.targets ?? {};
  const capped = v.positions.filter((p) => !uncapped.has(p.symbol) && p.weight != null);
  const biggest = capped.slice().sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0))[0];
  const drifted = Object.entries(targets).filter(([s, t]) => Math.abs((v.positions.find((p) => p.symbol === s)?.weight ?? 0) - t) > pol.band + 1e-9);
  const lossy = v.positions.filter((p) => p.gain != null && p.gain <= -pol.alert_drawdown_from_cost);
  const farFromHigh = r.reminders.filter((m) => m.kind === "回撤").map((m) => m.symbol);
  const hasTargets = Object.keys(targets).length > 0;
  const rows: { name: string; term: string; set: string; now: string; state: string; text: string }[] = [
    {
      name: "现金储备", term: "cash reserve", set: `不低于 ${pctPlain(pol.cash_reserve, 0)}`,
      now: pctPlain(v.cash_weight), state: ok(v.cash_weight == null ? null : v.cash_weight >= pol.cash_reserve - 1e-9),
      text: "手里至少留这么多现金，买入不会把现金花到这条线以下；现金不够时买入会按比例缩减。",
    },
    {
      name: "单只上限", term: "position cap", set: `不超过 ${pctPlain(pol.max_position, 0)}`,
      now: biggest ? `最大 ${biggest.symbol} ${pctPlain(biggest.weight)}` : "—",
      state: ok(biggest ? (biggest.weight ?? 0) <= pol.max_position + 1e-9 : null),
      text: "任何一只个股占可投资资金不超过这个比例，超过的部分会提议卖出，避免把钱押在一只股票上。",
    },
    {
      name: "不设上限的代码", term: "uncapped", set: (pol.uncapped ?? []).join("、") || "无", now: "—", state: "—",
      text: "宽基指数 ETF 本身已分散在几百家公司，不受单只上限约束（你 2026-10-23 的选择）。",
    },
    {
      name: "目标仓位与再平衡区间", term: "target weight / rebalancing band",
      set: hasTargets ? `${Object.entries(targets).map(([s, t]) => `${s} ${pctPlain(t, 0)}`).join("、")}；区间 ±${pctPlain(pol.band, 0)}` : `没设目标；区间 ±${pctPlain(pol.band, 0)}`,
      now: hasTargets ? (drifted.length ? `${drifted.map(([s]) => s).join("、")} 偏离超过区间` : "都在区间内") : "—",
      state: hasTargets ? ok(drifted.length === 0) : "—",
      text: "给某只股票定目标占比，偏离目标超过区间才调整（买入或卖出到目标），避免天天小调整、白付手续费。没设目标的只受上限约束。",
    },
    { name: "最小下单金额", term: "min trade", set: money(pol.min_trade_usd), now: "—", state: "—", text: "算出来的调整不到这个金额就不提议，免得为了几美元下单。" },
    { name: "按整股", term: "whole shares", set: pol.whole_shares ? "是" : "否（碎股）", now: "—", state: "—", text: "多数券商不能买碎股，建议订单按整股取整：买入向下取整保证现金够，卖出取最接近规则的整数股。" },
    {
      name: "低于成本提醒", term: "drawdown from cost", set: `比平均成本低 ${pctPlain(pol.alert_drawdown_from_cost, 0)}`,
      now: lossy.length ? lossy.map((p) => `${p.symbol} ${pctPlain(p.gain, 0)}`).join("、") : "没有触发", state: ok(lossy.length === 0),
      text: "持仓亏到平均成本以下这么多时提醒你复查，只是提醒，不生成订单。",
    },
    {
      name: "离一年高点提醒", term: "drawdown from 52-week high", set: `比一年最高价低 ${pctPlain(pol.alert_drawdown_from_high, 0)}`,
      now: farFromHigh.length ? farFromHigh.join("、") : "没有触发", state: ok(farFromHigh.length === 0),
      text: "股价从近一年最高点回落到这个幅度时提醒（最大回撤的一种看法），只是提醒，不生成订单。",
    },
  ];

  const orders = r.proposals.filter((o) => o.status !== "rejected" && o.status !== "superseded");
  const syms = [...new Set([...v.positions.map((p) => p.symbol), ...Object.keys(targets)])].sort();
  const plan = syms.map((s) => {
    const p = v.positions.find((x) => x.symbol === s);
    const value = p?.value ?? 0;
    const o = orders.find((x) => x.symbol === s);
    const after = value + (o ? (o.side === "buy" ? o.value : -o.value) : 0);
    const t = targets[s];
    const cap = uncapped.has(s) ? null : pol.max_position;
    const weight = inv > 0 ? value / inv : 0;
    let state = "无需调整";
    if (cap != null && weight > cap + 1e-9) state = `超上限 ${pp(weight - cap)}`;
    else if (t != null && Math.abs(weight - t) > pol.band + 1e-9) state = `${weight > t ? "高于" : "低于"}目标 ${pp(weight - t)}，超出区间`;
    else if (t != null) state = `偏离目标 ${pp(weight - t)}，在区间内`;
    return { s, value, weight, t, cap, state, o, afterW: inv > 0 ? after / inv : 0 };
  });
  const sells = orders.filter((o) => o.side === "sell"), buys = orders.filter((o) => o.side === "buy");
  const sellTotal = sells.reduce((a, o) => a + o.value, 0), buyTotal = buys.reduce((a, o) => a + o.value, 0);
  const cashAfter = v.cash + sellTotal - buyTotal;
  const floor = pol.cash_reserve * inv;
  const step = [...sells, ...buys];
  const compared = rows.filter((x) => x.state !== "—");

  return (
    <>
      <div className="card">
        <div className="stats">
          <Stat k="可投资资金" v={money(inv)} s={`股票 ${money(v.stocks)} + 现金 ${money(v.cash)}`} />
          <Stat k="符合规则" v={`${compared.filter((x) => x.state.startsWith("✓")).length} / ${compared.length} 条`} s="只数能对照的规则" />
          <Stat k="需要调整" v={step.length ? `${step.length} 笔` : "不需要"} s={`依据检查 #${r.id}${r.price_date ? `，${r.price_date} 收盘价` : ""}`} />
        </div>
      </div>
      <div className="card">
        <h2>规则说明和现状对照</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>规则</th><th>你的设定</th><th>现在</th><th>对照</th></tr></thead>
            <tbody>
              {rows.map((x) => (
                <tr key={x.name}>
                  <td style={{ whiteSpace: "normal" }}><b>{x.name}</b><div className="hint">{x.term}</div></td>
                  <td style={{ whiteSpace: "normal" }}>{x.set}</td>
                  <td style={{ whiteSpace: "normal" }}>{x.now}</td>
                  <td className={x.state.startsWith("✗") ? "down" : undefined}>{x.state}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rows.map((x) => <p key={x.name} className="hint">· <b>{x.name}</b>：{x.text}</p>)}
      </div>
      <div className="card">
        <h2>详细调仓计划</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>代码</th><th className="num">现值</th><th className="num">现占比</th><th className="num">目标</th><th className="num">上限</th><th>判断</th><th>建议</th><th className="num">调整后占比</th></tr></thead>
            <tbody>
              {plan.map((x) => (
                <tr key={x.s}>
                  <td>{x.s}</td><td className="num">{money(x.value)}</td><td className="num">{pctPlain(x.weight)}</td>
                  <td className="num">{x.t != null ? pctPlain(x.t, 0) : "—"}</td><td className="num">{x.cap != null ? pctPlain(x.cap, 0) : "不设"}</td>
                  <td style={{ whiteSpace: "normal" }}>{x.state}</td>
                  <td className={x.o ? (x.o.side === "buy" ? "up" : "down") : undefined}>{x.o ? `${x.o.side === "buy" ? "买入" : "卖出"} ${x.o.shares} 股（约 ${money(x.o.value)}）` : "不动"}</td>
                  <td className="num">{pctPlain(x.afterW)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {step.length ? (
          <>
            <h3 style={{ marginTop: 14 }}>执行顺序（先卖后买）</h3>
            <ol style={{ margin: 0, paddingLeft: 20 }}>
              {step.map((o) => <li key={o.id}>{o.side === "buy" ? "买入" : "卖出"} {o.symbol} {o.shares} 股，约 {money(o.value)}（按 {o.price.toFixed(2)}）：{o.reason}</li>)}
            </ol>
          </>
        ) : <p className="empty">现在不需要调整：持仓符合你的规则。</p>}
        <p style={{ marginTop: 12 }}>
          现金：现在 {money(v.cash)}，卖出回笼 {money(sellTotal)}，买入花 {money(buyTotal)}，调整后 <b>{money(cashAfter)}</b>（占 {pctPlain(inv > 0 ? cashAfter / inv : null)}），
          储备下限 {money(floor)}：<span className={cashAfter >= floor - 1 ? undefined : "down"}>{cashAfter >= floor - 1 ? "✓ 不低于储备" : "✗ 低于储备"}</span>。
        </p>
        {r.notes.map((n, i) => <p key={i} className="hint">· {n}</p>)}
        <p className="hint">计划来自最近一次检查，改规则后先点“同步账本并检查”再回来看。建议订单要到“我的持仓”确认或拒绝；确认后自己去券商下单，成交后在账本记一笔。</p>
      </div>
    </>
  );
}

function toForm(p: HoldingsPolicy): Record<string, string> {
  const pct = (v: number | undefined) => String(Math.round((v ?? 0) * 1000) / 10);
  return {
    targets: Object.entries(p.targets ?? {}).map(([s, w]) => `${s}:${pct(w)}`).join(", "),
    band: pct(p.band), max_position: pct(p.max_position), cash_reserve: pct(p.cash_reserve),
    uncapped: (p.uncapped ?? []).join(", "), min_trade_usd: String(p.min_trade_usd ?? 50), whole_shares: p.whole_shares === false ? "0" : "1",
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
      uncapped: (form.uncapped ?? "").split(/[,，\s]+/).filter(Boolean).map((s) => s.toUpperCase()),
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
        <div className="field wide"><label>不设上限的代码（宽基 ETF，逗号分隔）<input value={form.uncapped ?? ""} onChange={set("uncapped")} placeholder="例如 VOO, QQQM" /></label></div>
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
