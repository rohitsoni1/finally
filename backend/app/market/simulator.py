"""Built-in market simulator: correlated GBM with random jump events."""

from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .models import normalize_ticker
from .seed_prices import (
    CROSS_SECTOR_CORR,
    INTRA_SECTOR_CORR,
    UNCORRELATED_SECTORS,
    params_for,
    sector_for,
    seed_price_for,
)

logger = logging.getLogger(__name__)

TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # GBM time is measured in trading years


class GBMSimulator:
    """Pure, synchronous correlated-GBM engine over a dynamic ticker set.

    No asyncio, no cache, no clock: deterministic given `seed`, so it is
    trivially unit-testable.
    """

    def __init__(
        self,
        tickers: list[str] | None = None,
        tick_seconds: float = 0.5,
        time_scale: float = 1.0,
        event_probability: float = 0.001,
        event_range: tuple[float, float] = (0.02, 0.05),
        seed: int | None = None,
    ) -> None:
        self.dt = tick_seconds * time_scale / TRADING_SECONDS_PER_YEAR
        self.event_probability = event_probability
        self.event_range = event_range
        self.rng = np.random.default_rng(seed)

        self._tickers: list[str] = []
        self._prices = np.empty(0)
        self._mu = np.empty(0)
        self._sigma = np.empty(0)
        self._chol = np.empty((0, 0))

        for t in tickers or []:
            self.add_ticker(t)

    # ── ticker management ────────────────────────────────────────
    def add_ticker(self, ticker: str, price: float | None = None) -> float:
        ticker = ticker.upper()
        if ticker in self._tickers:
            return self.get_price(ticker)
        p = params_for(ticker)
        start = float(price) if price is not None else seed_price_for(ticker)
        self._tickers.append(ticker)
        self._prices = np.append(self._prices, start)
        self._mu = np.append(self._mu, p["mu"])
        self._sigma = np.append(self._sigma, p["sigma"])
        self._rebuild_correlation()
        return start

    def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper()
        if ticker not in self._tickers:
            return
        i = self._tickers.index(ticker)
        self._tickers.pop(i)
        self._prices = np.delete(self._prices, i)
        self._mu = np.delete(self._mu, i)
        self._sigma = np.delete(self._sigma, i)
        self._rebuild_correlation()

    @property
    def tickers(self) -> list[str]:
        return list(self._tickers)

    def get_price(self, ticker: str) -> float:
        return float(self._prices[self._tickers.index(ticker.upper())])

    # ── maths ────────────────────────────────────────────────────
    def correlation_matrix(self) -> np.ndarray:
        n = len(self._tickers)
        sectors = [sector_for(t) for t in self._tickers]
        corr = np.full((n, n), CROSS_SECTOR_CORR)
        for i in range(n):
            for j in range(n):
                if i == j:
                    corr[i, j] = 1.0
                elif sectors[i] == sectors[j] and sectors[i] not in UNCORRELATED_SECTORS:
                    corr[i, j] = INTRA_SECTOR_CORR
        return corr

    def _rebuild_correlation(self) -> None:
        n = len(self._tickers)
        if n == 0:
            self._chol = np.empty((0, 0))
            return
        corr = self.correlation_matrix()
        try:
            self._chol = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:  # defensive; block matrix is PD for rho in [0,1)
            self._chol = np.linalg.cholesky(corr + 1e-6 * np.eye(n))

    def step(self) -> dict[str, float]:
        """Advance one tick and return {ticker: new_price} (full precision)."""
        n = len(self._tickers)
        if n == 0:
            return {}
        z = self._chol @ self.rng.standard_normal(n)  # correlated N(0,1) shocks
        drift = (self._mu - 0.5 * self._sigma**2) * self.dt  # Ito-corrected drift
        diffusion = self._sigma * np.sqrt(self.dt) * z
        self._prices = self._prices * np.exp(drift + diffusion)

        # Rare 2–5 % jump events for drama
        hits = self.rng.random(n) < self.event_probability
        if hits.any():
            size = self.rng.uniform(*self.event_range, size=n)
            sign = self.rng.choice([-1.0, 1.0], size=n)
            self._prices = np.where(hits, self._prices * (1 + sign * size), self._prices)
            for i in np.flatnonzero(hits):
                logger.debug("Sim event %s %+.1f%%", self._tickers[i], sign[i] * size[i] * 100)

        return dict(zip(self._tickers, self._prices.tolist()))


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource backed by GBMSimulator; one asyncio task ticks every 0.5 s."""

    name = "simulator"

    def __init__(self, cache: PriceCache, tick_seconds: float = 0.5, **sim_kwargs) -> None:
        self._cache = cache
        self._tick = tick_seconds
        self._sim = GBMSimulator(tick_seconds=tick_seconds, **sim_kwargs)
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        for t in tickers:
            await self.add_ticker(t)  # seeds the cache immediately
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="market-simulator")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def add_ticker(self, ticker: str, price: float | None = None) -> None:
        t = normalize_ticker(ticker)
        if t in self._sim.tickers:
            return
        start = self._sim.add_ticker(t, price=price)
        # reference = starting price, so "day change %" is measured from session start
        self._cache.update(t, start, reference_price=start)

    async def remove_ticker(self, ticker: str) -> None:
        t = ticker.strip().upper()
        self._sim.remove_ticker(t)
        self._cache.remove(t)

    def get_tickers(self) -> list[str]:
        return sorted(self._sim.tickers)

    async def _run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                now = time.time()
                for ticker, price in self._sim.step().items():
                    self._cache.update(ticker, price, timestamp=now)
            except Exception:  # never let one bad tick kill the loop
                logger.exception("Simulator tick failed")
            # steady cadence regardless of how long the step took
            await asyncio.sleep(max(0.0, self._tick - (time.monotonic() - started)))
