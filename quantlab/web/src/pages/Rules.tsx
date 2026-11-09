import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useState } from "react";
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
      <PolicyCard investable={r?.valuation.investable} />
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

function PolicyCard({ investable }: { investable?: number }) {
  const pol = useQuery({ queryKey: ["hpolicy"], queryFn: () => api<HoldingsPolicy>("/api/holdings/policy") });
  if (!pol.data) return <div className="card"><h2>你的规则</h2><ErrorBox error={pol.error} /><p className="empty">正在读取…</p></div>;
  return <PolicyForm key={JSON.stringify(pol.data)} initial={pol.data} investable={investable} />;
}

function parseTargets(text: string): Record<string, number> {
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
  function submit(e: FormEvent) {
    e.preventDefault();
    const targets = parseTargets(form.targets ?? "");
    const pct = (k: string) => Number(form[k]) / 100;
    save.mutate({
      targets, band: pct("band"), max_position: pct("max_position"), cash_reserve: pct("cash_reserve"),
      uncapped: (form.uncapped ?? "").split(/[,，\s]+/).filter(Boolean).map((s) => s.toUpperCase()),
      min_trade_usd: Number(form.min_trade_usd), whole_shares: form.whole_shares === "1",
      alert_drawdown_from_cost: pct("alert_drawdown_from_cost"), alert_drawdown_from_high: pct("alert_drawdown_from_high"),
    });
  }
  const num = (k: string) => Number(form[k]);
  const inv = investable ?? 0;
  const usd = (x: number) => (inv > 0 ? money(x) : "—");
  const parsed = parseTargets(form.targets ?? "");
  const tsum = Object.values(parsed).reduce((x, y) => x + y, 0);
  const cap = num("max_position") / 100, reserve = num("cash_reserve") / 100, band = num("band") / 100;
  const uncappedSet = new Set((form.uncapped ?? "").split(/[,，\s]+/).filter(Boolean).map((x) => x.toUpperCase()));
  const overCap = Object.entries(parsed).filter(([sym, w]) => !uncappedSet.has(sym) && w > cap + 1e-9);
  const field = (k: string, unit: string) => (
    <label className="inl"><input type="number" step="any" min={0} value={form[k] ?? ""} onChange={set(k)} /> <span>{unit}</span></label>
  );
  const rule = (name: string, term: string, control: ReactNode, what: string, now: ReactNode, tip: string) => (
    <div className="rule">
      <div className="rule-in"><div className="rule-name">{name}<small>{term}</small></div>{control}</div>
      <div className="rule-text">
        <p>{what}</p>
        <p className="hint"><b>按你现在的情况：</b>{now}</p>
        <p className="hint"><b>怎么定：</b>{tip}</p>
      </div>
    </div>
  );
  return (
    <div className="card">
      <h2>修改规则</h2>
      <p className="hint">占比都按“可投资资金”（股票 + 现金；国债和定存持有到期，不参与）计算。只有这些规则会生成建议订单，其余只是提醒。保存后点“我的持仓”的“同步账本并检查”生效。</p>
      <form onSubmit={submit}>
        <h3 className="rule-group">一、买什么、买多少</h3>
        {rule("目标仓位", "target weight",
          <input className="wide" value={form.targets ?? ""} onChange={set("targets")} placeholder="例如 QQQM:40, VOO:30" />,
          "给想长期持有的股票定一个占可投资资金的目标比例，格式“代码:百分比”，用逗号隔开。可以不填：不填的股票只受下面的“单只上限”约束，不会被要求买到某个比例。",
          Object.keys(parsed).length ? <>目标合计 <b>{(tsum * 100).toFixed(0)}%</b>，现金储备 {(reserve * 100).toFixed(0)}%，剩下 {Math.max(0, (1 - tsum - reserve) * 100).toFixed(0)}% 没有指定，可以放在其他股票或现金上。{Object.entries(parsed).map(([sym, w]) => ` ${sym} 目标 ${(w * 100).toFixed(0)}% ≈ ${usd(w * inv)}。`)}{tsum + reserve > 1 + 1e-9 ? <span className="down"> ✗ 目标合计加现金储备超过 100%，保存会被拒绝。</span> : null}{overCap.length ? <span className="down"> ✗ {overCap.map(([sym]) => sym).join("、")} 的目标高于单只上限，保存会被拒绝（宽基 ETF 请先加到下面的“不设上限”）。</span> : null}</> : "还没有设目标：现在只按上限和现金储备检查。",
          "目标合计不要超过“100% − 现金储备”。核心宽基 ETF 可以定高一点，个股定低一点；定了目标，偏离超过“再平衡区间”才会提议买卖。")}
        {rule("单只上限", "position cap", field("max_position", "%"),
          "任何一只个股占可投资资金的比例不能超过这个数；超出的部分会提议卖出。目的是不把钱押在一家公司上。",
          <>上限 {(cap * 100).toFixed(0)}%：现在资金 {usd(inv)}，单只最多约 <b>{usd(cap * inv)}</b>。</>,
          "常见做法是 10%–30%：越看重分散越低。范围 2%–100%。")}
        <div className="rule">
          <div className="rule-in"><div className="rule-name">不设上限的代码<small>uncapped</small></div>
            <input className="wide" value={form.uncapped ?? ""} onChange={set("uncapped")} placeholder="例如 VOO, QQQM" /></div>
          <div className="rule-text">
            <p>宽基指数 ETF 本身就分散在几百家公司里，不适用“单只上限”。写在这里的代码不受上限限制（逗号隔开）。</p>
            <p className="hint"><b>按你现在的情况：</b>{uncappedSet.size} 个代码不设上限。<b>注意：</b>行业 ETF、杠杆 ETF 或个股不要放进来，它们并不分散。</p>
          </div>
        </div>
        {rule("再平衡区间", "rebalancing band", field("band", "个百分点"),
          "只有设了目标的股票才用到：占比偏离目标超过这个距离，才提议买或卖回目标；没超过就不动，免得天天为小偏差下单、白付手续费。",
          <>目标 40% 的股票，占比低于 <b>{((0.4 - band) * 100).toFixed(0)}%</b> 或高于 <b>{((0.4 + band) * 100).toFixed(0)}%</b> 才会调整。</>,
          "常见 3–5 个百分点：越小越勤、费用越多；越大越懒、偏离越久。范围 1–50。")}
        <h3 className="rule-group">二、现金和下单</h3>
        {rule("现金储备", "cash reserve", field("cash_reserve", "%"),
          "现金占可投资资金至少要留这么多。买入不会把现金花到这条线以下，现金不够时买入会按比例缩减；现金已经低于它时会提醒。",
          <>储备 {(reserve * 100).toFixed(0)}%：现在资金 {usd(inv)}，至少留 <b>{usd(reserve * inv)}</b> 现金，其余 {usd((1 - reserve) * inv)} 才能用来买。</>,
          "留着应急或等机会。5%–20% 常见；越大越稳，但闲置的钱越多。")}
        {rule("最小下单金额", "minimum trade", field("min_trade_usd", "美元"),
          "算出来的调整不到这个金额就不提议，免得为了几美元的偏差下单。",
          <>不到 <b>${(num("min_trade_usd") || 0).toFixed(0)}</b>{inv > 0 ? `（约占资金 ${((num("min_trade_usd") / inv) * 100).toFixed(1)}%）` : ""} 的调整会被忽略，并在“说明”里写出原因。</>,
          "定成手续费的几倍以上比较划算：金额太小，手续费占比就高。")}
        <div className="rule">
          <div className="rule-in"><div className="rule-name">按整股<small>whole shares</small></div>
            <select value={form.whole_shares ?? "1"} onChange={set("whole_shares")}><option value="1">是</option><option value="0">否（碎股）</option></select></div>
          <div className="rule-text">
            <p>多数券商不能买碎股。选“是”，建议订单按整数股取整：买入向下取整，保证现金够；卖出取最接近规则的整数股，不会为了几美元多卖一整股。</p>
            <p className="hint"><b>怎么定：</b>按整股下单的券商保持“是”。股价很高（几百美元一股）时，单次调整可能不到 1 股，这种会写明“不到半股，暂不建议”。</p>
          </div>
        </div>
        <h3 className="rule-group">三、提醒线（只提醒，不生成订单）</h3>
        {rule("低于成本提醒", "drawdown from cost", field("alert_drawdown_from_cost", "%"),
          "持仓现价比平均成本低这么多时，提醒你复查这只股票的逻辑还在不在。平均成本按账本的移动平均法算。",
          <>平均成本 $100 的股票，现价跌到 <b>${(100 * (1 - num("alert_drawdown_from_cost") / 100)).toFixed(0)}</b> 以下就提醒。</>,
          "只是提醒，不会自动卖。比较常见的是 15%–25%。")}
        {rule("离一年高点提醒", "drawdown from 52-week high", field("alert_drawdown_from_high", "%"),
          "股价比近一年（约 252 个交易日）的最高点低这么多时提醒，说明这一年里回撤已经很深。",
          <>一年最高 $100 的股票，现价跌到 <b>${(100 * (1 - num("alert_drawdown_from_high") / 100)).toFixed(0)}</b> 以下就提醒。</>,
          "和上一条不同：它看的是高点，不管你什么价买的。20%–30% 常见。")}
        <button type="submit" disabled={save.isPending}>保存规则</button>
      </form>
      <ErrorBox error={save.error} />
      {save.isSuccess ? <p className="hint">已保存。到“我的持仓”点“同步账本并检查”后，上面的对照和调仓计划会按新规则重算。</p> : null}
    </div>
  );
}
