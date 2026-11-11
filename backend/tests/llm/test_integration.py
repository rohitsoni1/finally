"""End-to-end /api/chat through the real app, DB and portfolio logic (LLM mocked)."""

import json

import httpx
import pytest

from app.db import connection
from app.llm import service
from app.main import create_app
from tests.fakes import FakeMarketSource


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINALLY_DB_PATH", str(tmp_path / "finally.db"))
    monkeypatch.setenv("FINALLY_SNAPSHOT_INTERVAL", "3600")
    monkeypatch.setenv("FINALLY_STATIC_DIR", str(tmp_path / "static"))
    monkeypatch.setenv("LLM_MOCK", "true")
    connection.reset_for_tests()
    app = create_app(market_factory=FakeMarketSource)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    connection.reset_for_tests()


async def test_mock_buy_executes_and_persists(client):
    r = await client.post("/api/chat", json={"message": "buy 5 AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["actions"]["trades"] == [{"ticker": "AAPL", "side": "buy", "quantity": 5.0,
                                          "status": "executed", "price": 190.0, "error": None}]
    pf = (await client.get("/api/portfolio")).json()
    assert pf["cash_balance"] == pytest.approx(10000 - 5 * 190.0)
    assert [p["ticker"] for p in pf["positions"]] == ["AAPL"]

    hist = (await client.get("/api/chat/history")).json()["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[0]["content"] == "buy 5 AAPL" and hist[0]["actions"] is None
    assert hist[1]["actions"]["trades"][0]["status"] == "executed"


async def test_mock_failed_sell_and_watchlist(client):
    r = await client.post("/api/chat", json={"message": "sell 3 MSFT and add PYPL and add AAPL"})
    body = r.json()
    trade = body["actions"]["trades"][0]
    assert trade["status"] == "failed" and trade["error"]
    changes = body["actions"]["watchlist_changes"]
    assert changes[0] == {"ticker": "PYPL", "action": "add", "status": "executed", "error": None}
    assert changes[1]["status"] == "failed" and "already" in changes[1]["error"]
    assert "Some actions failed" in body["message"]
    wl = [t["ticker"] for t in (await client.get("/api/watchlist")).json()["tickers"]]
    assert "PYPL" in wl


async def test_mock_add_then_buy_new_ticker(client):
    r = await client.post("/api/chat", json={"message": "add PYPL then buy 2 PYPL"})
    body = r.json()
    assert body["actions"]["trades"][0]["status"] == "executed"


async def test_mock_remove(client):
    r = await client.post("/api/chat", json={"message": "remove NFLX"})
    assert r.json()["actions"]["watchlist_changes"][0]["status"] == "executed"
    wl = [t["ticker"] for t in (await client.get("/api/watchlist")).json()["tickers"]]
    assert "NFLX" not in wl


async def test_mock_default_message(client):
    r = await client.post("/api/chat", json={"message": "hi"})
    assert r.json() == {"message": "Mock response: I received your message.",
                        "actions": {"trades": [], "watchlist_changes": []}}


async def test_real_path_with_patched_llm_uses_live_context(client, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "false")
    seen = []

    async def fake_call(messages):
        seen.append(messages)
        return json.dumps({"message": "Bought.", "trades": [{"ticker": "NVDA", "side": "buy", "quantity": 1}]})

    monkeypatch.setattr(service, "call_llm", fake_call)
    r = await client.post("/api/chat", json={"message": "buy one nvidia"})
    assert r.json()["actions"]["trades"][0]["status"] == "executed"
    ctx = seen[0][-2]["content"]
    assert "Cash: $10,000.00" in ctx and "NVDA | $120.00" in ctx


async def test_garbage_llm_output_is_200(client, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "false")

    async def fake_call(messages):
        return "<<<not json>>>"

    monkeypatch.setattr(service, "call_llm", fake_call)
    r = await client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200
    assert r.json()["message"] == service.APOLOGY_MESSAGE


async def test_empty_message_is_400(client):
    r = await client.post("/api/chat", json={"message": "  "})
    assert r.status_code == 400
