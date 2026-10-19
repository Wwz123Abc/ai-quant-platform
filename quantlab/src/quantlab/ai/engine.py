"""Adapter for TradingAgents (https://github.com/TauricResearch/TradingAgents, Apache-2.0).

TradingAgents is an optional dependency pinned to one commit (pip install quantlab[ai]); nothing here imports it
until an analysis actually runs. This module turns QuantLab settings into a TradingAgents config, runs one
analysis, and measures the tokens every model call used so the run can be charged to the budget."""
import importlib.util
import os
import threading
from pathlib import Path

from ..config import get_settings
from ..core.data import yahoo_symbol
from ..errors import Unavailable

# The reports kept from a run, in reading order, with their Chinese titles.
REPORTS = [
    ("market_report", "技术分析"),
    ("sentiment_report", "情绪分析"),
    ("news_report", "新闻分析"),
    ("fundamentals_report", "基本面分析"),
    ("investment_plan", "研究经理：多空辩论后的投资计划"),
    ("trader_investment_plan", "交易员方案"),
    ("final_trade_decision", "投资组合经理：最终决定"),
]
RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell", "REVIEW")


def available():
    return importlib.util.find_spec("tradingagents") is not None


def _load_env():
    """Provider keys live in .env (never in settings or code); load them without overriding the real environment."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path.cwd() / ".env", override=False)


def key_env(provider):
    if not available():
        return None
    from tradingagents.llm_clients.api_key_env import get_api_key_env
    return get_api_key_env(provider)


def configured():
    """Whether the provider's key is present. Only its presence is checked; the value is never read here."""
    _load_env()
    name = key_env(get_settings().ai_provider)
    return bool(name and os.environ.get(name))


def require_ready():
    s = get_settings()
    if not available():
        raise Unavailable("没有安装 AI 组件。请运行：pip install -e \".[ai]\"")
    if not configured():
        raise Unavailable("没有配置大模型密钥：请在 .env 文件里加一行 %s=你的密钥，然后重启服务" % (key_env(s.ai_provider) or "API_KEY"))


def price_for(model, prices):
    """[cache hit, cache miss, output] USD per million tokens. Exact name first, then by family; unknown models
    are charged at the highest listed rates so the budget is never under-counted."""
    if model in prices:
        return prices[model]
    m = (model or "").lower()
    for family in ("pro", "flash"):
        matches = [v for k, v in prices.items() if family in k.lower()]
        if family in m and matches:
            return max(matches, key=lambda p: p[1])
    return [max(p[i] for p in prices.values()) for i in range(3)]


class Usage:
    """A LangChain callback that adds up calls and tokens per model, safe to share between threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self.models = {}  # model -> [calls, input, cached input, output]

    def handler(self):
        from langchain_core.callbacks import BaseCallbackHandler

        usage = self

        class Handler(BaseCallbackHandler):
            def on_llm_end(self, response, **kwargs):
                usage.add_result(response)

        return Handler()

    def add_result(self, response):
        model, meta = "unknown", None
        try:
            gen = response.generations[0][0]
            msg = getattr(gen, "message", None)
            meta = getattr(msg, "usage_metadata", None)
            model = (getattr(msg, "response_metadata", None) or {}).get("model_name") or \
                (response.llm_output or {}).get("model_name") or model
        except (IndexError, TypeError, AttributeError):
            pass
        meta = meta or {}
        cached = (meta.get("input_token_details") or {}).get("cache_read") or 0
        self.add(model, meta.get("input_tokens", 0), cached, meta.get("output_tokens", 0))

    def add(self, model, tokens_in, tokens_cached, tokens_out):
        with self._lock:
            m = self.models.setdefault(model, [0, 0, 0, 0])
            m[0] += 1
            m[1] += tokens_in
            m[2] += tokens_cached
            m[3] += tokens_out

    def totals(self, prices):
        with self._lock:
            models = {k: list(v) for k, v in self.models.items()}
        cost = 0.0
        for model, (_, tin, cached, tout) in models.items():
            hit, miss, out = price_for(model, prices)
            cost += (cached * hit + (tin - cached) * miss + tout * out) / 1e6
        return {
            "llm_calls": sum(v[0] for v in models.values()), "tokens_in": sum(v[1] for v in models.values()),
            "tokens_cached": sum(v[2] for v in models.values()), "tokens_out": sum(v[3] for v in models.values()),
            "cost_usd": cost,
        }


def build_config(s=None):
    """A TradingAgents config from QuantLab settings, with every file kept under the data directory."""
    from tradingagents.default_config import build_default_config

    s = s or get_settings()
    home = (s.data_dir / "ai").resolve()
    cfg = build_default_config()
    cfg.update({
        "llm_provider": s.ai_provider, "quick_think_llm": s.ai_quick_model, "deep_think_llm": s.ai_deep_model,
        "output_language": s.ai_language, "results_dir": str(home / "results"), "data_cache_dir": str(home / "cache"),
        "memory_log_path": str(home / "memory" / "trading_memory.md"), "checkpoint_enabled": False,
    })
    return cfg


def analyze(symbol, trade_date, usage, s=None):
    """Runs one TradingAgents analysis of symbol as of the close of trade_date. Returns (rating, reports)."""
    require_ready()
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    s = s or get_settings()
    graph = TradingAgentsGraph(tuple(s.ai_analysts), config=build_config(s), callbacks=[usage.handler()])
    state, rating = graph.propagate(yahoo_symbol(symbol), str(trade_date))
    reports = {key: {"title": title, "text": str(state.get(key) or "")} for key, title in REPORTS}
    return (rating if rating in RATINGS else "REVIEW"), reports
