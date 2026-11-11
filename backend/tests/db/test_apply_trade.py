import pytest

from app import db


def test_buy_creates_position_and_debits_cash(db_path):
    r = db.apply_trade("aapl", "buy", 10, 190.0)
    assert r["cash_balance"] == pytest.approx(8100.0)
    assert r["position"]["ticker"] == "AAPL"
    assert r["position"]["quantity"] == 10 and r["position"]["avg_cost"] == 190.0
    assert r["trade"]["side"] == "buy" and r["trade"]["price"] == 190.0
    assert db.get_cash() == pytest.approx(8100.0)
    assert len(db.list_trades()) == 1


def test_second_buy_weighted_average(db_path):
    db.apply_trade("AAPL", "buy", 10, 100.0)
    r = db.apply_trade("AAPL", "buy", 10, 200.0)
    assert r["position"]["quantity"] == 20
    assert r["position"]["avg_cost"] == pytest.approx(150.0)
    assert db.get_cash() == pytest.approx(10000 - 3000)


def test_partial_sell_keeps_avg_cost(db_path):
    db.apply_trade("AAPL", "buy", 10, 100.0)
    r = db.apply_trade("AAPL", "sell", 4, 80.0)  # sell at a loss
    assert r["position"]["quantity"] == pytest.approx(6)
    assert r["position"]["avg_cost"] == 100.0
    assert r["cash_balance"] == pytest.approx(10000 - 1000 + 320)


def test_full_sell_deletes_position(db_path):
    db.apply_trade("AAPL", "buy", 0.3, 100.0)
    db.apply_trade("AAPL", "buy", 0.6, 100.0)  # float dust: 0.3 + 0.6 != 0.9 exactly
    r = db.apply_trade("AAPL", "sell", 0.9, 110.0)
    assert r["position"] is None
    assert db.get_position("AAPL") is None
    assert db.get_cash() == pytest.approx(10000 - 90 + 99)


def test_insufficient_cash_writes_nothing(db_path):
    with pytest.raises(db.InsufficientCashError):
        db.apply_trade("AAPL", "buy", 100, 190.0)
    assert db.get_cash() == 10000.0
    assert db.get_positions() == []
    assert db.list_trades() == []


def test_buy_exact_cash(db_path):
    r = db.apply_trade("AAPL", "buy", 50, 200.0)
    assert r["cash_balance"] == pytest.approx(0.0)


def test_insufficient_shares(db_path):
    with pytest.raises(db.InsufficientSharesError):
        db.apply_trade("AAPL", "sell", 1, 190.0)
    db.apply_trade("AAPL", "buy", 2, 100.0)
    with pytest.raises(db.InsufficientSharesError):
        db.apply_trade("AAPL", "sell", 3, 100.0)
    assert db.get_position("AAPL")["quantity"] == 2
    assert len(db.list_trades()) == 1


@pytest.mark.parametrize(
    "side,qty,price",
    [("hold", 1, 100.0), ("buy", 0, 100.0), ("buy", -1, 100.0), ("buy", 1, 0.0)],
)
def test_invalid_inputs(db_path, side, qty, price):
    with pytest.raises(db.TradeValidationError):
        db.apply_trade("AAPL", side, qty, price)
    assert db.list_trades() == []


def test_errors_are_value_errors(db_path):
    assert issubclass(db.InsufficientCashError, ValueError)
    assert issubclass(db.InsufficientSharesError, db.TradeValidationError)
