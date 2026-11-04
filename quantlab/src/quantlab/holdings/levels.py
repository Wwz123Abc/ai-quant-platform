"""Key price levels of one stock, computed from its daily bars: prior highs and lows, pivot points, support and
resistance from swing turns, ATR bands and round numbers.

The idea comes from the level lists of other chart tools (tick-stock-panel's stock page); the formulas are the
textbook ones and every number is a pure function of the bars. A level is information for you to weigh: it never
creates an order. Each level says how it was computed and from which dates. Prices are the cached bars: adjusted for
splits and dividends, so older levels can sit a little below the quote printed on that day."""
import math

import pandas as pd

HIGH_LOW_WINDOWS = (20, 60, 252)  # trading days
SWING_SIDE, SWING_LOOKBACK, CLUSTER = 3, 120, 0.01  # a turn is the extreme of 3 bars either side; merged within 1%
ATR_DAYS = 14


def _day(ts):
    return str(ts)[:10]


def _step(price):
    """The spacing of the round numbers worth watching at this price."""
    for limit, step in ((20, 1), (50, 5), (200, 10), (500, 25), (1000, 50)):
        if price < limit:
            return step
    return 100


def _swings(bars):
    """(turn highs, turn lows) as lists of (date, price) over the last SWING_LOOKBACK bars. A turn needs SWING_SIDE
    bars after it, so the last few bars cannot be one yet."""
    h, lo = bars["high"], bars["low"]
    start = max(SWING_SIDE, len(bars) - SWING_LOOKBACK)
    highs, lows = [], []
    for i in range(start, len(bars) - SWING_SIDE):
        window = slice(i - SWING_SIDE, i + SWING_SIDE + 1)
        if h.iloc[i] >= h.iloc[window].max():
            highs.append((bars.index[i], float(h.iloc[i])))
        if lo.iloc[i] <= lo.iloc[window].min():
            lows.append((bars.index[i], float(lo.iloc[i])))
    return highs, lows


def _clusters(points):
    """Turns within CLUSTER of each other merged into one level: (mean price, count, latest date)."""
    out = []
    for date, price in sorted(points, key=lambda p: p[1]):
        if out and price <= out[-1]["lo"] * (1 + CLUSTER):
            c = out[-1]
            c["prices"].append(price)
            c["date"] = max(c["date"], date)
        else:
            out.append({"lo": price, "prices": [price], "date": date})
    return [(sum(c["prices"]) / len(c["prices"]), len(c["prices"]), c["date"]) for c in out]


def compute(bars, source="日线"):
    """The levels as {"date", "close", "source", "levels": [{group, name, price, distance, basis}]}, or None when the
    bars are too short (under 30 days)."""
    if bars is None or len(bars) < 30 or not {"high", "low", "close"} <= set(bars.columns):
        return None
    bars = bars.dropna(subset=["high", "low", "close"])
    if len(bars) < 30:
        return None
    last = float(bars["close"].iloc[-1])
    date = _day(bars.index[-1])
    out = []

    def add(group, name, price, basis):
        if price is not None and price > 0 and math.isfinite(price):
            out.append({"group": group, "name": name, "price": round(float(price), 2), "distance": float(price) / last - 1,
                        "basis": basis})

    # 前高前低
    for n in HIGH_LOW_WINDOWS:
        if len(bars) >= n:
            w = bars.iloc[-n:]
            hi, lo = w["high"].idxmax(), w["low"].idxmin()
            add("前高前低", "%d 日最高" % n, w["high"].max(), "近 %d 个交易日的最高价，出现在 %s" % (n, _day(hi)))
            add("前高前低", "%d 日最低" % n, w["low"].min(), "近 %d 个交易日的最低价，出现在 %s" % (n, _day(lo)))

    # 枢轴点: classic floor pivots from the latest bar
    h, lo, c = (float(bars[k].iloc[-1]) for k in ("high", "low", "close"))
    p = (h + lo + c) / 3
    basis = "按 %s 这根日线：最高 %.2f、最低 %.2f、收盘 %.2f；枢轴 P=(H+L+C)/3" % (date, h, lo, c)
    add("枢轴点", "枢轴 P", p, basis)
    add("枢轴点", "压力 R1", 2 * p - lo, basis + "，R1=2P−L")
    add("枢轴点", "压力 R2", p + (h - lo), basis + "，R2=P+(H−L)")
    add("枢轴点", "支撑 S1", 2 * p - h, basis + "，S1=2P−H")
    add("枢轴点", "支撑 S2", p - (h - lo), basis + "，S2=P−(H−L)")

    # 压力支撑: where the price turned several times
    highs, lows = _swings(bars)
    above = [x for x in _clusters(highs + lows) if x[0] > last * (1 + 1e-9)]
    below = [x for x in _clusters(highs + lows) if x[0] < last * (1 - 1e-9)]
    for price, count, when in sorted(above, key=lambda x: x[0])[:2]:
        add("压力支撑", "压力", price, "近 %d 个交易日里在此价附近（±%d%%）转折 %d 次，最近一次 %s" % (SWING_LOOKBACK, CLUSTER * 100, count, _day(when)))
    for price, count, when in sorted(below, key=lambda x: -x[0])[:2]:
        add("压力支撑", "支撑", price, "近 %d 个交易日里在此价附近（±%d%%）转折 %d 次，最近一次 %s" % (SWING_LOOKBACK, CLUSTER * 100, count, _day(when)))

    # ATR 通道: Wilder's average true range
    prev = bars["close"].shift(1)
    tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - prev).abs(), (bars["low"] - prev).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / ATR_DAYS, adjust=False, min_periods=ATR_DAYS).mean().iloc[-1])
    if math.isfinite(atr):
        basis = "ATR(%d)=$%.2f（Wilder 平均真实波幅，截至 %s），收盘 %.2f" % (ATR_DAYS, atr, date, last)
        for k in (1, 2):
            add("ATR 通道", "上轨 +%d×ATR" % k, last + k * atr, basis + " + %d×ATR" % k)
            add("ATR 通道", "下轨 −%d×ATR" % k, last - k * atr, basis + " − %d×ATR" % k)

    # 整数关口: the nearest round numbers either side
    step = _step(last)
    below_r = math.floor(last / step) * step
    if abs(below_r - last) < 1e-9:
        below_r -= step
    above_r = below_r + step if below_r + step > last else below_r + 2 * step
    basis = "现价 %.2f 所在价位的整数关口，间隔 $%d" % (last, step)
    add("整数关口", "下方整数", below_r, basis)
    add("整数关口", "上方整数", above_r, basis)

    return {"date": date, "close": last, "source": source, "levels": out}


def nearest(result):
    """The closest resistance and support among the swing-turn levels: (resistance, support), either may be None."""
    if not result:
        return None, None
    swing = [x for x in result["levels"] if x["group"] == "压力支撑"]
    up = [x for x in swing if x["name"] == "压力"]
    down = [x for x in swing if x["name"] == "支撑"]
    return (min(up, key=lambda x: x["price"]) if up else None), (max(down, key=lambda x: x["price"]) if down else None)
