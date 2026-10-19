import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { type Account, type AccountCreate, api, post } from "../api/client";
import { ErrorBox, Field, PageHead, Signed } from "../components/ui";
import { money } from "../lib/format";

export default function Paper() {
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api<Account[]>("/api/paper/accounts") });
  return (
    <>
      <PageHead title="模拟盘">
        每个交易日收盘后，策略对每只股票记录一次“持有”或“空仓”，写入后不能修改；成交和资产都从这些决定推算，是真正的前向测试。
      </PageHead>
      <div className="card">
        <h2>模拟账户</h2>
        <ErrorBox error={accounts.error} />
        {accounts.isPending ? <p className="empty">正在读取…</p> : null}
        {accounts.data?.length === 0 ? <p className="empty">还没有模拟账户，在下面创建一个。</p> : null}
        {accounts.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>账户</th><th>股票</th><th>均线</th><th>开始（收盘）</th><th className="num">已运行</th><th className="num">资产</th><th className="num">收益</th><th className="num">同期买入持有</th></tr>
              </thead>
              <tbody>
                {accounts.data.map((a) => (
                  <tr key={a.name}>
                    <td><Link to={`/paper/${encodeURIComponent(a.name)}`}>{a.name}</Link></td>
                    <td>{a.symbols.join("、")}</td>
                    <td>{String(a.params.fast)}/{String(a.params.slow)}</td>
                    <td>{a.start}</td>
                    <td className="num">{a.days} 天</td>
                    <td className="num">{money(a.value)}</td>
                    <td className="num"><Signed v={a.return} /></td>
                    <td className="num"><Signed v={a.benchmark_return} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
      <CreateAccount />
    </>
  );
}

function CreateAccount() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [form, setForm] = useState({ name: "", symbols: "QQQM,VOO", fast: "50", slow: "200", cash: "10000", fee: "0", slippage: "5" });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const create = useMutation({
    mutationFn: (req: AccountCreate) => post<Account>("/api/paper/accounts", req),
    onSuccess: (a) => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      nav(`/paper/${encodeURIComponent(a.name)}`);
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate({
      name: form.name.trim(), symbols: form.symbols.split(/[,，\s]+/).filter(Boolean),
      fast: Number(form.fast), slow: Number(form.slow), cash: Number(form.cash), fee: Number(form.fee), slippage: Number(form.slippage),
    });
  }

  return (
    <div className="card">
      <h2>创建模拟账户</h2>
      <p className="hint">资金平均分给每只股票，每份资金只交易自己那只股票。第一批决定按最近的收盘价做出，下一个交易日开盘开始模拟成交。</p>
      <form className="params" onSubmit={submit}>
        <Field label="名称"><input value={form.name} onChange={set("name")} required maxLength={40} placeholder="例如 核心" /></Field>
        <Field label="股票代码（逗号分隔）" wide><input value={form.symbols} onChange={set("symbols")} required /></Field>
        <Field label="短均线（天）"><input type="number" min={2} value={form.fast} onChange={set("fast")} /></Field>
        <Field label="长均线（天）"><input type="number" min={3} value={form.slow} onChange={set("slow")} /></Field>
        <Field label="初始资金（$）"><input type="number" min={1} value={form.cash} onChange={set("cash")} /></Field>
        <Field label="每笔手续费（$）"><input type="number" min={0} step="any" value={form.fee} onChange={set("fee")} /></Field>
        <Field label="滑点（bp）"><input type="number" min={0} step="any" value={form.slippage} onChange={set("slippage")} /></Field>
        <button className="primary" type="submit" disabled={create.isPending}>{create.isPending ? "创建中…" : "创建"}</button>
      </form>
      <ErrorBox error={create.error} />
    </div>
  );
}
