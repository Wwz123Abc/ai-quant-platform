import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";
import { api, type Limits, type MlSignal, post, type Study, type StudyRequest } from "../api/client";
import Bars from "../components/Bars";
import TimeChart, { type Line } from "../components/TimeChart";
import { ErrorBox, Field, PageHead } from "../components/ui";
import { drawdown, money, num, pct, pctPlain } from "../lib/format";

const COLORS: Record<string, string> = { model: "--series-1", momentum: "--series-2", equal: "--series-3", qqq: "--series-4" };
const fmtMoney = (v: number) => money(v);
const fmtPct = (v: number) => pct(v);

export default function Ml() {
  const [form, setForm] = useState({ start: "2012-01-01", top: "10", maxWeight: "0.2" });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const study = useMutation({ mutationFn: (req: StudyRequest) => post<Study>("/api/ml/backtests", req) });

  function submit(e: FormEvent) {
    e.preventDefault();
    study.mutate({ start: form.start, end: null, top: Number(form.top), max_weight: Number(form.maxWeight), cash: 10000, slippage: 5, fee: 0 });
  }

  return (
    <>
      <PageHead title="机器学习选股">
        纳斯达克 100 成分股，用 12 个价格和成交量特征训练 LightGBM，预测未来一个月谁跑得更好；每月调仓，持有排名靠前的股票，按波动率倒数分配仓位。
        每次预测都只用当时已经知道结果的数据训练（滚动训练），并和动量、等权、QQQ 三个基准用同样的规则和成本比较。
      </PageHead>
      <SignalCard />
      <div className="card">
        <h2>回测</h2>
        <form className="params" onSubmit={submit}>
          <Field label="开始日期"><input type="date" value={form.start} onChange={set("start")} /></Field>
          <Field label="持有几只"><input type="number" min={3} max={30} value={form.top} onChange={set("top")} /></Field>
          <Field label="单只上限"><input type="number" min={0.05} max={1} step={0.05} value={form.maxWeight} onChange={set("maxWeight")} /></Field>
          <button className="primary" type="submit" disabled={study.isPending}>{study.isPending ? "训练和回测中，约一分钟…" : "开始回测"}</button>
        </form>
        <ErrorBox error={study.error} />
      </div>
      {study.data ? <Result s={study.data} /> : null}
    </>
  );
}

function SignalCard() {
  const qc = useQueryClient();
  const latest = useQuery({ queryKey: ["ml-signal"], queryFn: () => api<MlSignal>("/api/ml/signal"), retry: false });
  const limits = useQuery({ queryKey: ["limits"], queryFn: () => api<Limits>("/api/trading/limits") });
  const compute = useMutation({
    mutationFn: () => post<MlSignal>("/api/ml/signal", {}),
    onSuccess: (s) => qc.setQueryData(["ml-signal"], s),
  });
  const sig = compute.data ?? latest.data;
  const capital = limits.data?.capital ?? 500;
  return (
    <div className="card">
      <div className="chart-head">
        <h2 style={{ margin: 0 }}>最新目标组合{sig ? `（${sig.signal_date} 收盘后）` : ""}</h2>
        <button onClick={() => compute.mutate()} disabled={compute.isPending}>{compute.isPending ? "计算中，约一分钟…" : "按最新收盘价重新计算"}</button>
      </div>
      <ErrorBox error={compute.error} />
      {sig ? (
        <>
          <p className="hint">模型用 {sig.trained_to} 为止已知结果的 {sig.samples.toLocaleString()} 个样本训练。金额按交易资金 {money(capital)} 计算。</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>代码</th><th className="num">权重</th><th className="num">金额</th><th className="num">预测分</th></tr></thead>
              <tbody>
                {sig.holdings.map((h) => (
                  <tr key={h.symbol}>
                    <td>{h.symbol}</td>
                    <td className="num">{pctPlain(h.weight)}</td>
                    <td className="num">{money(h.weight * capital)}</td>
                    <td className="num">{num(h.score, 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">这是模型的计算结果，不构成投资建议。自动交易账户会在每月最后一个交易日收盘后按它调仓。</p>
        </>
      ) : latest.isPending ? <p className="empty">正在读取…</p> : <p className="empty">还没有算过，点右上角的按钮计算。</p>}
    </div>
  );
}

function Result({ s }: { s: Study }) {
  const eq: Line[] = useMemo(() => {
    const names = Object.fromEntries(s.strategies.map((x) => [x.key, x.name]));
    return Object.keys(s.equity).map((k) => ({ name: names[k] ?? k, color: COLORS[k] ?? "--series-1", values: s.equity[k] ?? [] }));
  }, [s]);
  const dd: Line[] = useMemo(() => eq.map((l) => ({ ...l, values: drawdown(l.values.map((v) => v ?? 0)) })), [eq]);
  const dates = useMemo(() => s.dates, [s]);
  return (
    <>
      <div className="notice">
        股票池是<b>现在</b>的纳斯达克 100 成分股（名单截至 {s.universe_as_of}），等于事先知道哪些公司后来成功了（幸存者偏差），所有策略的历史收益都偏高。
        请主要和“股票池等权”比较：两者带着同样的偏差。
      </div>
      <div className="card">
        <h2>{s.start} → {s.end} · {s.size} 只股票 · 模型重新训练 {s.fits} 次</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>策略</th><th className="num">年化</th><th className="num">波动率</th><th className="num">夏普</th><th className="num">最大回撤</th><th className="num">月均换手</th><th className="num">交易成本</th></tr></thead>
            <tbody>
              {s.strategies.map((x) => (
                <tr key={x.key}>
                  <td><span className="legend"><span className="sw" style={{ background: `var(${COLORS[x.key]})` }} /></span>{x.name}</td>
                  <td className="num">{pct(x.summary.cagr)}</td>
                  <td className="num">{pctPlain(x.summary.vol)}</td>
                  <td className="num">{num(x.summary.sharpe)}</td>
                  <td className="num">{pct(x.summary.mdd)}</td>
                  <td className="num">{pctPlain(x.turnover, 0)}</td>
                  <td className="num">{money(x.costs)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="card"><TimeChart label="账户价值（对数坐标）" dates={dates} lines={eq} format={fmtMoney} log size="tall" /></div>
      <div className="card"><TimeChart label="回撤" dates={dates} lines={dd} format={fmtPct} size="short" /></div>
      <div className="grid-3">
        <div className="card">
          <h2>预测能力（IC）</h2>
          <p className="hint">
            每月：预测排名和之后 21 个交易日实际收益排名的相关系数。平均 {num(s.ic_mean, 3)}，为正的月份 {pctPlain(s.ic_positive, 0)}（共 {s.ic_months} 个月）。
            一般认为平均 0.02–0.05 是弱信号。
          </p>
          <Bars label="每年平均 IC" kind="signed" format={(v) => num(v, 3)} items={Object.entries(s.ic_by_year).map(([y, v]) => ({ label: y, value: v }))} />
        </div>
        <div className="card">
          <h2>按预测分 10 组</h2>
          <p className="hint">每组之后 21 个交易日的平均收益。第 10 组是模型最看好的股票；如果从左到右逐组升高，说明排序有效。</p>
          <Bars label="分组收益" format={(v) => pct(v, 2)} items={Object.entries(s.deciles).map(([k, v]) => ({ label: `第 ${k} 组`, value: v }))} />
        </div>
        <div className="card">
          <h2>特征重要性</h2>
          <p className="hint">最终模型里每个特征贡献的增益占比。</p>
          <Bars label="特征重要性" format={(v) => pctPlain(v, 0)} items={Object.entries(s.importance).map(([k, v]) => ({ label: s.feature_names[k] ?? k, value: v }))} />
        </div>
      </div>
      <p className="hint">回测是历史表现，不代表未来；机器学习模型在实盘中通常比回测差。</p>
    </>
  );
}
