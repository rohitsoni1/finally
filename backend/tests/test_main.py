from app import main


async def test_lifespan_wires_cache_source_and_health(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    async with main.lifespan(main.app):
        state = main.app.state
        assert set(state.price_cache.get_all()) == set(main.DEFAULT_TICKERS)
        assert state.market.name == "simulator"

        class Req:
            app = main.app

        assert await main.health(Req()) == {
            "status": "ok",
            "market": {"source": "simulator", "tickers": len(main.DEFAULT_TICKERS)},
        }
        assert main.get_cache(Req()) is state.price_cache
        assert main.get_market(Req()) is state.market
    assert state.market._task is None  # stopped on shutdown


def test_initial_tickers_is_a_copy():
    t = main.initial_tickers()
    t.append("X")
    assert "X" not in main.DEFAULT_TICKERS
