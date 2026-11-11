import httpx
import pytest

from app.db import connection
from app.main import create_app
from tests.fakes import FakeMarketSource


@pytest.fixture
def static_dir(tmp_path, monkeypatch):
    d = tmp_path / "static"
    (d / "_next").mkdir(parents=True)
    (d / "index.html").write_text("<html>INDEX</html>")
    (d / "about.html").write_text("<html>ABOUT</html>")
    (d / "_next" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("FINALLY_STATIC_DIR", str(d))
    return d


@pytest.fixture
async def app(tmp_path, monkeypatch, static_dir):
    monkeypatch.setenv("FINALLY_DB_PATH", str(tmp_path / "finally.db"))
    monkeypatch.setenv("FINALLY_SNAPSHOT_INTERVAL", "3600")
    connection.reset_for_tests()
    application = create_app(market_factory=FakeMarketSource)
    async with application.router.lifespan_context(application):
        yield application
    connection.reset_for_tests()


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def market(app):
    return app.state.market
