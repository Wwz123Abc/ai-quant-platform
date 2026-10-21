import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { type AiRun, type AiStatus, api, post } from "../api/client";
import { ErrorBox, Field, PageHead, Stat } from "../components/ui";

export const STATUS: Record<AiRun["status"], string> = { queued: "排队中", running: "运行中", done: "完成", failed: "失败" };
export const RATING: Record<string, string> = {
  Buy: "买入", Overweight: "增持", Hold: "持有", Underweight: "减持", Sell: "卖出", REVIEW: "无法判断",
};
export const yuan = (v: number) => `¥${v.toFixed(2)}`;

export default function Ai() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["ai-status"], queryFn: () => api<AiStatus>("/api/ai/status"), refetchInterval: 15_000 });
  const busy = (status.data?.budget.queued ?? 0) + (status.data?.budget.running ?? 0) > 0;
  const runs = useQuery({
    queryKey: ["ai-runs"],
    queryFn: () => api<AiRun[]>("/api/ai/runs?limit=100"),
    refetchInterval: busy ? 5_000 : false, // poll only while something is queued or running
  });
  const [symbol, setSymbol] = useState("QQQM");
  const create = useMutation({
    mutationFn: (sym: string) => post<AiRun>("/api/ai/runs", { symbol: sym }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ai-runs"] });
      qc.invalidateQueries({ queryKey: ["ai-status"] });
    },
  });
  const daily = useMutation({
    mutationFn: () => post<{ status: string }>("/api/schedule/run", {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ai-status"] }),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(symbol.trim());
  }

  const st = status.data;
  const b = st?.budget;
  const ready = st?.available && st.configured;
  const used = b ? Math.min(1, b.committed_cny / b.budget_cny) : 0;

  return (
    <>
      <PageHead title="AI 研报">
        用 TradingAgents 的多智能体（技术、情绪、新闻、基本面分析师 → 多空辩论 → 交易员 → 风控与投资组合经理）分析一只股票，给出 5 档评级和完整报告。
        只分析最近一个收盘日，不回溯历史，避免模型“偷看未来”。
      </PageHead>
      <ErrorBox error={status.error} />
      {st && !st.available ? (
        <div className="notice">没有安装 AI 组件。在 quant-lab 文件夹里运行 <code>pip install -e ".[ai]"</code>，然后重启服务。</div>
      ) : null}
      {st && st.available && !st.configured ? (
        <div className="notice">
          还没有配置大模型密钥。在 quant-lab 文件夹的 <code>.env</code> 文件里加一行 <code>DEEPSEEK_API_KEY=你的密钥</code>，然后重启服务。
        </div>
      ) : null}

      {b ? (
        <div className="card">
          <h2>{b.month} 月预算</h2>
          <div className="stats">
            <Stat k="已用" v={yuan(b.spent_cny)} s={`预算 ${yuan(b.budget_cny)}`} />
            <Stat k="排队中预计" v={yuan(b.committed_cny - b.spent_cny)} s={`${b.queued} 个排队，${b.running} 个运行中`} />
            <Stat k="剩余可用" v={yuan(Math.max(0, b.remaining_cny))} />
            <Stat k="每次分析预计" v={yuan(b.estimate_cny)} s={`模型 ${st.quick_model} / ${st.deep_model}`} />
          </div>
          <div className={"meter" + (used >= 1 ? " over" : "")} role="meter" aria-valuemin={0} aria-valuemax={b.budget_cny}
            aria-valuenow={b.committed_cny} aria-label="本月预算使用情况">
            <span style={{ width: `${used * 100}%` }} />
          </div>
          <p className="hint">按高峰时段价格计算（非高峰时段实际只要一半），汇率 {b.usd_cny}。超过预算的分析不会被提交，也不会被运行。</p>
        </div>
      ) : null}

      <div className="grid-2">
        <div className="card">
          <h2>新建分析</h2>
          <form className="params" onSubmit={submit}>
            <Field label="代码"><input value={symbol} onChange={(e) => setSymbol(e.target.value)} required maxLength={10} /></Field>
            <button className="primary" type="submit" disabled={!ready || create.isPending}>{create.isPending ? "提交中…" : "开始分析"}</button>
          </form>
          <p className="hint">一次分析通常需要几分钟，会产生模型费用。同一只股票、同一个收盘日只分析一次。</p>
          <ErrorBox error={create.error} />
        </div>
        <div className="card">
          <h2>每日任务</h2>
          {st ? (
            <p>
              {st.schedule.enabled ? `每周二至周六北京时间 ${st.schedule.time} 后自动运行` : "自动运行已关闭"}
              {st.schedule.last_run ? `，上次 ${st.schedule.last_run}` : "，还没有运行过"}。
              先为 AI 模拟账户分析到期的股票，再给所有模拟账户记录当天的决定。
            </p>
          ) : null}
          <button onClick={() => daily.mutate()} disabled={daily.isPending || st?.schedule.running}>
            {st?.schedule.running ? "运行中…" : "立即运行一次"}
          </button>
          <ErrorBox error={daily.error} />
        </div>
      </div>

      <div className="card">
        <h2>分析记录</h2>
        <ErrorBox error={runs.error} />
        {runs.data?.length === 0 ? <p className="empty">还没有分析记录。</p> : null}
        {runs.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>编号</th><th>代码</th><th>收盘日</th><th>来源</th><th>状态</th><th>评级</th><th className="num">模型调用</th><th className="num">token（输入/输出）</th><th className="num">费用</th></tr>
              </thead>
              <tbody>
                {runs.data.map((r) => (
                  <tr key={r.id}>
                    <td><Link to={`/ai/runs/${r.id}`}>#{r.id}</Link></td>
                    <td>{r.symbol}</td>
                    <td>{r.trade_date}</td>
                    <td className="hint">{{ manual: "手动", schedule: "每日任务", cli: "命令行" }[r.source] ?? r.source}</td>
                    <td className={r.status === "failed" ? "down" : undefined} title={r.error ?? undefined}>{STATUS[r.status]}</td>
                    <td>{r.rating ? RATING[r.rating] ?? r.rating : "—"}</td>
                    <td className="num">{r.llm_calls}</td>
                    <td className="num">{r.tokens_in.toLocaleString()} / {r.tokens_out.toLocaleString()}</td>
                    <td className="num">{yuan(r.cost_cny)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <p className="hint">报告由 AI 生成，可能有事实错误，只作研究参考，不构成投资建议。</p>
      </div>
    </>
  );
}
