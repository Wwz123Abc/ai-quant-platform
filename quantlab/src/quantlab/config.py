"""Settings, read from QUANTLAB_* environment variables and an optional .env file in the working directory."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QUANTLAB_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_dir: Path = Path("data")          # SQLite databases: prices cache, paper accounts
    reports_dir: Path = Path("reports")    # charts and CSV files written by the command line
    host: str = "127.0.0.1"                # the API listens on this machine only unless changed
    port: int = 8000
    api_token: str | None = None           # when unset, one is generated once and kept in data_dir/api_token
    cors_origins: list[str] = []           # e.g. ["http://localhost:5173"] for the web dev server
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    cache_max_age: int = 6 * 3600          # seconds before a cached symbol is downloaded again
    http_timeout: float = 20.0

    # AI research (TradingAgents). Provider keys such as DEEPSEEK_API_KEY are read from the environment or .env.
    ai_provider: str = "deepseek"
    ai_quick_model: str = "deepseek-flash"   # analysts, researchers, trader
    ai_deep_model: str = "deepseek-flash"    # research and portfolio managers
    ai_analysts: list[str] = ["market", "social", "news", "fundamentals"]
    ai_language: str = "Chinese"
    ai_budget_cny: float = 100.0             # hard monthly limit on model spending
    ai_usd_cny: float = 7.3                  # for showing USD prices in CNY; set it near the current rate
    ai_first_estimate_cny: float = 2.0       # assumed cost of a run until real runs have been measured
    # USD per million tokens: [input from cache, input not from cache, output]. Peak-hour list prices, so the
    # ledger never under-counts; off-peak calls actually cost half. Unknown models are charged at the highest rates.
    ai_prices: dict[str, list[float]] = {
        "deepseek-flash": [0.006, 0.30, 1.20],
        "deepseek-v4-flash": [0.006, 0.30, 1.20],
        "deepseek-v4-pro": [0.044, 1.32, 3.96],
    }

    # Automated trading of the machine-learning portfolio. Every limit is enforced on each order, whatever the mode.
    trading_capital: float = 500.0          # the most money the program may ever put to work, in USD
    trading_top_n: int = 10
    trading_max_weight: float = 0.20        # largest share of the capital in one stock
    trading_max_order_usd: float = 200.0
    trading_max_daily_usd: float = 1_000.0  # buys plus sells in one day
    trading_max_orders_per_day: int = 30
    trading_min_order_usd: float = 1.0      # smaller differences are not traded
    trading_max_drawdown: float = 0.25      # halt trading if equity falls this far below its peak
    trading_max_data_age_days: int = 5      # refuse to trade on prices older than this
    trading_slippage_bps: float = 5.0       # paper fills: open price plus or minus this
    trading_fee: float = 0.0                # paper fills: fee per order, USD
    trading_live_enabled: bool = False      # live orders also need a broker connector and 8 weeks of paper trading
    trading_paper_weeks_before_live: int = 8

    # Daily job: AI analyses for AI accounts, then a decision for every paper account. Runs inside `serve`.
    schedule_enabled: bool = True
    schedule_time: str = "07:30"             # Beijing time, Tuesday to Saturday, after the US close


@lru_cache
def get_settings() -> Settings:
    return Settings()
