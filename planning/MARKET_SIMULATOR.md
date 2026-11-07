# Market Simulator — Approach & Code Structure

> The default market data source, used when `MASSIVE_API_KEY` is not set. It produces realistic-looking, correlated, occasionally dramatic price streams with no external dependencies. It implements the `MarketDataSource` interface defined in `MARKET_INTERFACE.md`.

---

## 1. Goals

| Goal | How |
|---|---|
| Realistic price paths | Geometric Brownian Motion (GBM): log-normal, never negative, volatility scales with price |
| Starts at believable levels | Seed prices close to real-world levels (AAPL ≈ $190, GOOGL ≈ $175, …) |
| Stocks move together | Correlated random shocks via a Cholesky-decomposed sector correlation matrix |
| Drama for the demo | Rare random "events": sudden 2–5 % jumps on a single ticker |
| Live feel | Tick every **500 ms**, matching the SSE cadence |
| Any ticker works | Unknown tickers get a deterministic pseudo-random seed price and default parameters |
| Testable | Pure, synchronous `GBMSimulator` core with an injectable RNG seed, wrapped by a thin async `SimulatorDataSource` |

---

## 2. The math

### 2.1 GBM step

For each ticker with annualised drift μ and volatility σ, over a time step Δt (in years):

```
S(t+Δt) = S(t) · exp( (μ − σ²/2)·Δt + σ·√Δt·Z )        Z ~ N(0,1)
```

- `exp(...)` keeps prices strictly positive.
- The `−σ²/2` term (Itô correction) makes the *expected* price grow at μ.

### 2.2 Time step

Ticks are 0.5 s of wall-clock time, measured in **trading-time years** (252 days × 6.5 h):

```
Δt = 0.5 / (252 × 6.5 × 3600) ≈ 8.48 × 10⁻⁸ years
```

Resulting per-tick move: σ·√Δt. For σ = 22 % (AAPL) that is ≈ 0.0064 %, or about **±1.2 ¢ per tick on a $190 stock**. Over one hour (7,200 ticks) the typical drift is about ±0.5 %. That feels like a real trading day, and the events below add the excitement.

A `time_scale` multiplier on Δt (default `1.0`) lets you speed the market up for demos: `time_scale=10` gives about 3× bigger moves per tick, because size scales with √10.

### 2.3 Correlated shocks

Independent Zs would make every ticker wander alone. Real markets move in sympathy, so we draw correlated shocks:

```
ε ~ N(0, I)            (n independent normals)
Z = L · ε              where L = cholesky(C), C = correlation matrix
```

`C` is built from a sector map:

| Pair | Correlation ρ |
|---|---|
| Same sector (e.g. AAPL–MSFT) | 0.6 |
| Different sectors | 0.3 |
| TSLA with anything | 0.3 (idiosyncratic) |
| Diagonal | 1.0 |

This block structure is always positive-definite for ρ ∈ [0, 1). For safety, if `cholesky` ever raises, add `1e-6·I` and retry. `L` is rebuilt **only** when the ticker set changes, not every tick.

### 2.4 Random events

Each tick, each ticker independently has probability `p_event = 0.001` of a shock:

```
S ← S · (1 + sign · U(0.02, 0.05))      sign = ±1 with equal probability
```

With 10 tickers at 2 ticks/s, the expected rate is 10 × 2 × 0.001 = 0.02 events/s, or **about one event every 50 seconds** somewhere on the watchlist. That is frequent enough to notice and rare enough to stay plausible. Events are logged at DEBUG level.

---

## 3. Ticker parameters — `seed_prices.py`

```python
"""Seed prices and per-ticker GBM parameters for the simulator."""

SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00, "TSLA": 250.00,
    "NVDA": 130.00, "META": 500.00, "JPM": 200.00, "V": 280.00, "NFLX": 650.00,
}

# Annualised drift (mu) and volatility (sigma)
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL":  {"mu": 0.08, "sigma": 0.22},
    "GOOGL": {"mu": 0.08, "sigma": 0.25},
    "MSFT":  {"mu": 0.08, "sigma": 0.22},
    "AMZN":  {"mu": 0.10, "sigma": 0.28},
    "TSLA":  {"mu": 0.10, "sigma": 0.55},
    "NVDA":  {"mu": 0.15, "sigma": 0.50},
    "META":  {"mu": 0.10, "sigma": 0.35},
    "JPM":   {"mu": 0.06, "sigma": 0.18},
    "V":     {"mu": 0.06, "sigma": 0.16},
    "NFLX":  {"mu": 0.08, "sigma": 0.35},
}
DEFAULT_PARAMS = {"mu": 0.05, "sigma": 0.30}

SECTORS: dict[str, str] = {
    "AAPL": "tech", "GOOGL": "tech", "MSFT": "tech", "AMZN": "tech",
    "META": "tech", "NVDA": "tech", "NFLX": "tech",
    "JPM": "finance", "V": "finance",
    "TSLA": "tsla",          # its own bucket → only cross-sector correlation
}
DEFAULT_SECTOR = "other"

INTRA_SECTOR_CORR = 0.6
CROSS_SECTOR_CORR = 0.3
```

**Unknown tickers** (e.g. the user adds `PYPL`) get a stable seed price derived from the symbol, so the same ticker always starts at the same price across restarts:

```python
import zlib

def seed_price_for(ticker: str) -> float:
    if ticker in SEED_PRICES:
        return SEED_PRICES[ticker]
    return 50.0 + (zlib.crc32(ticker.encode()) % 25_000) / 100   # $50.00–$299.99
```

(Use `zlib.crc32`, not `hash()`, because Python string hashing is randomised per process.)

---

## 4. Code structure — `simulator.py`

There are two classes with a strict split:

- **`GBMSimulator`**: pure and synchronous. Holds prices and parameters and does the maths. No asyncio, no cache, no clock. Easy to unit-test deterministically.
- **`SimulatorDataSource`**: implements `MarketDataSource`. Owns a `GBMSimulator`, runs the asyncio tick loop, and writes to `PriceCache`.

```python
from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CROSS_SECTOR_CORR, DEFAULT_PARAMS, DEFAULT_SECTOR, INTRA_SECTOR_CORR,
    SECTORS, TICKER_PARAMS, seed_price_for,
)

logger = logging.getLogger(__name__)

TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600


class GBMSimulator:
    """Correlated geometric Brownian motion over a dynamic set of tickers."""

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
        self._prices: np.ndarray = np.empty(0)
        self._mu: np.ndarray = np.empty(0)
        self._sigma: np.ndarray = np.empty(0)
        self._chol: np.ndarray = np.empty((0, 0))

        for t in tickers or []:
            self.add_ticker(t)

    # ── ticker management ───────────────────────────────────────
    def add_ticker(self, ticker: str, price: float | None = None) -> float:
        ticker = ticker.upper()
        if ticker in self._tickers:
            return self.get_price(ticker)
        p = TICKER_PARAMS.get(ticker, DEFAULT_PARAMS)
        start = price if price is not None else seed_price_for(ticker)
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

    # ── maths ───────────────────────────────────────────────────
    def _rebuild_correlation(self) -> None:
        n = len(self._tickers)
        if n == 0:
            self._chol = np.empty((0, 0))
            return
        sectors = [SECTORS.get(t, DEFAULT_SECTOR) for t in self._tickers]
        corr = np.full((n, n), CROSS_SECTOR_CORR)
        for i in range(n):
            for j in range(n):
                if i == j:
                    corr[i, j] = 1.0
                elif sectors[i] == sectors[j] and sectors[i] not in ("tsla", DEFAULT_SECTOR):
                    corr[i, j] = INTRA_SECTOR_CORR
        try:
            self._chol = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            self._chol = np.linalg.cholesky(corr + 1e-6 * np.eye(n))

    def step(self) -> dict[str, float]:
        """Advance one tick. Returns {ticker: new_price}."""
        n = len(self._tickers)
        if n == 0:
            return {}
        z = self._chol @ self.rng.standard_normal(n)
        drift = (self._mu - 0.5 * self._sigma**2) * self.dt
        diffusion = self._sigma * np.sqrt(self.dt) * z
        self._prices = self._prices * np.exp(drift + diffusion)

        # Random events: rare 2–5 % jumps
        hits = self.rng.random(n) < self.event_probability
        if hits.any():
            size = self.rng.uniform(*self.event_range, size=n)
            sign = self.rng.choice([-1.0, 1.0], size=n)
            self._prices = np.where(hits, self._prices * (1 + sign * size), self._prices)
            for i in np.flatnonzero(hits):
                logger.debug("Sim event %s %+.1f%%", self._tickers[i], sign[i] * size[i] * 100)

        return dict(zip(self._tickers, self._prices.tolist()))


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource backed by GBMSimulator; ticks every `tick_seconds`."""

    name = "simulator"

    def __init__(self, cache: PriceCache, tick_seconds: float = 0.5, **sim_kwargs) -> None:
        self._cache = cache
        self._tick = tick_seconds
        self._sim = GBMSimulator(tick_seconds=tick_seconds, **sim_kwargs)
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        for t in tickers:
            await self.add_ticker(t)                 # seeds cache immediately
        self._task = asyncio.create_task(self._run(), name="market-simulator")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def add_ticker(self, ticker: str) -> None:
        t = ticker.strip().upper()
        if t in self._sim.tickers:
            return
        price = self._sim.add_ticker(t)
        # Reference price = seed, so "day change %" is measured from session start.
        self._cache.update(t, price, reference_price=round(price, 2))

    async def remove_ticker(self, ticker: str) -> None:
        t = ticker.strip().upper()
        self._sim.remove_ticker(t)
        self._cache.remove(t)

    def get_tickers(self) -> list[str]:
        return self._sim.tickers

    async def _run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                now = time.time()
                for ticker, price in self._sim.step().items():
                    self._cache.update(ticker, price, timestamp=now)
            except Exception:
                logger.exception("Simulator tick failed")      # never kill the loop
            # Keep a steady cadence regardless of how long the step took
            await asyncio.sleep(max(0.0, self._tick - (time.monotonic() - started)))
```

### Design notes

- **Vectorised with numpy:** one `step()` is a matrix-vector product plus element-wise ops, so it costs microseconds even for 100+ tickers. Add `numpy` to `backend/pyproject.toml` (`uv add numpy`).
- **Internal precision:** prices are kept as full-precision floats in the simulator. `PriceCache.update` rounds to cents for display and trading. Rounding inside the simulator would bias small moves to zero.
- **Event-loop safety:** `step()` runs on the event loop thread. It is fast enough not to need `to_thread`. Both add/remove and step run on the same loop, so there are no data races on the numpy arrays.
- **State is not persisted:** after a restart, prices start again from seed. This is fine for a simulation. Users holding positions will see P&L measured against their stored `avg_cost`, which is correct behaviour.
- **Optional enhancement (not required):** a seed price could be the last trade price for tickers the user holds, to avoid a jump on restart: `GBMSimulator.add_ticker(t, price=last_trade_price)`. The API already supports this.

---

## 5. Configuration knobs

| Parameter | Default | Where | Effect |
|---|---|---|---|
| `tick_seconds` | 0.5 | `SimulatorDataSource` | Update cadence |
| `time_scale` | 1.0 | `GBMSimulator` | Speeds up simulated time (bigger moves per tick) |
| `event_probability` | 0.001 | `GBMSimulator` | Per-ticker, per-tick chance of a jump |
| `event_range` | (0.02, 0.05) | `GBMSimulator` | Jump size range (fraction) |
| `seed` | None | `GBMSimulator` | RNG seed, for deterministic tests |
| `INTRA_SECTOR_CORR` / `CROSS_SECTOR_CORR` | 0.6 / 0.3 | `seed_prices.py` | Co-movement strength |

These are constructor arguments only. No new environment variables are needed, which keeps the PLAN's env surface unchanged. To expose one later, read it in `create_market_data_source`.

---

## 6. Testing — `backend/tests/market/test_simulator.py`

| Test | Assertion |
|---|---|
| Seed prices | `GBMSimulator(["AAPL"]).get_price("AAPL") == 190.0`. Unknown ticker price is in [50, 300) and identical across two instances |
| Positivity | 10,000 steps with `time_scale=1000` → all prices > 0 |
| Determinism | Two simulators with `seed=42` produce identical paths |
| GBM statistics | With events off (`event_probability=0`), `seed` fixed and a large `time_scale`, the sample std of log-returns ≈ `σ·√Δt` within 5 %, and the mean ≈ `(μ − σ²/2)·Δt` |
| Correlation | Over 20k steps, the empirical correlation of AAPL/MSFT log-returns ≈ 0.6 and AAPL/JPM ≈ 0.3 (±0.05) |
| Cholesky | `L @ L.T` ≈ correlation matrix after adds and removes |
| Events | `event_probability=1.0` → every step moves each ticker by ≥ 2 % in absolute terms |
| Add/remove | Arrays stay aligned: after removing a middle ticker, `get_price` for the others is unchanged |
| Data source | `await src.start(["AAPL","TSLA"])` → cache has both immediately. After `asyncio.sleep(1.1)`, `cache.version` has advanced by ≥ 4. `stop()` twice doesn't raise |
| Interface conformance | Shared parametrised lifecycle test with `MassiveDataSource` (see `MARKET_INTERFACE.md` §10) |

Example:

```python
import numpy as np
from app.market.simulator import GBMSimulator

def test_correlated_tech_stocks():
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], event_probability=0, seed=7)
    paths = np.array([list(sim.step().values()) for _ in range(20_000)])
    r = np.diff(np.log(paths), axis=0)
    c = np.corrcoef(r.T)
    assert abs(c[0, 1] - 0.6) < 0.05   # AAPL–MSFT, same sector
    assert abs(c[0, 2] - 0.3) < 0.05   # AAPL–JPM, cross sector
```
