"""Your portfolio rules. Only these rules propose orders; signals (trend, AI ratings, fundamentals) only remind.

Weights are shares of the investable money: every stock held, SGOV included. The ledger keeps no cash (2026-11-10): the owner
holds SGOV, a short-term Treasury ETF, as the cash reserve, so "cash" in these rules is the value of the SGOV position, which is
never capped and never trimmed for profit. Treasuries and deposits are held to maturity,
count in total assets, and are never traded."""
import json
import math

from ..config import get_settings
from ..core.data import normalize
from ..errors import InvalidInput

RESERVE = "SGOV"  # the holding that plays cash
MAX_POSITION = 0.25  # fixed by the owner (2026-11-09): no single stock above a quarter of the investable money; not a setting

DEFAULT = {
    "targets": {},                    # symbol -> target weight; holdings without a target are only capped
    "band": 0.05,                     # rebalance a target when it drifts more than this (absolute)
    "max_position": MAX_POSITION,     # no stock above this share (fixed: load and validate always set it)
    # broad index ETFs already spread over hundreds of companies: no cap for these (the owner's choice, 2026-10-23)
    "uncapped": ["DIA", "IVV", "ITOT", "QQQ", "QQQM", "SPLG", "SPY", "VOO", "VT", "VTI"],
    "cash_reserve": 0.10,             # buys never take the SGOV position below this share
    "min_trade_usd": 50.0,            # smaller orders are not proposed
    "whole_shares": True,             # round orders to whole shares (most brokers without fractional trading)
    "alert_drawdown_from_cost": 0.20,  # remind when a holding is this far below its average cost
    "alert_drawdown_from_high": 0.25,  # remind when a holding is this far below its one-year high
    # take profit, in two steps, for stocks that are not in `uncapped` (broad index ETFs are held, not trimmed): when a holding's
    # price is tp_gain_1 above its average cost, propose selling tp_sell_1 of the shares; at tp_gain_2, tp_sell_2 of what is left.
    # Each step is proposed once per purchase: a new buy of the same stock starts the steps again.
    "take_profit": True,
    "tp_gain_1": 0.50, "tp_sell_1": 0.33,
    "tp_gain_2": 1.00, "tp_sell_2": 0.33,
}


def path():
    return get_settings().data_dir / "holdings" / "policy.json"


def load():
    p = path()
    stored = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {**DEFAULT, **stored, "max_position": MAX_POSITION, "uncapped": sorted({*(stored.get("uncapped") or DEFAULT["uncapped"]), RESERVE})}


def validate(policy):
    """A checked copy, or InvalidInput explaining the first problem."""
    p = {**DEFAULT, **policy}
    p["max_position"] = MAX_POSITION
    for k in ("band", "cash_reserve", "alert_drawdown_from_cost", "alert_drawdown_from_high"):
        v = float(p[k])
        if not (math.isfinite(v) and 0 <= v <= 1):
            raise InvalidInput("%s 要在 0 到 1 之间" % k)
        p[k] = v
    if not 0.01 <= p["band"] <= 0.5:
        raise InvalidInput("再平衡区间要在 1% 到 50% 之间")
    p["take_profit"] = bool(p["take_profit"])
    for k, lo, hi, what in (("tp_gain_1", 0.05, 10, "第一档止盈的涨幅"), ("tp_gain_2", 0.05, 10, "第二档止盈的涨幅"),
                            ("tp_sell_1", 0.05, 1, "第一档卖出的比例"), ("tp_sell_2", 0.05, 1, "第二档卖出的比例")):
        v = float(p[k])
        if not (math.isfinite(v) and lo <= v <= hi):
            raise InvalidInput("%s要在 %.0f%% 到 %.0f%% 之间" % (what, lo * 100, hi * 100))
        p[k] = v
    if p["tp_gain_2"] <= p["tp_gain_1"]:
        raise InvalidInput("第二档止盈的涨幅要比第一档高")
    p["min_trade_usd"] = max(0.0, float(p["min_trade_usd"]))
    p["whole_shares"] = bool(p["whole_shares"])
    p["uncapped"] = sorted({normalize(s) for s in (p.get("uncapped") or []) if str(s).strip()} | {RESERVE})
    targets = {}
    for sym, w in (p.get("targets") or {}).items():
        w, sym = float(w), normalize(sym)
        top = cap_for(p, sym)
        if not 0 <= w <= top + 1e-9:
            raise InvalidInput("%s 的目标仓位要在 0 到 %.0f%% 之间" % (sym, top * 100))
        if w > 0:
            targets[sym] = w
    if sum(targets.values()) > 1 - p["cash_reserve"] + 1e-9:
        raise InvalidInput("目标仓位合计 %.0f%% 超过了 1 减去 SGOV 储备（%.0f%%）" % (sum(targets.values()) * 100, (1 - p["cash_reserve"]) * 100))
    p["targets"] = targets
    return {k: p[k] for k in DEFAULT}


def cap_for(policy, sym):
    """The largest share one holding may take: the single-stock cap, or none for an uncapped (broad ETF) symbol."""
    return 1.0 if normalize(sym) in policy["uncapped"] else policy["max_position"]


def save(policy):
    p = validate(policy)
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
