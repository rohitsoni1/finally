"""Watchlist operations that keep the DB and the market data source in sync.

Used by the REST routes and by the LLM chat flow.
"""

from __future__ import annotations

from typing import Any

from app import db
from app.market import MarketDataSource, PriceCache, normalize_ticker


class WatchlistError(Exception):
    """Watchlist operation failed. ``status_code``: 400 invalid, 404 absent, 409 duplicate."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _normalize(ticker: str) -> str:
    try:
        return normalize_ticker(ticker)
    except ValueError as e:
        raise WatchlistError(str(e), 400) from None


def list_watchlist(price_cache: PriceCache) -> list[dict[str, Any]]:
    """[{ticker, added_at, price: PriceUpdate.to_dict() | None}] in watchlist order."""
    out = []
    for row in db.get_watchlist():
        update = price_cache.get(row["ticker"])
        out.append({
            "ticker": row["ticker"],
            "added_at": row["added_at"],
            "price": update.to_dict() if update else None,
        })
    return out


def tracked_tickers() -> list[str]:
    """Tickers the market source should track: watchlist plus open positions."""
    tickers = {r["ticker"] for r in db.get_watchlist()} | {p["ticker"] for p in db.get_positions()}
    return sorted(tickers)


async def add_to_watchlist(ticker: str, market_source: MarketDataSource) -> dict[str, Any]:
    """Add a ticker and start tracking it. Returns ``{ticker, added_at}``."""
    t = _normalize(ticker)
    row = db.add_watchlist_ticker(t)
    if row is None:
        raise WatchlistError(f"{t} is already on the watchlist", 409)
    await market_source.add_ticker(t)
    return row


async def remove_from_watchlist(ticker: str, market_source: MarketDataSource) -> dict[str, Any]:
    """Remove a ticker; stop tracking it unless an open position still needs its price."""
    t = _normalize(ticker)
    if not db.remove_watchlist_ticker(t):
        raise WatchlistError(f"{t} is not on the watchlist", 404)
    if db.get_position(t) is None:
        await market_source.remove_ticker(t)
    return {"ticker": t, "removed": True}
