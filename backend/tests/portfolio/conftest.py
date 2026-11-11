import pytest

from app import db
from app.db import connection
from app.market import PriceCache
from tests.fakes import FakeMarketSource


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "finally.db"
    monkeypatch.setenv("FINALLY_DB_PATH", str(path))
    connection.reset_for_tests()
    db.init_db(path)
    yield path
    connection.reset_for_tests()


@pytest.fixture
def cache():
    return PriceCache()


@pytest.fixture
async def market(cache, temp_db):
    src = FakeMarketSource(cache)
    await src.start([w["ticker"] for w in db.get_watchlist()])
    return src
