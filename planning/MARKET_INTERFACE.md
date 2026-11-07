# Market Data Interface — Unified Design

> The single Python API that every part of the FinAlly backend uses to get stock prices. It uses the **Massive** REST API when `MASSIVE_API_KEY` is set and non-empty, and the **built-in simulator** otherwise. Downstream code (SSE, trades, portfolio valuation, LLM context) never knows which one is running.
>
> Related: `MASSIVE_API.md` (API research) and `MARKET_SIMULATOR.md` (simulator internals).

---

## 1. Architecture

```
                    create_market_data_source(cache)        ◄── reads MASSIVE_API_KEY
                               │
              ┌────────────────┴─────────────────┐
              ▼                                  ▼
   SimulatorDataSource                  MassiveDataSource
   (GBM, ticks every 0.5 s)             (poll loop every 15 s; API responses
                                          cached 120 s; ≤5 calls/min budget;
                                          snapshot or end-of-day mode)
              │  writes                          │  writes
              └────────────────┬─────────────────┘
                               ▼
                         PriceCache  (in-memory, thread-safe, versioned)
                               │  reads
        ┌──────────────┬───────┴────────┬────────────────┬──────────────┐
        ▼              ▼                ▼                ▼              ▼
  SSE /api/stream   POST trade     GET /portfolio   GET /watchlist   LLM context
  /prices           (fill price)   (valuation)      (latest prices)  (chat)
```

Principles:

1. **Producers write, consumers read.** A data source's only job is to keep `PriceCache` fresh. Nobody calls the data source to "get a price"; consumers read from the cache. This makes reads O(1), keeps them synchronous, and means they never hit the network.
2. **One interface, two implementations.** Both sources implement the `MarketDataSource` ABC. They differ only in lifecycle internals.
3. **One background task.** Exactly one asyncio task (simulator tick loop or Massive poll loop) runs for the life of the app, started and stopped in FastAPI's `lifespan`.
4. **The ticker set is dynamic.** Watchlist add/remove calls `add_ticker` / `remove_ticker` on the source.

---

## 2. Module layout

```
backend/app/market/
├── __init__.py          # Public exports: PriceUpdate, PriceCache, MarketDataSource, create_market_data_source
├── models.py            # PriceUpdate dataclass
├── cache.py             # PriceCache
├── interface.py         # MarketDataSource ABC
├── factory.py           # create_market_data_source()
├── massive_source.py    # MassiveDataSource
├── seed_prices.py       # Seed prices, per-ticker params, sector map (shared with simulator)
├── simulator.py         # GBMSimulator (pure math) + SimulatorDataSource (async wrapper)
└── stream.py            # FastAPI router for GET /api/stream/prices (SSE)
```

Public import for the rest of the backend:

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source
```

---

## 3. Data model — `models.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field
import time


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of one ticker's price at a point in time."""

    ticker: str
    price: float
    previous_price: float          # price from the previous update (tick-to-tick change → flash colour)
    reference_price: float         # previous close / session open (daily change %)
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
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "reference_price": self.reference_price,
            "change": self.change,
            "change_percent": self.change_percent,
            "day_change_percent": self.day_change_percent,
            "direction": self.direction,
            "timestamp": self.timestamp,
        }
```

`to_dict()` is the **wire format** for SSE and the `/api/watchlist` response, so frontend and backend share one shape.

---

## 4. Price cache — `cache.py`

```python
import threading
import time
from .models import PriceUpdate


class PriceCache:
    """Thread-safe store of the latest PriceUpdate per ticker.

    Writers: exactly one MarketDataSource.
    Readers: SSE stream, trade execution, portfolio, watchlist, LLM context.
    `version` increments on every write so SSE can cheaply detect changes.
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
        with self._lock:
            prev = self._prices.get(ticker)
            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=prev.price if prev else round(price, 2),
                reference_price=(
                    reference_price if reference_price is not None
                    else prev.reference_price if prev
                    else round(price, 2)
                ),
                timestamp=timestamp or time.time(),
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
        return self._version

    def __contains__(self, ticker: str) -> bool:
        return self.get(ticker) is not None
```

Why `threading.Lock` and not `asyncio.Lock`: the Massive source writes from a worker thread (`asyncio.to_thread`), and readers are sync. The critical sections are tiny, so contention is irrelevant.

---

## 5. The interface — `interface.py`

```python
from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Produces prices for a dynamic set of tickers and writes them into a PriceCache.

    Lifecycle:  source = Impl(cache, ...)  →  await source.start(tickers)
                →  add_ticker / remove_ticker as the watchlist changes
                →  await source.stop()
    """

    #: "simulator" or "massive". Exposed via /api/health for debugging.
    name: str = "abstract"

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing prices. Must seed the cache for `tickers` before
        returning (best-effort for Massive) so the first SSE event is complete."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task. Idempotent. Never raises."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking a ticker. Idempotent. Should populate the cache soon
        (simulator: immediately; Massive: on the next poll, which is triggered early)."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and remove it from the cache. Idempotent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """The tickers currently being tracked."""
```

Contract rules shared by both implementations:

| Rule | Detail |
|---|---|
| Tickers are normalised | `ticker.strip().upper()` on entry |
| Never crash the loop | Exceptions inside the background loop are logged and the loop continues |
| Cache-only output | Sources do not return prices. They write `cache.update(...)` |
| `remove_ticker` → `cache.remove` | So SSE stops sending it |
| `stop()` is idempotent | Safe to call in `lifespan` shutdown even if `start` failed |

---

## 6. Factory — `factory.py`

```python
import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


def create_market_data_source(cache: PriceCache) -> MarketDataSource:
    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    if api_key:
        from .massive_source import MassiveDataSource   # lazy: simulator users don't need the SDK loaded
        interval = float(os.getenv("MASSIVE_POLL_INTERVAL", "15"))
        ttl = float(os.getenv("MASSIVE_CACHE_TTL", "120"))
        max_calls = int(os.getenv("MASSIVE_MAX_CALLS_PER_MIN", "5"))
        logger.info("Market data: Massive API (poll %.0fs, cache TTL %.0fs, ≤%d calls/min)",
                    interval, ttl, max_calls)
        return MassiveDataSource(api_key=api_key, cache=cache, poll_interval=interval,
                                 cache_ttl=ttl, max_calls_per_min=max_calls)

    from .simulator import SimulatorDataSource
    logger.info("Market data: built-in simulator")
    return SimulatorDataSource(cache=cache)
```

| Env var | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Non-empty selects Massive. Empty or absent selects the simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between poll-loop iterations. Most iterations are served from the response cache and make **no** API call |
| `MASSIVE_CACHE_TTL` | `120` | Seconds an API response is reused before the API is called again (see §7.1) |
| `MASSIVE_MAX_CALLS_PER_MIN` | `5` | Hard ceiling on real API calls per rolling 60 s (free-tier limit). Raise it for paid plans |

---

## 7. Massive implementation — `massive_source.py`

There are two modes, chosen automatically on the first poll:

| Mode | Endpoint | Calls per refresh | Who gets it |
|---|---|---|---|
| `snapshot` | `get_snapshot_all("stocks", tickers=[...])` | 1 | Starter+ (15-min delayed or real-time) |
| `eod` | `get_grouped_daily_aggs(<latest session>)` | 1 (+ walk-back on weekends/holidays) | Free Basic plan (snapshot → NOT_AUTHORIZED) |

### 7.1 Rate-limit protection: 2-minute response cache + call budget ⭐

The free tier allows only **5 API calls per minute**. Exceeding it returns HTTP 429, and repeated 429s risk a temporary block. FinAlly therefore **never calls the Massive API more often than necessary**:

1. **Response cache (TTL = 120 s, `MASSIVE_CACHE_TTL`).** Every API response is stored with its fetch time. For the next 2 minutes, every poll **reuses the cached response instead of calling the API**. The `PriceCache` keeps holding those prices, and the SSE stream keeps sending the same data to the browser. Prices simply don't change until the next refresh.
2. **Call budget (`MASSIVE_MAX_CALLS_PER_MIN`, default 5).** A sliding-window limiter is the last line of defence. Even if the cache misses (a new ticker, a weekend walk-back, a restart loop), no more than 5 real calls are made per rolling 60 s. When the budget is exhausted, the source **serves the stale cached response** and tries again on a later poll.
3. **No SDK auto-retries.** `RESTClient(retries=0)`. The SDK's default retries 429/5xx up to 3 times with a 0.1 s backoff, and each retry burns quota. We retry on our own schedule instead.
4. **Back off on 429.** If a 429 still happens, the limiter is marked full for the next 60 s and cached data keeps being served.

Resulting call volume on the free tier:

| Situation | Real API calls |
|---|---|
| Steady state | **1 call every 2 minutes** (0.5/min, 10 % of the limit) |
| Poll loop ticks every 15 s | 7 of every 8 ticks are cache hits, so no call |
| User adds a ticker (EOD mode) | **0 calls**: the grouped-daily response already contains every US ticker |
| User adds a ticker (snapshot mode) | 1 call, only if the budget allows. Otherwise it appears at the next refresh |
| Monday morning / holiday walk-back | Up to 3–4 calls once, then cached for 2 min |
| Burst of failures or restarts | Capped at 5 calls/min by the limiter |

Trade-off: prices can be up to **2 minutes stale** (on top of the plan's own delay; free-tier data is end-of-day anyway). For a simulated-money app this is acceptable. Paid-plan users can lower `MASSIVE_CACHE_TTL` (e.g. `10`) and raise `MASSIVE_MAX_CALLS_PER_MIN`.

```
 poll tick (every 15 s)
      │
      ▼
 response cache fresh (< 120 s) and covers all tickers? ──yes──► reuse cached response (no API call)
      │ no
      ▼
 call budget available (< 5 calls in last 60 s)? ──no──► serve stale cached response, retry next tick
      │ yes
      ▼
 call Massive API ──► store response + timestamp ──► write prices to PriceCache
      │ 429?
      └──► mark budget exhausted for 60 s, serve stale cached response
```

### 7.2 Helpers: `TTLResponseCache` and `CallBudget`

```python
import time
from collections import deque
from typing import Any


class TTLResponseCache:
    """Stores API responses for `ttl` seconds. Expired entries are kept so they
    can still be served as stale data when the call budget is exhausted."""

    def __init__(self, ttl: float = 120.0) -> None:
        self.ttl = ttl
        self._store: dict[str, tuple[float, Any]] = {}

    def get_fresh(self, key: str) -> Any | None:
        hit = self._store.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        return None

    def get_stale(self, key: str) -> Any | None:
        hit = self._store.get(key)
        return hit[1] if hit else None

    def put(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic(), value)

    def age(self, key: str) -> float | None:
        hit = self._store.get(key)
        return time.monotonic() - hit[0] if hit else None


class CallBudget:
    """Sliding window: at most `max_calls` real API calls per `period` seconds."""

    def __init__(self, max_calls: int = 5, period: float = 60.0) -> None:
        self.max_calls = max_calls
        self.period = period
        self._calls: deque[float] = deque()

    def try_acquire(self) -> bool:
        now = time.monotonic()
        while self._calls and now - self._calls[0] >= self.period:
            self._calls.popleft()
        if len(self._calls) < self.max_calls:
            self._calls.append(now)
            return True
        return False

    def exhaust(self) -> None:
        """After a 429: treat the whole window as used for the next `period` seconds."""
        now = time.monotonic()
        self._calls = deque([now] * self.max_calls)
```

Both are only touched by the single poll loop (calls are sequential, one worker thread at a time), so they need no locking.

### 7.3 `MassiveDataSource`

```python
import asyncio
import logging
from datetime import date, timedelta

from massive import RESTClient
from massive.exceptions import BadResponse

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


class MassiveDataSource(MarketDataSource):
    name = "massive"

    def __init__(
        self,
        api_key: str,
        cache: PriceCache,
        poll_interval: float = 15.0,
        cache_ttl: float = 120.0,
        max_calls_per_min: int = 5,
    ) -> None:
        # Pass the key explicitly (the SDK reads the env at import time).
        # retries=0: SDK retries would burn the 5/min quota.
        self._client = RESTClient(api_key=api_key, retries=0)
        self._cache = cache
        self._interval = poll_interval
        self._responses = TTLResponseCache(ttl=cache_ttl)
        self._budget = CallBudget(max_calls=max_calls_per_min, period=60.0)
        self._tickers: set[str] = set()
        self._mode: str | None = None                       # None = undetected, "snapshot" | "eod"
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()                         # set to poll immediately (e.g. new ticker)

    # ── lifecycle ────────────────────────────────────────────────
    async def start(self, tickers: list[str]) -> None:
        self._tickers = {t.strip().upper() for t in tickers}
        await self._poll_once()                              # seed the cache before first SSE event
        self._task = asyncio.create_task(self._run(), name="massive-poller")

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
        if t not in self._tickers:
            self._tickers.add(t)
            self._wake.set()       # next iteration serves it from cache if possible, else 1 budgeted call

    async def remove_ticker(self, ticker: str) -> None:
        t = ticker.strip().upper()
        self._tickers.discard(t)
        self._cache.remove(t)

    def get_tickers(self) -> list[str]:
        return sorted(self._tickers)

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
        if not self._tickers:
            return
        try:
            if self._mode in (None, "snapshot"):
                try:
                    await asyncio.to_thread(self._refresh_snapshot)
                    self._mode = self._mode or "snapshot"
                    return
                except BadResponse as e:
                    if self._mode is None and _is_not_entitled(e):
                        logger.warning("Massive plan lacks snapshot access; using end-of-day prices")
                        self._mode = "eod"
                    else:
                        raise
            await asyncio.to_thread(self._refresh_eod)
        except Exception:                                    # never kill the loop
            logger.exception("Massive poll failed; keeping last cached prices")

    # ── guarded API call ─────────────────────────────────────────
    def _call(self, fn, *args, **kwargs):
        """Make one real API call if the budget allows; return None if it doesn't."""
        if not self._budget.try_acquire():
            logger.debug("Massive call budget exhausted; serving cached data")
            return None
        try:
            return fn(*args, **kwargs)
        except BadResponse as e:
            if _is_rate_limited(e):
                logger.warning("Massive 429 rate limit hit; backing off 60s")
                self._budget.exhaust()
                return None
            raise

    # ── snapshot mode (paid plans) ───────────────────────────────
    def _refresh_snapshot(self) -> None:
        key = "snapshot"
        cached: dict | None = self._responses.get_fresh(key)
        if cached is not None and self._tickers <= cached.keys():
            self._publish(cached, fresh=False)               # cache hit: no API call
            return
        snaps = self._call(self._client.get_snapshot_all, "stocks", tickers=sorted(self._tickers))
        if snaps is None:                                    # no budget / 429 → stale data
            self._publish(self._responses.get_stale(key) or {}, fresh=False)
            return
        data = {}
        for s in snaps:
            price = (s.last_trade.price if s.last_trade and s.last_trade.price
                     else s.day.close if s.day and s.day.close else None)
            if price is None:
                continue
            data[s.ticker] = (
                price,
                s.prev_day.close if s.prev_day and s.prev_day.close else None,  # reference
                s.updated / 1e9 if s.updated else None,                         # ns → s
            )
        self._responses.put(key, data)
        self._publish(data, fresh=True)

    # ── end-of-day mode (free plan) ──────────────────────────────
    def _refresh_eod(self) -> None:
        key = "eod"
        cached: dict | None = self._responses.get_fresh(key)
        if cached is not None:
            self._publish(cached, fresh=False)               # cache hit: no API call
            return
        d = date.today()
        for _ in range(7):                                   # walk back over weekends/holidays
            if d.weekday() < 5:
                bars = self._call(self._client.get_grouped_daily_aggs, d.isoformat(), adjusted=True)
                if bars is None:                             # no budget / 429 → stale data
                    self._publish(self._responses.get_stale(key) or {}, fresh=False)
                    return
                if bars:
                    # Keep ALL tickers (~10k small tuples) so later watchlist adds cost 0 calls.
                    data = {b.ticker: (b.close, b.open, b.timestamp / 1000) for b in bars}  # ms → s
                    self._responses.put(key, data)
                    self._publish(data, fresh=True)
                    return
            d -= timedelta(days=1)

    # ── write to PriceCache ──────────────────────────────────────
    def _publish(self, data: dict, fresh: bool) -> None:
        """fresh=True: new API data → update every watched ticker.
        fresh=False: cached response → only fill tickers missing from PriceCache
        (e.g. a just-added ticker). Re-writing identical prices would reset
        previous_price and bump the SSE version for no reason; the PriceCache
        already holds those values and SSE keeps sending them."""
        for t in self._tickers:
            if t not in data:
                continue
            if fresh or t not in self._cache:
                price, ref, ts = data[t]
                self._cache.update(t, price, timestamp=ts, reference_price=ref)


def _is_not_entitled(e: BadResponse) -> bool:
    msg = str(e).lower()
    return "not_authorized" in msg or "not entitled" in msg or "upgrade" in msg


def _is_rate_limited(e: BadResponse) -> bool:
    msg = str(e).lower()
    return "429" in msg or "too many" in msg or "exceeded the maximum" in msg
```

Notes:

- **Detection also costs budget.** The first-poll snapshot attempt that returns NOT_AUTHORIZED counts as one call. After that, the free tier makes one grouped-daily call per 2 minutes.
- **Restarts:** the response cache is in memory, so a container restart makes one fresh call. The budget makes sure a crash/restart loop can't hammer the API. Persisting the cache to SQLite isn't worth the complexity.
- **Unknown tickers:** if the API doesn't return a ticker, it never gets a `PriceCache` entry. Trades on it are rejected with "no price available". The watchlist route can check `cache.get(t)` after a short wait and report it.
- **Market closed:** snapshot still returns the last trade, so prices simply stop moving. That is correct behaviour.
- **`/api/health`** can expose `mode`, the age of the cached response (`self._responses.age(...)`), and calls used in the current window. This helps when debugging "why aren't prices moving?".

---

## 8. Simulator implementation (summary)

`SimulatorDataSource` (full design in `MARKET_SIMULATOR.md`) implements the same ABC:

- `start(tickers)` creates a `GBMSimulator`, seeds the cache with each ticker's seed price (reference price = seed), and launches a tick loop every **0.5 s**.
- Each tick calls `GBMSimulator.step()`, which returns `{ticker: price}`. Each price is written with `cache.update(t, p)`.
- `add_ticker` adds to the simulator (rebuilding the correlation matrix) and seeds the cache **immediately**.
- `remove_ticker` removes the ticker from the simulator and the cache.

---

## 9. Integration with FastAPI

### 9.1 Lifespan wiring

```python
# backend/app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.market import PriceCache, create_market_data_source
from app.market.stream import router as stream_router
from app.db import get_watchlist_tickers, get_position_tickers


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = PriceCache()
    source = create_market_data_source(cache)
    # Track the watchlist and every held position (positions need live valuation).
    tickers = sorted(set(get_watchlist_tickers()) | set(get_position_tickers()))
    await source.start(tickers)
    app.state.price_cache = cache
    app.state.market = source
    try:
        yield
    finally:
        await source.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(stream_router)
```

Dependency helpers:

```python
from fastapi import Request

def get_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache

def get_market(request: Request) -> MarketDataSource:
    return request.app.state.market
```

### 9.2 SSE endpoint — `stream.py`

```python
import asyncio
import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()


@router.get("/api/stream/prices")
async def stream_prices(request: Request) -> StreamingResponse:
    cache = request.app.state.price_cache

    async def events():
        yield "retry: 1000\n\n"                          # EventSource reconnect delay (ms)
        last_version = -1
        while not await request.is_disconnected():
            if cache.version != last_version:
                last_version = cache.version
                payload = {t: u.to_dict() for t, u in cache.get_all().items()}
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                yield ": keepalive\n\n"                   # comment line keeps proxies from timing out
            await asyncio.sleep(0.5)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

Each event is a JSON object keyed by ticker:

```json
{"AAPL": {"ticker":"AAPL","price":190.42,"previous_price":190.37,"reference_price":190.00,
          "change":0.05,"change_percent":0.0263,"day_change_percent":0.2211,
          "direction":"up","timestamp":1762540800.12}, "...": {}}
```

Frontend: `new EventSource('/api/stream/prices')`, `onmessage = e => merge(JSON.parse(e.data))`. The flash colour comes from `direction`, the daily % from `day_change_percent`, and sparklines append `price` per ticker.

### 9.3 Consumers

```python
# Trade execution — fill at the cached price
price = cache.get_price(ticker)
if price is None:
    raise HTTPException(400, f"No price available for {ticker}")

# Watchlist add
await market.add_ticker(ticker)      # after the DB insert succeeds

# Watchlist remove — keep streaming if a position is still held
if not has_position(ticker):
    await market.remove_ticker(ticker)

# After a buy of a ticker not on the watchlist (e.g. via LLM)
await market.add_ticker(ticker)

# Portfolio valuation / LLM context
prices = cache.get_all()
value = cash + sum(p.quantity * prices[p.ticker].price for p in positions if p.ticker in prices)
```

### 9.4 Health

`GET /api/health` can return `{"status": "ok", "market_data": market.name, "tickers": len(market.get_tickers())}`.

---

## 10. Testing strategy

| Test | Approach |
|---|---|
| `PriceCache` | previous_price chaining, sticky reference_price, version increments, case-insensitive keys, remove |
| `PriceUpdate` | direction / change / percent maths, zero-division guards, `to_dict` keys |
| Factory | `monkeypatch.setenv("MASSIVE_API_KEY", "")` gives the simulator; `"abc"` gives Massive (with `RESTClient` patched) |
| `TTLResponseCache` / `CallBudget` | Patch `time.monotonic`: fresh before TTL, stale-but-available after; budget allows 5 calls, refuses the 6th, frees a slot after 60 s; `exhaust()` blocks for 60 s |
| Response caching | With a fake client counting calls: 8 polls within 120 s → **1** API call; poll after TTL → 2nd call; adding a ticker in EOD mode → 0 extra calls; a fake 429 → no further calls for 60 s while cached prices remain in `PriceCache`; `RESTClient` constructed with `retries=0` |
| `MassiveDataSource` | Patch `RESTClient` with a fake returning `TickerSnapshot`-like `SimpleNamespace` objects. Assert cache contents, ns→s conversion, `prev_day` as reference, and that a `BadResponse('{"status":"NOT_AUTHORIZED"}')` switches mode to `eod` and calls `get_grouped_daily_aggs`. Assert that an exception in the poll doesn't stop the loop |
| `SimulatorDataSource` | `start` seeds every ticker; after `await asyncio.sleep(0.6)` versions have advanced; add/remove reflect in the cache |
| Interface conformance | Parametrised test over both implementations running the same lifecycle script |
| SSE | `httpx.AsyncClient` with `ASGITransport`, read the first `data:` line, assert keys |

Every API-route test can use a lightweight fake:

```python
class FakeSource(MarketDataSource):
    name = "fake"
    def __init__(self, cache): self.cache, self.tickers = cache, set()
    async def start(self, tickers): [await self.add_ticker(t) for t in tickers]
    async def stop(self): pass
    async def add_ticker(self, t): self.tickers.add(t.upper()); self.cache.update(t, 100.0)
    async def remove_ticker(self, t): self.tickers.discard(t.upper()); self.cache.remove(t)
    def get_tickers(self): return sorted(self.tickers)
```
