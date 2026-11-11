"""The one abstraction every market data producer implements."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Produces prices for a dynamic ticker set and writes them into a PriceCache.

    Lifecycle:
        source = Impl(cache, ...)
        await source.start(tickers)      # seeds cache, launches ONE background task
        await source.add_ticker("PYPL")  # as the watchlist / positions change
        await source.remove_ticker("PYPL")
        await source.stop()              # idempotent, never raises

    Sources never *return* prices. Consumers read the PriceCache.
    """

    #: "simulator" | "massive" — surfaced in /api/health
    name: str = "abstract"

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing prices. Seed the cache for `tickers` before returning
        (best effort for Massive) so the first SSE event is complete."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task. Idempotent; safe if start() never ran."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Track a ticker. Idempotent."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and drop it from the cache. Idempotent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Sorted list of tracked tickers."""

    def status(self) -> dict:
        """Diagnostics for /api/health. Override to add source-specific detail."""
        return {"source": self.name, "tickers": len(self.get_tickers())}
