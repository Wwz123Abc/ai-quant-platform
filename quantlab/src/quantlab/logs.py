"""Logging setup: readable text by default, one JSON object per line for log collectors."""
import json
import logging
import sys

from .config import get_settings


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
                 "logger": record.name, "message": record.getMessage()}
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup():
    s = get_settings()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if s.log_format == "json" else
                         logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(s.log_level.upper())
