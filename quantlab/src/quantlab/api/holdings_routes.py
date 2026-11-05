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
    uncapped: list[str] = Field(default_factory=list, description="不设单只上限的代码（宽基 ETF）")
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


class FixedItem(BaseModel):
    name: str
    kind: Literal["bond", "deposit"]
    currency: str
    maturity: str
    started: bool
    matured: bool
    days_left: int = Field(description="距到期的天数，已到期是负数")
    value: float | None = Field(description="按账本的算法折成美元的价值：持有到期、含应计利息；未开始是 null，已到期是 0")


class Valuation(BaseModel):
    positions: list[HoldingPosition]
    cash: float
    stocks: float
    investable: float
    fixed: float
    fixed_items: list[FixedItem] = Field(default_factory=list)
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
    basis: str = ""
    status: str
    decided_at: str | None
    note: str | None


class Reminder(BaseModel):
    symbol: str
    level: Literal["info", "warn"]
    kind: str
    text: str
    basis: str = ""


class PriceCheck(BaseModel):
    symbol: str
    against: str = Field(description="第二个来源：新浪财经或东方财富；没能核对时为空")
    ok: bool | None = Field(description="一致、不一致，或没能核对（null）")
    text: str


class Review(BaseModel):
    id: int
    at: str
    synced: str | None
    price_date: str | None = Field(None, description="检查用到的收盘价里最早的日期")
    account: str | None
    policy: Policy
    valuation: Valuation
    proposals: list[Proposal]
    notes: list[str]
    reminders: list[Reminder]
    price_check: list[PriceCheck] = Field(default_factory=list, description="持有股票最近 5 个交易日的收盘价和第二个来源的核对（规则和账本相同）")


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


class KeyLevel(BaseModel):
    group: str = Field(description="前高前低、枢轴点、压力支撑、ATR 通道、整数关口")
    name: str
    price: float
    distance: float = Field(description="相对收盘价的距离，0.05 表示高 5%")
    basis: str = Field(description="算法和用到的日期")


class MaEvidence(BaseModel):
    verdict: str = Field(description="历史检验的结论：通过、只降回撤、未通过")
    cagr: float = Field(description="规则的年化收益（12 只美股的中位数）")
    bh_cagr: float
    mdd: float
    bh_mdd: float
    sharpe: float
    bh_sharpe: float
    buys: float = Field(description="每年买入次数")
    exposure: float = Field(description="持有时间占比")


class MaRule(BaseModel):
    id: str
    name: str
    says: str = Field(description="规则的文字定义")
    on: bool | None = Field(description="现在是否处于规则的持有状态；历史不够时为空")
    since: str | None = Field(description="这个状态从哪天开始")
    days: int | None
    evidence: MaEvidence


class MaRules(BaseModel):
    date: str
    rules: list[MaRule]
    on_count: int
    ready_count: int


class KeyLevels(BaseModel):
    date: str
    close: float
    source: str
    levels: list[KeyLevel]


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
    current_weight: float | None = Field(description="现在占可投资资金的比例")
    max_buy: float = Field(description="规则允许的最大金额")
    max_shares: float | None = Field(description="最大金额按收盘价能买的股数（规则要求整股时向下取整）")
    amount_given: bool = Field(description="是否填了打算买的金额")
    amount: float = Field(description="这次判断用的金额；按整股时是整股的花费")
    shares: float | None = Field(description="这次判断用的股数")
    whole_shares: bool
    price: float | None = Field(description="计算股数用的收盘价")
    weight_after: float | None
    correlations: dict[str, float]
    avg_correlation: float | None


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
    levels: KeyLevels | None = Field(None, description="关键价位；行情不足 30 天时没有")
    ma_rules: MaRules | None = Field(None, description="均线规则：每条的状态和历史检验结果，只是描述，不是买卖建议")
    fundamentals: Fundamentals | None
    fundamentals_note: str | None = Field(None, description="没有基本面时的原因")
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
