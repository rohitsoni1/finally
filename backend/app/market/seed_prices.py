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
