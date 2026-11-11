import sqlite3
from pathlib import Path

from app import db
from app.db import connection

TABLES = {"users_profile", "watchlist", "positions", "trades", "portfolio_snapshots", "chat_messages"}


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_init_creates_parent_dir_tables_and_seed(db_path):
    assert db_path.exists()
    assert TABLES <= _tables(db_path)
    assert db.get_cash() == 10000.0
    assert [w["ticker"] for w in db.get_watchlist()] == db.DEFAULT_WATCHLIST
    assert db.get_positions() == []


def test_init_is_idempotent(db_path):
    db.set_cash(123.0)
    db.remove_watchlist_ticker("AAPL")
    db.init_db(db_path)
    db.init_db(db_path)
    assert db.get_cash() == 123.0
    tickers = [w["ticker"] for w in db.get_watchlist()]
    assert "AAPL" not in tickers and len(tickers) == 9


def test_emptied_watchlist_is_not_reseeded(db_path):
    for t in db.DEFAULT_WATCHLIST:
        db.remove_watchlist_ticker(t)
    db.init_db(db_path)
    assert db.get_watchlist() == []


def test_env_path_used_by_default(tmp_path, monkeypatch):
    path = tmp_path / "envdir" / "x.db"
    monkeypatch.setenv("FINALLY_DB_PATH", str(path))
    connection.reset_for_tests()
    try:
        assert db.get_db_path() == path
        assert db.get_cash() == 10000.0  # lazy init on first use
        assert path.exists()
    finally:
        connection.reset_for_tests()


def test_default_path_is_repo_db_dir(monkeypatch):
    monkeypatch.delenv("FINALLY_DB_PATH", raising=False)
    connection.reset_for_tests()
    repo = Path(connection.__file__).resolve().parents[3]
    assert db.get_db_path() == repo / "db" / "finally.db"


def test_lazy_reinit_if_file_deleted(db_path):
    db.set_cash(5.0)
    db_path.unlink()
    for suffix in ("-wal", "-shm"):
        p = db_path.with_name(db_path.name + suffix)
        if p.exists():
            p.unlink()
    assert db.get_cash() == 10000.0
    assert len(db.get_watchlist()) == 10


def test_wal_mode(db_path):
    with db.get_connection() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_unique_constraints(db_path):
    with db.get_connection() as c:
        c.execute(
            "INSERT INTO positions (id, ticker, quantity, avg_cost, updated_at) VALUES ('a','X',1,1,'t')"
        )
        try:
            c.execute(
                "INSERT INTO positions (id, ticker, quantity, avg_cost, updated_at) VALUES ('b','X',1,1,'t')"
            )
            raise AssertionError("expected IntegrityError")
        except sqlite3.IntegrityError:
            pass
        row = c.execute("SELECT user_id FROM positions WHERE id='a'").fetchone()
        assert row["user_id"] == "default"
