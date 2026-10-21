"""SQLite connections with versioned schema migrations.

Each database lists its migrations in order; PRAGMA user_version records how many have been applied, so a
database created by an older version is brought up to date on first use and nothing is applied twice."""
import sqlite3
from pathlib import Path


def connect(path, migrations):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")  # readers do not block the daily writer
    version = con.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(migrations[version:], start=version + 1):
        try:  # one transaction per migration: it applies completely or not at all
            con.executescript("BEGIN;\n%s;\nPRAGMA user_version = %d;\nCOMMIT;" % (sql, i))
        except Exception:
            con.rollback()
            con.close()
            raise
    return con
