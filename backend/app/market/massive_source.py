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
