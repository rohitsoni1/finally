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
