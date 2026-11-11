"""GET /api/health."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.market import MarketDataSource

from .deps import get_market_source

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health(market: MarketDataSource = Depends(get_market_source)) -> dict:
    return {"status": "ok", "market": market.status()}
