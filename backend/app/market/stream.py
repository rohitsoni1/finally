"""GET /api/stream/prices — Server-Sent Events from the PriceCache."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

router = APIRouter()

SSE_INTERVAL = 0.5  # seconds between checks; matches the simulator tick
KEEPALIVE_EVERY = 15.0  # seconds of silence before a comment line


async def price_events(cache: PriceCache, request: Request | None = None,
                       interval: float = SSE_INTERVAL,
                       keepalive_every: float = KEEPALIVE_EVERY) -> AsyncIterator[str]:
    yield "retry: 1000\n\n"  # EventSource reconnect delay (ms)
    last_version = -1
    last_sent = asyncio.get_running_loop().time()
    event_id = 0
    while request is None or not await request.is_disconnected():
        now = asyncio.get_running_loop().time()
        if cache.version != last_version:
            last_version = cache.version
            event_id += 1
            payload = {t: u.to_dict() for t, u in cache.get_all().items()}
            yield f"id: {event_id}\ndata: {json.dumps(payload)}\n\n"
            last_sent = now
        elif now - last_sent >= keepalive_every:
            yield ": keepalive\n\n"  # keeps proxies from closing an idle stream
            last_sent = now
        await asyncio.sleep(interval)


@router.get("/api/stream/prices")
async def stream_prices(request: Request) -> StreamingResponse:
    return StreamingResponse(
        price_events(request.app.state.price_cache, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
