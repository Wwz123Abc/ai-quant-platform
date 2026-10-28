"""What an order costs at HSBC Hong Kong, for US stocks bought online or in the app.

Rates as published on 2026-10-28 (hsbc.com.hk, "US stock trading" and "HSBC Trade25"):
- standard: USD18 per order for the first 1,000 shares, plus USD0.015 for each share above that;
- Trade25: no commission while the month's turnover (buys and sells, in HKD) is at most HKD250,000, standard
  commission above it; the HKD25 monthly account fee is paid whether or not a strategy trades, so it is left out;
- every sale also pays the SEC fee, 0.00206% of the amount (since 2026-04-04), rounded up to the cent.
Turnover counts only the orders of one run, not other trades in the same HSBC account."""
import math

PLANS = ("trade25", "standard")
BASE, PER_SHARE, FREE_SHARES = 18.0, 0.015, 1000
SEC_RATE = 0.0000206
TRADE25_LIMIT_HKD, HKD_PER_USD = 250_000, 7.8  # the Hong Kong dollar is pegged within 7.75-7.85


def commission(shares):
    return BASE + PER_SHARE * max(0, shares - FREE_SHARES)


def sec_fee(amount):
    return math.ceil(amount * SEC_RATE * 100 - 1e-9) / 100


def describe(fee):
    if fee == "trade25":
        return "汇丰 Trade25（当月买卖合计不超过 25 万港元免佣金，超出每笔 $18；卖出另收 SEC 费）"
    if fee == "standard":
        return "汇丰标准收费（每笔 $18，超过 1,000 股每股加 $0.015；卖出另收 SEC 费）"
    return "每笔手续费 $%g" % fee


class Schedule:
    """Prices the orders of one run in date order. fee is dollars per order, or the name of an HSBC plan."""

    def __init__(self, fee):
        if isinstance(fee, str) and fee not in PLANS:
            raise ValueError("unknown fee plan: %s" % fee)
        self.fee, self.month, self.turnover = fee, None, 0.0

    def quote(self, date, shares, amount, sell):
        """The cost of an order without recording it."""
        if not isinstance(self.fee, str):
            return float(self.fee)
        cost = sec_fee(amount) if sell else 0.0
        if self.fee == "standard":
            return cost + commission(shares)
        turnover = (self.turnover if (date.year, date.month) == self.month else 0.0) + amount * HKD_PER_USD
        return cost + (0.0 if turnover <= TRADE25_LIMIT_HKD else commission(shares))

    def charge(self, date, shares, amount, sell):
        """The cost of an order, recording it in the month's turnover."""
        cost = self.quote(date, shares, amount, sell)
        if (date.year, date.month) != self.month:
            self.month, self.turnover = (date.year, date.month), 0.0
        self.turnover += amount * HKD_PER_USD
        return cost
