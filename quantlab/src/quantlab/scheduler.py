"""The daily job, run inside `quantlab serve`: queue the AI analyses that are due, wait for them, then record a
decision for every paper account.

It runs once per Beijing day, Tuesday to Saturday, after the configured time (the US market has closed by then).
With Chinese prices it first waits until Tencent's daily bars have the latest finished session (they arrive some
hours after the close), checking every half hour; from the give-up time it runs anyway and notes how old the bars
are. Every symbol is downloaded afresh once per run, so no bar cached before the close is used.
If the service was not running at that time, the job runs as soon as it starts; paper accounts catch up on any
days they missed. AI analyses are only ever made for the latest close, never back-filled."""
import datetime as dt
import json
import logging
import threading
from zoneinfo import ZoneInfo

from .ai import engine, ledger
from .config import get_settings
from .core import data, paper
from .errors import QuantLabError

log = logging.getLogger(__name__)
BEIJING = ZoneInfo("Asia/Shanghai")
RUN_DAYS = {1, 2, 3, 4, 5}  # Tuesday to Saturday in Beijing: the morning after each US trading day
PROBE = "QQQ"  # the symbol whose bars tell whether the latest close has arrived
RETRY = dt.timedelta(minutes=30)


def is_due(now, last_run, at):
    local = now.astimezone(BEIJING)
    return local.weekday() in RUN_DAYS and local.strftime("%H:%M") >= at and last_run != local.date().isoformat()


def queue_due_analyses(paper_con, ai_con, load, now=None):
    """Queues an analysis of each AI account's symbols whose last one is at least every_days before the latest close."""
    queued, notes = [], []
    if not (engine.available() and engine.configured()):
        return queued, ["AI 没有安装或没有配置密钥，跳过 AI 分析"]
    for acct in paper.accounts(paper_con):
        if acct["strategy"] != "ai_rating":
            continue
        for sym in acct["symbols"]:
            try:
                latest = load(sym).index[-1].date()
                last = ledger.last_analysis_date(ai_con, sym)
                if last and (latest - dt.date.fromisoformat(last)).days < acct["params"]["every_days"]:
                    continue
                queued.append(ledger.enqueue(ai_con, sym, latest.isoformat(), "schedule", now))
            except QuantLabError as e:  # duplicate, over budget, no data: note it and go on
                notes.append("%s：%s" % (sym, e))
    return queued, notes


def record_all(paper_con, load):
    out = {}
    for acct in paper.accounts(paper_con):
        try:
            out[acct["name"]] = len(paper.record(paper_con, acct, load))
        except QuantLabError as e:
            out[acct["name"]] = str(e)
    return out


def market_date():
    """The date of the latest finished US session per Tencent's real-time quote, or None when it cannot be told
    (Yahoo prices, or the quote is unreachable); the job then does not wait."""
    if get_settings().price_source != "cn":
        return None
    from .core import cn_data
    try:
        fields = cn_data.tencent_quote(PROBE)
    except QuantLabError:
        return None
    stamp = fields[30] if len(fields) > 30 else ""
    return stamp.replace("/", "-")[:10] or None


class Scheduler:
    def __init__(self, worker, load=None, clock=None, market=None):
        self.worker = worker
        self.load = load or data.load
        self.clock = clock or (lambda: dt.datetime.now(dt.UTC))
        self.market = market or market_date
        self._next_check = None
        self._probe = None  # (bars end, market closed) from the last check
        self._busy = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("daily job failed")
            self._stop.wait(60)

    def status(self):
        con = ledger.connect()
        try:
            last = ledger.get_meta(con, "daily_last")
            result = ledger.get_meta(con, "daily_result")
        finally:
            con.close()
        s = get_settings()
        return {"enabled": s.schedule_enabled, "time": s.schedule_time, "last_run": last,
                "last_result": json.loads(result) if result else None, "running": self._busy.locked()}

    def tick(self):
        con = ledger.connect()
        try:
            last = ledger.get_meta(con, "daily_last")
        finally:
            con.close()
        now = self.clock()
        if is_due(now, last, get_settings().schedule_time) and self.data_ready(now):
            self.run_daily(now)

    def data_ready(self, now):
        """True once the probe's daily bars reach the latest finished session, or from the give-up time on."""
        if now.astimezone(BEIJING).strftime("%H:%M") >= get_settings().schedule_give_up:
            return True
        if self._next_check and now < self._next_check:
            return False
        want = self.market()
        try:
            have = self.load(PROBE, refresh=True).index[-1].date().isoformat()
        except QuantLabError:
            return True  # no way to tell: run as before
        self._probe = (have, want)
        if not want or have >= want:
            return True
        self._next_check = now + RETRY
        log.info("daily job waits: %s bars end %s, the market closed %s", PROBE, have, want)
        return False

    def run_daily(self, now=None, wait_hours=3):
        """Runs the job now. Returns its result, or None if it was already running."""
        if not self._busy.acquire(blocking=False):
            return None
        try:
            now = now or self.clock()
            seen = set()

            def load(sym, refresh=False):  # each symbol downloaded afresh once per run
                key = data.normalize(sym)
                first = key not in seen
                seen.add(key)
                return self.load(sym, refresh=refresh or first)

            ai_con, paper_con = ledger.connect(), paper.connect()
            try:
                queued, notes = queue_due_analyses(paper_con, ai_con, load, now)
                if self._probe and self._probe[1] and self._probe[0] < self._probe[1]:
                    notes.append("到 %s 腾讯日线还没有 %s 的收盘价，这次按 %s 的收盘价运行" % (
                        get_settings().schedule_give_up, self._probe[1], self._probe[0]))
                if queued:
                    self.worker.wake()
                    if not self.worker.wait_idle(wait_hours * 3600):
                        notes.append("AI 分析超过 %d 小时还没跑完，先记录其他决定" % wait_hours)
                recorded = record_all(paper_con, load)
                trading = None  # paused unless QUANTLAB_TRADING_DAILY is set
                if get_settings().trading_daily:
                    try:
                        from .trading import daily as trading_daily
                        trading = trading_daily.run(load)
                    except Exception as e:  # trading problems must not stop the rest of the job; they are reported
                        log.exception("trading step failed")
                        trading, notes = {"error": str(e)}, [*notes, "自动交易步骤出错：%s" % e]
                holdings = None
                try:
                    from .holdings import ledger as hledger
                    from .holdings import review as hreview
                    if hledger.configured():
                        r = hreview.run(load=load)
                        holdings = {"review": r["id"], "proposals": len(r["proposals"]), "reminders": len(r["reminders"])}
                except Exception as e:  # a ledger problem must not stop the rest of the job
                    log.exception("holdings review failed")
                    holdings, notes = {"error": str(e)}, [*notes, "持仓检查出错：%s" % e]
                result = {"at": now.isoformat(timespec="seconds"), "queued": queued, "recorded": recorded,
                          "trading": trading, "holdings": holdings, "notes": notes}
                ledger.set_meta(ai_con, "daily_last", now.astimezone(BEIJING).date().isoformat())
                self._next_check = self._probe = None
                ledger.set_meta(ai_con, "daily_result", json.dumps(result, ensure_ascii=False))
            finally:
                ai_con.close()
                paper_con.close()
            log.info("daily job: %s", result)
            return result
        finally:
            self._busy.release()
