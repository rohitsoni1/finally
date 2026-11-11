from datetime import date

import pytest
from massive.exceptions import AuthError, BadResponse
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


def fake_today(monkeypatch, y, m, d):
    class D(date):
        @classmethod
        def today(cls):
            return date(y, m, d)

    monkeypatch.setattr(ms, "date", D)


def test_ttl_cache_and_budget():
    clk = Clock()
    c = TTLResponseCache(ttl=120, clock=clk)
    assert c.age("k") is None
    c.put("k", 1)
    clk.t += 119
    assert c.get_fresh("k") == 1 and c.age("k") == 119.0
    clk.t += 2
    assert c.get_fresh("k") is None and c.get_stale("k") == 1
    assert c.get_fresh("k", ttl=500) == 1  # custom ttl
    assert c.get_stale("missing") is None
    b = CallBudget(5, 60, clock=clk)
    assert all(b.try_acquire() for _ in range(5)) and not b.try_acquire()
    assert b.used == 5
    clk.t += 60
    assert b.used == 0 and b.try_acquire()
    b.exhaust()
    assert not b.try_acquire()


def test_error_classifiers():
    assert ms.is_rate_limited(RATE_429)
    assert not ms.is_rate_limited(MaxRetryError(None, "/", ResponseError("too many 500 error responses")))
    assert ms.is_rate_limited(BadResponse("You exceeded the maximum requests per minute"))
    assert not ms.is_rate_limited(RuntimeError("other"))
    assert ms.is_not_entitled(NOT_AUTH)
    assert not ms.is_not_entitled(RuntimeError("not_authorized"))
    assert ms.is_bad_key(BadResponse('{"status":"ERROR","error":"Unknown API Key"}'))
    assert ms.is_bad_key(AuthError("no key"))
    assert not ms.is_bad_key(RuntimeError("x"))


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
    await src.stop()


async def test_snapshot_parsing_fallbacks(cache):
    from types import SimpleNamespace as NS

    snaps = [
        NS(ticker="DAY", last_trade=None, day=NS(close=5.0), prev_day=None, updated=None),
        NS(ticker="NOPRICE", last_trade=NS(price=0), day=None, prev_day=None, updated=1),
    ]
    src = make(cache, FakeMassiveClient(snapshot=lambda t: snaps), Clock())
    await src.start(["DAY", "NOPRICE"])
    assert cache.get_price("DAY") == 5.0 and "NOPRICE" not in cache
    await src.stop()


async def test_snapshot_none_response(cache):
    src = make(cache, FakeMassiveClient(snapshot=lambda t: None), Clock())
    await src.start(["AAPL"])
    assert src._mode == "snapshot" and "AAPL" not in cache
    await src.stop()


async def test_cache_hit_does_not_rewrite_existing_prices(cache):
    clk = Clock()
    client = FakeMassiveClient(snapshot=lambda t: [snap(x, 10.0) for x in t])
    src = make(cache, client, clk)
    await src.start(["AAPL"])
    v = cache.version
    await src._poll_once()
    assert cache.version == v
    await src.stop()


async def test_invalid_ticker_does_not_force_refetch(cache):
    client = FakeMassiveClient(snapshot=lambda t: [snap("AAPL", 1.0)])  # ZZZZ never returned
    src = make(cache, client, Clock())
    await src.start(["AAPL", "ZZZZ"])
    await src._poll_once()
    await src._poll_once()
    assert len(client.calls) == 1 and src.status()["missing"] == ["ZZZZ"]
    await src.stop()


async def test_free_plan_falls_back_to_eod_and_walks_back(cache, monkeypatch):
    fake_today(monkeypatch, 2026, 11, 9)  # Monday
    grouped = {"2026-11-06": [bar("AAPL", 190.0, 188.0), bar("PYPL", 70.0), bar("ZERO", 0)]}  # Friday
    client = FakeMassiveClient(snapshot=lambda t: NOT_AUTH, grouped=lambda d: grouped.get(d, []))
    src = make(cache, client, Clock())
    await src.start(["AAPL"])
    assert src._mode == "eod"
    assert [c[1] for c in client.calls] == [("AAPL",), "2026-11-09", "2026-11-06"]  # skipped Sat/Sun
    assert cache.get("AAPL").price == 190.0 and cache.get("AAPL").reference_price == 188.0
    await src.add_ticker("PYPL")  # already in the grouped response -> 0 calls
    await src._poll_once()
    assert cache.get_price("PYPL") == 70.0 and len(client.calls) == 3
    assert src.status()["mode"] == "eod"
    await src.stop()


async def test_eod_with_missing_timestamp(cache, monkeypatch):
    fake_today(monkeypatch, 2026, 11, 6)
    client = FakeMassiveClient(snapshot=lambda t: NOT_AUTH, grouped=lambda d: [bar("AAPL", 1.0, ts_ms=0)])
    src = make(cache, client, Clock())
    await src.start(["AAPL"])
    assert cache.get_price("AAPL") == 1.0
    await src.stop()


async def test_eod_no_data_in_lookback(cache, monkeypatch):
    fake_today(monkeypatch, 2026, 11, 9)
    client = FakeMassiveClient(snapshot=lambda t: NOT_AUTH, grouped=lambda d: [])
    src = make(cache, client, Clock())
    await src.start(["AAPL"])
    assert "AAPL" not in cache and src._mode == "eod"
    await src.stop()


async def test_eod_throttled_serves_stale(cache, monkeypatch):
    fake_today(monkeypatch, 2026, 11, 6)
    clk = Clock()
    responses = iter([[bar("AAPL", 50.0)], RATE_429])
    client = FakeMassiveClient(snapshot=lambda t: NOT_AUTH, grouped=lambda d: next(responses))
    src = make(cache, client, clk)
    await src.start(["AAPL"])
    clk.t += 1000  # past the EOD TTL
    await src._poll_once()  # 429 -> stale
    assert cache.get_price("AAPL") == 50.0
    await src.add_ticker("MSFT")
    await src._poll_once()  # budget exhausted, stale has no MSFT
    assert "MSFT" not in cache
    await src.stop()


async def test_snapshot_throttled_without_stale_data(cache):
    src = make(cache, FakeMassiveClient(snapshot=lambda t: RATE_429), Clock())
    await src.start(["AAPL"])
    assert "AAPL" not in cache and src._mode is None
    await src.stop()


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
    await src.stop()


async def test_budget_caps_calls(cache):
    clk = Clock()
    client = FakeMassiveClient(snapshot=lambda t: [snap(x, 1.0) for x in t])
    src = make(cache, client, clk, max_calls_per_min=5)
    await src.start(["A"])
    for i in range(10):  # each add forces a miss
        await src.add_ticker(f"T{i}")
        await src._poll_once()
    assert len(client.calls) == 5
    await src.stop()


async def test_poll_errors_never_raise(cache):
    client = FakeMassiveClient(snapshot=lambda t: RuntimeError("boom"))
    src = make(cache, client, Clock())
    await src.start(["AAPL"])  # does not raise
    assert "boom" in src.status()["last_error"]
    await src.stop()
    await src.stop()


async def test_bad_key_is_recorded(cache):
    client = FakeMassiveClient(snapshot=lambda t: BadResponse('{"error":"Unknown API Key"}'))
    src = make(cache, client, Clock())
    await src.start(["AAPL"])
    assert "Unknown API Key" in src.status()["last_error"]
    await src.stop()


async def test_success_clears_last_error(cache):
    clk = Clock()
    responses = iter([RuntimeError("boom"), [snap("AAPL", 1.0)]])
    src = make(cache, FakeMassiveClient(snapshot=lambda t: next(responses)), clk)
    await src.start(["AAPL"])
    assert src.status()["last_error"]
    await src._poll_once()
    st = src.status()
    assert st["last_error"] is None and st["last_success"] is not None
    await src.stop()


async def test_remove_ticker(cache):
    client = FakeMassiveClient(snapshot=lambda t: [snap(x, 1.0) for x in t])
    src = make(cache, client, Clock())
    await src.start(["AAPL", "MSFT"])
    await src.remove_ticker("msft")
    assert "MSFT" not in cache and src.get_tickers() == ["AAPL"]
    await src.stop()


async def test_empty_ticker_set_makes_no_calls(cache):
    client = FakeMassiveClient(snapshot=lambda t: [])
    src = make(cache, client, Clock())
    await src.start([])
    assert client.calls == []
    await src.stop()


async def test_publish_skips_tickers_removed_mid_poll(cache):
    src = make(cache, FakeMassiveClient(), Clock())
    src._tickers = {"AAPL"}
    src._publish({"AAPL": (1.0, None, None), "GONE": (2.0, None, None)},
                 frozenset({"AAPL", "GONE"}), fresh=True)
    assert "AAPL" in cache and "GONE" not in cache


async def test_start_rejects_invalid_ticker(cache):
    src = make(cache, FakeMassiveClient(), Clock())
    with pytest.raises(ValueError):
        await src.start(["not valid!"])


async def test_add_ticker_validates_and_wakes(cache):
    src = make(cache, FakeMassiveClient(), Clock())
    with pytest.raises(ValueError):
        await src.add_ticker("$$")
    await src.add_ticker("aapl")
    assert src._wake.is_set() and src.get_tickers() == ["AAPL"]
    src._wake.clear()
    await src.add_ticker("AAPL")  # duplicate: no wake
    assert not src._wake.is_set()


async def test_poll_loop_wakes_on_add_and_on_timeout(cache):
    import asyncio

    client = FakeMassiveClient(snapshot=lambda t: [snap(x, 1.0) for x in t])
    src = make(cache, client, None, poll_interval=0.05)
    await src.start(["AAPL"])
    await src.add_ticker("MSFT")
    await asyncio.sleep(0.2)  # woken poll + timeout polls
    assert "MSFT" in cache
    await src.stop()


def test_constructor_builds_real_client_with_zero_retries(cache):
    src = MassiveDataSource("key", cache)
    assert src._client.retries == 0
