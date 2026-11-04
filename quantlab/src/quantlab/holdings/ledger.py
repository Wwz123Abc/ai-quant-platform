"""Your real holdings, read from the stock ledger (stock.wwz.cool) with its read-only key.

The key (LEDGER_TOKEN) is read from the environment or .env, like the other provider keys; it can only read
trades, Treasuries and deposits, and cash. Each sync is saved, so analysis also works when the ledger cannot
be reached."""
import calendar
import datetime as dt
import json
import math
import os
import re
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from ..config import get_settings
from ..errors import DataSourceError, Unavailable


def _token():
    try:
        from dotenv import load_dotenv
        load_dotenv(".env", override=False)
    except ImportError:
        pass
    return os.environ.get("LEDGER_TOKEN", "").strip()


def configured():
    return bool(_token())


def snapshot_path():
    return get_settings().data_dir / "holdings" / "snapshot.json"


def fetch():
    token = _token()
    if not token:
        raise Unavailable("没有配置账本的只读访问密钥：在账本的“账号”页面创建，然后在 .env 里加一行 LEDGER_TOKEN=密钥")
    url = get_settings().ledger_url.rstrip("/") + "/api/holdings"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "User-Agent": "quantlab"})
    try:
        with urllib.request.urlopen(req, timeout=get_settings().http_timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise Unavailable("账本拒绝了只读访问密钥：可能已停用或被重新创建，请在账本里重新创建并更新 .env") from e
        raise DataSourceError("账本返回错误 %d" % e.code) from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise DataSourceError("连不上账本：%s" % getattr(e, "reason", e)) from e


def sync():
    """Fetches the holdings and saves them with the time they were read."""
    data = fetch()
    data["synced"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    p = snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def load(refresh=False):
    """The last saved holdings, synced first if there are none or refresh is set (and a key is configured)."""
    p = snapshot_path()
    if refresh or not p.exists():
        return sync()
    return json.loads(p.read_text(encoding="utf-8"))


def positions(snapshot):
    """Shares and average cost per stock from the trades, oldest first. A sale reduces shares at the average cost."""
    pos = {}
    for t in sorted(snapshot.get("trades", []), key=lambda t: (t.get("date", ""), t.get("createdAt", ""))):
        sym = str(t.get("symbol", "")).upper()
        shares, price, fees = float(t.get("shares") or 0), float(t.get("price") or 0), float(t.get("fees") or 0)
        p = pos.setdefault(sym, {"symbol": sym, "currency": t.get("currency") or "USD", "shares": 0.0, "cost": 0.0})
        if t.get("side") == "buy":
            p["shares"] += shares
            p["cost"] += shares * price + fees
        elif t.get("side") == "sell" and p["shares"] > 0:
            p["cost"] *= max(0.0, 1 - shares / p["shares"])
            p["shares"] = max(0.0, p["shares"] - shares)
    out = []
    for p in pos.values():
        if p["shares"] > 1e-9:
            p["avg_cost"] = p["cost"] / p["shares"]
            out.append(p)
    return sorted(out, key=lambda p: p["symbol"])


def usd(amount, currency):
    return amount if currency == "USD" else amount / get_settings().hkd_per_usd


def cash_usd(snapshot):
    return sum(usd(float(c.get("amount") or 0), c.get("currency") or "USD") for c in snapshot.get("cash", []))


# ---- Treasuries and deposits, valued by the ledger's rules (ledger/static/calc.js; both are checked against
# ledger/tests/fixtures/contract.json). Dates are YYYY-MM-DD strings, compared as text.

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def today_us():
    """Today in New York: the ledger values Treasuries and deposits on the US market day."""
    return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def day_diff(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def add_months(s, n):
    """Month arithmetic that keeps month-end dates at month end (6/30 - 6 months = 12/31), as Treasury coupon dates do."""
    d = dt.date.fromisoformat(s)
    eom = d.day == calendar.monthrange(d.year, d.month)[1]
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = calendar.monthrange(y, m)[1]
    return dt.date(y, m, last if eom else min(d.day, last)).isoformat()


def coupons(b):
    """Coupon dates counted back from maturity: the first is on or before settlement, the last is maturity."""
    step, out = int(12 / (b["freq"] or 2)), []
    for i in range(400):
        c = add_months(b["maturity"], -i * step)
        out.insert(0, c)
        if c <= b["settle"]:
            break
    return out


def bond_at(b, d, mark=None):
    """Hold-to-maturity value: the clean price pulls straight-line to par, plus the accrued coupon; a reference
    price replaces the clean part. None before settlement; value 0 once matured."""
    if d < b["settle"]:
        return None
    if d >= b["maturity"]:
        return {"value": 0.0, "matured": True}
    face, cs = b["face"], coupons(b)
    cpn, clean_cost = face * b["coupon"] / 100 / (b["freq"] or 2), face * b["price"] / 100
    k = 0
    while k + 1 < len(cs) and cs[k + 1] <= d:
        k += 1
    accrued = cpn * day_diff(cs[k], d) / day_diff(cs[k], cs[k + 1])
    clean = face * mark / 100 if mark is not None else clean_cost + (face - clean_cost) * day_diff(b["settle"], d) / day_diff(b["settle"], b["maturity"])
    return {"value": clean + accrued, "matured": False}


def deposit_at(t, d):
    """A time deposit: its interest spread evenly over the days of the term. None before it starts; 0 once matured."""
    if d < t["start"]:
        return None
    if d >= t["maturity"]:
        return {"value": 0.0, "matured": True}
    return {"value": t["principal"] + t["interest"] * day_diff(t["start"], d) / day_diff(t["start"], t["maturity"]), "matured": False}


def fixed_on(f, d):
    """One Treasury or deposit on date d, in its own currency (LedgerCalc.fixedOn): None before it starts, value 0
    once matured, when the money is back in savings."""
    if f["kind"] != "bond":
        return deposit_at(f, d)
    mark = f["markPrice"] if f["markPrice"] and f["markPrice"] > 0 and f["markDate"] and d >= f["markDate"] else None
    return bond_at(f, d, mark)


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def fixed_items(snapshot):
    """The Treasuries and deposits as the ledger's home page reads them: numbers parsed, defaults filled in, and
    records it could not value left out."""
    out = []
    for x in snapshot.get("fixed", []):
        bond = x.get("kind") == "bond"
        f = {"name": str(x.get("name") or ("国债" if bond else "定期存款")), "kind": "bond" if bond else "deposit",
             "currency": "HKD" if x.get("currency") == "HKD" else "USD", "maturity": x.get("maturity"),
             "start": x.get("start"), "principal": _num(x.get("principal")), "interest": _num(x.get("interest")),
             "settle": x.get("settle"), "face": _num(x.get("face")), "price": _num(x.get("price")), "coupon": _num(x.get("coupon")),
             "freq": _num(x.get("freq")) or 2, "accruedPaid": _num(x.get("accruedPaid")) or 0,
             "markPrice": _num(x.get("markPrice")), "markDate": x.get("markDate") or ""}
        dated = all(DATE_RE.match(str(f[k] or "")) for k in ("maturity", "settle" if bond else "start"))
        sane = ((f["face"] or 0) > 0 and (f["price"] or 0) > 0 and (f["coupon"] if f["coupon"] is not None else -1) >= 0) if bond else (
            (f["principal"] or 0) > 0 and (f["interest"] if f["interest"] is not None else -1) >= 0)
        if dated and sane:
            out.append(f)
    return out


def fixed_status(snapshot, day=None):
    """Each Treasury and deposit on day (default today in New York): its value in dollars (None before it starts,
    0 once matured) and days to maturity."""
    day = day or today_us()
    out = []
    for f in fixed_items(snapshot):
        r = fixed_on(f, day)
        out.append({"name": f["name"], "kind": f["kind"], "currency": f["currency"], "maturity": f["maturity"],
                    "started": r is not None, "matured": bool(r and r["matured"]), "days_left": day_diff(day, f["maturity"]),
                    "value": usd(r["value"], f["currency"]) if r else None})
    return out


def fixed_usd(snapshot, day=None):
    """Treasuries and deposits valued as the ledger values them: held to maturity with interest accrued to day; the
    policy never trades them. Matured ones are back in savings and count no more."""
    return sum(f["value"] or 0.0 for f in fixed_status(snapshot, day))
