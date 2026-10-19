"""The stock universe: Nasdaq-100 constituents from Nasdaq's own list, saved with the date they were taken.

The list is today's membership. Backtests over earlier years therefore only see companies that later made it
into the index (survivorship bias), which flatters every strategy run on it; compare strategies with each other
on the same universe, not with the index."""
import datetime as dt
import json
import logging
import re
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


# ---------------------------------------------------------------- point-in-time membership

HISTORY_URL = "https://en.wikipedia.org/w/index.php?title=Historical_components_of_the_Nasdaq-100&action=raw"
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def parse_changes(wikitext):
    """The changes table of the Wikipedia article: [{date, added, removed}], newest first as in the article.

    Each row has six cells: date, added ticker, added name, removed ticker, removed name, reason; either side
    can be empty."""
    start = wikitext.index('id="changes"')
    body = wikitext[start:wikitext.index("\n|}", start)]
    out = []
    for chunk in body.split("\n|-")[1:]:
        cells = []
        for line in chunk.split("\n"):
            if line.startswith("|") and not line.startswith("|}"):
                cells += line[1:].split("||")
            elif cells:
                cells[-1] += "\n" + line
        if len(cells) != 6:
            continue
        date = dt.datetime.strptime(cells[0].strip(), "%B %d, %Y").date()
        added, removed = cells[1].strip().upper(), cells[3].strip().upper()
        out.append({"date": date.isoformat(), "added": added if TICKER_RE.match(added) else "",
                    "removed": removed if TICKER_RE.match(removed) else ""})
    if len(out) < 100:
        raise DataSourceError("纳斯达克 100 调整记录解析出的行太少（%d 行），条目格式可能变了" % len(out))
    return out


def history_path():
    return get_settings().data_dir / "universe" / "nasdaq100_changes.json"


def load_history(refresh=False):
    """The changes table, fetched from Wikipedia (CC BY-SA) on first use or when refresh is set."""
    p = history_path()
    if refresh or not p.exists():
        req = urllib.request.Request(HISTORY_URL, headers={"User-Agent": "quantlab research (https://github.com/Wwz123Abc/quant-lab)"})
        try:
            with urllib.request.urlopen(req, timeout=get_settings().http_timeout) as r:
                text = r.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as e:
            raise DataSourceError("取不到纳斯达克 100 历史调整记录：%s" % e) from e
        changes = parse_changes(text)
        data = {"source": HISTORY_URL, "fetched": dt.date.today().isoformat(), "changes": changes}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return json.loads(p.read_text(encoding="utf-8"))


def members_at(day, current, changes):
    """Index members at the close of `day`: today's list with every later change undone.

    A change dated D takes effect before the open of D, so it is undone for any day before D."""
    day = str(day)[:10]
    members = set(current)
    for c in sorted(changes, key=lambda c: c["date"], reverse=True):
        if c["date"] <= day:
            break
        if c["added"]:
            members.discard(c["added"])
        if c["removed"]:
            members.add(c["removed"])
    return members


def ever_members(current, changes, since):
    """Every symbol that was in the index at some point since `since`."""
    syms = set(current)
    for c in changes:
        if c["date"] > str(since)[:10] and c["removed"]:
            syms.add(c["removed"])
    return sorted(syms)


def membership_mask(calendar, symbols, current, changes):
    """Wide table (date x symbol): True where the symbol was an index member at that date's close."""
    import pandas as pd

    cal = pd.DatetimeIndex(calendar)
    mask = pd.DataFrame(False, index=cal, columns=list(symbols))
    bounds = sorted({c["date"] for c in changes if c["date"] > str(cal[0].date())})
    starts = [cal[0]] + [pd.Timestamp(b) for b in bounds]
    for i, start in enumerate(starts):
        end = pd.Timestamp(bounds[i]) if i < len(bounds) else None
        rows = (cal >= start) & ((cal < end) if end is not None else True)
        if not rows.any():
            continue
        members = members_at(cal[rows][0].date(), current, changes)
        cols = [s for s in mask.columns if s in members]
        mask.loc[rows, cols] = True
    return mask
