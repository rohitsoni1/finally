"""Portfolio logic: trade execution, valuation and P&L snapshots.

Shared by the REST routes and the LLM chat flow (which must not reimplement any of this).
All DB access is synchronous sqlite3 via ``app.db`` (fast, local file).
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from app import db
from app.market import PriceCache, normalize_ticker

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_SECONDS = 30.0


class TradeError(Exception):
    """A trade failed validation. ``str(err)`` is a human-readable message (-> HTTP 400)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _r(x: float, nd: int = 2) -> float:
    return round(float(x), nd)


def get_portfolio(price_cache: PriceCache) -> dict[str, Any]:
    """Cash, positions valued at the latest cached price (fallback: avg cost), totals, P&L."""
    cash = db.get_cash()
    rows = db.get_positions()

    positions: list[dict[str, Any]] = []
    positions_value = 0.0
    total_cost = 0.0
    for p in rows:
        qty = p["quantity"]
        avg = p["avg_cost"]
        price = price_cache.get_price(p["ticker"])
        if price is None:
            price = avg
        mv = qty * price
        cb = qty * avg
        positions_value += mv
        total_cost += cb
        positions.append({
            "ticker": p["ticker"],
            "quantity": qty,
            "avg_cost": _r(avg, 4),
            "current_price": _r(price),
            "market_value": _r(mv),
            "cost_basis": _r(cb),
            "unrealized_pnl": _r(mv - cb),
            "unrealized_pnl_percent": _r((mv - cb) / cb * 100, 4) if cb else 0.0,
            "_mv": mv,
        })

    total_value = cash + positions_value
    for pos in positions:
        mv = pos.pop("_mv")
        pos["weight"] = round(mv / total_value, 4) if total_value > 0 else 0.0
    positions.sort(key=lambda p: p["market_value"], reverse=True)

    pnl = positions_value - total_cost
    return {
        "cash_balance": _r(cash),
        "positions_value": _r(positions_value),
        "total_value": _r(total_value),
        "total_cost": _r(total_cost),
        "unrealized_pnl": _r(pnl),
        "unrealized_pnl_percent": _r(pnl / total_cost * 100, 4) if total_cost else 0.0,
        "positions": positions,
    }


def record_snapshot(price_cache: PriceCache) -> dict[str, Any]:
    """Persist the current total portfolio value. Returns ``{total_value, recorded_at}``."""
    total = get_portfolio(price_cache)["total_value"]
    return db.insert_snapshot(total)


def execute_trade(ticker: str, side: str, quantity: float, price_cache: PriceCache) -> dict[str, Any]:
    """Fill a market order at the latest cached price.

    Returns ``{"trade": {id, ticker, side, quantity, price, executed_at}, "portfolio": {...}}``.
    Raises TradeError on invalid input, missing price, insufficient cash or shares.
    """
    try:
        t = normalize_ticker(ticker)
    except ValueError as e:
        raise TradeError(str(e)) from None

    s = (side or "").strip().lower()
    if s not in ("buy", "sell"):
        raise TradeError(f"Invalid side {side!r}; must be 'buy' or 'sell'")

    try:
        qty = float(quantity)
    except (TypeError, ValueError):
        raise TradeError(f"Invalid quantity {quantity!r}") from None
    if not math.isfinite(qty) or qty <= 0:
        raise TradeError("Quantity must be greater than 0")

    price = price_cache.get_price(t)
    if price is None or price <= 0:
        raise TradeError(f"No price available for {t}; add it to the watchlist first")

    try:
        result = db.apply_trade(t, s, qty, price)
    except db.TradeValidationError as e:
        raise TradeError(str(e)) from None

    try:
        record_snapshot(price_cache)
    except Exception:  # a failed snapshot must not undo a committed trade
        logger.exception("Snapshot after trade failed")

    return {"trade": result["trade"], "portfolio": get_portfolio(price_cache)}


async def snapshot_loop(price_cache: PriceCache, interval: float = SNAPSHOT_INTERVAL_SECONDS) -> None:
    """Background task: record a snapshot every ``interval`` seconds (first one after one interval)."""
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(record_snapshot, price_cache)
        except Exception:
            logger.exception("Periodic portfolio snapshot failed")
