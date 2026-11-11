import pytest

from app.market import PriceCache, PriceUpdate, normalize_ticker


def test_price_update_math():
    u = PriceUpdate("AAPL", 101.0, 100.0, 95.0, 1.0)
    assert u.direction == "up" and u.change == 1.0 and u.change_percent == 1.0
    assert u.day_change == 6.0
    assert u.day_change_percent == pytest.approx(6.3158, abs=1e-4)
    assert PriceUpdate("X", 1, 0, 0).change_percent == 0.0  # zero guards
    assert PriceUpdate("X", 1, 0, 0).day_change_percent == 0.0
    assert set(u.to_dict()) >= {"ticker", "price", "previous_price", "timestamp", "direction"}


def test_direction_down_and_flat():
    assert PriceUpdate("X", 1.0, 2.0, 2.0, 1.0).direction == "down"
    assert PriceUpdate("X", 2.0, 2.0, 2.0, 1.0).direction == "flat"


def test_default_timestamp_is_set():
    assert PriceUpdate("X", 1.0, 1.0, 1.0).timestamp > 0


@pytest.mark.parametrize("raw,ok", [(" aapl ", "AAPL"), ("brk.b", "BRK.B"), ("BF-B", "BF-B")])
def test_normalize_ok(raw, ok):
    assert normalize_ticker(raw) == ok


@pytest.mark.parametrize("raw", ["", "1ABC", "TOOLONGTICKER", "A B", "$$", None])
def test_normalize_rejects(raw):
    with pytest.raises(ValueError):
        normalize_ticker(raw)


def test_cache_chains_previous_and_sticky_reference(cache: PriceCache):
    first = cache.update("aapl", 100.004, reference_price=99)
    assert first.previous_price == 100.0 and first.direction == "flat"
    second = cache.update("AAPL", 100.5)
    assert second.previous_price == 100.0 and second.reference_price == 99.0
    assert second.direction == "up" and cache.get_price("aapl") == 100.5


def test_cache_first_update_defaults_reference_to_price(cache: PriceCache):
    assert cache.update("A", 10).reference_price == 10.0


def test_cache_explicit_timestamp(cache: PriceCache):
    assert cache.update("A", 10, timestamp=123.0).timestamp == 123.0


def test_cache_get_missing(cache: PriceCache):
    assert cache.get("NOPE") is None and cache.get_price("NOPE") is None


def test_cache_get_all_is_a_copy(cache: PriceCache):
    cache.update("A", 1)
    snapshot = cache.get_all()
    snapshot.clear()
    assert "A" in cache and len(cache) == 1


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


async def test_wait_for_sees_late_arrival(cache: PriceCache):
    import asyncio

    async def later():
        await asyncio.sleep(0.05)
        cache.update("LATE", 7)

    task = asyncio.create_task(later())
    got = await cache.wait_for("LATE", timeout=1.0, poll=0.01)
    await task
    assert got is not None and got.price == 7
