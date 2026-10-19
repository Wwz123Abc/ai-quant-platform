"""The stock universe: Nasdaq-100 constituents from Nasdaq's own list, saved with the date they were taken.

The list is today's membership. Backtests over earlier years therefore only see companies that later made it
into the index (survivorship bias), which flatters every strategy run on it; compare strategies with each other
on the same universe, not with the index."""
import json
import logging
import urllib.error
import urllib.request

from ..config import get_settings
from ..core.data import normalize
from ..errors import DataSourceError

log = logging.getLogger(__name__)
URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"


def path():
    return get_settings().data_dir / "universe" / "nasdaq100.json"


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=get_settings().http_timeout) as r:
            body = json.load(r)
        rows = body["data"]["data"]["rows"]
        as_of = body["data"].get("date") or ""
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, TypeError) as e:
        raise DataSourceError("取不到纳斯达克 100 成分股名单：%s" % e) from e
    symbols = sorted({normalize(r["symbol"]) for r in rows if r.get("symbol")})
    if len(symbols) < 90:
        raise DataSourceError("纳斯达克 100 成分股名单不完整（只有 %d 只）" % len(symbols))
    return {"index": "Nasdaq-100", "as_of": as_of, "symbols": symbols}


def load(refresh=False):
    """The saved list, fetched first if there is none yet or refresh is set."""
    p = path()
    if refresh or not p.exists():
        data = fetch()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("saved %d Nasdaq-100 symbols as of %s", len(data["symbols"]), data["as_of"])
    return json.loads(p.read_text(encoding="utf-8"))
