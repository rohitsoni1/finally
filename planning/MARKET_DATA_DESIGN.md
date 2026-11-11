# Market Data Backend — Detailed Design

> **Status:** implementation-ready design for the FinAlly market data layer: the unified API, the GBM simulator, the Massive REST poller, the price cache and the SSE stream.
>
> **Sources:** `PLAN.md` §6 and §8, `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md` and `MASSIVE_API.md`. This document brings them together into a single build spec and **supersedes them where they disagree** (see §14).
>
> **Verified:** every code listing in this document was run as a real package before being written here (Python 3.13, `massive` 2.9.2, `numpy`, FastAPI, `pytest-asyncio`). The test suite in §12 (32 tests) passes. A uvicorn run served `/api/stream/prices` and `/api/health` correctly with the simulator.

---

## Table of contents

1. [Requirements](#1-requirements)
2. [Architecture](#2-architecture)
3. [Module layout and dependencies](#3-module-layout-and-dependencies)
4. [Data model: `models.py`](#4-data-model--modelspy)
5. [Price cache: `cache.py`](#5-price-cache--cachepy)
6. [Unified interface: `interface.py`](#6-unified-interface--interfacepy)
7. [Source selection: `factory.py`](#7-source-selection--factorypy)
8. [Simulator: `seed_prices.py` and `simulator.py`](#8-simulator)
9. [Massive API: `massive_source.py`](#9-massive-api--massive_sourcepy)
10. [SSE stream: `stream.py`](#10-sse-stream--streampy)
11. [FastAPI integration and consumers](#11-fastapi-integration-and-consumers)
12. [Tests](#12-tests)
13. [Implementation checklist](#13-implementation-checklist)
14. [Changes from the earlier planning docs](#14-changes-from-the-earlier-planning-docs)

---

## 1. Requirements

| # | Requirement (from `PLAN.md`) | Where it is met |
|---|---|---|
| R1 | Simulator by default; Massive when `MASSIVE_API_KEY` is set and non-empty | `factory.py` §7 |
| R2 | Both implement one abstract interface, and downstream code doesn't know which is running | `interface.py` §6 |
| R3 | GBM with per-ticker drift and volatility, ~500 ms updates | `simulator.py` §8 |
| R4 | Correlated moves (tech moves together) | Cholesky sector correlation §8.1 |
| R5 | Random 2–5 % "events" | `GBMSimulator.step` §8.3 |
| R6 | Realistic seed prices (AAPL ~$190 …) | `seed_prices.py` §8.2 |
| R7 | Massive REST polling for the union of watched tickers; free tier every 15 s | `MassiveDataSource` §9 |
| R8 | Shared in-memory cache holding latest price, previous price and timestamp | `PriceCache` §5 |
| R9 | `GET /api/stream/prices` SSE at ~500 ms, with ticker, price, previous price, timestamp and direction | `stream.py` §10 |
| R10 | Dynamic ticker set (watchlist add/remove, also from the LLM) | `add_ticker` / `remove_ticker` §6, §11 |
| R11 | Unit tests: valid prices, GBM maths, Massive parsing, interface conformance | §12 |

Non-goals: order books, historical bar storage, WebSockets, multi-user fan-out (the cache design already supports that).

---

## 2. Architecture

```
                 create_market_data_source(cache)   ◄── reads MASSIVE_API_KEY
                              │
            ┌─────────────────┴──────────────────┐
            ▼                                    ▼
   SimulatorDataSource                  MassiveDataSource
   GBMSimulator.step() every 0.5 s      poll loop every 15 s
   (pure numpy, on the event loop)      SDK calls in asyncio.to_thread
                                        response cache (120 s) + call budget (5/min)
                                        mode: snapshot (paid) | eod (free)
            │ cache.update(...)                  │ cache.update(...)
            └─────────────────┬──────────────────┘
                              ▼
                    PriceCache  (thread-safe, versioned, ticker → PriceUpdate)
                              │ reads only
     ┌─────────────┬──────────┴───────┬──────────────┬─────────────┐
     ▼             ▼                  ▼              ▼             ▼
 SSE stream    POST /trade       GET /portfolio  GET /watchlist  LLM context
 (version poll (fill price)      (valuation,     (latest         (prices in
  every 0.5 s)                    snapshots)      prices)         prompt)
```

**Design principles**

1. **Producers write, consumers read.** A data source's only output is `cache.update(...)`. Nothing asks a source for a price. Reads are synchronous, O(1) and never touch the network.
2. **One interface, two implementations.** `MarketDataSource` is an ABC with five methods plus `status()`. Tests and API routes can swap in a `FakeSource`.
3. **One background task per process.** It is started and stopped in FastAPI's `lifespan`. Every exception inside the loop is caught and logged, so the loop never dies.
4. **Dynamic ticker set.** Tracked tickers = watchlist ∪ held positions (§11.3).
5. **Rate limits are a hard constraint.** The Massive source cannot exceed `MASSIVE_MAX_CALLS_PER_MIN` real calls in any rolling 60 s, whatever the user does.

**Concurrency model**

| Actor | Thread | Touches |
|---|---|---|
| Simulator tick loop | event loop | `GBMSimulator` arrays, `PriceCache.update` |
| Massive poll loop | event loop + one worker thread at a time (`asyncio.to_thread`) | SDK, `TTLResponseCache`, `CallBudget` (worker only, sequential), `PriceCache.update` |
| API routes / SSE | event loop | `PriceCache` reads, `add_ticker` / `remove_ticker` |

`PriceCache` uses a `threading.Lock` because the Massive worker thread writes to it. The Massive poll takes a **frozen copy** of the ticker set before entering the worker thread, so a watchlist change during a poll can't raise `set changed size during iteration`.

---

## 3. Module layout and dependencies

```
backend/
├── pyproject.toml
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app + lifespan (§11)
│   └── market/
│       ├── __init__.py             # public API
│       ├── models.py               # PriceUpdate, normalize_ticker
│       ├── cache.py                # PriceCache
│       ├── interface.py            # MarketDataSource ABC
│       ├── factory.py              # create_market_data_source()
│       ├── seed_prices.py          # seeds, GBM params, sectors
│       ├── simulator.py            # GBMSimulator + SimulatorDataSource
│       ├── massive_source.py       # TTLResponseCache, CallBudget, MassiveDataSource
│       └── stream.py               # GET /api/stream/prices
├── scripts/
│   └── market_demo.py              # terminal demo (§11.6)
└── tests/
    └── market/
        ├── conftest.py
        ├── test_models_cache.py
        ├── test_simulator.py
        ├── test_massive.py
        └── test_factory_stream.py
```

Everything else in the backend imports only from the package root:

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source, normalize_ticker
```

**Dependencies** (`backend/pyproject.toml`):

```bash
cd backend
uv add fastapi "uvicorn[standard]" numpy massive
uv add --dev pytest pytest-asyncio httpx
```

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
```

`massive` pulls in `urllib3`, which `massive_source.py` imports directly for `MaxRetryError` (§9.4). `massive` is imported lazily by the factory, so simulator-only runs never load it.

---

## 4. Data model: `models.py`

`PriceUpdate` is an immutable value object. Its `to_dict()` is **the wire format** used by SSE and by `/api/watchlist`, so the frontend sees one shape everywhere.

| Field | Meaning | Used for |
|---|---|---|
| `price` | latest price, rounded to cents | display, trade fills, valuation |
| `previous_price` | price at the previous update | flash colour (`direction`) |
| `reference_price` | previous close (Massive snapshot), session open (Massive EOD), or start price (simulator) | daily change % |
| `timestamp` | Unix seconds of the price (source time where available) | charts |

`normalize_ticker` is the single place where symbols are validated. API routes call it and turn the `ValueError` into an HTTP 400. Without it, the simulator would happily invent a price for `"hello world"`.

```python
"""Market data models shared by every producer and consumer."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# 1–10 chars: letters first, then letters/digits/'.'/'-' (BRK.B, BF-B).
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def normalize_ticker(raw: str) -> str:
    """Upper-case + strip a ticker and validate its shape.

    Raises ValueError for anything that cannot be a US equity symbol.
    Every public entry point (API routes, LLM actions, data sources) calls this.
    """
    t = (raw or "").strip().upper()
    if not _TICKER_RE.match(t):
        raise ValueError(f"Invalid ticker symbol: {raw!r}")
    return t


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of one ticker's price at a point in time."""

    ticker: str
    price: float
    previous_price: float  # price at the previous update -> flash colour
    reference_price: float  # previous close / session open -> daily change %
    timestamp: float = field(default_factory=time.time)  # Unix seconds

    @property
    def change(self) -> float:
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def day_change(self) -> float:
        return round(self.price - self.reference_price, 4)

    @property
    def day_change_percent(self) -> float:
        if self.reference_price == 0:
            return 0.0
        return round((self.price - self.reference_price) / self.reference_price * 100, 4)

    @property
    def direction(self) -> str:
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    def to_dict(self) -> dict:
        """Wire format for SSE events and /api/watchlist."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "reference_price": self.reference_price,
            "change": self.change,
            "change_percent": self.change_percent,
            "day_change": self.day_change,
            "day_change_percent": self.day_change_percent,
            "direction": self.direction,
            "timestamp": self.timestamp,
        }
```

---

## 5. Price cache: `cache.py`

Semantics:

- `update()` rounds to cents. `previous_price` chains from the last entry (the first update is `flat`). `reference_price` is **sticky**: it is set once (explicitly or as the first price) and kept until a source passes a new one.
- `version` increments on every mutation, so the SSE loop compares one integer instead of diffing dicts.
- `wait_for()` helps the watchlist route: after `add_ticker`, wait briefly to confirm the symbol got a price before responding (§11.4).

```python
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
```

---

## 6. Unified interface: `interface.py`

```python
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
```

**Contract** (both implementations, checked by `test_both_sources_conform_to_interface`):

| Rule | Detail |
|---|---|
| Normalised tickers | `add_ticker` validates with `normalize_ticker` (raises `ValueError`). `remove_ticker` only upper-cases, so removing junk is a no-op, not an error |
| Idempotent | Adding a tracked ticker or removing an untracked one does nothing. `stop()` is safe to call twice or without `start()` |
| Cache-only output | Sources write `cache.update(...)` and never return prices |
| Remove clears cache | `remove_ticker` calls `cache.remove`, so SSE stops sending the ticker immediately |
| Never crash | Loop exceptions are logged. `start()` does not raise on network failure (Massive starts with an empty cache and fills in later) |
| `get_tickers()` sorted | Deterministic for tests and `/api/health` |

---

## 7. Source selection: `factory.py`

```python
"""Pick the market data source from the environment."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r", name, os.getenv(name))
        return default


def create_market_data_source(cache: PriceCache) -> MarketDataSource:
    """MASSIVE_API_KEY set and non-empty -> Massive; otherwise -> simulator."""
    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    if api_key:
        from .massive_source import MassiveDataSource  # lazy: simulator users skip the SDK

        interval = _env_float("MASSIVE_POLL_INTERVAL", 15.0)
        ttl = _env_float("MASSIVE_CACHE_TTL", 120.0)
        max_calls = int(_env_float("MASSIVE_MAX_CALLS_PER_MIN", 5))
        logger.info("Market data: Massive API (poll %.0fs, cache TTL %.0fs, <=%d calls/min)",
                    interval, ttl, max_calls)
        return MassiveDataSource(api_key=api_key, cache=cache, poll_interval=interval,
                                 cache_ttl=ttl, max_calls_per_min=max_calls)

    from .simulator import SimulatorDataSource

    logger.info("Market data: built-in simulator")
    return SimulatorDataSource(cache=cache)
```

| Env var | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Non-empty (after strip) selects Massive |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between poll-loop wake-ups. Most wake-ups are cache hits and make **no** API call |
| `MASSIVE_CACHE_TTL` | `120` | Seconds a snapshot response is reused. EOD mode uses `max(TTL, 900)` |
| `MASSIVE_MAX_CALLS_PER_MIN` | `5` | Hard ceiling on real API calls per rolling minute (free tier). Raise it for paid plans |

Only `MASSIVE_API_KEY` is in `PLAN.md` §5. The other three are optional tuning knobs and should go in `.env.example` commented out.

---

## 8. Simulator

### 8.1 Maths

**GBM step** per ticker, with annualised drift μ and volatility σ:

```
S(t+Δt) = S(t) · exp( (μ − σ²/2)·Δt + σ·√Δt·Z )
```

- `exp` keeps prices strictly positive. The `−σ²/2` Itô term makes the expected price grow at μ.
- **Δt** = one 0.5 s tick in trading-years: `0.5 / (252 × 6.5 × 3600) ≈ 8.48e-8`. For AAPL (σ = 22 %) that is about ±1.2 ¢ per tick on $190, or about ±0.5 % per hour, which looks like a believable intraday tape. `time_scale` multiplies Δt to speed the demo up (move size scales with √time_scale).

**Correlated shocks.** `Z = L·ε`, where `ε ~ N(0, I)` and `L = cholesky(C)`. `C` is a block matrix built from the sector map:

| Pair | ρ |
|---|---|
| Same sector (tech–tech, finance–finance) | 0.6 |
| Different sectors, TSLA with anything, unknown tickers with each other | 0.3 |
| Diagonal | 1.0 |

For ρ ∈ [0, 1) this matrix is positive-definite. `L` is rebuilt only when the ticker set changes (O(n³) once, not per tick). Each step is one mat-vec product plus element-wise ops, which takes microseconds even for 100 tickers. That is why it runs directly on the event loop.

**Events.** Each tick, each ticker independently has `p = 0.001` of a jump `S ← S·(1 ± U(0.02, 0.05))`. With 10 tickers at 2 ticks/s that is about **one event every 50 s** somewhere on the board.

### 8.2 Parameters: `seed_prices.py`

```python
"""Seed prices, per-ticker GBM parameters and sector map for the simulator."""

from __future__ import annotations

import zlib

SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00, "TSLA": 250.00,
    "NVDA": 130.00, "META": 500.00, "JPM": 200.00, "V": 280.00, "NFLX": 650.00,
}

# Annualised drift (mu) and volatility (sigma)
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"mu": 0.08, "sigma": 0.22},
    "GOOGL": {"mu": 0.08, "sigma": 0.25},
    "MSFT": {"mu": 0.08, "sigma": 0.22},
    "AMZN": {"mu": 0.10, "sigma": 0.28},
    "TSLA": {"mu": 0.10, "sigma": 0.55},
    "NVDA": {"mu": 0.15, "sigma": 0.50},
    "META": {"mu": 0.10, "sigma": 0.35},
    "JPM": {"mu": 0.06, "sigma": 0.18},
    "V": {"mu": 0.06, "sigma": 0.16},
    "NFLX": {"mu": 0.08, "sigma": 0.35},
}
DEFAULT_PARAMS: dict[str, float] = {"mu": 0.05, "sigma": 0.30}

SECTORS: dict[str, str] = {
    "AAPL": "tech", "GOOGL": "tech", "MSFT": "tech", "AMZN": "tech",
    "META": "tech", "NVDA": "tech", "NFLX": "tech",
    "JPM": "finance", "V": "finance",
    "TSLA": "tsla",  # its own bucket -> only cross-sector correlation
}
DEFAULT_SECTOR = "other"
# Sectors whose members do NOT get intra-sector correlation with each other
UNCORRELATED_SECTORS = frozenset({"tsla", DEFAULT_SECTOR})

INTRA_SECTOR_CORR = 0.6
CROSS_SECTOR_CORR = 0.3


def seed_price_for(ticker: str) -> float:
    """Known tickers use SEED_PRICES; unknown ones get a stable $50.00–$299.99
    derived from the symbol (crc32, not hash(), which is salted per process)."""
    if ticker in SEED_PRICES:
        return SEED_PRICES[ticker]
    return 50.0 + (zlib.crc32(ticker.encode()) % 25_000) / 100


def params_for(ticker: str) -> dict[str, float]:
    return TICKER_PARAMS.get(ticker, DEFAULT_PARAMS)


def sector_for(ticker: str) -> str:
    return SECTORS.get(ticker, DEFAULT_SECTOR)
```

Unknown tickers (e.g. `PYPL`) get a deterministic seed in $50–$300, default μ = 5 %, σ = 30 %, and sector `other`.

### 8.3 Engine and data source: `simulator.py`

There are two classes with a strict split:

- **`GBMSimulator`**: pure and synchronous, with an injectable RNG `seed`. It holds full-precision prices. Rounding happens only in `PriceCache`, because rounding inside the simulator would bias small moves to zero.
- **`SimulatorDataSource`**: the async `MarketDataSource` wrapper. It seeds the cache on add, with `reference_price` = start price, so "day change %" means "since the app started". It ticks on a steady cadence that subtracts step time.

```python
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
```

`SimulatorDataSource.add_ticker` accepts an optional `price=` so the backend *may* resume held positions from their last trade price instead of the seed, avoiding a visible jump after a restart. This is optional and not required by the PLAN.

### 8.4 Tuning knobs (constructor args, no env vars)

| Arg | Default | Effect |
|---|---|---|
| `tick_seconds` | 0.5 | update cadence |
| `time_scale` | 1.0 | bigger moves per tick (demo mode) |
| `event_probability` | 0.001 | per ticker, per tick |
| `event_range` | (0.02, 0.05) | jump size |
| `seed` | None | deterministic paths for tests |

---

## 9. Massive API: `massive_source.py`

### 9.1 What the plans allow

| Plan | Snapshot endpoint | Data | Strategy |
|---|---|---|---|
| Basic (free), 5 calls/min | ❌ (`NOT_AUTHORIZED`) | end-of-day | **`eod` mode**: `get_grouped_daily_aggs(date)` returns every US ticker in 1 call |
| Starter / Developer | ✅ | 15-min delayed | **`snapshot` mode**: `get_snapshot_all("stocks", tickers=[...])`, 1 call for the whole watchlist |
| Advanced+ | ✅ | real time | snapshot mode; lower `MASSIVE_CACHE_TTL` (e.g. 5) and raise the budget |

The mode is **auto-detected**. The source tries snapshot first and switches permanently to `eod` on a `NOT_AUTHORIZED` `BadResponse`. The mode is only committed after a real API answer, so a throttled first poll doesn't lock in the wrong mode.

Field mapping:

| Mode | `price` | `reference_price` | `timestamp` |
|---|---|---|---|
| snapshot | `last_trade.price`, falling back to `day.close` | `prev_day.close` | `updated / 1e9` (ns → s) |
| eod | `close` | `open` | `timestamp / 1000` (ms → s) |

### 9.2 Rate-limit protection

```
 poll wake-up (every 15 s, or immediately after add_ticker)
      │
      ▼
 response cached and fresh (<TTL) and requested-set ⊇ tickers? ──yes──► reuse it, 0 calls
      │ no
      ▼
 CallBudget.try_acquire()  (<5 calls in the last 60 s)? ──no──► serve stale response, retry later
      │ yes
      ▼
 SDK call (retries=0) ──ok──► store (requested set, data) ──► PriceCache.update for tracked tickers
      │ 429 (MaxRetryError)
      └──► budget.exhaust() (no calls for 60 s) ──► serve stale response
```

Resulting real call volume on the free tier:

| Situation | Real calls |
|---|---|
| Steady state, EOD mode | 1–2 per **15 min** (today usually empty before the close, then previous session) |
| Steady state, snapshot mode | 1 per 2 min |
| User adds a ticker (EOD) | **0**: the grouped response already holds all ~10k tickers |
| User adds a ticker (snapshot) | 1 (budget permitting), otherwise it appears at the next refresh |
| Invalid ticker in the watchlist | 0 extra: coverage is checked against the *requested* set, not the returned one |
| Monday morning / holiday walk-back | 2–3 once (weekends are skipped without a call) |
| Restart loop or error storm | capped at 5/min |

### 9.3 Code

```python
"""Massive (formerly Polygon.io) REST poller with strict rate-limit protection."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import date, timedelta
from typing import Any, Callable

from massive import RESTClient
from massive.exceptions import AuthError, BadResponse
from urllib3.exceptions import MaxRetryError

from .cache import PriceCache
from .interface import MarketDataSource
from .models import normalize_ticker

logger = logging.getLogger(__name__)

# Per-ticker payload kept in the response cache: (price, reference_price, timestamp_s)
Quote = tuple[float, float | None, float | None]

EOD_MIN_TTL = 900.0  # end-of-day bars change once a day; never refetch more than every 15 min
EOD_LOOKBACK_DAYS = 7  # covers weekends + long holiday weekends


# ── rate-limit helpers ───────────────────────────────────────────────
class TTLResponseCache:
    """API responses kept for `ttl` s. Expired entries remain readable as stale
    data, served when the call budget is exhausted or the API errors."""

    def __init__(self, ttl: float = 120.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._store: dict[str, tuple[float, Any]] = {}

    def get_fresh(self, key: str, ttl: float | None = None) -> Any | None:
        hit = self._store.get(key)
        if hit and self._clock() - hit[0] < (self.ttl if ttl is None else ttl):
            return hit[1]
        return None

    def get_stale(self, key: str) -> Any | None:
        hit = self._store.get(key)
        return hit[1] if hit else None

    def put(self, key: str, value: Any) -> None:
        self._store[key] = (self._clock(), value)

    def age(self, key: str) -> float | None:
        hit = self._store.get(key)
        return round(self._clock() - hit[0], 1) if hit else None


class CallBudget:
    """Sliding window: at most `max_calls` real API calls per `period` seconds."""

    def __init__(self, max_calls: int = 5, period: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_calls = max_calls
        self.period = period
        self._clock = clock
        self._calls: deque[float] = deque()

    def _prune(self) -> float:
        now = self._clock()
        while self._calls and now - self._calls[0] >= self.period:
            self._calls.popleft()
        return now

    def try_acquire(self) -> bool:
        now = self._prune()
        if len(self._calls) < self.max_calls:
            self._calls.append(now)
            return True
        return False

    def exhaust(self) -> None:
        """After a 429: treat the whole window as used for the next `period` s."""
        now = self._clock()
        self._calls = deque([now] * self.max_calls)

    @property
    def used(self) -> int:
        self._prune()
        return len(self._calls)


# ── error classification ─────────────────────────────────────────────
def is_rate_limited(exc: BaseException) -> bool:
    # With retries=0 the SDK's urllib3 Retry turns a 429 into
    # MaxRetryError(reason=ResponseError('too many 429 error responses')),
    # NOT BadResponse. Keep the body check as a fallback.
    if isinstance(exc, MaxRetryError):
        return "429" in str(exc.reason)
    msg = str(exc).lower()
    return "exceeded the maximum" in msg or "too many requests" in msg


def is_not_entitled(exc: BaseException) -> bool:
    """403 body on plans without snapshot access, e.g.
    {"status":"NOT_AUTHORIZED","message":"You are not entitled to this data..."}"""
    msg = str(exc).lower()
    return isinstance(exc, BadResponse) and ("not_authorized" in msg or "not entitled" in msg)


def is_bad_key(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return isinstance(exc, AuthError) or "unknown api key" in msg or "invalid api key" in msg


class _Throttled(Exception):
    """Internal: no budget left or 429 received; serve stale data."""


# ── data source ──────────────────────────────────────────────────────
class MassiveDataSource(MarketDataSource):
    """Polls Massive for the tracked tickers and writes results to PriceCache.

    Modes (auto-detected on the first successful call):
      snapshot – get_snapshot_all(tickers=...)      paid plans, 1 call/refresh
      eod      – get_grouped_daily_aggs(<session>)  free plan, 1 call/refresh
    """

    name = "massive"

    def __init__(
        self,
        api_key: str,
        cache: PriceCache,
        poll_interval: float = 15.0,
        cache_ttl: float = 120.0,
        max_calls_per_min: int = 5,
        client: Any | None = None,  # injectable for tests
    ) -> None:
        # Pass the key explicitly (SDK reads its env default at import time).
        # retries=0: SDK auto-retries on 429/5xx would burn the 5/min quota.
        self._client = client or RESTClient(api_key=api_key, retries=0)
        self._cache = cache
        self._interval = poll_interval
        self._responses = TTLResponseCache(ttl=cache_ttl)
        self._budget = CallBudget(max_calls=max_calls_per_min, period=60.0)
        self._tickers: set[str] = set()
        self._mode: str | None = None  # None (undetected) | "snapshot" | "eod"
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._last_error: str | None = None
        self._last_success: float | None = None  # wall clock of last fresh API data

    # ── lifecycle ────────────────────────────────────────────────
    async def start(self, tickers: list[str]) -> None:
        self._tickers = {normalize_ticker(t) for t in tickers}
        await self._poll_once()  # best-effort seed before the first SSE event
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="massive-poller")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def add_ticker(self, ticker: str) -> None:
        t = normalize_ticker(ticker)
        if t not in self._tickers:
            self._tickers.add(t)
            self._wake.set()  # poll now: served from cached response if it has t

    async def remove_ticker(self, ticker: str) -> None:
        t = ticker.strip().upper()
        self._tickers.discard(t)
        self._cache.remove(t)

    def get_tickers(self) -> list[str]:
        return sorted(self._tickers)

    def status(self) -> dict:
        key = self._mode or "snapshot"
        return {
            "source": self.name,
            "mode": self._mode,
            "tickers": len(self._tickers),
            "missing": sorted(t for t in self._tickers if t not in self._cache),
            "response_age_s": self._responses.age(key),
            "calls_last_minute": self._budget.used,
            "max_calls_per_minute": self._budget.max_calls,
            "last_success": self._last_success,
            "last_error": self._last_error,
        }

    # ── loop ─────────────────────────────────────────────────────
    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            await self._poll_once()

    async def _poll_once(self) -> None:
        tickers = frozenset(self._tickers)  # snapshot: the set may change while we're in a thread
        if not tickers:
            return
        try:
            if self._mode in (None, "snapshot"):
                try:
                    await asyncio.to_thread(self._refresh_snapshot, tickers)
                    return
                except BadResponse as e:
                    if not is_not_entitled(e):
                        raise
                    logger.warning("Massive plan has no snapshot access; using end-of-day prices")
                    self._mode = "eod"
            await asyncio.to_thread(self._refresh_eod, tickers)
        except Exception as e:  # never kill the loop
            self._last_error = f"{type(e).__name__}: {str(e)[:200]}"
            if is_bad_key(e):
                logger.error("Massive rejected the API key; check MASSIVE_API_KEY")
            else:
                logger.exception("Massive poll failed; keeping last cached prices")

    # ── guarded API call ─────────────────────────────────────────
    def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """One real API call if the budget allows; raise _Throttled otherwise."""
        if not self._budget.try_acquire():
            raise _Throttled("call budget exhausted")
        try:
            return fn(*args, **kwargs)
        except (BadResponse, MaxRetryError) as e:
            if is_rate_limited(e):
                logger.warning("Massive rate limit (429); backing off for 60 s")
                self._budget.exhaust()
                raise _Throttled("429") from e
            raise

    # ── snapshot mode (Starter+ plans) ───────────────────────────
    def _refresh_snapshot(self, tickers: frozenset[str]) -> None:
        key = "snapshot"
        cached = self._responses.get_fresh(key)
        # Cache entry = (tickers requested, data). Compare against what we ASKED for,
        # so an invalid ticker that never comes back doesn't force a call every poll.
        if cached is not None and tickers <= cached[0]:
            self._publish(cached[1], tickers, fresh=False)
            return
        try:
            snaps = self._call(self._client.get_snapshot_all, "stocks", tickers=sorted(tickers))
        except _Throttled:
            stale = self._responses.get_stale(key)
            self._publish(stale[1] if stale else {}, tickers, fresh=False)
            return
        data: dict[str, Quote] = {}
        for s in snaps or []:
            price = (
                s.last_trade.price if s.last_trade and s.last_trade.price
                else s.day.close if s.day and s.day.close
                else None
            )
            if price is None:
                continue
            ref = s.prev_day.close if s.prev_day and s.prev_day.close else None
            ts = s.updated / 1e9 if s.updated else None  # ns -> s
            data[s.ticker] = (price, ref, ts)
        self._mode = "snapshot"
        self._responses.put(key, (tickers, data))
        self._mark_success()
        self._publish(data, tickers, fresh=True)

    # ── end-of-day mode (free Basic plan) ────────────────────────
    def _refresh_eod(self, tickers: frozenset[str]) -> None:
        key = "eod"
        ttl = max(self._responses.ttl, EOD_MIN_TTL)
        cached = self._responses.get_fresh(key, ttl=ttl)
        if cached is not None:
            self._publish(cached, tickers, fresh=False)  # holds ALL US tickers
            return
        d = date.today()
        try:
            for _ in range(EOD_LOOKBACK_DAYS):
                if d.weekday() < 5:  # Sat/Sun never have bars; holidays come back empty
                    bars = self._call(self._client.get_grouped_daily_aggs, d.isoformat(), adjusted=True)
                    if bars:
                        # Keep every ticker (~10k tuples, ~1 MB) so watchlist adds cost 0 calls.
                        # reference = session open (free plan has no intraday data).
                        data: dict[str, Quote] = {
                            b.ticker: (b.close, b.open, b.timestamp / 1000 if b.timestamp else None)
                            for b in bars
                            if b.close
                        }
                        self._responses.put(key, data)
                        self._mark_success()
                        self._publish(data, tickers, fresh=True)
                        return
                d -= timedelta(days=1)
            logger.warning("Massive: no grouped-daily data in the last %d days", EOD_LOOKBACK_DAYS)
        except _Throttled:
            self._publish(self._responses.get_stale(key) or {}, tickers, fresh=False)

    # ── write to PriceCache ──────────────────────────────────────
    def _mark_success(self) -> None:
        self._last_success = time.time()
        self._last_error = None

    def _publish(self, data: dict[str, Quote], tickers: frozenset[str], fresh: bool) -> None:
        """fresh=True  -> new API data: write every tracked ticker.
        fresh=False -> reused response: only fill tickers missing from PriceCache
        (e.g. one just added). Rewriting identical prices would reset
        previous_price ('flat') and bump the SSE version for nothing."""
        for t in tickers:
            if t not in data or t not in self._tickers:  # skip if removed mid-poll
                continue
            if fresh or t not in self._cache:
                price, ref, ts = data[t]
                self._cache.update(t, price, timestamp=ts, reference_price=ref)
```

### 9.4 Error handling (verified against `massive` 2.9.2)

| Situation | What the SDK actually raises | Handling |
|---|---|---|
| HTTP 429 | **`urllib3.exceptions.MaxRetryError`** with `reason=ResponseError('too many 429 error responses')`. The SDK's urllib3 `Retry` has 429 in its `status_forcelist`, so even with `retries=0` it never reaches `BadResponse` | `is_rate_limited` → `budget.exhaust()`, serve stale |
| HTTP 5xx (500/502/503/504) | `MaxRetryError` ("too many 500 error responses") | logged in `last_error`, retried at the next refresh |
| Plan lacks snapshot (403) | `BadResponse(body)` with `"NOT_AUTHORIZED"` / "not entitled" | switch to `eod` mode |
| Bad key | `BadResponse` with "Unknown API Key". `AuthError` only if the key is `None` | logged at ERROR, loop continues |
| Timeout / DNS | urllib3 errors | logged, retried at the next refresh |
| Ticker not found | simply absent from the response | never enters the cache; `status()["missing"]` lists it |

`BadResponse` carries only the raw response body, not the status code, so classification is string-based. The 429 behaviour was confirmed against a local HTTP server returning 429 with `retries=0` and `retries=1`.

### 9.5 Raw REST reference (debugging without the SDK)

```bash
# Paid plans: snapshot for the watchlist
curl -s "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT" \
  -H "Authorization: Bearer $MASSIVE_API_KEY" | jq '.tickers[] | {ticker, p: .lastTrade.p, prev: .prevDay.c}'
```

```bash
# All plans: whole-market end-of-day bars for one session
curl -s "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/2026-11-06?adjusted=true" \
  -H "Authorization: Bearer $MASSIVE_API_KEY" | jq '.results[] | select(.T=="AAPL")'
```

---

## 10. SSE stream: `stream.py`

```python
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
                       interval: float = SSE_INTERVAL) -> AsyncIterator[str]:
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
        elif now - last_sent >= KEEPALIVE_EVERY:
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
```

Behaviour:

- `retry: 1000` tells `EventSource` to reconnect after 1 s if the connection drops (the PLAN's "SSE resilience" E2E test).
- An event is sent **only when `cache.version` changed**. With the simulator that is every 0.5 s. With Massive it happens only on new data, plus a `: keepalive` comment every 15 s so proxies don't close the idle stream.
- Every event is a **full snapshot** of all tracked tickers. That is about 2.5 KB for 10 tickers, small enough to need no delta protocol, and a reconnecting client is consistent after the first message.
- The events are unnamed (`data:` only), so the browser's `onmessage` receives them.

Event example (one line on the wire):

```
id: 42
data: {"AAPL": {"ticker": "AAPL", "price": 190.04, "previous_price": 190.05, "reference_price": 190.0, "change": -0.01, "change_percent": -0.0053, "day_change": 0.04, "day_change_percent": 0.0211, "direction": "down", "timestamp": 1794384689.52}, "GOOGL": {...}}
```

Frontend consumer (reference for the Frontend Engineer):

```ts
// frontend/src/lib/prices.ts
export type PriceUpdate = {
  ticker: string; price: number; previous_price: number; reference_price: number;
  change: number; change_percent: number; day_change: number; day_change_percent: number;
  direction: "up" | "down" | "flat"; timestamp: number; // Unix seconds
};
export type ConnState = "connected" | "reconnecting" | "disconnected";

export function subscribePrices(
  onPrices: (p: Record<string, PriceUpdate>) => void,
  onState: (s: ConnState) => void,
): () => void {
  const es = new EventSource("/api/stream/prices");
  es.onopen = () => onState("connected");
  es.onmessage = (e) => onPrices(JSON.parse(e.data));
  es.onerror = () => onState(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
  return () => es.close();
}
// Flash: when direction !== "flat" and timestamp changed, add .flash-up/.flash-down for ~500 ms.
// Sparklines: append {time: timestamp, value: price} per ticker (cap ~300 points).
```

---

## 11. FastAPI integration and consumers

### 11.1 Lifespan wiring: `app/main.py`

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.market import PriceCache, create_market_data_source
from app.market.stream import router as stream_router

DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


def initial_tickers() -> list[str]:
    # Real app: sorted(set(db.get_watchlist_tickers()) | set(db.get_position_tickers()))
    return DEFAULT_TICKERS


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = PriceCache()
    source = create_market_data_source(cache)
    await source.start(initial_tickers())
    app.state.price_cache = cache
    app.state.market = source
    try:
        yield
    finally:
        await source.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(stream_router)


@app.get("/api/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "market": request.app.state.market.status()}
```

The DB layer (owned by the backend agent) provides `get_watchlist_tickers()` and `get_position_tickers()`. Until it exists, the stub returns the ten default tickers.

Dependency helpers for routes:

```python
from fastapi import Request
from app.market import MarketDataSource, PriceCache

def get_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache

def get_market(request: Request) -> MarketDataSource:
    return request.app.state.market
```

### 11.2 Health

`GET /api/health` returns `market.status()`:

```json
{"status": "ok", "market": {"source": "simulator", "tickers": 10}}
```

```json
{"status": "ok", "market": {"source": "massive", "mode": "eod", "tickers": 11, "missing": ["ZZZZ"],
  "response_age_s": 42.3, "calls_last_minute": 0, "max_calls_per_minute": 5,
  "last_success": 1794384689.5, "last_error": null}}
```

This is the first place to look when someone asks "why aren't prices moving?". On the free plan, unchanging EOD prices are correct behaviour.

### 11.3 Ticker-tracking rule

**tracked = watchlist ∪ tickers with an open position.** Positions need live prices for valuation even when they are not on the watchlist (e.g. the LLM bought one).

| Event | Market call |
|---|---|
| Watchlist add | `await market.add_ticker(t)` |
| Watchlist remove | `await market.remove_ticker(t)` **only if** no open position |
| Buy (any source) | `await market.add_ticker(t)` (no-op if already tracked) |
| Sell that closes a position | `await market.remove_ticker(t)` **only if** not on the watchlist |

### 11.4 Consumer examples (illustrative; the DB helpers belong to the backend agent)

```python
# Watchlist add — validate, persist, track, confirm a price exists
@router.post("/api/watchlist", status_code=201)
async def add_to_watchlist(body: TickerIn, cache=Depends(get_cache), market=Depends(get_market)):
    try:
        t = normalize_ticker(body.ticker)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.add_watchlist(t)                      # UNIQUE(user_id, ticker) → idempotent
    await market.add_ticker(t)
    quote = await cache.wait_for(t, timeout=2.0)   # simulator: instant; Massive: next poll
    return {"ticker": t, "quote": quote.to_dict() if quote else None}

# Watchlist remove
@router.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str, market=Depends(get_market)):
    t = ticker.strip().upper()
    db.remove_watchlist(t)
    if not db.has_position(t):
        await market.remove_ticker(t)
    return {"ticker": t, "removed": True}

# Trade execution — fill at the cached price
price = cache.get_price(t)
if price is None:
    raise HTTPException(400, f"No price available for {t}")

# Portfolio valuation / snapshots / LLM context
prices = cache.get_all()
positions_value = sum(p.quantity * prices[p.ticker].price for p in positions if p.ticker in prices)
total_value = cash + positions_value

# GET /api/watchlist
return [{"ticker": t, **(cache.get(t).to_dict() if t in cache else {"price": None})}
        for t in db.get_watchlist_tickers()]
```

### 11.5 Test double for API-route tests

```python
class FakeSource(MarketDataSource):
    name = "fake"
    def __init__(self, cache: PriceCache, price: float = 100.0):
        self.cache, self.price, self.tickers = cache, price, set()
    async def start(self, tickers):
        for t in tickers:
            await self.add_ticker(t)
    async def stop(self): pass
    async def add_ticker(self, t):
        t = normalize_ticker(t); self.tickers.add(t); self.cache.update(t, self.price)
    async def remove_ticker(self, t):
        t = t.strip().upper(); self.tickers.discard(t); self.cache.remove(t)
    def get_tickers(self): return sorted(self.tickers)
```

Override `app.state.market` / `app.state.price_cache` in a fixture to get deterministic fill prices.

### 11.6 Usage examples

Terminal demo, `backend/scripts/market_demo.py`:

```python
"""Watch the market data layer in the terminal: `uv run python scripts/market_demo.py`.

Uses Massive if MASSIVE_API_KEY is set, otherwise the simulator.
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from app.market import PriceCache, create_market_data_source  # noqa: E402

ARROW = {"up": "\033[32m▲\033[0m", "down": "\033[31m▼\033[0m", "flat": " "}


async def main(seconds: float = 5.0) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cache = PriceCache()
    source = create_market_data_source(cache)
    await source.start(["AAPL", "NVDA", "TSLA", "JPM"])
    await source.add_ticker("PYPL")  # dynamic add, like a watchlist change
    try:
        for _ in range(int(seconds / 0.5)):
            await asyncio.sleep(0.5)
            row = "  ".join(
                f"{t} {u.price:>8.2f}{ARROW[u.direction]} ({u.day_change_percent:+.2f}%)"
                for t, u in sorted(cache.get_all().items())
            )
            print(row)
        print(source.status())
    finally:
        await source.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
cd backend && uv run python scripts/market_demo.py
```

Sample output (simulator):

```
AAPL   190.02▲ (+0.01%)  JPM   200.01▲ (+0.01%)  NVDA   130.00  (+0.00%)  PYPL   179.97▲ (+0.04%)  TSLA   249.99▲ (-0.00%)
AAPL   190.01▼ (+0.01%)  JPM   200.00▼ (+0.00%)  NVDA   129.98▼ (-0.02%)  PYPL   179.97  (+0.04%)  TSLA   249.95▼ (-0.02%)
{'source': 'simulator', 'tickers': 5}
```

Run the server and watch the stream:

```bash
cd backend && uv run uvicorn app.main:app --port 8000
```

```bash
curl -N http://localhost:8000/api/stream/prices
```

Use the library in a REPL or script:

```python
import asyncio
from app.market import PriceCache
from app.market.simulator import SimulatorDataSource, GBMSimulator

# Pure engine, deterministic
sim = GBMSimulator(["AAPL", "MSFT"], seed=42, time_scale=100)
print(sim.step())                     # {'AAPL': 190.0..., 'MSFT': 420.0...}

# Full source writing to a cache
async def demo():
    cache = PriceCache()
    src = SimulatorDataSource(cache)
    await src.start(["AAPL"])
    await asyncio.sleep(1.1)
    print(cache.get("AAPL").to_dict())
    await src.stop()
asyncio.run(demo())
```

---

## 12. Tests

Location: `backend/tests/market/`. Run with `cd backend && uv run pytest tests/market -q` (**32 passed in ~2.5 s**).

| File | Covers |
|---|---|
| `test_models_cache.py` | `PriceUpdate` maths and zero guards, `normalize_ticker`, previous-price chaining, sticky reference, version, remove, `wait_for` |
| `test_simulator.py` | seed prices, stable unknown seeds, positivity, determinism, σ matches theory (±5 %), sector correlations 0.6 / 0.3 (±0.05), Cholesky after add/remove, event size, async lifecycle |
| `test_massive.py` | TTL cache and budget with a fake clock, error classifiers, snapshot parsing (ns→s, prev close), 8 polls in 120 s → 1 call, invalid ticker doesn't force refetch, NOT_AUTHORIZED → EOD with weekend walk-back, EOD add costs 0 calls, 429 back-off, budget cap, errors never raise |
| `test_factory_stream.py` | env-based selection (`retries == 0`), SSE generator output, real uvicorn SSE over HTTP, **interface conformance of both sources** |

### 12.1 `conftest.py`

```python
from types import SimpleNamespace as NS

import pytest

from app.market import PriceCache


@pytest.fixture
def cache() -> PriceCache:
    return PriceCache()


def snap(ticker, price, prev_close=None, updated_ns=1_700_000_000_000_000_000):
    """TickerSnapshot-shaped stand-in."""
    return NS(ticker=ticker, last_trade=NS(price=price), day=NS(close=price),
              prev_day=NS(close=prev_close) if prev_close else None, updated=updated_ns)


def bar(ticker, close, open_=None, ts_ms=1_700_000_000_000):
    """GroupedDailyAgg-shaped stand-in."""
    return NS(ticker=ticker, close=close, open=open_ or close, timestamp=ts_ms)


class FakeMassiveClient:
    """Counts calls; scripted responses or exceptions."""

    def __init__(self, snapshot=None, grouped=None):
        self.snapshot = snapshot  # callable(tickers) -> list | Exception instance
        self.grouped = grouped  # callable(date_str) -> list | Exception instance
        self.calls: list[tuple[str, object]] = []

    def get_snapshot_all(self, market_type, tickers=None, **kw):
        self.calls.append(("snapshot", tuple(tickers or ())))
        r = self.snapshot(tickers)
        if isinstance(r, Exception):
            raise r
        return r

    def get_grouped_daily_aggs(self, d, adjusted=None, **kw):
        self.calls.append(("grouped", d))
        r = self.grouped(d)
        if isinstance(r, Exception):
            raise r
        return r
```

### 12.2 `test_models_cache.py`

```python
import pytest

from app.market import PriceCache, PriceUpdate, normalize_ticker


def test_price_update_math():
    u = PriceUpdate("AAPL", 101.0, 100.0, 95.0, 1.0)
    assert u.direction == "up" and u.change == 1.0 and u.change_percent == 1.0
    assert u.day_change_percent == pytest.approx(6.3158, abs=1e-4)
    assert PriceUpdate("X", 1, 0, 0).change_percent == 0.0  # zero guards
    assert set(u.to_dict()) >= {"ticker", "price", "previous_price", "timestamp", "direction"}


@pytest.mark.parametrize("raw,ok", [(" aapl ", "AAPL"), ("brk.b", "BRK.B"), ("BF-B", "BF-B")])
def test_normalize_ok(raw, ok):
    assert normalize_ticker(raw) == ok


@pytest.mark.parametrize("raw", ["", "1ABC", "TOOLONGTICKER", "A B", "$$"])
def test_normalize_rejects(raw):
    with pytest.raises(ValueError):
        normalize_ticker(raw)


def test_cache_chains_previous_and_sticky_reference(cache: PriceCache):
    first = cache.update("aapl", 100.004, reference_price=99)
    assert first.previous_price == 100.0 and first.direction == "flat"
    second = cache.update("AAPL", 100.5)
    assert second.previous_price == 100.0 and second.reference_price == 99.0
    assert second.direction == "up" and cache.get_price("aapl") == 100.5


def test_cache_version_and_remove(cache: PriceCache):
    v0 = cache.version
    cache.update("A", 1)
    assert cache.version == v0 + 1 and "a" in cache
    cache.remove("A")
    cache.remove("A")  # idempotent, no extra bump
    assert cache.version == v0 + 2 and "A" not in cache and len(cache) == 0


async def test_wait_for(cache: PriceCache):
    assert await cache.wait_for("NOPE", timeout=0.1) is None
    cache.update("X", 5)
    assert (await cache.wait_for("X")).price == 5
```

### 12.3 `test_simulator.py`

```python
import asyncio

import numpy as np

from app.market.simulator import GBMSimulator, SimulatorDataSource


def test_seed_prices_and_stable_unknown():
    assert GBMSimulator(["AAPL"]).get_price("AAPL") == 190.0
    a, b = GBMSimulator(["PYPL"]).get_price("PYPL"), GBMSimulator(["PYPL"]).get_price("PYPL")
    assert a == b and 50 <= a < 300


def test_positive_and_deterministic():
    s1 = GBMSimulator(["AAPL", "TSLA"], time_scale=1000, seed=42)
    s2 = GBMSimulator(["AAPL", "TSLA"], time_scale=1000, seed=42)
    for _ in range(5000):
        p1, p2 = s1.step(), s2.step()
    assert p1 == p2 and all(v > 0 for v in p1.values())


def test_gbm_volatility_matches_sigma():
    sim = GBMSimulator(["AAPL"], event_probability=0, time_scale=1000, seed=1)
    path = np.array([sim.step()["AAPL"] for _ in range(20_000)])
    r = np.diff(np.log(path))
    expected = 0.22 * np.sqrt(sim.dt)
    assert abs(r.std() / expected - 1) < 0.05


def test_correlation_structure():
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], event_probability=0, seed=7)
    paths = np.array([list(sim.step().values()) for _ in range(20_000)])
    c = np.corrcoef(np.diff(np.log(paths), axis=0).T)
    assert abs(c[0, 1] - 0.6) < 0.05  # same sector
    assert abs(c[0, 2] - 0.3) < 0.05  # cross sector


def test_cholesky_tracks_adds_and_removes():
    sim = GBMSimulator(["AAPL", "JPM", "TSLA", "MSFT"])
    sim.remove_ticker("JPM")
    sim.add_ticker("PYPL")
    assert np.allclose(sim._chol @ sim._chol.T, sim.correlation_matrix())
    assert sim.tickers == ["AAPL", "TSLA", "MSFT", "PYPL"]
    assert sim.get_price("MSFT") == 420.0


def test_events_jump_at_least_two_percent():
    sim = GBMSimulator(["AAPL", "MSFT"], event_probability=1.0, seed=3)
    moved = sim.step()
    assert abs(moved["AAPL"] / 190 - 1) >= 0.019 and abs(moved["MSFT"] / 420 - 1) >= 0.019


async def test_data_source_lifecycle(cache):
    src = SimulatorDataSource(cache, tick_seconds=0.05)
    await src.start(["aapl", "TSLA"])
    assert cache.get_price("AAPL") == 190.0 and "TSLA" in cache
    v = cache.version
    await asyncio.sleep(0.3)
    assert cache.version >= v + 4
    await src.add_ticker("PYPL")
    assert "PYPL" in cache and src.get_tickers() == ["AAPL", "PYPL", "TSLA"]
    await src.remove_ticker("TSLA")
    assert "TSLA" not in cache
    await src.stop()
    await src.stop()  # idempotent
```

### 12.4 `test_massive.py`

```python
from datetime import date

import pytest
from massive.exceptions import BadResponse
from urllib3.exceptions import MaxRetryError, ResponseError

from app.market import massive_source as ms
from app.market.massive_source import CallBudget, MassiveDataSource, TTLResponseCache

from .conftest import FakeMassiveClient, bar, snap

NOT_AUTH = BadResponse('{"status":"NOT_AUTHORIZED","message":"You are not entitled to this data."}')
RATE_429 = MaxRetryError(None, "/v2/...", ResponseError("too many 429 error responses"))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(cache, client, clock=None, **kw):
    src = MassiveDataSource("k", cache, client=client, **kw)
    if clock:
        src._responses._clock = clock
        src._budget._clock = clock
    return src


def test_ttl_cache_and_budget():
    clk = Clock()
    c = TTLResponseCache(ttl=120, clock=clk)
    c.put("k", 1)
    clk.t += 119
    assert c.get_fresh("k") == 1
    clk.t += 2
    assert c.get_fresh("k") is None and c.get_stale("k") == 1
    b = CallBudget(5, 60, clock=clk)
    assert all(b.try_acquire() for _ in range(5)) and not b.try_acquire()
    clk.t += 60
    assert b.try_acquire()
    b.exhaust()
    assert not b.try_acquire()


def test_error_classifiers():
    assert ms.is_rate_limited(RATE_429)
    assert not ms.is_rate_limited(MaxRetryError(None, "/", ResponseError("too many 500 error responses")))
    assert ms.is_not_entitled(NOT_AUTH)
    assert ms.is_bad_key(BadResponse('{"status":"ERROR","error":"Unknown API Key"}'))


async def test_snapshot_mode_writes_cache_and_caches_response(cache):
    clk = Clock()
    client = FakeMassiveClient(snapshot=lambda t: [snap(x, 100.0 + i, prev_close=99.0) for i, x in enumerate(t)])
    src = make(cache, client, clk)
    await src.start(["AAPL", "MSFT"])
    assert src._mode == "snapshot"
    a = cache.get("AAPL")
    assert a.price == 100.0 and a.reference_price == 99.0 and a.timestamp == pytest.approx(1.7e9)
    for _ in range(7):  # 7 polls inside the TTL -> served from cache
        clk.t += 15
        await src._poll_once()
    assert len(client.calls) == 1
    clk.t += 30  # past 120 s
    await src._poll_once()
    assert len(client.calls) == 2


async def test_invalid_ticker_does_not_force_refetch(cache):
    client = FakeMassiveClient(snapshot=lambda t: [snap("AAPL", 1.0)])  # ZZZZ never returned
    src = make(cache, client, Clock())
    await src.start(["AAPL", "ZZZZ"])
    await src._poll_once()
    await src._poll_once()
    assert len(client.calls) == 1 and src.status()["missing"] == ["ZZZZ"]


async def test_free_plan_falls_back_to_eod_and_walks_back(cache, monkeypatch):
    class D(date):
        @classmethod
        def today(cls):
            return date(2026, 11, 9)  # Monday

    monkeypatch.setattr(ms, "date", D)
    grouped = {"2026-11-06": [bar("AAPL", 190.0, 188.0), bar("PYPL", 70.0)]}  # Friday
    client = FakeMassiveClient(snapshot=lambda t: NOT_AUTH, grouped=lambda d: grouped.get(d, []))
    src = make(cache, client, Clock())
    await src.start(["AAPL"])
    assert src._mode == "eod"
    assert [c[1] for c in client.calls] == [("AAPL",), "2026-11-09", "2026-11-06"]  # skipped Sat/Sun
    assert cache.get("AAPL").price == 190.0 and cache.get("AAPL").reference_price == 188.0
    await src.add_ticker("PYPL")  # already in the grouped response -> 0 calls
    await src._poll_once()
    assert cache.get_price("PYPL") == 70.0 and len(client.calls) == 3


async def test_429_backs_off_and_keeps_prices(cache):
    clk = Clock()
    responses = iter([[snap("AAPL", 100.0)], RATE_429])
    client = FakeMassiveClient(snapshot=lambda t: next(responses))
    src = make(cache, client, clk)
    await src.start(["AAPL"])
    clk.t += 121
    await src._poll_once()  # -> 429
    for _ in range(3):
        clk.t += 15
        await src._poll_once()  # budget exhausted: no calls
    assert len(client.calls) == 2 and cache.get_price("AAPL") == 100.0


async def test_budget_caps_calls(cache):
    clk = Clock()
    client = FakeMassiveClient(snapshot=lambda t: [snap(x, 1.0) for x in t])
    src = make(cache, client, clk, max_calls_per_min=5)
    await src.start(["A"])
    for i in range(10):  # each add forces a miss
        await src.add_ticker(f"T{i}")
        await src._poll_once()
    assert len(client.calls) == 5


async def test_poll_errors_never_raise(cache):
    client = FakeMassiveClient(snapshot=lambda t: RuntimeError("boom"))
    src = make(cache, client, Clock())
    await src.start(["AAPL"])  # does not raise
    assert "boom" in src.status()["last_error"]
    await src.stop()
    await src.stop()


async def test_remove_ticker(cache):
    client = FakeMassiveClient(snapshot=lambda t: [snap(x, 1.0) for x in t])
    src = make(cache, client, Clock())
    await src.start(["AAPL", "MSFT"])
    await src.remove_ticker("msft")
    assert "MSFT" not in cache and src.get_tickers() == ["AAPL"]
    await src.stop()
```

### 12.5 `test_factory_stream.py`

```python
import asyncio
import json

from fastapi import FastAPI

from app.market import MarketDataSource, create_market_data_source
from app.market.massive_source import MassiveDataSource
from app.market.simulator import SimulatorDataSource
from app.market.stream import price_events, router


def test_factory_selects_source(monkeypatch, cache):
    monkeypatch.setenv("MASSIVE_API_KEY", "  ")
    assert isinstance(create_market_data_source(cache), SimulatorDataSource)
    monkeypatch.delenv("MASSIVE_API_KEY")
    assert isinstance(create_market_data_source(cache), SimulatorDataSource)
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_CACHE_TTL", "10")
    src = create_market_data_source(cache)
    assert isinstance(src, MassiveDataSource) and src._responses.ttl == 10
    assert src._client.retries == 0


async def test_sse_generator_emits_events(cache):
    cache.update("AAPL", 190.0)
    gen = price_events(cache, request=None, interval=0.01)
    assert await anext(gen) == "retry: 1000\n\n"
    first = await anext(gen)
    assert first.startswith("id: 1\ndata: ")
    payload = json.loads(first.split("data: ", 1)[1])
    assert payload["AAPL"]["price"] == 190.0
    cache.update("AAPL", 191.0)
    second = await anext(gen)
    assert json.loads(second.split("data: ", 1)[1])["AAPL"]["direction"] == "up"
    await gen.aclose()


async def test_sse_endpoint_over_http(cache):
    """Real uvicorn server: proves the endpoint streams through the ASGI stack."""
    import httpx
    import uvicorn

    app = FastAPI()
    app.include_router(router)
    app.state.price_cache = cache
    cache.update("AAPL", 190.0)
    server = uvicorn.Server(uvicorn.Config(app, port=8765, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            async with client.stream("GET", "http://127.0.0.1:8765/api/stream/prices") as r:
                assert r.headers["content-type"].startswith("text/event-stream")
                async for line in r.aiter_lines():
                    if line.startswith("data: "):
                        assert "AAPL" in json.loads(line[6:])
                        break
    finally:
        server.should_exit = True
        await task


async def test_both_sources_conform_to_interface(cache):
    from .conftest import FakeMassiveClient, snap

    fake = FakeMassiveClient(snapshot=lambda t: [snap(x, 10.0) for x in t])
    for src in (SimulatorDataSource(cache), MassiveDataSource("k", cache, client=fake)):
        assert isinstance(src, MarketDataSource)
        await src.start(["AAPL"])
        assert "AAPL" in cache
        await src.add_ticker("NVDA")
        await src.add_ticker("NVDA")
        await asyncio.sleep(0.1)  # Massive serves new tickers on the woken poll
        assert "NVDA" in cache and src.get_tickers() == ["AAPL", "NVDA"]
        await src.remove_ticker("NVDA")
        assert "NVDA" not in cache
        assert src.status()["source"] == src.name
        await src.stop()
        await src.stop()
        cache.remove("AAPL")
```

> SSE note: `httpx.ASGITransport` buffers the full response body, so it cannot test an infinite stream. Test the generator directly, and use a real uvicorn server for the HTTP path, as above.

---

## 13. Implementation checklist

1. `uv init` in `backend/` (if not done). Add the dependencies and pytest config from §3.
2. Create `app/market/` in this order: `models.py` → `cache.py` → `interface.py` → `seed_prices.py` → `simulator.py` → `massive_source.py` → `factory.py` → `stream.py` → `__init__.py`.
3. Add the tests from §12 and get them green.
4. Wire `lifespan`, `stream_router` and `/api/health` into `app/main.py` (§11.1).
5. Replace `initial_tickers()` with DB-backed watchlist ∪ positions once the DB layer exists.
6. Have the watchlist, trade and chat routes follow the tracking rule in §11.3.
7. Add the optional Massive tuning vars to `.env.example` (commented out).
8. Smoke test: `uv run python scripts/market_demo.py` and `curl -N localhost:8000/api/stream/prices`.
9. If you have a Massive key: run once with it and check `/api/health` shows `mode` and `calls_last_minute ≤ 5`.

---

## 14. Changes from the earlier planning docs

This design follows `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md` and `MASSIVE_API.md`, with these corrections found while building and testing the prototype:

| # | Earlier design | Problem | This design |
|---|---|---|---|
| 1 | 429 detected by matching "429" in a `BadResponse` message | With the SDK's urllib3 `Retry`, a 429 raises **`MaxRetryError`**, so the back-off never triggered and every poll would retry | `is_rate_limited` checks `MaxRetryError.reason` (verified against a local 429 server) |
| 2 | Snapshot cache considered "covering" only if every tracked ticker came **back** | One invalid symbol caused a cache miss on every poll, burning the budget (5 calls/min) | Cache stores the **requested** set, and coverage is checked against it |
| 3 | Worker thread iterated the live `self._tickers` set | A concurrent `add_ticker` raises `RuntimeError: set changed size during iteration` | Poll takes a `frozenset` snapshot. `_publish` skips tickers removed mid-poll |
| 4 | Mode set to `snapshot` even when the first call was throttled (no answer) | A free-tier key could be stuck in the wrong mode | Mode is committed only after a real response |
| 5 | EOD data refetched every 120 s | EOD bars change once a day, and "today" is usually an empty call before the close, so 2 calls per refresh | EOD TTL = `max(TTL, 900 s)` |
| 6 | No ticker validation | The simulator invents prices for any string | `normalize_ticker` regex, raising `ValueError` → HTTP 400 |
| 7 | SSE keepalive sent every 0.5 s when idle | Needless traffic in Massive mode | Keepalive only after 15 s of silence; events carry `id:` |
| 8 | `/api/health` had no source diagnostics | Hard to debug "prices not moving" | `MarketDataSource.status()` with mode, response age, call usage, missing tickers, last error |
| 9 | Factory crashed on a malformed numeric env var | Startup failure from a typo | `_env_float` falls back to the default with a warning |

The earlier documents stay useful as background research. Where they conflict with this document, **this document wins**.
