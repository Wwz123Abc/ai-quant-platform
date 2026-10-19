import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { type Account as AccountT, api, post, type RecordResult } from "../api/client";
import TimeChart, { type Line } from "../components/TimeChart";
import { ErrorBox, PageHead, Signed, Stat } from "../components/ui";
import { money } from "../lib/format";
import { describe } from "./Paper";

const fmtMoney = (v: number) => money(v);

export default function Account() {
  const name = useParams().name ?? "";
  const path = `/api/paper/accounts/${encodeURIComponent(name)}`;
  const qc = useQueryClient();
  const nav = useNavigate();
  const acct = useQuery({ queryKey: ["account", name], queryFn: () => api<AccountT>(path) });
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState(false);

  const record = useMutation({
    mutationFn: () => post<RecordResult>(path + "/record", {}),
    onSuccess: (r) => {
      qc.setQueryData(["account", name], r.account);
      qc.invalidateQueries({ queryKey: ["accounts"] });
      setNote(r.recorded ? `记录了 ${r.recorded} 条新的决定（到 ${r.latest}）。` : "没有新的收盘数据，决定已是最新。");
    },
  });
  const remove = useMutation({
    mutationFn: () => api<void>(path, { method: "DELETE" }),
    onSuccess: () => {
      qc.removeQueries({ queryKey: ["account", name] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
      nav("/paper");
    },
  });

  const a = acct.data;
  const lines: Line[] = useMemo(() => (a?.equity ? [
    { name: "策略", color: "--series-1", values: a.equity.strategy },
    { name: "买入持有", color: "--series-2", values: a.equity.benchmark },
  ] : []), [a]);

  if (acct.error) return (<><PageHead title={name} /><ErrorBox error={acct.error} /><Link to="/paper">返回模拟盘</Link></>);
  if (!a) return <p className="empty">正在读取…</p>;

  return (
    <>
      <PageHead title={`模拟账户 · ${a.name}`}>
        {a.symbols.join("、")} · {describe(a)} · {a.start} 收盘时开始 · 初始资金 {money(a.cash)}（每只 {money(a.cash / a.symbols.length)}）
      </PageHead>
      <div className="card">
        <div className="stats">
          <Stat k="当前资产" v={money(a.value)} s={`已运行 ${a.days} 个交易日`} />
          <Stat k="收益" v={<Signed v={a.return} />} />
          <Stat k="同期买入持有" v={<Signed v={a.benchmark_return} />} />
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap", alignItems: "center" }}>
          <button className="primary" onClick={() => record.mutate()} disabled={record.isPending}>
            {record.isPending ? "记录中…" : "记录最新决定"}
          </button>
          {confirming ? (
            <>
              <button className="danger" onClick={() => remove.mutate()} disabled={remove.isPending}>确认删除（不能恢复）</button>
              <button onClick={() => setConfirming(false)}>取消</button>
            </>
          ) : (
            <button onClick={() => setConfirming(true)}>删除账户</button>
          )}
          <span className="hint" aria-live="polite">{note}</span>
        </div>
        <ErrorBox error={record.error ?? remove.error} />
      </div>

      {a.pending.length ? (
        <div className="notice">
          <b>待执行</b>
          {a.pending.map((o) => (
            <div key={o.symbol}>
              {o.symbol}：下一个交易日开盘{o.side === "buy" ? "买入" : "卖出"}（{o.decision_date} 收盘后的决定）
              {o.affordable ? "" : "，但这一份资金不够买 1 股，不会成交"}
            </div>
          ))}
        </div>
      ) : null}

      <div className="card">
        {a.days && a.equity ? (
          <TimeChart label="账户价值" dates={a.equity.dates} lines={lines} format={fmtMoney} />
        ) : (
          <><h2>账户价值</h2><p className="empty">还没有成交日：下一个交易日开盘后开始模拟。</p></>
        )}
      </div>

      <div className="card">
        <h2>持仓</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>代码</th><th>最新决定</th><th>当前</th><th className="num">股数</th><th className="num">资产</th><th className="num">收益</th><th className="num">同期买入持有</th></tr>
            </thead>
            <tbody>
              {a.positions.map((q) => (
                <tr key={q.symbol}>
                  <td>{q.symbol}</td>
                  <td>{q.decision ? "持有" : "空仓"}（{q.decision_date}）</td>
                  <td>{q.held ? "持有" : "空仓"}</td>
                  <td className="num">{q.shares}</td>
                  <td className="num">{money(q.value)}</td>
                  <td className="num"><Signed v={q.return} /></td>
                  <td className="num"><Signed v={q.benchmark_return} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h2>成交记录（价格为复权价）</h2>
        {a.fills.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>日期</th><th>方向</th><th>代码</th><th className="num">股数</th><th className="num">价格</th></tr></thead>
              <tbody>
                {a.fills.map((f) => (
                  <tr key={`${f.date}-${f.symbol}-${f.side}`}>
                    <td>{f.date}</td>
                    <td className={f.side === "buy" ? "up" : "down"}>{f.side === "buy" ? "买入" : "卖出"}</td>
                    <td>{f.symbol}</td>
                    <td className="num">{f.shares}</td>
                    <td className="num">{f.price.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">还没有成交。</p>
        )}
      </div>
    </>
  );
}
