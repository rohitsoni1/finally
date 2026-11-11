import asyncio
import json
import socket

import httpx
import uvicorn
from fastapi import FastAPI

from app.market import MarketDataSource, factory, create_market_data_source
from app.market.massive_source import MassiveDataSource
from app.market.simulator import SimulatorDataSource
from app.market.stream import price_events, router

from .conftest import FakeMassiveClient, snap


def test_factory_selects_source(monkeypatch, cache):
    monkeypatch.setenv("MASSIVE_API_KEY", "  ")
    assert isinstance(create_market_data_source(cache), SimulatorDataSource)
    monkeypatch.delenv("MASSIVE_API_KEY")
    assert isinstance(create_market_data_source(cache), SimulatorDataSource)
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_CACHE_TTL", "10")
    monkeypatch.setenv("MASSIVE_MAX_CALLS_PER_MIN", "7")
    src = create_market_data_source(cache)
    assert isinstance(src, MassiveDataSource) and src._responses.ttl == 10
    assert src._budget.max_calls == 7
    assert src._client.retries == 0


def test_factory_ignores_malformed_env(monkeypatch, cache):
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", "fast")
    src = create_market_data_source(cache)
    assert src._interval == 15.0
    assert factory._env_float("UNSET_VAR_XYZ", 3.0) == 3.0


async def test_sse_generator_emits_events(cache):
    cache.update("AAPL", 190.0)
    gen = price_events(cache, request=None, interval=0.01)
    assert await anext(gen) == "retry: 1000\n\n"
    first = await anext(gen)
    assert first.startswith("id: 1\ndata: ")
    payload = json.loads(first.split("data: ", 1)[1])
    assert payload["AAPL"]["price"] == 190.0
    cache.update("AAPL", 191.0)
    second = await anext(gen)
    assert json.loads(second.split("data: ", 1)[1])["AAPL"]["direction"] == "up"
    await gen.aclose()


async def test_sse_generator_keepalive_when_idle(cache):
    cache.update("AAPL", 190.0)
    gen = price_events(cache, request=None, interval=0.01, keepalive_every=0.03)
    await anext(gen)  # retry
    await anext(gen)  # data
    assert await anext(gen) == ": keepalive\n\n"
    await gen.aclose()


async def test_sse_generator_stops_on_disconnect(cache):
    class Req:
        def __init__(self):
            self.calls = 0

        async def is_disconnected(self):
            self.calls += 1
            return self.calls > 1

    out = [chunk async for chunk in price_events(cache, request=Req(), interval=0.001)]
    assert out[0] == "retry: 1000\n\n" and len(out) == 2


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_sse_endpoint_over_http(cache):
    """Real uvicorn server: proves the endpoint streams through the ASGI stack."""
    app = FastAPI()
    app.include_router(router)
    app.state.price_cache = cache
    cache.update("AAPL", 190.0)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            async with client.stream("GET", f"http://127.0.0.1:{port}/api/stream/prices") as r:
                assert r.headers["content-type"].startswith("text/event-stream")
                async for line in r.aiter_lines():
                    if line.startswith("data: "):
                        assert "AAPL" in json.loads(line[6:])
                        break
    finally:
        server.should_exit = True
        await task


async def test_both_sources_conform_to_interface(cache):
    fake = FakeMassiveClient(snapshot=lambda t: [snap(x, 10.0) for x in t])
    for src in (SimulatorDataSource(cache), MassiveDataSource("k", cache, client=fake)):
        assert isinstance(src, MarketDataSource)
        await src.start(["AAPL"])
        assert "AAPL" in cache
        await src.add_ticker("NVDA")
        await src.add_ticker("NVDA")
        await asyncio.sleep(0.1)  # Massive serves new tickers on the woken poll
        assert "NVDA" in cache and src.get_tickers() == ["AAPL", "NVDA"]
        await src.remove_ticker("NVDA")
        assert "NVDA" not in cache
        assert src.status()["source"] == src.name
        await src.stop()
        await src.stop()
        cache.remove("AAPL")


def test_base_status_uses_name_and_ticker_count():
    class Dummy(MarketDataSource):
        name = "dummy"

        async def start(self, tickers): ...
        async def stop(self): ...
        async def add_ticker(self, ticker): ...
        async def remove_ticker(self, ticker): ...
        def get_tickers(self):
            return ["A", "B"]

    assert Dummy().status() == {"source": "dummy", "tickers": 2}
