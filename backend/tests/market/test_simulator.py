import asyncio

import numpy as np
import pytest

from app.market import simulator as sim_mod
from app.market.seed_prices import params_for, sector_for, seed_price_for
from app.market.simulator import GBMSimulator, SimulatorDataSource


def test_seed_prices_and_stable_unknown():
    assert GBMSimulator(["AAPL"]).get_price("AAPL") == 190.0
    a, b = GBMSimulator(["PYPL"]).get_price("PYPL"), GBMSimulator(["PYPL"]).get_price("PYPL")
    assert a == b and 50 <= a < 300


def test_seed_helpers():
    assert seed_price_for("MSFT") == 420.0
    assert params_for("AAPL")["sigma"] == 0.22 and params_for("ZZZ")["sigma"] == 0.30
    assert sector_for("AAPL") == "tech" and sector_for("ZZZ") == "other"


def test_positive_and_deterministic():
    s1 = GBMSimulator(["AAPL", "TSLA"], time_scale=1000, seed=42)
    s2 = GBMSimulator(["AAPL", "TSLA"], time_scale=1000, seed=42)
    for _ in range(5000):
        p1, p2 = s1.step(), s2.step()
    assert p1 == p2 and all(v > 0 for v in p1.values())


def test_empty_step():
    assert GBMSimulator().step() == {}


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


def test_add_existing_and_remove_missing_are_noops():
    sim = GBMSimulator(["AAPL"])
    assert sim.add_ticker("aapl", price=1.0) == 190.0
    sim.remove_ticker("NOPE")
    assert sim.tickers == ["AAPL"]


def test_add_with_explicit_price_and_remove_last():
    sim = GBMSimulator()
    assert sim.add_ticker("PYPL", price=12.5) == 12.5
    sim.remove_ticker("PYPL")
    assert sim.tickers == [] and sim._chol.shape == (0, 0)


def test_cholesky_fallback_on_linalg_error(monkeypatch):
    real = np.linalg.cholesky
    calls = {"n": 0}

    def flaky(a):
        calls["n"] += 1
        if calls["n"] == 1:
            raise np.linalg.LinAlgError("not PD")
        return real(a)

    monkeypatch.setattr(sim_mod.np.linalg, "cholesky", flaky)
    sim = GBMSimulator(["AAPL"])
    assert calls["n"] == 2 and sim._chol.shape == (1, 1)


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
    await src.add_ticker("PYPL")  # idempotent
    assert "PYPL" in cache and src.get_tickers() == ["AAPL", "PYPL", "TSLA"]
    await src.remove_ticker("TSLA")
    assert "TSLA" not in cache
    await src.stop()
    await src.stop()  # idempotent


async def test_start_twice_keeps_single_task(cache):
    src = SimulatorDataSource(cache, tick_seconds=0.05)
    await src.start(["AAPL"])
    task = src._task
    await src.start(["MSFT"])
    assert src._task is task
    await src.stop()


async def test_stop_without_start(cache):
    await SimulatorDataSource(cache).stop()


async def test_add_invalid_ticker_raises(cache):
    src = SimulatorDataSource(cache)
    with pytest.raises(ValueError):
        await src.add_ticker("hello world")


async def test_add_ticker_with_price(cache):
    src = SimulatorDataSource(cache)
    await src.add_ticker("PYPL", price=77.0)
    assert cache.get_price("PYPL") == 77.0


async def test_tick_loop_survives_step_errors(cache, monkeypatch):
    src = SimulatorDataSource(cache, tick_seconds=0.01)
    await src.start(["AAPL"])
    calls = {"n": 0}
    real = src._sim.step

    def boom():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real()

    monkeypatch.setattr(src._sim, "step", boom)
    v = cache.version
    await asyncio.sleep(0.2)
    assert calls["n"] > 1 and cache.version > v
    await src.stop()


def test_status_default(cache):
    assert SimulatorDataSource(cache).status() == {"source": "simulator", "tickers": 0}
