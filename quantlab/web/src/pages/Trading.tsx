import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Limits, post, type TradingAccount } from "../api/client";
import TimeChart, { type Line } from "../components/TimeChart";
import { ErrorBox, Field, PageHead, Signed, Stat } from "../components/ui";
import { money, pctPlain } from "../lib/format";

const MODE: Record<string, string> = { plan: "只出计划", paper: "模拟券商", live: "实盘" };
const STATUS: Record<string, string> = { pending: "待成交", filled: "已成交", rejected: "已拒绝", planned: "计划" };
const fmtMoney = (v: number) => money(v, 2);

function LimitsCard({ l }: { l: Limits }) {
  return (
    <div className="card">
      <h2>风控上限（每笔订单都检查，在 .env 里修改）</h2>
      <div className="stats">
        <Stat k="资金上限" v={money(l.capital)} s="程序最多动用的钱" />
        <Stat k="单只上限" v={pctPlain(l.max_weight, 0)} s={`持有前 ${l.top_n} 只`} />
        <Stat k="单笔 / 单日" v={`${money(l.max_order_usd)} / ${money(l.max_daily_usd)}`} s={`每天最多 ${l.max_orders_per_day} 笔`} />
        <Stat k="回撤熔断" v={pctPlain(l.max_drawdown, 0)} s={`行情超过 ${l.max_data_age_days} 天不交易`} />
        <Stat k="实盘" v={l.live_enabled ? "已允许" : "未开启"} s={`需要券商连接器，且模拟运行满 ${l.paper_weeks_before_live} 周`} />
      </div>
    </div>
  );
}

export default function Trading() {
  const qc = useQueryClient();
  const accounts = useQuery({ queryKey: ["trading"], queryFn: () => api<TradingAccount[]>("/api/trading/accounts") });
  const limits = useQuery({ queryKey: ["limits"], queryFn: () => api<Limits>("/api/trading/limits") });
  const [form, setForm] = useState({ name: "", mode: "paper", capital: "" });
  const create = useMutation({
    mutationFn: () => post<TradingAccount>("/api/trading/accounts", { name: form.name.trim(), mode: form.mode, capital: form.capital ? Number(form.capital) : null }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trading"] }),
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }
  return (
    <>
      <PageHead title="自动交易">
        按机器学习组合每月调仓：月末收盘后算出目标组合，生成订单，逐笔风控检查，第二天开盘成交。目前接的是内置模拟券商，不涉及真钱；
        实盘需要支持接口的券商账户，并且要先在模拟盘跑满规定的周数。
      </PageHead>
      {limits.data ? <LimitsCard l={limits.data} /> : null}
      <div className="card">
        <h2>交易账户</h2>
        <ErrorBox error={accounts.error} />
        {accounts.data?.length === 0 ? <p className="empty">还没有交易账户，在下面创建一个。</p> : null}
        {accounts.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>账户</th><th>模式</th><th className="num">净值</th><th className="num">现金</th><th className="num">距最高点</th><th className="num">持仓</th><th>状态</th></tr></thead>
              <tbody>
                {accounts.data.map((a) => (
                  <tr key={a.name}>
                    <td><Link to={`/trading/${encodeURIComponent(a.name)}`}>{a.name}</Link></td>
                    <td>{MODE[a.mode] ?? a.mode}</td>
                    <td className="num">{money(a.value, 2)}</td>
                    <td className="num">{money(a.cash, 2)}</td>
                    <td className="num"><Signed v={a.drawdown} /></td>
                    <td className="num">{a.positions.length} 只</td>
                    <td className={a.halted ? "down" : undefined}>{a.halted ? `已暂停：${a.halted}` : "运行中"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
      <div className="card">
        <h2>创建交易账户</h2>
        <form className="params" onSubmit={submit}>
          <Field label="名称"><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required maxLength={40} /></Field>
          <Field label="模式">
            <select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}>
              <option value="paper">模拟券商</option>
              <option value="plan">只出计划（不发送订单）</option>
            </select>
          </Field>
          <Field label="资金（$，默认取上限）"><input type="number" min={1} value={form.capital} onChange={(e) => setForm({ ...form, capital: e.target.value })} /></Field>
          <button className="primary" type="submit" disabled={create.isPending}>创建</button>
        </form>
        <ErrorBox error={create.error} />
      </div>
    </>
  );
}

export function TradingAccountPage() {
  const name = useParams().name ?? "";
  const path = `/api/trading/accounts/${encodeURIComponent(name)}`;
  const qc = useQueryClient();
  const acct = useQuery({ queryKey: ["trading", name], queryFn: () => api<TradingAccount>(path), refetchInterval: 15_000 });
  const limits = useQuery({ queryKey: ["limits"], queryFn: () => api<Limits>("/api/trading/limits"), refetchInterval: 5_000 });
  const [confirm, setConfirm] = useState(false);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["trading"] });
    qc.invalidateQueries({ queryKey: ["limits"] });
  };
  const act = useMutation({ mutationFn: (p: { url: string; body?: unknown }) => post<unknown>(p.url, p.body ?? {}), onSuccess: refresh });
  const a = acct.data;
  const line: Line[] = useMemo(() => (a?.equity ? [{ name: "净值", color: "--series-1", values: (a.equity.values as (number | null)[]) }] : []), [a]);
  if (acct.error) return (<><PageHead title={name} /><ErrorBox error={acct.error} /><Link to="/trading">返回自动交易</Link></>);
  if (!a) return <p className="empty">正在读取…</p>;
  const busy = limits.data?.busy;
  const dates = (a.equity?.dates as string[] | undefined) ?? [];

  return (
    <>
      <PageHead title={`交易账户 · ${a.name}`}>
        <Link to="/trading">← 自动交易</Link>
      </PageHead>
      {a.halted ? <div className="error" role="alert">已暂停：{a.halted}。暂停期间不会生成或发送新订单。</div> : null}
      <div className="card">
        <div className="stats">
          <Stat k="净值" v={money(a.value, 2)} s={`资金上限 ${money(a.capital)}`} />
          <Stat k="现金" v={money(a.cash, 2)} />
          <Stat k="距最高点" v={<Signed v={a.drawdown} />} s={`最高 ${money(a.peak_equity, 2)}`} />
          <Stat k="模式" v={MODE[a.mode] ?? a.mode} s={a.broker === "paper" ? "内置模拟券商" : a.broker} />
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 16, alignItems: "center" }}>
          {confirm ? (
            <>
              <button className="primary" disabled={busy || act.isPending} onClick={() => { setConfirm(false); act.mutate({ url: "/api/trading/run", body: { force: true } }); }}>
                确认：按最新收盘价立刻调仓
              </button>
              <button onClick={() => setConfirm(false)}>取消</button>
            </>
          ) : (
            <button disabled={busy || !!a.halted} onClick={() => setConfirm(true)}>立刻调仓</button>
          )}
          <button disabled={busy} onClick={() => act.mutate({ url: "/api/trading/run", body: { force: false } })}>结算和估值</button>
          {a.halted ? (
            <button onClick={() => act.mutate({ url: `${path}/resume` })}>恢复交易</button>
          ) : (
            <button className="danger" onClick={() => act.mutate({ url: `${path}/halt`, body: { reason: "手动暂停" } })}>暂停交易</button>
          )}
          <select value={a.mode} aria-label="模式" onChange={(e) => act.mutate({ url: `${path}/mode`, body: { mode: e.target.value } })}>
            <option value="paper">模拟券商</option>
            <option value="plan">只出计划</option>
            <option value="live">实盘（需要满足条件）</option>
          </select>
          <span className="hint" aria-live="polite">{busy ? "交易步骤运行中，完成后自动刷新…" : ""}</span>
        </div>
        <ErrorBox error={act.error} />
      </div>

      <div className="card">
        {dates.length > 1 ? <TimeChart label="净值" dates={dates} lines={line} format={fmtMoney} size="short" /> : <><h2>净值</h2><p className="empty">至少要两天的记录才能画出曲线。</p></>}
      </div>

      <div className="grid-2">
        <div className="card">
          <h2>持仓（按最新收盘价）</h2>
          {a.positions.length ? (
            <div className="table-wrap">
              <table>
                <thead><tr><th>代码</th><th className="num">股数</th><th className="num">价格</th><th className="num">市值</th><th className="num">占比</th></tr></thead>
                <tbody>
                  {a.positions.map((p) => (
                    <tr key={p.symbol}>
                      <td>{p.symbol}</td><td className="num">{p.qty.toFixed(4)}</td><td className="num">{p.price?.toFixed(2) ?? "—"}</td>
                      <td className="num">{money(p.value, 2)}</td><td className="num">{pctPlain(p.weight)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="empty">空仓。</p>}
        </div>
        <div className="card">
          <h2>操作日志</h2>
          <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
            <table>
              <tbody>
                {a.events?.map((e, i) => (
                  <tr key={i}><td className="hint">{e.at.replace("T", " ").slice(0, 16)}</td><td style={{ whiteSpace: "normal" }}>{e.message}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>订单</h2>
        {a.orders?.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>信号日</th><th>代码</th><th>方向</th><th className="num">数量</th><th className="num">预估价</th><th>状态</th><th className="num">成交价</th><th>成交日</th><th>说明</th></tr></thead>
              <tbody>
                {a.orders.map((o) => (
                  <tr key={o.client_id}>
                    <td>{o.signal_date}</td><td>{o.symbol}</td>
                    <td className={o.side === "buy" ? "up" : "down"}>{o.side === "buy" ? "买入" : "卖出"}</td>
                    <td className="num">{o.qty.toFixed(4)}</td><td className="num">{o.est_price.toFixed(2)}</td>
                    <td className={o.status === "rejected" ? "down" : undefined}>{STATUS[o.status] ?? o.status}</td>
                    <td className="num">{o.fill_price?.toFixed(2) ?? "—"}</td><td>{o.filled_at ?? "—"}</td>
                    <td className="hint" style={{ whiteSpace: "normal" }}>{o.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="empty">还没有订单。</p>}
        <p className="hint">模拟成交价 = 下一个交易日开盘价 ± 滑点，股数为碎股。这里没有任何真实交易。</p>
      </div>
    </>
  );
}
