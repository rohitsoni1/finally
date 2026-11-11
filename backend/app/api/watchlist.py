"""Watchlist CRUD, kept in sync with the market data source."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.market import MarketDataSource, PriceCache
from app.portfolio import WatchlistError, add_to_watchlist, list_watchlist, remove_from_watchlist

from .deps import get_market_source, get_price_cache

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class AddTickerRequest(BaseModel):
    ticker: str


@router.get("")
async def read_watchlist(cache: PriceCache = Depends(get_price_cache)) -> dict:
    return {"tickers": await run_in_threadpool(list_watchlist, cache)}


@router.post("", status_code=201)
async def add_ticker(req: AddTickerRequest, market: MarketDataSource = Depends(get_market_source)) -> dict:
    try:
        return await add_to_watchlist(req.ticker, market)
    except WatchlistError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from None


@router.delete("/{ticker}")
async def remove_ticker(ticker: str, market: MarketDataSource = Depends(get_market_source)) -> dict:
    try:
        return await remove_from_watchlist(ticker, market)
    except WatchlistError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from None
