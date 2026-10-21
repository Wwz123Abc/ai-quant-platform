"""Request and response models. They define the public contract shown in the OpenAPI document."""
import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Symbol = Field(min_length=1, max_length=10, examples=["QQQM"])


class Costs(BaseModel):
    cash: float = Field(10_000, gt=0, le=1e9, description="初始资金（美元）")
    fee: float = Field(0, ge=0, le=1_000, description="每笔手续费（美元）")
    slippage: float = Field(5, ge=0, le=500, description="滑点（基点）")


class Window(BaseModel):
    start: dt.date | None = None
    end: dt.date | None = None
    rf: float = Field(0, ge=-0.1, le=0.5, description="无风险年利率，用于夏普比率")
    refresh: bool = False


class BacktestRequest(Costs, Window):
    symbol: str = Symbol
    fast: int = Field(50, ge=2, le=400)
    slow: int = Field(200, ge=3, le=500)

    @model_validator(mode="after")
    def _order(self):
        if self.fast >= self.slow:
            raise ValueError("短均线天数要小于长均线天数")
        return self


class Summary(BaseModel):
    start: dt.date
    end: dt.date
    years: float
    final: float
    total: float
    cagr: float
    vol: float | None
    sharpe: float | None
    mdd: float
    mdd_peak: dt.date
    mdd_trough: dt.date
    exposure: float
    trades: int
    win_rate: float | None
    avg_trade: float | None


class Trade(BaseModel):
    entry_date: dt.date
    entry_price: float
    exit_date: dt.date
    exit_price: float
    shares: int
    pnl: float
    ret: float = Field(alias="return")
    open: bool


class BacktestSeries(BaseModel):
    """Columns of equal length, one value per trading day."""
    dates: list[dt.date]
    close: list[float]
    fast: list[float | None]
    slow: list[float | None]
    equity: list[float]
    benchmark: list[float]


class BacktestResponse(BaseModel):
    symbol: str
    fast: int
    slow: int
    summary: Summary
    benchmark: Summary
    series: BacktestSeries
    trades: list[Trade]


class ScanRequest(Costs, Window):
    symbol: str = Symbol
    fasts: list[int] = Field(default=[5, 10, 15, 20, 30, 40, 50, 75, 100], min_length=1, max_length=20)
    slows: list[int] = Field(default=[50, 75, 100, 125, 150, 175, 200, 250, 300], min_length=1, max_length=20)
    split: dt.date | None = Field(None, description="样本外从这天开始；不填则留出最后 30% 的交易日")
    by: Literal["sharpe", "cagr"] = "sharpe"

    @model_validator(mode="after")
    def _range(self):
        if not all(2 <= x <= 500 for x in self.fasts + self.slows):
            raise ValueError("均线天数要在 2 到 500 之间")
        self.fasts, self.slows = sorted(set(self.fasts)), sorted(set(self.slows))
        return self


class Period(BaseModel):
    start: dt.date
    end: dt.date
    years: float


class ScanRow(BaseModel):
    fast: int
    slow: int
    is_rank: int
    oos_rank: int
    is_sharpe: float | None
    oos_sharpe: float | None
    is_cagr: float
    oos_cagr: float
    is_mdd: float
    oos_mdd: float
    is_trades: int
    oos_trades: int


class ScanResponse(BaseModel):
    symbol: str
    by: str
    in_sample: Period
    out_sample: Period
    rows: list[ScanRow]
    benchmark_in: Summary
    benchmark_out: Summary
    rank_corr: float | None
    median_out: float | None


class SignalItem(BaseModel):
    symbol: str
    ok: bool
    error: str | None = None
    date: dt.date | None = None
    close: float | None = None
    fast: float | None = None
    slow: float | None = None
    gap: float | None = None
    hold: bool | None = None
    since: dt.date | None = None
    since_is_cross: bool | None = None
    days: int | None = None
    flipped: bool | None = None


class Bars(BaseModel):
    symbol: str
    dates: list[dt.date]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]


class AccountCreate(Costs):
    name: str = Field(min_length=1, max_length=40, examples=["核心"])
    symbols: list[str] = Field(min_length=1, max_length=20, examples=[["QQQM", "VOO"]])
    strategy: Literal["ma_cross", "ai_rating"] = "ma_cross"
    fast: int = Field(50, ge=2, le=400, description="均线账户：短均线天数")
    slow: int = Field(200, ge=3, le=500, description="均线账户：长均线天数")
    every_days: int = Field(7, ge=1, le=60, description="AI 账户：每隔多少天重新分析一次")


class Position(BaseModel):
    symbol: str
    decision: bool | None = Field(description="最新决定：持有、空仓，或还在等待第一个决定（null）")
    decision_date: dt.date | None
    held: bool
    shares: int
    value: float
    cash: float
    ret: float = Field(alias="return")
    benchmark_return: float


class PendingOrder(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    decision_date: dt.date
    affordable: bool


class Fill(BaseModel):
    date: dt.date
    side: Literal["buy", "sell"]
    symbol: str
    shares: int
    price: float


class EquitySeries(BaseModel):
    dates: list[dt.date]
    strategy: list[float]
    benchmark: list[float]


class Account(BaseModel):
    name: str
    strategy: str
    params: dict
    symbols: list[str]
    start: dt.date
    cash: float
    fee: float
    slippage: float
    value: float
    ret: float = Field(alias="return")
    benchmark_return: float
    days: int
    positions: list[Position]
    pending: list[PendingOrder]
    fills: list[Fill]
    equity: EquitySeries | None = None


class RecordResult(BaseModel):
    recorded: int
    latest: dt.date | None
    account: Account


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    auth: Literal["token", "proxy"] = "token"
    home_url: str | None = None


class AiBudget(BaseModel):
    month: str
    budget_cny: float
    usd_cny: float
    spent_cny: float
    estimate_cny: float
    committed_cny: float
    remaining_cny: float
    queued: int
    running: int


class Schedule(BaseModel):
    enabled: bool
    time: str
    last_run: dt.date | None
    last_result: dict | None
    running: bool


class AiStatus(BaseModel):
    available: bool = Field(description="是否安装了 AI 组件")
    configured: bool = Field(description="是否配置了大模型密钥（只检查是否存在）")
    provider: str
    quick_model: str
    deep_model: str
    analysts: list[str]
    language: str
    budget: AiBudget
    schedule: Schedule


class AiRunCreate(BaseModel):
    symbol: str = Symbol


class Report(BaseModel):
    title: str
    text: str


class AiRun(BaseModel):
    id: int
    symbol: str
    trade_date: dt.date
    status: Literal["queued", "running", "done", "failed"]
    source: str
    rating: str | None
    quick_model: str | None
    deep_model: str | None
    llm_calls: int
    tokens_in: int
    tokens_cached: int
    tokens_out: int
    cost_usd: float
    cost_cny: float
    created: dt.datetime
    started: dt.datetime | None
    finished: dt.datetime | None
    error: str | None
    reports: dict[str, Report] | None = None
