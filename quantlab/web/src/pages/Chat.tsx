import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { type ChatAnswer, post } from "../api/client";
import { ErrorBox, PageHead } from "../components/ui";
import { yuan } from "./Ai";

type Turn = { role: "user" | "assistant"; content: string; steps?: ChatAnswer["steps"]; cost?: number };

const EXAMPLES = ["我现在的持仓和占比是什么？", "最近一次持仓检查有什么建议和提醒？依据是什么？", "VOO 现在的关键价位在哪里？", "QQQM 最近的 AI 评级怎么说？"];

export default function Chat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const ask = useMutation({
    mutationFn: (q: { message: string; before: Turn[] }) =>
      post<ChatAnswer>("/api/ai/chat", {
        message: q.message,
        history: q.before.slice(-8).map((t) => ({ role: t.role, content: t.content })),
      }),
    onSuccess: (a) => setTurns((t) => [...t, { role: "assistant", content: a.answer, steps: a.steps, cost: a.cost_cny }]),
  });

  function send(q: string) {
    const content = q.trim();
    if (!content || ask.isPending) return;
    setTurns([...turns, { role: "user", content }]);
    setText("");
    ask.mutate({ message: content, before: turns });
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    send(text);
  }

  return (
    <>
      <PageHead title="问一问">
        <button onClick={() => { setTurns([]); ask.reset(); }} disabled={!turns.length || ask.isPending}>清空对话</button>
      </PageHead>
      <p className="hint">用大白话问你的持仓、最近一次检查、某只股票或 AI 研报。助手只能读这个平台里的数据，不能下单，也不能确认或拒绝建议；它查了什么都会列在回答下面，方便你核对。每次提问的费用计入本月 AI 预算。</p>
      <div className="card">
        {turns.length ? turns.map((t, i) => (
          <div key={i} style={{ marginBottom: 14 }}>
            <b>{t.role === "user" ? "你" : "助手"}</b>
            <div className="report">
              {/* Model output is untrusted: raw HTML is dropped and links are sanitized by react-markdown. */}
              {t.role === "user" ? <p>{t.content}</p> : <Markdown remarkPlugins={[remarkGfm]} skipHtml>{t.content}</Markdown>}
            </div>
            {t.steps?.length ? (
              <details>
                <summary className="hint">查了 {t.steps.length} 项数据{t.cost != null ? ` · 费用 ${yuan(t.cost)}` : ""}</summary>
                {t.steps.map((s, j) => (
                  <p key={j} className="hint" style={{ wordBreak: "break-all" }}>
                    <b>{s.tool}</b>({JSON.stringify(s.args)})：{s.result}
                  </p>
                ))}
              </details>
            ) : t.cost != null ? <span className="hint">费用 {yuan(t.cost)}</span> : null}
          </div>
        )) : (
          <div>
            <p className="empty">还没有提问。可以试试：</p>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {EXAMPLES.map((e) => <button key={e} onClick={() => send(e)} disabled={ask.isPending}>{e}</button>)}
            </div>
          </div>
        )}
        {ask.isPending ? <p className="hint">助手正在查数据，通常几秒到半分钟……</p> : null}
        {ask.error ? <ErrorBox error={ask.error} /> : null}
      </div>
      <form className="card" onSubmit={submit} style={{ display: "flex", gap: 8 }}>
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} placeholder="输入问题，回车发送" aria-label="问题"
          style={{ flex: 1 }} disabled={ask.isPending} />
        <button type="submit" disabled={ask.isPending || !text.trim()}>发送</button>
      </form>
      <p className="hint">回答由 AI 生成，可能有错，只作研究参考，不构成投资建议。</p>
    </>
  );
}

