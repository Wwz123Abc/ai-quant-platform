"""The daily job, run inside `quantlab serve`: queue the AI analyses that are due, wait for them, then record a
decision for every paper account.

It runs once per Beijing day, Tuesday to Saturday, after the configured time (the US market has closed by then).
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


class Scheduler:
    def __init__(self, worker, load=None, clock=None):
        self.worker = worker
        self.load = load or data.load
        self.clock = clock or (lambda: dt.datetime.now(dt.UTC))
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
        if is_due(now, last, get_settings().schedule_time):
            self.run_daily(now)

    def run_daily(self, now=None, wait_hours=3):
        """Runs the job now. Returns its result, or None if it was already running."""
        if not self._busy.acquire(blocking=False):
            return None
        try:
            now = now or self.clock()
            ai_con, paper_con = ledger.connect(), paper.connect()
            try:
                queued, notes = queue_due_analyses(paper_con, ai_con, self.load, now)
                if queued:
                    self.worker.wake()
                    if not self.worker.wait_idle(wait_hours * 3600):
                        notes.append("AI 分析超过 %d 小时还没跑完，先记录其他决定" % wait_hours)
                recorded = record_all(paper_con, self.load)
                try:
                    from .trading import daily as trading_daily
                    trading = trading_daily.run(self.load)
                except Exception as e:  # trading problems must not stop the rest of the job; they are reported
                    log.exception("trading step failed")
                    trading, notes = {"error": str(e)}, [*notes, "自动交易步骤出错：%s" % e]
                holdings = None
                try:
                    from .holdings import ledger as hledger
                    from .holdings import review as hreview
                    if hledger.configured():
                        r = hreview.run(load=self.load)
                        holdings = {"review": r["id"], "proposals": len(r["proposals"]), "reminders": len(r["reminders"])}
                except Exception as e:  # a ledger problem must not stop the rest of the job
                    log.exception("holdings review failed")
                    holdings, notes = {"error": str(e)}, [*notes, "持仓检查出错：%s" % e]
                result = {"at": now.isoformat(timespec="seconds"), "queued": queued, "recorded": recorded,
                          "trading": trading, "holdings": holdings, "notes": notes}
                ledger.set_meta(ai_con, "daily_last", now.astimezone(BEIJING).date().isoformat())
                ledger.set_meta(ai_con, "daily_result", json.dumps(result, ensure_ascii=False))
            finally:
                ai_con.close()
                paper_con.close()
            log.info("daily job: %s", result)
            return result
        finally:
            self._busy.release()
