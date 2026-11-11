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
