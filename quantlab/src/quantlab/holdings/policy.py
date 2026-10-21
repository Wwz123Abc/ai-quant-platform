"""Your portfolio rules. Only these rules propose orders; signals (trend, AI ratings, fundamentals) only remind.

Weights are shares of the investable money: stocks plus cash. Treasuries and deposits are held to maturity,
count in total assets, and are never traded."""
import json
import math

from ..config import get_settings
from ..core.data import normalize
from ..errors import InvalidInput

DEFAULT = {
    "targets": {},                    # symbol -> target weight; holdings without a target are only capped
    "band": 0.05,                     # rebalance a target when it drifts more than this (absolute)
    "max_position": 0.30,             # no stock above this share
    "cash_reserve": 0.10,             # buys never take cash below this share
    "min_trade_usd": 50.0,            # smaller orders are not proposed
    "whole_shares": True,             # round orders to whole shares (most brokers without fractional trading)
    "alert_drawdown_from_cost": 0.20,  # remind when a holding is this far below its average cost
    "alert_drawdown_from_high": 0.25,  # remind when a holding is this far below its one-year high
}


def path():
    return get_settings().data_dir / "holdings" / "policy.json"


def load():
    p = path()
    stored = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {**DEFAULT, **stored}


def validate(policy):
    """A checked copy, or InvalidInput explaining the first problem."""
    p = {**DEFAULT, **policy}
    for k in ("band", "max_position", "cash_reserve", "alert_drawdown_from_cost", "alert_drawdown_from_high"):
        v = float(p[k])
        if not (math.isfinite(v) and 0 <= v <= 1):
            raise InvalidInput("%s 要在 0 到 1 之间" % k)
        p[k] = v
    if not 0.01 <= p["band"] <= 0.5:
        raise InvalidInput("再平衡区间要在 1% 到 50% 之间")
    if not 0.02 <= p["max_position"] <= 1:
        raise InvalidInput("单只上限要在 2% 到 100% 之间")
    p["min_trade_usd"] = max(0.0, float(p["min_trade_usd"]))
    p["whole_shares"] = bool(p["whole_shares"])
    targets = {}
    for sym, w in (p.get("targets") or {}).items():
        w = float(w)
        if not 0 <= w <= p["max_position"] + 1e-9:
            raise InvalidInput("%s 的目标仓位要在 0 到单只上限（%.0f%%）之间" % (sym, p["max_position"] * 100))
        if w > 0:
            targets[normalize(sym)] = w
    if sum(targets.values()) > 1 - p["cash_reserve"] + 1e-9:
        raise InvalidInput("目标仓位合计 %.0f%% 超过了 1 减去现金储备（%.0f%%）" % (sum(targets.values()) * 100, (1 - p["cash_reserve"]) * 100))
    p["targets"] = targets
    return {k: p[k] for k in DEFAULT}


def save(policy):
    p = validate(policy)
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
