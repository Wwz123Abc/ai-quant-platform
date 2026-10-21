import { useQuery } from "@tanstack/react-query";
import Markdown from "react-markdown";
import { Link, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";
import { type AiRun as AiRunT, api } from "../api/client";
import { ErrorBox, PageHead, Stat } from "../components/ui";
import { RATING, STATUS, yuan } from "./Ai";

export default function AiRun() {
  const id = useParams().id ?? "";
  const run = useQuery({
    queryKey: ["ai-run", id],
    queryFn: () => api<AiRunT>(`/api/ai/runs/${encodeURIComponent(id)}`),
    refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.status) ? 5_000 : false),
  });
  const r = run.data;
  if (run.error) return (<><PageHead title={`分析 #${id}`} /><ErrorBox error={run.error} /><Link to="/ai">返回 AI 研报</Link></>);
  if (!r) return <p className="empty">正在读取…</p>;
  const minutes = r.started && r.finished ? (new Date(r.finished).getTime() - new Date(r.started).getTime()) / 60_000 : null;
  const sections = Object.entries(r.reports ?? {}).filter(([, v]) => v.text.trim());

  return (
    <>
      <PageHead title={`${r.symbol} · ${r.trade_date} 收盘后的 AI 分析`}>
        <Link to="/ai">← AI 研报</Link>
      </PageHead>
      <div className="card">
        <div className="stats">
          <Stat k="评级" v={r.rating ? RATING[r.rating] ?? r.rating : STATUS[r.status]} s={r.rating ?? undefined} />
          <Stat k="费用" v={yuan(r.cost_cny)} s={`${r.llm_calls} 次模型调用`} />
          <Stat k="token" v={(r.tokens_in + r.tokens_out).toLocaleString()} s={`输入 ${r.tokens_in.toLocaleString()}（缓存 ${r.tokens_cached.toLocaleString()}）· 输出 ${r.tokens_out.toLocaleString()}`} />
          <Stat k="用时" v={minutes == null ? "—" : `${minutes.toFixed(1)} 分钟`} s={`${r.quick_model} / ${r.deep_model}`} />
        </div>
        {r.error ? <div className="error">{r.error}</div> : null}
      </div>
      {sections.length ? (
        <div className="card">
          {sections.map(([key, sec], i) => (
            <details key={key} className="report-section" open={i === sections.length - 1}>
              <summary>{sec.title}</summary>
              <div className="report">
                {/* Model output is untrusted: raw HTML is dropped and links are sanitized by react-markdown. */}
                <Markdown remarkPlugins={[remarkGfm]} skipHtml>{sec.text}</Markdown>
              </div>
            </details>
          ))}
        </div>
      ) : r.status === "done" ? <p className="empty">这次分析没有生成报告。</p> : <p className="empty">{STATUS[r.status]}…</p>}
      <p className="hint">报告由 AI 生成，可能有事实错误，只作研究参考，不构成投资建议。</p>
    </>
  );
}
