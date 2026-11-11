"""FastAPI application: market data lifespan, SSE stream and health check."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.market import MarketDataSource, PriceCache, create_market_data_source
from app.market.stream import router as stream_router

DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


def initial_tickers() -> list[str]:
    # Real app: sorted(set(db.get_watchlist_tickers()) | set(db.get_position_tickers()))
    return list(DEFAULT_TICKERS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = PriceCache()
    source = create_market_data_source(cache)
    await source.start(initial_tickers())
    app.state.price_cache = cache
    app.state.market = source
    try:
        yield
    finally:
        await source.stop()


app = FastAPI(title="FinAlly", lifespan=lifespan)
app.include_router(stream_router)


def get_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache


def get_market(request: Request) -> MarketDataSource:
    return request.app.state.market


@app.get("/api/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "market": request.app.state.market.status()}
