"""HTTP routes. Handlers validate input, call the domain layer and convert its results; no business logic here.

Handlers are plain functions, so FastAPI runs them in its thread pool: downloads and backtests do not block
other requests."""
import math

import pandas as pd
from fastapi import APIRouter, Depends, Query, Response

from .. import __version__
from ..core import data, paper, research
from ..errors import InvalidInput, QuantLabError
from . import schemas as s
from .security import require_token

public = APIRouter(prefix="/api", tags=["system"])
api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

MAX_SCAN_PAIRS = 200


def num(v):
    """A float for JSON: NaN and infinity become null."""
    v = float(v)
    return v if math.isfinite(v) else None


def nums(series):
    return [num(v) for v in series]


def summary(m):
    return {k: (num(v) if isinstance(v, float) else v) for k, v in m.items()}


def period(bars):
    return {"start": bars.index[0].date(), "end": bars.index[-1].date(), "years": (bars.index[-1] - bars.index[0]).days / 365.25}


def trades(df):
    return [{**r, "entry_date": r["entry_date"].date(), "exit_date": r["exit_date"].date()} for r in df.to_dict("records")]


def account(acct_summary, with_equity):
    a = dict(acct_summary)
    eq = a.pop("equity")
    a["equity"] = ({"dates": [d.date() for d in eq.index], "strategy": nums(eq["strategy"]), "benchmark": nums(eq["benchmark"])}
                   if with_equity else None)
    return a


@public.get("/health", response_model=s.Health)
def health():
    return {"status": "ok", "version": __version__}


@api.get("/market/{symbol}/bars", response_model=s.Bars, tags=["market"], summary="复权日线")
def bars(symbol: str, start: str | None = None, refresh: bool = False):
    sym = data.normalize(symbol)
    df = data.load(sym, refresh)
    if start:
        df = df.loc[start:]
    return {"symbol": sym, "dates": [d.date() for d in df.index], **{c: nums(df[c]) for c in ("open", "high", "low", "close", "volume")}}


@api.get("/signals", response_model=list[s.SignalItem], tags=["research"], summary="最新的均线信号")
def signals(symbols: str = Query(..., examples=["QQQM,VOO"]), fast: int = Query(50, ge=2, le=400),
            slow: int = Query(200, ge=3, le=500)):
    out = []
    for raw in [x for x in symbols.split(",") if x.strip()][:20]:
        try:
            sym = data.normalize(raw)
            out.append({"ok": True, **research.latest_signal(sym, data.load(sym), fast, slow)})
        except QuantLabError as e:  # one bad symbol does not fail the others
            out.append({"symbol": raw.strip().upper(), "ok": False, "error": str(e)})
    return out


@api.post("/backtests", response_model=s.BacktestResponse, tags=["research"], summary="均线交叉回测")
def run_backtest(req: s.BacktestRequest):
    sym = data.normalize(req.symbol)
    run = research.backtest_ma(sym, data.load(sym, req.refresh), req.fast, req.slow, req.start, req.end,
                               req.cash, req.fee, req.slippage, req.rf)
    idx = run.strategy.equity.index
    sig = run.signals.loc[idx]
    return {
        "symbol": sym, "fast": req.fast, "slow": req.slow,
        "summary": summary(run.summary), "benchmark": summary(run.benchmark_summary),
        "series": {"dates": [d.date() for d in idx], "close": nums(sig["close"]), "fast": nums(sig["fast"]),
                   "slow": nums(sig["slow"]), "equity": nums(run.strategy.equity), "benchmark": nums(run.benchmark.equity)},
        "trades": trades(run.strategy.trades),
    }


@api.post("/scans", response_model=s.ScanResponse, tags=["research"], summary="参数扫描与样本外检验")
def run_scan(req: s.ScanRequest):
    pairs = sum(1 for f in req.fasts for w in req.slows if f < w)
    if pairs > MAX_SCAN_PAIRS:
        raise InvalidInput("参数组合太多（%d 组），一次最多 %d 组" % (pairs, MAX_SCAN_PAIRS))
    sym = data.normalize(req.symbol)
    run = research.scan_ma(sym, data.load(sym, req.refresh), tuple(req.fasts), tuple(req.slows), req.split, req.start, req.end,
                           req.by, req.cash, req.fee, req.slippage, req.rf)
    cols = list(s.ScanRow.model_fields)
    rows = [{k: (num(v) if isinstance(v, float) else int(v)) for k, v in r.items()} for r in run.table[cols].to_dict("records")]
    return {
        "symbol": sym, "by": run.by, "in_sample": period(run.in_sample), "out_sample": period(run.out_sample), "rows": rows,
        "benchmark_in": summary(run.benchmark_in), "benchmark_out": summary(run.benchmark_out),
        "rank_corr": num(run.rank_corr), "median_out": num(run.median_out),
    }


def _with_paper(fn):
    con = paper.connect()
    try:
        return fn(con)
    finally:
        con.close()


@api.get("/paper/accounts", response_model=list[s.Account], tags=["paper"], summary="全部模拟账户")
def list_accounts():
    return _with_paper(lambda con: [account(paper.summarize(a, paper.replay(con, a)), False) for a in paper.accounts(con)])


@api.post("/paper/accounts", response_model=s.Account, status_code=201, tags=["paper"], summary="创建模拟账户")
def create_account(req: s.AccountCreate):
    if req.fast >= req.slow:
        raise InvalidInput("短均线天数要小于长均线天数")

    def go(con):
        paper.create(con, req.name, req.symbols, {"fast": req.fast, "slow": req.slow}, req.cash, req.fee, req.slippage)
        acct = paper.get_account(con, req.name)
        paper.record(con, acct)
        return account(paper.summarize(acct, paper.replay(con, acct)), True)
    return _with_paper(go)


@api.get("/paper/accounts/{name}", response_model=s.Account, tags=["paper"], summary="模拟账户详情")
def get_account(name: str):
    def go(con):
        acct = paper.get_account(con, name)
        return account(paper.summarize(acct, paper.replay(con, acct)), True)
    return _with_paper(go)


@api.post("/paper/accounts/{name}/record", response_model=s.RecordResult, tags=["paper"], summary="记录最新的决定")
def record_account(name: str):
    def go(con):
        acct = paper.get_account(con, name)
        new = paper.record(con, acct)
        return {"recorded": len(new), "latest": max((pd.Timestamp(x["date"]).date() for x in new), default=None),
                "account": account(paper.summarize(acct, paper.replay(con, acct)), True)}
    return _with_paper(go)


@api.delete("/paper/accounts/{name}", status_code=204, tags=["paper"], summary="删除模拟账户")
def delete_account(name: str):
    def go(con):
        paper.get_account(con, name)
        paper.delete(con, name)
    _with_paper(go)
    return Response(status_code=204)
