import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { type Account, api, type HoldingsReview, type SignalItem } from "../api/client";
import { ErrorBox, PageHead, Signed, Stat } from "../components/ui";
import { money, pct, when } from "../lib/format";

const DEFAULT_WATCH = ["QQQM", "VOO"];

export default function Dashboard() {
  const review = useQuery({ queryKey: ["hreview"], queryFn: () => api<HoldingsReview>("/api/holdings/review"), retry: false });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api<Account[]>("/api/paper/accounts") });
  const symbols = [...new Set(accounts.data?.flatMap((a) => a.symbols) ?? [])];
  const watch = symbols.length ? symbols : DEFAULT_WATCH;
  const signals = useQuery({
    queryKey: ["signals", watch.join(",")],
    queryFn: () => api<SignalItem[]>(`/api/signals?symbols=${encodeURIComponent(watch.join(","))}`),
    enabled: !accounts.isPending,
  });

  return (
    <>
      <PageHead title="总览">今天要关注的事：按你的规则检查持仓的结果，以及模拟账户和均线信号。系统不会替你下单。</PageHead>

      <TodayCard review={review.data} pending={review.isPending} />

      <h2>模拟账户</h2>
      <ErrorBox error={accounts.error} />
      {accounts.data?.length === 0 ? (
        <div className="card"><p className="empty">还没有模拟账户。<Link to="/paper">去创建一个</Link></p></div>
      ) : null}
      <div className="grid-3">
        {accounts.data?.map((a) => (
          <Link key={a.name} to={`/paper/${encodeURIComponent(a.name)}`} className="card" style={{ textDecoration: "none", color: "inherit" }}>
            <h2 style={{ marginBottom: 8 }}>{a.name}</h2>
            <div className="stats">
              <Stat k="资产" v={money(a.value)} s={`${a.symbols.join("、")} · ${a.days} 个交易日`} />
              <Stat k="收益" v={<Signed v={a.return} />} s={`买入持有 ${pct(a.benchmark_return)}`} />
            </div>
          </Link>
        ))}
      </div>

      <div className="card">
        <h2>最新信号（均线 50/200）</h2>
        <ErrorBox error={signals.error} />
        {signals.isPending ? <p className="empty">正在读取行情…</p> : null}
        {signals.data ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>代码</th><th>收盘日</th><th className="num">收盘价</th><th className="num">50 日线</th><th className="num">200 日线</th><th className="num">两线差</th><th>状态</th><th>自从</th></tr>
              </thead>
              <tbody>
                {signals.data.map((s) =>
                  s.ok ? (
                    <tr key={s.symbol}>
                      <td>{s.symbol}</td>
                      <td>{s.date}</td>
                      <td className="num">{s.close?.toFixed(2)}</td>
                      <td className="num">{s.fast?.toFixed(2)}</td>
                      <td className="num">{s.slow?.toFixed(2)}</td>
                      <td className="num"><Signed v={s.gap} /></td>
                      <td>
                        {s.hold ? "持有" : "空仓"}
                        {s.flipped ? <b>（今天刚{s.hold ? "金叉" : "死叉"}）</b> : null}
                      </td>
                      <td className="hint">{s.since_is_cross ? (s.hold ? "金叉于 " : "死叉于 ") : ""}{s.since}（{s.days} 个交易日）</td>
                    </tr>
                  ) : (
                    <tr key={s.symbol}><td>{s.symbol}</td><td colSpan={7} className="down">{s.error}</td></tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
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
        检查 #{review.id} · {when(review.at)}（北京时间）· <Link to="/holdings">查看持仓和规则 →</Link>
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
