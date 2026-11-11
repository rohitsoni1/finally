import pytest

from app.llm.mock import DEFAULT_MOCK_MESSAGE, is_mock_enabled, mock_response


def test_default_message():
    r = mock_response("hello there, how is my portfolio?")
    assert r.message == DEFAULT_MOCK_MESSAGE
    assert r.trades == [] and r.watchlist_changes == []


def test_buy():
    r = mock_response("Please BUY 5 aapl")
    assert len(r.trades) == 1
    t = r.trades[0]
    assert (t.ticker, t.side, t.quantity) == ("AAPL", "buy", 5)
    assert r.message.startswith("Mock response:")


def test_sell_fractional():
    r = mock_response("sell 2.5 TSLA")
    assert (r.trades[0].side, r.trades[0].quantity, r.trades[0].ticker) == ("sell", 2.5, "TSLA")


def test_add_and_remove():
    r = mock_response("add PYPL and remove nflx")
    assert [(c.ticker, c.action) for c in r.watchlist_changes] == [("PYPL", "add"), ("NFLX", "remove")]
    assert r.trades == []


def test_multiple_commands_in_order():
    r = mock_response("buy 1 AAPL then sell 2 MSFT and add PYPL")
    assert [(t.side, t.ticker) for t in r.trades] == [("buy", "AAPL"), ("sell", "MSFT")]
    assert [c.ticker for c in r.watchlist_changes] == ["PYPL"]


def test_deterministic():
    assert mock_response("buy 3 NVDA") == mock_response("buy 3 NVDA")


@pytest.mark.parametrize("text", ["address the issue", "buy AAPL", "buying 5 AAPL", "added"])
def test_non_matching(text):
    r = mock_response(text)
    assert r.message == DEFAULT_MOCK_MESSAGE and not r.trades and not r.watchlist_changes


@pytest.mark.parametrize("val,expected", [("true", True), ("TRUE", True), ("1", True),
                                          ("false", False), ("", False)])
def test_is_mock_enabled(monkeypatch, val, expected):
    monkeypatch.setenv("LLM_MOCK", val)
    assert is_mock_enabled() is expected


def test_is_mock_enabled_unset(monkeypatch):
    monkeypatch.delenv("LLM_MOCK", raising=False)
    assert is_mock_enabled() is False
