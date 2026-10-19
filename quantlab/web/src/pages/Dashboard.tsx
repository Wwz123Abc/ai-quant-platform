import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { type Account, api, type SignalItem } from "../api/client";
import { ErrorBox, PageHead, Signed, Stat } from "../components/ui";
import { money, pct } from "../lib/format";

const DEFAULT_WATCH = ["QQQM", "VOO"];

export default function Dashboard() {
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
      <PageHead title="总览">模拟账户的表现和最新的均线信号。信号只是策略的计算结果，是否下单由你自己决定。</PageHead>

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

      <div className="grid-2">
        <Link to="/backtest" className="card" style={{ textDecoration: "none", color: "inherit" }}>
          <h2>均线回测 →</h2>
          <p className="hint">用几十年的复权行情检验一条均线规则，并和买入持有比较。</p>
        </Link>
        <Link to="/scan" className="card" style={{ textDecoration: "none", color: "inherit" }}>
          <h2>参数扫描 →</h2>
          <p className="hint">挑出来的“最优参数”在之后的数据上还好用吗？用样本外检验判断是否过拟合。</p>
        </Link>
      </div>
    </>
  );
}
