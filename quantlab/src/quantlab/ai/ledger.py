"""The AI run ledger: every analysis with its status, rating, reports, token usage and cost, and the monthly budget.

Costs are kept in USD, as providers bill them, and shown in CNY at the configured rate. A run is admitted only
if this month's spending plus the expected cost of everything already queued and of the new run stays within
the budget; the worker checks again just before it starts, so the limit holds even if runs were queued earlier."""
import datetime as dt
import json
import threading

from .. import storage
from ..config import get_settings
from ..errors import BudgetExceeded, Conflict, NotFound

MIGRATIONS = [
    """CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
                          status TEXT NOT NULL, source TEXT NOT NULL, rating TEXT, reports TEXT,
                          quick_model TEXT, deep_model TEXT, llm_calls INTEGER NOT NULL DEFAULT 0,
                          tokens_in INTEGER NOT NULL DEFAULT 0, tokens_cached INTEGER NOT NULL DEFAULT 0,
                          tokens_out INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0,
                          created TEXT NOT NULL, started TEXT, finished TEXT, error TEXT);
       CREATE INDEX runs_symbol_date ON runs (symbol, trade_date);
       CREATE INDEX runs_status ON runs (status);
       CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)""",
]
ACTIVE = ("queued", "running")
_admit = threading.Lock()  # admission checks and the insert happen as one step
COLS = ["id", "symbol", "trade_date", "status", "source", "rating", "reports", "quick_model", "deep_model", "llm_calls",
        "tokens_in", "tokens_cached", "tokens_out", "cost_usd", "created", "started", "finished", "error"]


def connect(path=None):
    return storage.connect(path or get_settings().data_dir / "ai.db", MIGRATIONS)


def utcnow():
    return dt.datetime.now(dt.UTC)


def iso(t):
    return t.isoformat(timespec="seconds")


def _row(r, with_reports=False):
    run = dict(zip(COLS, r, strict=True))
    run["reports"] = json.loads(run["reports"]) if with_reports and run["reports"] else None
    return run


def get(con, run_id, with_reports=True):
    r = con.execute("SELECT %s FROM runs WHERE id = ?" % ", ".join(COLS), (run_id,)).fetchone()
    if not r:
        raise NotFound("没有编号为 %s 的分析" % run_id)
    return _row(r, with_reports)


def runs(con, symbol=None, limit=50):
    sql, args = "SELECT %s FROM runs" % ", ".join(COLS), []
    if symbol:
        sql, args = sql + " WHERE symbol = ?", [symbol]
    rows = con.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    return [_row(r) for r in rows]


def month_start(now):
    return now.astimezone(dt.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def spent_usd(con, now):
    """What runs started this calendar month (UTC) cost, failed ones included: their tokens were billed too."""
    return con.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM runs WHERE started >= ?", (iso(month_start(now)),)).fetchone()[0]


def estimate_usd(con, s=None):
    """Expected cost of one run: the average of the last ten completed runs, or the configured first guess."""
    s = s or get_settings()
    costs = [r[0] for r in con.execute("SELECT cost_usd FROM runs WHERE status = 'done' AND cost_usd > 0 ORDER BY id DESC LIMIT 10")]
    return sum(costs) / len(costs) if costs else s.ai_first_estimate_cny / s.ai_usd_cny


def budget(con, now=None, s=None):
    s, now = s or get_settings(), now or utcnow()
    spent, est = spent_usd(con, now), estimate_usd(con, s)
    queued = con.execute("SELECT COUNT(*) FROM runs WHERE status = 'queued'").fetchone()[0]
    running = con.execute("SELECT COUNT(*) FROM runs WHERE status = 'running'").fetchone()[0]
    return {
        "month": month_start(now).strftime("%Y-%m"), "budget_cny": s.ai_budget_cny, "usd_cny": s.ai_usd_cny,
        "spent_cny": spent * s.ai_usd_cny, "estimate_cny": est * s.ai_usd_cny,
        "committed_cny": (spent + est * (queued + running)) * s.ai_usd_cny,
        "remaining_cny": s.ai_budget_cny - (spent + est * (queued + running)) * s.ai_usd_cny,
        "queued": queued, "running": running,
    }


def check_budget(con, now=None, s=None, extra_runs=1):
    """Raises unless this month's spending, the queued runs and extra_runs more all fit in the budget."""
    b = budget(con, now, s)
    need = b["committed_cny"] + b["estimate_cny"] * extra_runs
    if need > b["budget_cny"] + 1e-9:
        raise BudgetExceeded("本月 AI 预算 ¥%.0f 不够了：已用 ¥%.2f，排队中的任务预计 ¥%.2f，再跑一次预计 ¥%.2f" % (
            b["budget_cny"], b["spent_cny"], b["committed_cny"] - b["spent_cny"], b["estimate_cny"] * extra_runs))
    return b


def enqueue(con, symbol, trade_date, source="manual", now=None, s=None):
    """Queues an analysis of symbol at the close of trade_date. One run per symbol and day, unless earlier ones failed."""
    s, now = s or get_settings(), now or utcnow()
    with _admit:
        return _enqueue(con, symbol, trade_date, source, now, s)


def _enqueue(con, symbol, trade_date, source, now, s):
    dup = con.execute("SELECT id, status FROM runs WHERE symbol = ? AND trade_date = ? AND status IN ('queued', 'running', 'done')",
                      (symbol, trade_date)).fetchone()
    if dup:
        raise Conflict("%s 在 %s 收盘后的分析已经%s（编号 %d）" % (
            symbol, trade_date, {"queued": "在排队", "running": "在运行", "done": "完成"}[dup[1]], dup[0]))
    with con:
        check_budget(con, now, s)
        cur = con.execute("INSERT INTO runs (symbol, trade_date, status, source, quick_model, deep_model, created)"
                          " VALUES (?, ?, 'queued', ?, ?, ?, ?)", (symbol, trade_date, source, s.ai_quick_model, s.ai_deep_model, iso(now)))
    return cur.lastrowid


def claim_next(con, now=None):
    """Marks the oldest queued run as running and returns it, or None.

    Several processes may share the ledger (the service and the command line): a run belongs to whichever one's
    update actually changed it from queued to running, so no run is ever started twice."""
    while True:
        r = con.execute("SELECT id FROM runs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
        if not r:
            return None
        with con:
            won = con.execute("UPDATE runs SET status = 'running', started = ? WHERE id = ? AND status = 'queued'",
                              (iso(now or utcnow()), r[0])).rowcount
        if won:
            return get(con, r[0], with_reports=False)


def _usage(usage):
    return (usage["llm_calls"], usage["tokens_in"], usage["tokens_cached"], usage["tokens_out"], usage["cost_usd"])


def finish(con, run_id, rating, reports, usage, now=None):
    with con:
        con.execute("UPDATE runs SET status = 'done', rating = ?, reports = ?, llm_calls = ?, tokens_in = ?, tokens_cached = ?,"
                    " tokens_out = ?, cost_usd = ?, finished = ? WHERE id = ?",
                    (rating, json.dumps(reports, ensure_ascii=False), *_usage(usage), iso(now or utcnow()), run_id))


def fail(con, run_id, error, usage=None, now=None):
    usage = usage or {"llm_calls": 0, "tokens_in": 0, "tokens_cached": 0, "tokens_out": 0, "cost_usd": 0.0}
    with con:
        con.execute("UPDATE runs SET status = 'failed', error = ?, llm_calls = ?, tokens_in = ?, tokens_cached = ?, tokens_out = ?,"
                    " cost_usd = ?, started = COALESCE(started, ?), finished = ? WHERE id = ?",
                    (str(error)[:1000], *_usage(usage), iso(now or utcnow()), iso(now or utcnow()), run_id))


def recover(con, now=None):
    """After a restart, runs left 'running' were cut off: mark them failed rather than run (and pay for) them again."""
    ids = [r[0] for r in con.execute("SELECT id FROM runs WHERE status = 'running'")]
    for i in ids:
        fail(con, i, "服务重启，这次分析被中断（已用的 token 没有记录）", now=now)
    return ids


def active(con, symbol=None, trade_date=None):
    sql, args = "SELECT COUNT(*) FROM runs WHERE status IN ('queued', 'running')", []
    if symbol:
        sql, args = sql + " AND symbol = ?", [symbol]
    if trade_date:
        sql, args = sql + " AND trade_date = ?", [*args, trade_date]
    return con.execute(sql, args).fetchone()[0]


def ratings(con, symbol):
    """(trade_date, rating) of every completed analysis of symbol, oldest first."""
    return con.execute("SELECT trade_date, rating FROM runs WHERE symbol = ? AND status = 'done' ORDER BY trade_date, id",
                       (symbol,)).fetchall()


def last_analysis_date(con, symbol):
    r = con.execute("SELECT MAX(trade_date) FROM runs WHERE symbol = ? AND status IN ('queued', 'running', 'done')", (symbol,)).fetchone()
    return r[0]


def get_meta(con, key):
    r = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return r[0] if r else None


def set_meta(con, key, value):
    with con:
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
