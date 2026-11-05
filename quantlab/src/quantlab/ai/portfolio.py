"""Tells the AI research run about the person it is writing for: what they hold, at what cost, how much cash is free and
which rules their own holdings follow.

Without this TradingAgents is told "portfolio not provided" and can only guess, so a report may talk of cutting 25% of a
position that does not exist or of buying what the rules would not allow. The facts come from the ledger (the last sync,
refreshed when a key is set) and the owner's holdings rules; nothing is invented: when the ledger cannot be read the run
says so and the report is marked as written without holdings."""
import functools

from ..errors import QuantLabError
from ..holdings import ledger, policy, review


def facts(symbol, val, pol):
    """What the account looks like for symbol, as a dict: held, shares, avg_cost, value, weight, gain, cap, uncapped,
    investable, cash, stocks, fixed, reserve, room_cap, room_cash, max_buy. val is review.valuation(); pol the rules."""
    inv = val["investable"]
    pos = next((p for p in val["positions"] if p["symbol"] == symbol), None)
    held = bool(pos and pos["shares"] > 0)
    value = (pos["value"] or 0.0) if held else 0.0
    cap = policy.cap_for(pol, symbol)
    room_cap = max(0.0, cap * inv - value)
    room_cash = max(0.0, val["cash"] - pol["cash_reserve"] * inv)
    return {
        "symbol": symbol, "held": held, "shares": pos["shares"] if held else 0, "avg_cost": pos["avg_cost"] if held else None,
        "value": value, "weight": (value / inv) if inv > 0 else None, "gain": pos["gain"] if held else None,
        "cap": cap, "uncapped": cap >= 1, "investable": inv, "cash": val["cash"], "stocks": val["stocks"], "fixed": val["fixed"],
        "reserve": pol["cash_reserve"], "targets": pol["targets"], "band": pol["band"], "whole": pol["whole_shares"],
        "min_trade": pol["min_trade_usd"], "room_cap": room_cap, "room_cash": room_cash, "max_buy": min(room_cap, room_cash),
        "cash_weight": (val["cash"] / inv) if inv > 0 else None,
        "unpriced": sorted(p["symbol"] for p in val["positions"] if p["shares"] > 0 and p["value"] is None),
    }


def render(f):
    """The account block written for the AI agents (English: their prompts are)."""
    lines = ["Account facts for the owner of this book (all USD): investable funds $%s (stocks $%s + cash $%s). Treasuries and "
             "deposits ($%s) are held separately and are not part of this decision." % (
                 format(f["investable"], ",.2f"), format(f["stocks"], ",.2f"), format(f["cash"], ",.2f"), format(f["fixed"], ",.2f"))]
    if f["unpriced"]:
        lines.append("WARNING: no price could be found for %s, so they are left out of the totals above; every percentage here is "
                     "overstated and the investable total understated by their value." % ", ".join(f["unpriced"]))
    if f["held"]:
        lines.append("%s: holds %s shares at an average cost of $%.2f, market value $%s = %.1f%% of investable funds%s." % (
            f["symbol"], format(f["shares"], ",.4g"), f["avg_cost"] or 0, format(f["value"], ",.2f"), (f["weight"] or 0) * 100,
            ", unrealized %+.1f%% against cost" % (f["gain"] * 100) if f["gain"] is not None else ""))
    else:
        lines.append("%s: the owner holds NONE of it now. A 'reduce' or 'sell' view therefore has nothing to act on; it can only mean "
                     "'do not buy', and a 'buy' view is a new position." % f["symbol"])
    cap = "no single-holding cap applies to %s (a broad index ETF)" % f["symbol"] if f["uncapped"] else \
        "one stock may be at most %.0f%% of investable funds" % (f["cap"] * 100)
    targets = ", ".join("%s %.0f%%" % (k, v * 100) for k, v in sorted(f["targets"].items())) or "none set"
    lines.append("The owner's own rules: %s; cash must stay at or above %.0f%% of investable funds (now %.1f%%); targets: %s with a "
                 "rebalancing band of %.0f points; %s; minimum order $%.0f. Under these rules the most that could be added to %s "
                 "now is about $%.0f (room under the cap $%.0f, free cash above the reserve $%.0f)." % (
                     cap, f["reserve"] * 100, (f["cash_weight"] or 0) * 100, targets, f["band"] * 100,
                     "whole shares only" if f["whole"] else "fractional shares allowed", f["min_trade"], f["symbol"], f["max_buy"],
                     f["room_cap"], f["room_cash"]))
    lines.append("Execution: the owner places orders by hand at a retail broker, US-listed stocks and ETFs only, with no options, "
                 "collars, spreads, short selling or margin. Give any sizing as shares or dollars of THIS account and keep it inside "
                 "the rules above. If your view conflicts with a rule, say so plainly instead of ignoring the rule.")
    return "\n".join(lines)


@functools.cache
def _book_class():
    """TradingAgents' PortfolioContext with the account block appended to what it renders for the agents."""
    from tradingagents.portfolio import PortfolioContext

    class Book(PortfolioContext):
        account: str = ""

        def render(self, ticker):
            return super().render(ticker) + ("\n" + self.account if self.account else "")

    return Book


def describe(book, symbol):
    """What the report page shows of what the AI was told (Chinese heading, the exact text it received)."""
    if book is None:
        return ("没有读到你的持仓（账本没连上或还没同步）。这次分析是在**不知道你有没有这只股票、买了多少、有多少现金**的情况下做的，"
                "评级和仓位说法只能当通用参考。")
    return "下面是交给 AI 的原文（它据此判断你有没有这只股票、买了多少、规则允许多少）：\n\n" + book.render(symbol)


def build(symbol, refresh=None):
    """(book for TradingAgents, facts dict) for symbol, or (None, None) when the ledger cannot be read. refresh defaults to
    syncing when a ledger key is configured."""
    from tradingagents.portfolio import Position

    try:
        snap = ledger.load(refresh=ledger.configured() if refresh is None else refresh)
        pol = policy.load()
        syms = {p["symbol"] for p in ledger.positions(snap)} | set(pol["targets"]) | {symbol}
        val = review.valuation(snap, review.prices_for(syms))
    except QuantLabError:
        return None, None
    f = facts(symbol, val, pol)
    book = _book_class()(
        cash=val["cash"], currency="USD",
        positions=[Position(ticker=p["symbol"], quantity=p["shares"], average_price=p["avg_cost"]) for p in val["positions"] if p["shares"] > 0],
        account=render(f))
    return book, f
