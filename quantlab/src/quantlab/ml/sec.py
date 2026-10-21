"""SEC EDGAR company facts (XBRL), cached on disk.

SEC asks every client to identify itself with a contact in the User-Agent (SEC_EDGAR_USER_AGENT in .env) and
to stay under ten requests a second."""
import json
import logging
import os
import time
import urllib.error
import urllib.request

from ..config import get_settings
from ..errors import DataSourceError, NotFound

log = logging.getLogger(__name__)
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json"
MAX_AGE = 7 * 86400
_last_call = [0.0]


def _agent():
    try:
        from dotenv import load_dotenv
        load_dotenv(".env", override=False)
    except ImportError:
        pass
    return os.environ.get("SEC_EDGAR_USER_AGENT") or "quantlab research https://github.com/Wwz123Abc/quant-lab"


def _get(url):
    wait = 0.15 - (time.time() - _last_call[0])  # under ten requests a second
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": _agent(), "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFound(url) from e
        raise DataSourceError("SEC 返回错误 %d：%s" % (e.code, url)) from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise DataSourceError("连不上 SEC：%s" % e) from e


def _cache(name):
    d = get_settings().data_dir / "sec"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def cik_map(refresh=False):
    """Ticker -> CIK for companies filing with SEC today. Delisted companies are not in it."""
    p = _cache("company_tickers.json")
    if refresh or not p.exists() or time.time() - p.stat().st_mtime > MAX_AGE:
        p.write_text(json.dumps(_get(TICKERS_URL)), encoding="utf-8")
    rows = json.loads(p.read_text(encoding="utf-8")).values()
    return {r["ticker"].upper().replace("-", "."): int(r["cik_str"]) for r in rows}


def company_facts(cik, refresh=False):
    p = _cache("CIK%010d.json" % cik)
    if refresh or not p.exists() or time.time() - p.stat().st_mtime > MAX_AGE:
        p.write_text(json.dumps(_get(FACTS_URL % cik)), encoding="utf-8")
    return json.loads(p.read_text(encoding="utf-8"))
