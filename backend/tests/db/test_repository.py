import threading

import pytest

from app import db


def test_cash_roundtrip(db_path):
    assert db.set_cash(4321.5) == 4321.5
    assert db.get_cash() == 4321.5


def test_cash_other_user_autocreated(db_path):
    assert db.get_cash(user_id="alice") == 10000.0
    db.set_cash(1.0, user_id="alice")
    assert db.get_cash(user_id="alice") == 1.0
    assert db.get_cash() == 10000.0


def test_position_crud(db_path):
    assert db.get_position("AAPL") is None
    p = db.upsert_position(" aapl ", 10, 190.0)
    assert p["ticker"] == "AAPL" and p["quantity"] == 10.0 and p["avg_cost"] == 190.0
    db.upsert_position("AAPL", 15.5, 191.0)
    db.upsert_position("MSFT", 1, 400.0)
    got = db.get_position("aapl")
    assert got["quantity"] == 15.5 and got["avg_cost"] == 191.0 and got["updated_at"]
    assert [p["ticker"] for p in db.get_positions()] == ["AAPL", "MSFT"]
    assert db.delete_position("AAPL") is True
    assert db.delete_position("AAPL") is False
    assert [p["ticker"] for p in db.get_positions()] == ["MSFT"]


def test_trades_insert_and_list(db_path):
    t1 = db.insert_trade("aapl", "BUY", 2, 100.0)
    assert set(t1) == {"id", "ticker", "side", "quantity", "price", "executed_at"}
    assert t1["ticker"] == "AAPL" and t1["side"] == "buy"
    db.insert_trade("AAPL", "sell", 1, 110.0)
    db.insert_trade("MSFT", "buy", 1, 400.0)
    all_trades = db.list_trades()
    assert [t["ticker"] for t in all_trades] == ["AAPL", "AAPL", "MSFT"]
    last2 = db.list_trades(limit=2)
    assert [t["side"] for t in last2] == ["sell", "buy"]


def test_watchlist_add_remove(db_path):
    added = db.add_watchlist_ticker(" pypl ")
    assert added["ticker"] == "PYPL" and added["added_at"]
    assert db.add_watchlist_ticker("PYPL") is None  # duplicate
    assert db.get_watchlist()[-1]["ticker"] == "PYPL"
    assert db.remove_watchlist_ticker("pypl") is True
    assert db.remove_watchlist_ticker("PYPL") is False
    assert len(db.get_watchlist()) == 10


def test_snapshots(db_path):
    db.insert_snapshot(10000.0, recorded_at="2026-01-01T00:00:00+00:00")
    db.insert_snapshot(10050.0, recorded_at="2026-01-01T00:00:30+00:00")
    s = db.insert_snapshot(9990.0)
    assert set(s) == {"total_value", "recorded_at"}
    vals = [x["total_value"] for x in db.list_snapshots()]
    assert vals == [10000.0, 10050.0, 9990.0]
    assert [x["total_value"] for x in db.list_snapshots(limit=2)] == [10050.0, 9990.0]


def test_chat_messages(db_path):
    db.insert_chat_message("user", "hi")
    actions = {"trades": [{"ticker": "AAPL", "status": "executed"}], "watchlist_changes": []}
    m = db.insert_chat_message("assistant", "hello", actions=actions)
    assert m["actions"] == actions
    msgs = db.list_chat_messages()
    assert [x["role"] for x in msgs] == ["user", "assistant"]
    assert msgs[0]["actions"] is None
    assert msgs[1]["actions"] == actions
    assert set(msgs[1]) == {"id", "role", "content", "actions", "created_at"}
    for i in range(5):
        db.insert_chat_message("user", f"m{i}")
    assert [x["content"] for x in db.list_chat_messages(limit=2)] == ["m3", "m4"]
    assert len(db.list_chat_messages(limit=None)) == 7


def test_transaction_rolls_back(db_path):
    with pytest.raises(RuntimeError):
        with db.transaction() as c:
            db.set_cash(1.0, conn=c)
            db.upsert_position("AAPL", 1, 1.0, conn=c)
            raise RuntimeError("boom")
    assert db.get_cash() == 10000.0
    assert db.get_position("AAPL") is None


def test_transaction_commits(db_path):
    with db.transaction() as c:
        db.set_cash(5.0, conn=c)
        db.insert_trade("AAPL", "buy", 1, 1.0, conn=c)
    assert db.get_cash() == 5.0
    assert len(db.list_trades()) == 1


def test_concurrent_writes_from_threads(db_path):
    errors = []

    def worker(n):
        try:
            for i in range(20):
                db.insert_snapshot(float(n * 100 + i))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(db.list_snapshots()) == 100
