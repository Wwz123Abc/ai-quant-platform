import { useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, ApiError, BASE, type Health, setToken } from "./api/client";
import Account from "./pages/Account";
import Analyze from "./pages/Analyze";
import Holdings from "./pages/Holdings";
import Ai from "./pages/Ai";
import AiRun from "./pages/AiRun";
import Ml from "./pages/Ml";
import Trading, { TradingAccountPage } from "./pages/Trading";
import Backtest from "./pages/Backtest";
import Dashboard from "./pages/Dashboard";
import Paper from "./pages/Paper";
import Scan from "./pages/Scan";

export default function App() {
  // A cheap authenticated call decides between the workbench and the token prompt.
  const auth = useQuery({
    queryKey: ["session"],
    queryFn: () => api<Health>("/api/session"),
    retry: false,
  });
  if (auth.isPending) return <p className="empty">正在连接…</p>;
  if (auth.error instanceof ApiError && auth.error.status === 401) {
    // under the ledger site a 401 means the ledger session ended; nginx sends page loads back to its sign-in
    if (BASE) return <p className="empty">登录已过期，请<a href="/">回到美股账本</a>重新登录。</p>;
    return <TokenGate onDone={() => auth.refetch()} />;
  }

  return (
    <div className="shell">
      <nav className="nav" aria-label="主导航">
        <div className="brand">QuantLab<small>量化研究工作站</small></div>
        <NavLink to="/" end>总览</NavLink>
        <NavLink to="/holdings">我的持仓</NavLink>
        <NavLink to="/analyze">个股分析</NavLink>
        <NavLink to="/backtest">均线回测</NavLink>
        <NavLink to="/scan">参数扫描</NavLink>
        <NavLink to="/paper">模拟盘</NavLink>
        <NavLink to="/ai">AI 研报</NavLink>
        <NavLink to="/ml">机器学习选股</NavLink>
        <NavLink to="/trading">自动交易</NavLink>
        <a href={`${BASE}/api/docs`} target="_blank" rel="noreferrer">接口文档 ↗</a>
        {auth.data?.home_url ? <a href={auth.data.home_url}>← 美股账本</a> : null}
        <div className="foot">v{auth.data?.version} · 仅用于研究，不构成投资建议</div>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/holdings" element={<Holdings />} />
          <Route path="/analyze" element={<Analyze />} />
          <Route path="/backtest" element={<Backtest />} />
          <Route path="/scan" element={<Scan />} />
          <Route path="/paper" element={<Paper />} />
          <Route path="/paper/:name" element={<Account />} />
          <Route path="/ai" element={<Ai />} />
          <Route path="/ai/runs/:id" element={<AiRun />} />
          <Route path="/ml" element={<Ml />} />
          <Route path="/trading" element={<Trading />} />
          <Route path="/trading/:name" element={<TradingAccountPage />} />
          <Route path="*" element={<p className="empty">没有这个页面。<NavLink to="/">回到总览</NavLink></p>} />
        </Routes>
      </main>
    </div>
  );
}

function TokenGate({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    setToken(value);
    onDone();
  }
  return (
    <main style={{ maxWidth: 520, margin: "10vh auto" }}>
      <div className="card">
        <h1>需要访问令牌</h1>
        <p>请用启动服务时终端里显示的链接打开（链接里带有 <code>?token=</code>），或者把令牌粘贴到下面。</p>
        <p className="hint">令牌保存在 data/api_token 文件里，也可以用环境变量 QUANTLAB_API_TOKEN 指定。</p>
        <form className="params" onSubmit={submit}>
          <div className="field wide">
            <label>
              访问令牌
              <input type="password" value={value} onChange={(e) => setValue(e.target.value)} required autoComplete="off" />
            </label>
          </div>
          <button className="primary" type="submit">进入</button>
        </form>
      </div>
    </main>
  );
}
