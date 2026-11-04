import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type HoldingsReview } from "../api/client";
import { PageHead } from "../components/ui";
import { money, when } from "../lib/format";

export default function Dashboard() {
  const review = useQuery({ queryKey: ["hreview"], queryFn: () => api<HoldingsReview>("/api/holdings/review"), retry: false });

  return (
    <>
      <PageHead title="总览">今天要关注的事：按你的规则检查持仓的结果。系统不会替你下单。</PageHead>

      <TodayCard review={review.data} pending={review.isPending} />
    </>
  );
}

/** The latest holdings check first: orders waiting for you, then the reminders that need attention. */
function TodayCard({ review, pending }: { review: HoldingsReview | undefined; pending: boolean }) {
  if (!review) {
    return (
      <div className="card">
        <h2>持仓检查</h2>
        <p className="empty">{pending ? "正在读取…" : <>还没有检查过持仓。<Link to="/holdings">去检查</Link></>}</p>
      </div>
    );
  }
  const orders = review.proposals.filter((o) => o.status === "pending");
  const warns = review.reminders.filter((m) => m.level === "warn");
  const others = review.reminders.length - warns.length;
  return (
    <div className="card">
      <h2>持仓检查</h2>
      <p className="hint">
        检查 #{review.id} · {when(review.at)}（北京时间）{review.price_date ? ` · 按 ${review.price_date} 收盘价` : ""} · <Link to="/holdings">查看持仓和规则 →</Link>
      </p>
      {orders.length ? (
        <>
          <p><b>{orders.length} 条建议订单等你确认</b>（确认后自己去券商下单）：</p>
          <ul className="todo">
            {orders.map((o) => (
              <li key={o.id}>
                <span className={o.side === "buy" ? "up" : "down"}>{o.side === "buy" ? "买入" : "卖出"}</span> {o.symbol} {o.shares} 股，约 {money(o.value)}：{o.reason}
              </li>
            ))}
          </ul>
        </>
      ) : <p>✓ 没有建议订单：持仓符合你的规则。</p>}
      {review.notes.map((n, i) => <p key={i} className="hint">· {n}</p>)}
      {warns.length ? (
        <>
          <p style={{ marginTop: 12 }}><b>{warns.length} 条提醒需要留意</b>（只是信号，不生成订单）：</p>
          <ul className="todo">
            {warns.map((m, i) => <li key={i} className="warn-item">{m.symbol} [{m.kind}] {m.text}</li>)}
          </ul>
        </>
      ) : null}
      {others ? <p className="hint">另有 {others} 条一般提醒，见“我的持仓”。</p> : null}
    </div>
  );
}
