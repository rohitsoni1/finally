import json

from app.market.stream import price_events


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["market"]["source"] == "fake"


async def test_lifespan_tracks_seeded_watchlist(app, market):
    assert market.started
    assert len(market.get_tickers()) == 10
    assert set(app.state.price_cache.get_all()) == set(market.get_tickers())


async def test_lifespan_records_initial_snapshot(client):
    r = await client.get("/api/portfolio/history")
    snaps = r.json()["snapshots"]
    assert len(snaps) >= 1 and snaps[0]["total_value"] == 10000.0


async def test_portfolio_initial(client):
    r = await client.get("/api/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["cash_balance"] == 10000.0 and body["total_value"] == 10000.0
    assert body["positions"] == []
    assert set(body) == {"cash_balance", "positions_value", "total_value", "total_cost",
                         "unrealized_pnl", "unrealized_pnl_percent", "positions"}


async def test_buy_then_sell(client, market):
    r = await client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["trade"]) == {"id", "ticker", "side", "quantity", "price", "executed_at"}
    assert body["trade"]["price"] == 190.0
    assert body["portfolio"]["cash_balance"] == 8100.0
    pos = body["portfolio"]["positions"][0]
    assert set(pos) == {"ticker", "quantity", "avg_cost", "current_price", "market_value", "cost_basis",
                        "unrealized_pnl", "unrealized_pnl_percent", "weight"}

    market.set_price("AAPL", 200.0)
    r = await client.get("/api/portfolio")
    assert r.json()["unrealized_pnl"] == 100.0

    r = await client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "sell"})
    assert r.status_code == 200
    assert r.json()["portfolio"]["positions"] == []
    assert r.json()["portfolio"]["cash_balance"] == 10100.0

    snaps = (await client.get("/api/portfolio/history")).json()["snapshots"]
    assert len(snaps) >= 3
    assert snaps == sorted(snaps, key=lambda s: s["recorded_at"])


async def test_trade_errors_are_400_with_string_detail(client):
    cases = [
        {"ticker": "AAPL", "quantity": 1000, "side": "buy"},
        {"ticker": "AAPL", "quantity": 1, "side": "sell"},
        {"ticker": "ZZZZ", "quantity": 1, "side": "buy"},
        {"ticker": "AAPL", "quantity": 0, "side": "buy"},
        {"ticker": "AAPL", "quantity": 1, "side": "short"},
        {"ticker": "AAPL"},
    ]
    for payload in cases:
        r = await client.post("/api/portfolio/trade", json=payload)
        assert r.status_code == 400, payload
        assert isinstance(r.json()["detail"], str) and r.json()["detail"]


async def test_watchlist_crud(client, market, app):
    r = await client.get("/api/watchlist")
    tickers = r.json()["tickers"]
    assert len(tickers) == 10
    assert tickers[0]["ticker"] == "AAPL" and tickers[0]["price"]["direction"] in ("up", "down", "flat")

    r = await client.post("/api/watchlist", json={"ticker": "pypl"})
    assert r.status_code == 201
    assert r.json()["ticker"] == "PYPL" and r.json()["added_at"]
    assert "PYPL" in market.get_tickers()
    assert (await client.post("/api/watchlist", json={"ticker": "PYPL"})).status_code == 409
    assert (await client.post("/api/watchlist", json={"ticker": "bad ticker!"})).status_code == 400
    assert (await client.post("/api/watchlist", json={})).status_code == 400

    r = await client.get("/api/watchlist")
    assert r.json()["tickers"][-1]["ticker"] == "PYPL"
    assert r.json()["tickers"][-1]["price"]["price"] == 100.0

    r = await client.delete("/api/watchlist/PYPL")
    assert r.status_code == 200 and r.json() == {"ticker": "PYPL", "removed": True}
    assert "PYPL" not in market.get_tickers()
    assert (await client.delete("/api/watchlist/PYPL")).status_code == 404


async def test_remove_watchlist_keeps_price_for_open_position(client, market):
    await client.post("/api/portfolio/trade", json={"ticker": "TSLA", "quantity": 1, "side": "buy"})
    assert (await client.delete("/api/watchlist/TSLA")).status_code == 200
    assert "TSLA" in market.get_tickers()
    pos = (await client.get("/api/portfolio")).json()["positions"][0]
    assert pos["current_price"] == 250.0


async def test_sse_payload_matches_contract(app):
    gen = price_events(app.state.price_cache, None, interval=0)
    assert await anext(gen) == "retry: 1000\n\n"
    event = await anext(gen)
    data = json.loads(event.split("data: ", 1)[1])
    assert set(data) == set(app.state.market.get_tickers())
    assert set(data["AAPL"]) == {"ticker", "price", "previous_price", "reference_price", "change",
                                 "change_percent", "day_change", "day_change_percent", "direction",
                                 "timestamp"}
    await gen.aclose()


async def test_sse_route_registered(app):
    paths = set(app.openapi()["paths"])
    assert "/api/stream/prices" in paths


async def test_static_and_spa_fallback(client):
    r = await client.get("/")
    assert r.status_code == 200 and "INDEX" in r.text
    assert "console.log" in (await client.get("/_next/app.js")).text
    assert "ABOUT" in (await client.get("/about")).text
    assert "INDEX" in (await client.get("/some/client/route")).text
    assert (await client.get("/_next/missing.js")).status_code == 404
    assert (await client.get("/../../etc/passwd")).status_code in (200, 404)
    assert "root:" not in (await client.get("/..%2F..%2F..%2Fetc%2Fpasswd")).text


async def test_unknown_api_path_is_404_json(client):
    r = await client.get("/api/nope")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


async def test_no_static_build(client, static_dir):
    (static_dir / "index.html").unlink()
    r = await client.get("/")
    assert r.status_code == 200 and "Frontend build not found" in r.text
