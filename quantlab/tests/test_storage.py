import sqlite3

import pytest

from quantlab import storage
from quantlab.core import data, paper
from quantlab.errors import InvalidInput


def test_migrations_apply_once_and_in_order(tmp_path):
    db = tmp_path / "x.db"
    steps = ["CREATE TABLE a (x INTEGER)", "ALTER TABLE a ADD COLUMN y TEXT"]
    con = storage.connect(db, steps[:1])
    con.execute("INSERT INTO a VALUES (1)")
    con.commit()
    con.close()
    con = storage.connect(db, steps)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    assert con.execute("SELECT x, y FROM a").fetchall() == [(1, None)]
    con.close()
    storage.connect(db, steps).close()  # nothing left to apply


def test_failed_migration_leaves_database_unchanged(tmp_path):
    db = tmp_path / "x.db"
    storage.connect(db, ["CREATE TABLE a (x INTEGER)"]).close()
    with pytest.raises(sqlite3.Error):
        storage.connect(db, ["CREATE TABLE a (x INTEGER)", "CREATE TABLE b (x INTEGER); CREATE TABLE a (x)"])
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    assert con.execute("SELECT name FROM sqlite_master WHERE name = 'b'").fetchall() == []
    con.close()


def test_existing_paper_database_is_adopted(tmp_path):
    """Databases created before migrations existed (user_version 0) keep their accounts."""
    db = tmp_path / "paper.db"
    con = sqlite3.connect(db)
    con.executescript(paper.MIGRATIONS[0])
    con.execute("INSERT INTO accounts VALUES ('核心', 'ma_cross', '{\"fast\": 50, \"slow\": 200}', '[\"QQQM\"]', 10000, 0, 5,"
                " '2026-10-16', '2026-10-19T00:00:00+00:00')")
    con.commit()
    con.close()
    con = paper.connect(db)
    assert [a["name"] for a in paper.accounts(con)] == ["核心"]
    assert con.execute("PRAGMA user_version").fetchone()[0] == len(paper.MIGRATIONS)
    con.close()


@pytest.mark.parametrize("raw,ok", [("qqqm", "QQQM"), (" 700 ", "700"), ("BRK.B", "BRK.B"), ("../x", None), ("A B", None), ("", None)])
def test_symbol_validation(raw, ok):
    if ok:
        assert data.normalize(raw) == ok
    else:
        with pytest.raises(InvalidInput):
            data.normalize(raw)
