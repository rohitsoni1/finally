from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import chat as chat_api


def make_client(monkeypatch, captured):
    async def fake_handle_chat(message, price_cache, market_source):
        captured.append((message, price_cache, market_source))
        return {"message": "hi", "actions": {"trades": [], "watchlist_changes": []}}

    monkeypatch.setattr(chat_api, "handle_chat", fake_handle_chat)
    monkeypatch.setattr(chat_api, "get_chat_history", lambda limit: [
        {"id": "1", "role": "user", "content": "x", "actions": None, "created_at": "t"}])
    app = FastAPI()
    app.include_router(chat_api.router)
    app.state.price_cache = "CACHE"
    app.state.market = "SOURCE"
    return TestClient(app)


def test_post_chat(monkeypatch):
    captured = []
    client = make_client(monkeypatch, captured)
    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200
    assert r.json() == {"message": "hi", "actions": {"trades": [], "watchlist_changes": []}}
    assert captured == [("hello", "CACHE", "SOURCE")]


def test_post_chat_validation(monkeypatch):
    client = make_client(monkeypatch, [])
    assert client.post("/api/chat", json={}).status_code == 422
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    r = client.post("/api/chat", json={"message": "   "})
    assert r.status_code == 400 and "detail" in r.json()


def test_history(monkeypatch):
    client = make_client(monkeypatch, [])
    r = client.get("/api/chat/history")
    assert r.status_code == 200
    assert r.json()["messages"][0]["role"] == "user"
