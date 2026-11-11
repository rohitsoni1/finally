"""Test doubles shared by backend tests."""

from __future__ import annotations

from app.market import MarketDataSource, PriceCache, normalize_ticker

FIXED_PRICES = {
    "AAPL": 190.0, "GOOGL": 175.0, "MSFT": 420.0, "AMZN": 185.0, "TSLA": 250.0,
    "NVDA": 120.0, "META": 500.0, "JPM": 200.0, "V": 280.0, "NFLX": 650.0,
}


class FakeMarketSource(MarketDataSource):
    """Deterministic source: seeds fixed prices, never ticks."""

    name = "fake"

    def __init__(self, cache: PriceCache, default_price: float = 100.0) -> None:
        self.cache = cache
        self.default_price = default_price
        self.tickers: set[str] = set()
        self.started = False
        self.stopped = False

    async def start(self, tickers: list[str]) -> None:
        self.started = True
        for t in tickers:
            await self.add_ticker(t)

    async def stop(self) -> None:
        self.stopped = True

    async def add_ticker(self, ticker: str) -> None:
        t = normalize_ticker(ticker)
        if t not in self.tickers:
            self.tickers.add(t)
            self.cache.update(t, FIXED_PRICES.get(t, self.default_price))

    async def remove_ticker(self, ticker: str) -> None:
        t = ticker.strip().upper()
        self.tickers.discard(t)
        self.cache.remove(t)

    def get_tickers(self) -> list[str]:
        return sorted(self.tickers)

    def set_price(self, ticker: str, price: float) -> None:
        self.cache.update(ticker, price)
