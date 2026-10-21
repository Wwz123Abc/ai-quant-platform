import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { post, type ScanRequest, type ScanResponse, type ScanRow } from "../api/client";
import Heatmap from "../components/Heatmap";
import { ErrorBox, Field, PageHead, Signed } from "../components/ui";
import { num, parseInts, pct } from "../lib/format";

export default function Scan() {
  const [form, setForm] = useState({
    symbol: "QQQ", start: "2005-01-01", split: "2019-01-01", by: "sharpe" as "sharpe" | "cagr",
    fasts: "5,10,15,20,30,40,50,75,100", slows: "50,75,100,125,150,175,200,250,300",
  });
  const run = useMutation({ mutationFn: (req: ScanRequest) => post<ScanResponse>("/api/scans", req) });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  function submit(e: FormEvent) {
    e.preventDefault();
    run.mutate({
      symbol: form.symbol.trim(), fasts: parseInts(form.fasts), slows: parseInts(form.slows), by: form.by,
      start: form.start || null, split: form.split || null, end: null, cash: 10000, fee: 0, slippage: 5, rf: 0, refresh: false,
    });
  }

  return (
    <>
      <PageHead title="参数扫描与样本外检验">
        在“样本外开始日”之前的数据上给所有均线组合排名（挑参数），再看它们在之后的数据上表现如何（检验）。两段排名不相关，就说明挑出来的“最优参数”是过拟合。
      </PageHead>
      <div className="card">
        <form className="params" onSubmit={submit}>
          <Field label="代码"><input value={form.symbol} onChange={set("symbol")} required maxLength={10} /></Field>
          <Field label="开始日期"><input type="date" value={form.start} onChange={set("start")} /></Field>
          <Field label="样本外开始日"><input type="date" value={form.split} onChange={set("split")} /></Field>
          <Field label="排名依据">
            <select value={form.by} onChange={set("by")}>
              <option value="sharpe">夏普比率</option>
              <option value="cagr">年化收益</option>
            </select>
          </Field>
          <Field label="短均线（逗号分隔）" wide><input value={form.fasts} onChange={set("fasts")} /></Field>
          <Field label="长均线（逗号分隔）" wide><input value={form.slows} onChange={set("slows")} /></Field>
          <button className="primary" type="submit" disabled={run.isPending}>{run.isPending ? "扫描中…" : "开始扫描"}</button>
        </form>
        <ErrorBox error={run.error} />
      </div>
      {run.data ? <Result r={run.data} /> : null}
    </>
  );
}

function Result({ r }: { r: ScanResponse }) {
  const by = r.by as "sharpe" | "cagr";
  const name = by === "sharpe" ? "夏普比率" : "年化收益";
  const key = (side: "is" | "oos") => `${side}_${by}` as const;
  const val = (side: "is" | "oos") => (row: ScanRow) => row[key(side)] as number | null;
  const fmt = by === "sharpe" ? (v: number) => num(v) : (v: number) => pct(v, 0);
  const best = r.rows[0]!;
  const all = r.rows.flatMap((x) => [val("is")(x), val("oos")(x)]).filter((v): v is number => v != null);
  const domain: [number, number] = [Math.min(...all), Math.max(...all)];
  const n = r.rows.length;
  const benchOut = r.benchmark_out[by] ?? null;
  const bestOut = val("oos")(best);
  const rho = r.rank_corr;
  const strength = rho == null ? "无法计算" : Math.abs(rho) < 0.3 ? "很弱" : Math.abs(rho) < 0.7 ? "中等" : "较强";
  const period = (p: ScanResponse["in_sample"]) => `${p.start} → ${p.end}（${p.years.toFixed(1)} 年）`;

  return (
    <>
      <div className="card">
        <h2>结论</h2>
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          <li>
            样本内第 1 名 <b>{best.fast}/{best.slow}</b> 到了样本外：{name} {bestOut == null ? "—" : fmt(bestOut)}，在 {n} 组里排第 <b>{best.oos_rank}</b>；
            所有组合样本外的中位数是 {r.median_out == null ? "—" : fmt(r.median_out)}。
          </li>
          <li>
            同期买入持有的{name}是 {benchOut == null ? "—" : fmt(benchOut)}，样本内第 1 名
            <b>{bestOut != null && benchOut != null && bestOut > benchOut ? "跑赢了" : "没有跑赢"}</b>它。
          </li>
          <li>
            样本内和样本外排名的相关系数（Spearman）是 <b>{num(rho)}</b>，相关性{strength}。接近 1 说明过去的排名对未来有参考价值；接近 0 或为负，说明“最优参数”主要是在拟合噪声。
          </li>
        </ul>
      </div>
      <div className="grid-2">
        <div className="card">
          <Heatmap rows={r.rows} value={val("is")} rank={(x) => x.is_rank} format={fmt} domain={domain} best={best}
            title={`样本内（挑参数）· ${name}`} subtitle={period(r.in_sample)} />
        </div>
        <div className="card">
          <Heatmap rows={r.rows} value={val("oos")} rank={(x) => x.oos_rank} format={fmt} domain={domain} best={best}
            title={`样本外（检验）· ${name}`} subtitle={period(r.out_sample)} />
        </div>
      </div>
      <p className="hint" style={{ marginTop: -8, marginBottom: 16 }}>颜色越深数值越高，两张图用同一个色阶；黑框是样本内第 1 名。鼠标停在格子上可以看排名。</p>
      <div className="card">
        <h2>样本内前 10 名</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>样本内排名</th><th>参数</th><th className="num">样本内夏普</th><th className="num">样本内年化</th><th className="num">样本外夏普</th><th className="num">样本外年化</th><th className="num">样本外排名</th></tr>
            </thead>
            <tbody>
              {r.rows.slice(0, 10).map((x) => (
                <tr key={`${x.fast}/${x.slow}`}>
                  <td>{x.is_rank}</td>
                  <td>{x.fast}/{x.slow}</td>
                  <td className="num">{num(x.is_sharpe)}</td>
                  <td className="num"><Signed v={x.is_cagr} /></td>
                  <td className="num">{num(x.oos_sharpe)}</td>
                  <td className="num"><Signed v={x.oos_cagr} /></td>
                  <td className="num">{x.oos_rank} / {n}</td>
                </tr>
              ))}
              <tr className="ref">
                <td>—</td>
                <td>买入持有</td>
                <td className="num">{num(r.benchmark_in.sharpe)}</td>
                <td className="num"><Signed v={r.benchmark_in.cagr} /></td>
                <td className="num">{num(r.benchmark_out.sharpe)}</td>
                <td className="num"><Signed v={r.benchmark_out.cagr} /></td>
                <td className="num">—</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
