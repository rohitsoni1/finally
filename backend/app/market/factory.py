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
