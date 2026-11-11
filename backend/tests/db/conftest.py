import pytest

from app.db import connection


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Fresh, initialised database in a temp dir; module state reset afterwards."""
    path = tmp_path / "sub" / "finally.db"
    monkeypatch.setenv("FINALLY_DB_PATH", str(path))
    connection.reset_for_tests()
    connection.init_db(path)
    yield path
    connection.reset_for_tests()
