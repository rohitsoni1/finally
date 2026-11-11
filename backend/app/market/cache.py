"""In-memory latest-price store. One writer (the data source), many readers."""

from __future__ import annotations

import asyncio
import threading
import time

from .models import PriceUpdate


class PriceCache:
    """Thread-safe map of ticker -> latest PriceUpdate.

    `version` increments on every mutation so the SSE loop can cheaply tell
    whether anything changed since it last sent an event.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = threading.Lock()
        self._version = 0

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        reference_price: float | None = None,
    ) -> PriceUpdate:
        ticker = ticker.upper()
        price = round(float(price), 2)
        with self._lock:
            prev = self._prices.get(ticker)
            if reference_price is not None:
                ref = round(float(reference_price), 2)
            elif prev is not None:
                ref = prev.reference_price  # sticky across ticks
            else:
                ref = price
            update = PriceUpdate(
                ticker=ticker,
                price=price,
                previous_price=prev.price if prev else price,
                reference_price=ref,
                timestamp=timestamp if timestamp is not None else time.time(),
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker.upper())

    def get_price(self, ticker: str) -> float | None:
        u = self.get(ticker)
        return u.price if u else None

    def get_all(self) -> dict[str, PriceUpdate]:
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        with self._lock:
            if self._prices.pop(ticker.upper(), None) is not None:
                self._version += 1

    @property
    def version(self) -> int:
        return self._version  # int read is atomic under the GIL

    def __contains__(self, ticker: str) -> bool:
        return self.get(ticker) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    async def wait_for(self, ticker: str, timeout: float = 2.0, poll: float = 0.05) -> PriceUpdate | None:
        """Wait up to `timeout` s for a ticker to appear (e.g. right after add_ticker)."""
        deadline = time.monotonic() + timeout
        while True:
            u = self.get(ticker)
            if u is not None or time.monotonic() >= deadline:
                return u
            await asyncio.sleep(poll)
