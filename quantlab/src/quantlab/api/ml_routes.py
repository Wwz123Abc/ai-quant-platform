"""Routes for machine-learning stock selection and automated trading."""
import datetime as dt
import json
import threading
from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..config import get_settings
from ..core import data
from ..errors import Conflict, NotFound
from ..ml import features as F
from ..ml import research as R
from ..ml import signal as S
from ..ml import universe as U
from ..trading import daily, engine
from ..trading.broker import PaperBroker, connect
from .routes import num, nums, summary
from .schemas import Summary
from .security import require_token

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


# ---------------------------------------------------------------- schemas

class StudyRequest(BaseModel):
    start: dt.date = dt.date(2012, 1, 1)
    end: dt.date | None = None
    top: int = Field(10, ge=3, le=30)
    max_weight: float = Field(0.2, gt=0, le=1)
    cash: float = Field(10_000, gt=0, le=1e9)
    slippage: float = Field(5, ge=0, le=500)
    fee: float = Field(0, ge=0, le=100)


class StrategyResult(BaseModel):
    key: str
    name: str
    summary: Summary
    turnover: float | None
    costs: float


class Study(BaseModel):
    universe: str
    universe_as_of: str
    size: int
    skipped: list[str]
    start: dt.date
    end: dt.date
    strategies: list[StrategyResult]
    dates: list[dt.date]
    equity: dict[str, list[float | None]]
    ic_mean: float | None
    ic_std: float | None
    ic_positive: float | None
    ic_months: int
    ic_by_year: dict[str, float | None]
    deciles: dict[str, float | None]
    importance: dict[str, float]
    feature_names: dict[str, str]
    fits: int


class Holding(BaseModel):
    symbol: str
    weight: float
    score: float | None


class Signal(BaseModel):
    signal_date: dt.date
    trained_to: str
    samples: int
    holdings: list[Holding]


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    mode: Literal["plan", "paper"] = "paper"
    capital: float | None = Field(None, gt=0)


class Position(BaseModel):
    symbol: str
    qty: float
    price: float | None
    value: float | None
    weight: float | None


class Order(BaseModel):
    client_id: str
    signal_date: str
    symbol: str
    side: str
    qty: float
    est_price: float
    status: str
    reason: str | None
    created: str
    filled_at: str | None
    fill_price: float | None
    fee: float


class Event(BaseModel):
    at: str
    kind: str
    message: str


class TradingAccount(BaseModel):
    name: str
    broker: str
    mode: str
    capital: float
    cash: float
    value: float
    peak_equity: float
    drawdown: float
    halted: str | None
    created: str
    positions: list[Position]
    orders: list[Order] | None = None
    events: list[Event] | None = None
    equity: dict[str, list] | None = None


class Limits(BaseModel):
    capital: float
    top_n: int
    max_weight: float
    max_order_usd: float
    max_daily_usd: float
    max_orders_per_day: int
    min_order_usd: float
    max_drawdown: float
    max_data_age_days: int
    live_enabled: bool
    paper_weeks_before_live: int
    busy: bool


# ---------------------------------------------------------------- machine learning

@router.post("/ml/backtests", response_model=Study, tags=["ml"], summary="机器学习选股：滚动训练 + 组合回测（约一分钟）")
def ml_backtest(req: StudyRequest):
    u = U.load()
    wide, skipped = F.load_panel(u["symbols"])
    st = R.run_study(wide, u["symbols"], skipped, req.start, req.end, req.top, req.max_weight, req.cash, req.slippage, req.fee)
    eq = pd.DataFrame({k: v["result"].equity for k, v in st.strategies.items()}).ffill()
    return {
        "universe": "Nasdaq-100", "universe_as_of": u["as_of"], "size": len(st.universe), "skipped": list(st.skipped),
        "start": st.start.date(), "end": st.end.date(),
        "strategies": [{"key": k, "name": R.NAMES[k], "summary": summary(v["summary"]), "turnover": num(v["result"].turnover.mean()),
                        "costs": v["result"].costs} for k, v in st.strategies.items()],
        "dates": [d.date() for d in eq.index], "equity": {k: nums(eq[k]) for k in eq.columns},
        "ic_mean": num(st.ic.mean()), "ic_std": num(st.ic.std()), "ic_positive": num((st.ic > 0).mean()), "ic_months": len(st.ic),
        "ic_by_year": {str(y): num(v) for y, v in st.ic.groupby(st.ic.index.year).mean().items()},
        "deciles": {str(k): num(v) for k, v in st.deciles.items()},
        "importance": {k: float(v) for k, v in st.importance.items()}, "feature_names": F.FEATURES, "fits": len(st.fits),
    }


def _signal_response(card, weights, scores):
    return {"signal_date": card["signal_date"], "trained_to": card["to"], "samples": card["samples"],
            "holdings": [{"symbol": s, "weight": float(w), "score": num(scores.get(s)) if scores is not None else None}
                         for s, w in sorted(weights.items(), key=lambda kv: -kv[1])]}


@router.get("/ml/signal", response_model=Signal, tags=["ml"], summary="最近一次算出的目标组合")
def ml_signal_latest():
    folders = sorted((get_settings().data_dir / "models").glob("*/weights.json"))
    if not folders:
        raise NotFound("还没有算过目标组合")
    folder = folders[-1].parent
    card = json.loads((folder / "card.json").read_text(encoding="utf-8"))
    weights = json.loads((folder / "weights.json").read_text(encoding="utf-8"))
    return _signal_response(card, weights, None)


@router.post("/ml/signal", response_model=Signal, tags=["ml"], summary="按最新收盘价重新计算目标组合（约一分钟）")
def ml_signal_compute():
    sig = S.latest()
    return _signal_response(sig["card"], sig["weights"].to_dict(), sig["scores"])


# ---------------------------------------------------------------- trading

def _with(fn):
    con = connect()
    try:
        return fn(con)
    finally:
        con.close()


def _account(con, name, detail):
    a = engine.get_account(con, name)
    st = PaperBroker(con, name).account()
    prices = {s: float(data.load(s)["close"].iloc[-1]) for s in st["positions"]}
    value = engine.valuation(st, prices)
    positions = [{"symbol": s, "qty": q, "price": prices.get(s), "value": q * prices[s] if s in prices else None,
                  "weight": q * prices[s] / value if s in prices and value else None} for s, q in sorted(st["positions"].items())]
    out = {**a, "cash": st["cash"], "value": value, "drawdown": value / a["peak_equity"] - 1 if a["peak_equity"] else 0.0,
           "positions": positions}
    if detail:
        curve = engine.equity_curve(con, name)
        out.update({"orders": engine.orders(con, name), "events": engine.events(con, name),
                    "equity": {"dates": [d.date().isoformat() for d in curve.index], "values": nums(curve)}})
    return out


@router.get("/trading/limits", response_model=Limits, tags=["trading"], summary="风控上限")
def trading_limits():
    s = get_settings()
    return {"capital": s.trading_capital, "top_n": s.trading_top_n, "max_weight": s.trading_max_weight, "max_order_usd": s.trading_max_order_usd,
            "max_daily_usd": s.trading_max_daily_usd, "max_orders_per_day": s.trading_max_orders_per_day, "min_order_usd": s.trading_min_order_usd,
            "max_drawdown": s.trading_max_drawdown, "max_data_age_days": s.trading_max_data_age_days, "live_enabled": s.trading_live_enabled,
            "paper_weeks_before_live": s.trading_paper_weeks_before_live, "busy": daily.busy()}


@router.get("/trading/accounts", response_model=list[TradingAccount], tags=["trading"], summary="交易账户")
def trading_accounts():
    return _with(lambda con: [_account(con, a["name"], False) for a in engine.accounts(con)])


@router.post("/trading/accounts", response_model=TradingAccount, status_code=201, tags=["trading"], summary="创建交易账户")
def trading_create(req: AccountCreate):
    def go(con):
        engine.create_account(con, req.name.strip(), req.mode, req.capital)
        return _account(con, req.name.strip(), True)
    return _with(go)


@router.get("/trading/accounts/{name}", response_model=TradingAccount, tags=["trading"], summary="交易账户详情")
def trading_account(name: str):
    return _with(lambda con: _account(con, name, True))


class HaltRequest(BaseModel):
    reason: str = Field("手动暂停", max_length=200)


@router.post("/trading/accounts/{name}/halt", response_model=TradingAccount, tags=["trading"], summary="暂停交易")
def trading_halt(name: str, req: HaltRequest):
    def go(con):
        engine.get_account(con, name)
        engine.halt(con, name, req.reason)
        return _account(con, name, True)
    return _with(go)


@router.post("/trading/accounts/{name}/resume", response_model=TradingAccount, tags=["trading"], summary="恢复交易")
def trading_resume(name: str):
    def go(con):
        engine.resume(con, name)
        return _account(con, name, True)
    return _with(go)


class ModeRequest(BaseModel):
    mode: Literal["plan", "paper", "live"]


@router.post("/trading/accounts/{name}/mode", response_model=TradingAccount, tags=["trading"], summary="切换模式")
def trading_mode(name: str, req: ModeRequest):
    def go(con):
        engine.set_mode(con, name, req.mode)
        return _account(con, name, True)
    return _with(go)


class RunRequest(BaseModel):
    force: bool = Field(False, description="不等月末，按最新收盘价立刻调仓")


@router.post("/trading/run", status_code=202, tags=["trading"], summary="运行一次交易步骤（后台执行）")
def trading_run(req: RunRequest):
    if daily.busy():
        raise Conflict("交易步骤正在运行")
    threading.Thread(target=daily.run, kwargs={"force": req.force}, name="trading-manual", daemon=True).start()
    return {"status": "started"}
