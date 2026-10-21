"""Logging setup: readable text by default, one JSON object per line for log collectors, optionally to a file."""
import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from .config import get_settings


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
                 "logger": record.name, "message": record.getMessage()}
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup(log_file=None):
    """Sends logs to stderr, or to log_file (rotated at 5 MB, five old files kept) when given.

    Without a console (pythonw, as when started at login) there is no stderr; output that would go there is
    written to the log file too, or discarded."""
    s = get_settings()
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)
    if sys.stdout is None or sys.stderr is None:
        sink = open(path, "a", encoding="utf-8") if log_file else open(os.devnull, "w")  # noqa: SIM115 (lives as long as the process)
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    handler.setFormatter(JsonFormatter() if s.log_format == "json" else
                         logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(s.log_level.upper())
