"""Routes for your real holdings (synced from the ledger, reviewed against your rules) and stock analysis."""
import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..errors import NotFound
from ..holdings import analysis, ledger, policy, review
from .security import require_token

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)], tags=["holdings"])


class Policy(BaseModel):
    targets: dict[str, float] = Field(default_factory=dict, description="股票 -> 目标仓位（占股票加现金的比例）")
    band: float = Field(0.05, description="偏离目标超过这么多（绝对值）才再平衡")
    max_position: float = Field(0.30, description="单只股票上限")
    cash_reserve: float = Field(0.10, description="现金储备下限")
    min_trade_usd: float = 50.0
    whole_shares: bool = True
    alert_drawdown_from_cost: float = 0.20
    alert_drawdown_from_high: float = 0.25


class HoldingPosition(BaseModel):
    symbol: str
    currency: str
    shares: float
    avg_cost: float
    price: float | None
    value: float | None
    gain: float | None
    weight: float | None


class Valuation(BaseModel):
    positions: list[HoldingPosition]
    cash: float
    stocks: float
    investable: float
    fixed: float
    total: float
    cash_weight: float | None


class Proposal(BaseModel):
    id: int
    symbol: str
    side: Literal["buy", "sell"]
    shares: float
    price: float
    value: float
    reason: str
    status: str
    decided_at: str | None
    note: str | None


class Reminder(BaseModel):
    symbol: str
    level: Literal["info", "warn"]
    kind: str
    text: str


class Review(BaseModel):
    id: int
    at: str
    synced: str | None
    account: str | None
    policy: Policy
    valuation: Valuation
    proposals: list[Proposal]
    notes: list[str]
    reminders: list[Reminder]


class HoldingsStatus(BaseModel):
    configured: bool = Field(description="是否配置了账本的只读访问密钥（只检查是否存在）")
    synced: str | None
    account: str | None


class Decision(BaseModel):
    note: str = Field("", max_length=200)


class Technical(BaseModel):
    symbol: str
    date: dt.date | None
    close: float | None
    r1m: float | None = None
    r3m: float | None = None
    r6m: float | None = None
    r1y: float | None = None
    mom_12_1: float | None = None
    vol: float | None = None
    from_high: float | None = None
    max_dd_1y: float | None = None
    ma50: float | None = None
    ma200: float | None = None
    uptrend: bool | None = None
    last_cross: dt.date | None = None
    beta: float | None = None


class Fundamentals(BaseModel):
    revenue_ttm: float | None
    net_income_ttm: float | None
    op_margin: float | None
    rev_growth: float | None
    roa: float | None
    pe: float | None
    pb: float | None
    market_cap: float | None
    sue: float | None
    cfo_to_ni: float | None


class AiInfo(BaseModel):
    id: int
    rating: str | None
    trade_date: str


class ModelRank(BaseModel):
    rank: int
    of: int
    signal_date: str


class PortfolioFit(BaseModel):
    investable: float
    cash: float
    held_value: float
    max_buy: float
    amount: float
    weight_after: float | None
    correlations: dict[str, float]
    avg_correlation: float | None
    shares_at_close: float | None


class Check(BaseModel):
    rule: str
    ok: bool
    text: str


class Note(BaseModel):
    kind: str
    text: str


class Analysis(BaseModel):
    symbol: str
    technical: Technical
    fundamentals: Fundamentals | None
    ai: AiInfo | None
    model: ModelRank | None
    portfolio: PortfolioFit | None
    checks: list[Check]
    allowed: bool
    verdict: str
    notes: list[Note]
    disclaimer: str


@router.get("/holdings/status", response_model=HoldingsStatus, summary="账本同步状态")
def holdings_status():
    snap = None
    if ledger.snapshot_path().exists():
        snap = ledger.load(refresh=False)
    return {"configured": ledger.configured(), "synced": (snap or {}).get("synced"), "account": (snap or {}).get("account")}


@router.get("/holdings/review", response_model=Review, summary="最近一次持仓检查")
def holdings_review():
    r = review.latest()
    if r is None:
        raise NotFound("还没有检查过持仓")
    return r


@router.post("/holdings/review", response_model=Review, summary="同步账本并按你的规则检查持仓")
def holdings_review_now():
    return review.run()


@router.get("/holdings/policy", response_model=Policy, summary="你的规则")
def get_policy():
    return policy.load()


@router.put("/holdings/policy", response_model=Policy, summary="修改你的规则")
def put_policy(p: Policy):
    return policy.save(p.model_dump())


@router.post("/holdings/proposals/{pid}/approve", response_model=Proposal, summary="确认一条建议订单（你会自己去券商下单）")
def approve(pid: int, d: Decision):
    return review.decide(pid, True, d.note)


@router.post("/holdings/proposals/{pid}/reject", response_model=Proposal, summary="拒绝一条建议订单")
def reject(pid: int, d: Decision):
    return review.decide(pid, False, d.note)


@router.get("/analyze/{symbol}", response_model=Analysis, summary="个股分析：趋势、基本面、AI 评级、和持仓的关系、你的规则允许买多少")
def analyze(symbol: str, amount: float | None = Query(None, gt=0, le=1e8)):
    return analysis.analyze(symbol, amount)
