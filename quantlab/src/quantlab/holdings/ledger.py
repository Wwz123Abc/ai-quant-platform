"""Your real holdings, read from the stock ledger (stock.wwz.cool) with its read-only key.

The key (LEDGER_TOKEN) is read from the environment or .env, like the other provider keys; it can only read
trades, Treasuries and deposits, and cash. Each sync is saved, so analysis also works when the ledger cannot
be reached."""
import datetime as dt
import json
import os
import urllib.error
import urllib.request

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


def fixed_usd(snapshot):
    """Treasuries and deposits at cost: they are held to maturity and never traded by the policy."""
    total = 0.0
    for f in snapshot.get("fixed", []):
        if f.get("kind") == "bond":
            v = float(f.get("face") or 0) * float(f.get("price") or 0) / 100 + float(f.get("accruedPaid") or 0)
        else:
            v = float(f.get("principal") or 0)
        total += usd(v, f.get("currency") or "USD")
    return total
