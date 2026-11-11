import pytest

from app import db
from app.portfolio import (
    WatchlistError,
    add_to_watchlist,
    execute_trade,
    list_watchlist,
    remove_from_watchlist,
    tracked_tickers,
)


def test_list_watchlist_includes_prices(market, cache):
    rows = list_watchlist(cache)
    assert [r["ticker"] for r in rows][:2] == ["AAPL", "GOOGL"]
    assert len(rows) == 10
    assert rows[0]["price"]["price"] == 190.0 and rows[0]["added_at"]


async def test_add_tracks_ticker(market, cache):
    row = await add_to_watchlist(" pypl ", market)
    assert row["ticker"] == "PYPL" and row["added_at"]
    assert "PYPL" in market.get_tickers()
    assert cache.get_price("PYPL") == 100.0


async def test_add_duplicate_and_invalid(market):
    with pytest.raises(WatchlistError) as e:
        await add_to_watchlist("AAPL", market)
    assert e.value.status_code == 409
    with pytest.raises(WatchlistError) as e:
        await add_to_watchlist("$$$", market)
    assert e.value.status_code == 400


async def test_remove_untracks_ticker(market, cache):
    assert await remove_from_watchlist("nflx", market) == {"ticker": "NFLX", "removed": True}
    assert "NFLX" not in market.get_tickers()
    assert "NFLX" not in cache
    with pytest.raises(WatchlistError) as e:
        await remove_from_watchlist("NFLX", market)
    assert e.value.status_code == 404


async def test_remove_keeps_tracking_when_position_open(market, cache):
    execute_trade("TSLA", "buy", 1, cache)
    await remove_from_watchlist("TSLA", market)
    assert "TSLA" in market.get_tickers()
    assert "TSLA" not in [w["ticker"] for w in db.get_watchlist()]
    assert "TSLA" in tracked_tickers()
