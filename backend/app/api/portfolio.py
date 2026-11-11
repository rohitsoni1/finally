"""Portfolio routes: valuation, trade execution, value history."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app import db
from app.market import PriceCache
from app.portfolio import TradeError, execute_trade, get_portfolio

from .deps import get_price_cache

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class TradeRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    quantity: float = Field(gt=0)
    side: Literal["buy", "sell"]


@router.get("")
async def read_portfolio(cache: PriceCache = Depends(get_price_cache)) -> dict:
    return await run_in_threadpool(get_portfolio, cache)


@router.post("/trade")
async def trade(req: TradeRequest, cache: PriceCache = Depends(get_price_cache)) -> dict:
    try:
        return await run_in_threadpool(execute_trade, req.ticker, req.side, req.quantity, cache)
    except TradeError as e:
        raise HTTPException(status_code=400, detail=e.message) from None


@router.get("/history")
async def history() -> dict:
    return {"snapshots": await run_in_threadpool(db.list_snapshots)}
