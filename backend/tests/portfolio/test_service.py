import asyncio

import pytest

from app import db
from app.portfolio import (
    TradeError,
    execute_trade,
    get_portfolio,
    record_snapshot,
    snapshot_loop,
)


def test_fresh_portfolio(market, cache):
    p = get_portfolio(cache)
    assert p == {
        "cash_balance": 10000.0, "positions_value": 0.0, "total_value": 10000.0,
        "total_cost": 0.0, "unrealized_pnl": 0.0, "unrealized_pnl_percent": 0.0, "positions": [],
    }


def test_buy_creates_position_and_debits_cash(market, cache):
    r = execute_trade("aapl ", "BUY", 10, cache)
    t = r["trade"]
    assert t["ticker"] == "AAPL" and t["side"] == "buy" and t["quantity"] == 10 and t["price"] == 190.0
    assert t["id"] and t["executed_at"]
    p = r["portfolio"]
    assert p["cash_balance"] == 8100.0
    assert p["total_value"] == 10000.0
    [pos] = p["positions"]
    assert pos["ticker"] == "AAPL" and pos["quantity"] == 10 and pos["avg_cost"] == 190.0
    assert pos["market_value"] == 1900.0 and pos["weight"] == 0.19


def test_weighted_average_cost_and_pnl(market, cache):
    execute_trade("AAPL", "buy", 10, cache)
    market.set_price("AAPL", 210.0)
    execute_trade("AAPL", "buy", 10, cache)
    market.set_price("AAPL", 220.0)
    p = get_portfolio(cache)
    [pos] = p["positions"]
    assert pos["quantity"] == 20
    assert pos["avg_cost"] == 200.0
    assert pos["unrealized_pnl"] == 400.0
    assert pos["unrealized_pnl_percent"] == 10.0
    assert p["cash_balance"] == 10000 - 1900 - 2100
    assert p["total_value"] == 6000 + 4400
    assert p["unrealized_pnl"] == 400.0


def test_partial_sell_keeps_avg_cost_full_sell_deletes(market, cache):
    execute_trade("AAPL", "buy", 10, cache)
    market.set_price("AAPL", 180.0)  # sell at a loss
    r = execute_trade("AAPL", "sell", 4, cache)
    [pos] = r["portfolio"]["positions"]
    assert pos["quantity"] == 6 and pos["avg_cost"] == 190.0
    assert r["portfolio"]["cash_balance"] == 8100 + 720
    r = execute_trade("AAPL", "sell", 6, cache)
    assert r["portfolio"]["positions"] == []
    assert db.get_position("AAPL") is None
    assert r["portfolio"]["cash_balance"] == pytest.approx(10000 - 100 + 0)  # lost 10/share


def test_fractional_shares(market, cache):
    r = execute_trade("NVDA", "buy", 0.5, cache)
    assert r["portfolio"]["positions"][0]["quantity"] == 0.5
    assert r["portfolio"]["cash_balance"] == 9940.0


@pytest.mark.parametrize("args, msg", [
    (("AAPL", "buy", 1000), "Insufficient cash"),
    (("AAPL", "sell", 1), "Insufficient shares"),
    (("ZZZZ", "buy", 1), "No price available"),
    (("AAPL", "hold", 1), "Invalid side"),
    (("AAPL", "buy", 0), "greater than 0"),
    (("AAPL", "buy", -3), "greater than 0"),
    (("AAPL", "buy", float("nan")), "greater than 0"),
    (("AAPL", "buy", "abc"), "Invalid quantity"),
    (("not a ticker!", "buy", 1), "Invalid ticker"),
])
def test_trade_validation(market, cache, args, msg):
    with pytest.raises(TradeError, match=msg):
        execute_trade(*args, cache)
    assert db.get_cash() == 10000.0
    assert db.list_trades() == []


def test_sell_more_than_owned_leaves_state_unchanged(market, cache):
    execute_trade("AAPL", "buy", 2, cache)
    with pytest.raises(TradeError):
        execute_trade("AAPL", "sell", 3, cache)
    assert db.get_position("AAPL")["quantity"] == 2
    assert len(db.list_trades()) == 1


def test_snapshot_recorded_after_each_trade(market, cache):
    execute_trade("AAPL", "buy", 1, cache)
    execute_trade("AAPL", "sell", 1, cache)
    snaps = db.list_snapshots()
    assert len(snaps) == 2
    assert all(s["total_value"] == 10000.0 for s in snaps)


def test_missing_price_falls_back_to_avg_cost(market, cache):
    execute_trade("AAPL", "buy", 10, cache)
    cache.remove("AAPL")
    [pos] = get_portfolio(cache)["positions"]
    assert pos["current_price"] == 190.0 and pos["unrealized_pnl"] == 0.0


def test_record_snapshot(market, cache):
    execute_trade("AAPL", "buy", 10, cache)
    market.set_price("AAPL", 200.0)
    snap = record_snapshot(cache)
    assert snap["total_value"] == 10100.0
    assert db.list_snapshots()[-1]["total_value"] == 10100.0


async def test_snapshot_loop_records_periodically(market, cache):
    task = asyncio.create_task(snapshot_loop(cache, interval=0.05))
    await asyncio.sleep(0.18)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(db.list_snapshots()) >= 2


async def test_snapshot_loop_survives_errors(market, cache, monkeypatch):
    calls = []

    def boom(_cache):
        calls.append(1)
        raise RuntimeError("db down")

    monkeypatch.setattr("app.portfolio.service.record_snapshot", boom)
    task = asyncio.create_task(snapshot_loop(cache, interval=0.02))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 2
