"""FinAlly FastAPI application.

Run locally:  cd backend && uv run uvicorn app.main:app --port 8000
Serves /api/* routes, the SSE stream and the static Next.js export (SPA fallback).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app import db
from app.api import health, portfolio, watchlist
from app.market import MarketDataSource, PriceCache, create_market_data_source
from app.market.stream import router as stream_router
from app.portfolio import SNAPSHOT_INTERVAL_SECONDS, record_snapshot, snapshot_loop, tracked_tickers

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


def load_env() -> None:
    """Load .env from the repo root (local) or the app dir (Docker). Real env vars win."""
    for candidate in (REPO_ROOT / ".env", BACKEND_DIR / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


load_env()


def static_dir() -> Path:
    env = os.getenv("FINALLY_STATIC_DIR", "").strip()
    return Path(env).expanduser().resolve() if env else (BACKEND_DIR / "static").resolve()


def _snapshot_interval() -> float:
    try:
        return float(os.getenv("FINALLY_SNAPSHOT_INTERVAL", "") or SNAPSHOT_INTERVAL_SECONDS)
    except ValueError:
        return SNAPSHOT_INTERVAL_SECONDS


MarketFactory = Callable[[PriceCache], MarketDataSource]


def create_app(market_factory: MarketFactory = create_market_data_source) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db()
        cache = PriceCache()
        source = market_factory(cache)
        await source.start(tracked_tickers())
        app.state.price_cache = cache
        app.state.market = source
        try:
            record_snapshot(cache)  # so the P&L chart has a point right away
        except Exception:
            logger.exception("Initial portfolio snapshot failed")
        snapshots = asyncio.create_task(snapshot_loop(cache, _snapshot_interval()), name="portfolio-snapshots")
        app.state.snapshot_task = snapshots
        try:
            yield
        finally:
            snapshots.cancel()
            try:
                await snapshots
            except asyncio.CancelledError:
                pass
            await source.stop()

    app = FastAPI(title="FinAlly", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        msgs = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
            msgs.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
        return JSONResponse(status_code=400, content={"detail": "; ".join(msgs) or "Invalid request"})

    app.include_router(health.router)
    app.include_router(stream_router)
    app.include_router(portfolio.router)
    app.include_router(watchlist.router)
    _include_chat(app)
    _mount_frontend(app)
    return app


def _include_chat(app: FastAPI) -> None:
    try:
        from app.api.chat import router as chat_router
    except ModuleNotFoundError as e:
        if e.name != "app.api.chat":
            raise
        logger.warning("app.api.chat not found; /api/chat disabled")
        return
    app.include_router(chat_router)


def _mount_frontend(app: FastAPI) -> None:
    """Serve the Next.js static export with SPA fallback. Registered last so /api wins."""

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        root = static_dir()
        index = root / "index.html"
        if full_path:
            target = (root / full_path).resolve()
            if target.is_relative_to(root):
                for candidate in (target, target / "index.html", target.with_name(target.name + ".html")):
                    if candidate.is_file():
                        return FileResponse(candidate)
                if Path(full_path).suffix and Path(full_path).suffix != ".html":
                    raise HTTPException(status_code=404, detail="Not Found")  # missing asset
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(
            "<h1>FinAlly</h1><p>Frontend build not found. API is available under /api.</p>",
            status_code=200,
        )


app = create_app()
