"""The AI worker: runs queued analyses one at a time in a background thread.

One at a time keeps spending predictable and stays within provider rate limits. Every outcome is written to the
ledger, including the tokens a failed run had already used."""
import logging
import threading

from ..config import get_settings
from ..errors import BudgetExceeded
from . import engine, ledger

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, analyze=None, connect=None):
        self.analyze = analyze or engine.analyze
        self.connect = connect or ledger.connect
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._thread = None

    def start(self):
        con = self.connect()
        try:
            stale = ledger.recover(con)
        finally:
            con.close()
        if stale:
            log.warning("marked %d interrupted AI runs as failed: %s", len(stale), stale)
        self._thread = threading.Thread(target=self._loop, name="ai-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout=5):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def wake(self):
        self._idle.clear()
        self._wake.set()

    def wait_idle(self, timeout=None):
        """Blocks until the queue is empty and nothing is running (or the timeout passes). Returns whether it is idle."""
        return self._idle.wait(timeout)

    def _loop(self):
        while not self._stop.is_set():
            self._wake.wait(timeout=10)
            self._wake.clear()
            try:
                while not self._stop.is_set() and self.run_next():
                    pass
            except Exception:  # keep the worker alive; the failure is logged and the next wake retries
                log.exception("AI worker loop failed")
            self._idle.set()

    def run_next(self):
        """Claims and runs the oldest queued analysis. Returns False when there was none."""
        con = self.connect()
        try:
            run = ledger.claim_next(con)
            if not run:
                return False
            self._run(con, run)
            return True
        finally:
            con.close()

    def _run(self, con, run):
        s = get_settings()
        try:
            # Admitted earlier; check again with the spending since then. This run is already counted as running.
            ledger.check_budget(con, s=s, extra_runs=0)
        except BudgetExceeded as e:
            ledger.fail(con, run["id"], "没有运行：%s" % e)
            return
        usage = engine.Usage()
        log.info("AI run %d: %s at %s", run["id"], run["symbol"], run["trade_date"])
        try:
            rating, reports = self.analyze(run["symbol"], run["trade_date"], usage, s)
        except Exception as e:
            log.warning("AI run %d failed: %s", run["id"], e)
            ledger.fail(con, run["id"], "分析失败：%s" % e, usage.totals(s.ai_prices))
            return
        totals = usage.totals(s.ai_prices)
        ledger.finish(con, run["id"], rating, reports, totals)
        log.info("AI run %d: %s, %d calls, $%.4f", run["id"], rating, totals["llm_calls"], totals["cost_usd"])
