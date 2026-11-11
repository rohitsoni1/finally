"""SQLite connection handling and lazy initialisation.

Design: one short-lived ``sqlite3`` connection per unit of work. That is safe to use
from FastAPI handlers (sync or async, any thread) without sharing a connection across
threads. WAL mode + a busy timeout let the background snapshot task and request
handlers write concurrently without "database is locked" errors.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DEFAULT_USER_ID = "default"
DEFAULT_CASH = 10000.0
DEFAULT_WATCHLIST = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_lock = threading.Lock()
_db_path: Path | None = None
_initialized: set[Path] = set()


def _default_db_path() -> Path:
    env = os.environ.get("FINALLY_DB_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    # this file: <backend>/app/db/connection.py
    backend_dir = Path(__file__).resolve().parents[2]
    # Local checkout: <repo>/backend -> <repo>/db. Docker (/app/app/db/...): /app/db.
    root = backend_dir.parent if backend_dir.name == "backend" else backend_dir
    return root / "db" / "finally.db"


def get_db_path() -> Path:
    """Path of the active database (set by init_db, else env / default)."""
    return _db_path if _db_path is not None else _default_db_path()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def _connect(path: Path) -> sqlite3.Connection:
    # isolation_level=None -> autocommit; transactions are explicit (see transaction()).
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str | os.PathLike[str] | None = None) -> Path:
    """Create tables and seed default data if missing. Idempotent.

    ``path`` overrides the location and becomes the active database for all
    subsequent repository calls. Returns the resolved database path.
    """
    global _db_path
    resolved = Path(path).expanduser() if path is not None else get_db_path()
    with _lock:
        _db_path = resolved
        resolved.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(resolved)
        try:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError:
                pass  # e.g. some network filesystems; default journal still works
            conn.executescript(_SCHEMA_PATH.read_text())
            _seed(conn)
        finally:
            conn.close()
        _initialized.add(resolved)
    return resolved


def _seed(conn: sqlite3.Connection) -> None:
    """Seed the default user; seed the watchlist only when the user is first created
    (so a user who deliberately empties their watchlist is not re-seeded)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
            (DEFAULT_USER_ID, DEFAULT_CASH, now_iso()),
        )
        if cur.rowcount == 1:
            ts = now_iso()
            conn.executemany(
                "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
                [(new_id(), DEFAULT_USER_ID, t, ts) for t in DEFAULT_WATCHLIST],
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _ensure_initialized() -> Path:
    path = get_db_path()
    if path not in _initialized or not path.exists():
        _initialized.discard(path)
        init_db(path)
    return path


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Yield a fresh autocommit connection to the (lazily initialised) database."""
    conn = _connect(_ensure_initialized())
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Yield a connection inside ``BEGIN IMMEDIATE``; commit on success, roll back on error.

    Pass the yielded connection as ``conn=`` to repository functions to group writes.
    """
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def reset_for_tests() -> None:
    """Forget the active path / init cache (test helper)."""
    global _db_path
    with _lock:
        _db_path = None
        _initialized.clear()
