import asyncio
import json

import pytest

from app.llm import service
from app.llm.client import LLMCallError
from app.llm.prompt import SYSTEM_PROMPT, build_messages, build_portfolio_context


class FakeTradeError(Exception):
    pass


class FakeWatchlistError(Exception):
    pass


class FakeUpdate:
    def __init__(self, ticker, price):
        self.ticker, self.price = ticker, price

    def to_dict(self):
        return {"ticker": self.ticker, "price": self.price, "day_change_percent": 1.5}


class FakeCache:
    def __init__(self, prices):
        self.prices = prices

    def get(self, ticker):
        p = self.prices.get(ticker)
        return FakeUpdate(ticker, p) if p is not None else None


PORTFOLIO = {
    "cash_balance": 8100.0, "positions_value": 1905.0, "total_value": 10005.0,
    "total_cost": 1900.0, "unrealized_pnl": 5.0, "unrealized_pnl_percent": 0.263,
    "positions": [{"ticker": "AAPL", "quantity": 10, "avg_cost": 190.0, "current_price": 190.5,
                   "market_value": 1905.0, "cost_basis": 1900.0, "unrealized_pnl": 5.0,
                   "unrealized_pnl_percent": 0.263, "weight": 0.1904}],
}


@pytest.fixture
def env(monkeypatch):
    """Wire the service adapters to in-memory fakes."""
    state = {"cash": 1000.0, "watchlist": ["AAPL", "MSFT"], "messages": [], "trades": [],
             "llm_raw": None, "llm_calls": []}
    cache = FakeCache({"AAPL": 190.0, "MSFT": 400.0})

    def execute_trade(ticker, side, qty, price_cache):
        price = price_cache.prices.get(ticker)
        if price is None:
            raise FakeTradeError(f"No price available for {ticker}")
        if side == "buy" and qty * price > state["cash"]:
            raise FakeTradeError("Insufficient cash")
        if side == "sell":
            raise FakeTradeError("Insufficient shares")
        state["cash"] -= qty * price
        state["trades"].append((ticker, side, qty))
        return {"trade": {"ticker": ticker, "side": side, "quantity": qty, "price": price},
                "portfolio": {}}

    async def add(ticker, pc, ms):
        if ticker in state["watchlist"]:
            raise FakeWatchlistError(f"{ticker} is already on the watchlist")
        state["watchlist"].append(ticker)

    async def remove(ticker, pc, ms):
        if ticker not in state["watchlist"]:
            raise FakeWatchlistError(f"{ticker} is not on the watchlist")
        state["watchlist"].remove(ticker)

    def save(role, content, actions):
        state["messages"].append({"id": str(len(state["messages"])), "role": role,
                                  "content": content,
                                  "actions": json.dumps(actions) if actions else None,
                                  "created_at": "t"})

    async def call_llm(messages):
        state["llm_calls"].append(messages)
        if isinstance(state["llm_raw"], Exception):
            raise state["llm_raw"]
        return state["llm_raw"]

    monkeypatch.setattr(service, "_execute_trade", execute_trade)
    monkeypatch.setattr(service, "_trade_error_types", lambda: (FakeTradeError,))
    monkeypatch.setattr(service, "_add_to_watchlist", add)
    monkeypatch.setattr(service, "_remove_from_watchlist", remove)
    monkeypatch.setattr(service, "_save_message", save)
    monkeypatch.setattr(service, "_load_history", lambda limit: state["messages"][-limit:])
    monkeypatch.setattr(service, "_get_portfolio", lambda pc: PORTFOLIO)
    monkeypatch.setattr(service, "_get_watchlist_with_prices",
                        lambda pc: [{"ticker": t, "price": (pc.get(t).to_dict() if pc.get(t) else None)}
                                    for t in state["watchlist"]])
    monkeypatch.setattr(service, "call_llm", call_llm)
    monkeypatch.setenv("LLM_MOCK", "false")
    state["cache"] = cache
    return state


def run(state, msg):
    return asyncio.run(service.handle_chat(msg, state["cache"], object()))


def test_llm_trade_and_watchlist_executed(env):
    env["llm_raw"] = json.dumps({
        "message": "Buying 2 AAPL and adding PYPL.",
        "trades": [{"ticker": "aapl", "side": "buy", "quantity": 2}],
        "watchlist_changes": [{"ticker": "PYPL", "action": "add"}],
    })
    out = run(env, "buy 2 apple and watch paypal")
    assert out["message"] == "Buying 2 AAPL and adding PYPL."
    assert out["actions"]["trades"] == [{"ticker": "AAPL", "side": "buy", "quantity": 2,
                                         "status": "executed", "price": 190.0, "error": None}]
    assert out["actions"]["watchlist_changes"] == [{"ticker": "PYPL", "action": "add",
                                                    "status": "executed", "error": None}]
    assert env["trades"] == [("AAPL", "buy", 2)]
    assert "PYPL" in env["watchlist"]
    # persisted user + assistant with actions
    assert [m["role"] for m in env["messages"]] == ["user", "assistant"]
    assert json.loads(env["messages"][1]["actions"])["trades"][0]["status"] == "executed"


def test_failed_trade_reported_with_error(env):
    env["llm_raw"] = json.dumps({
        "message": "Buying 100 MSFT.",
        "trades": [{"ticker": "MSFT", "side": "buy", "quantity": 100},
                   {"ticker": "AAPL", "side": "sell", "quantity": 1},
                   {"ticker": "ZZZZ", "side": "buy", "quantity": 1}],
        "watchlist_changes": [{"ticker": "AAPL", "action": "add"}],
    })
    out = run(env, "go big")
    trades = out["actions"]["trades"]
    assert [t["status"] for t in trades] == ["failed"] * 3
    assert trades[0]["error"] == "Insufficient cash"
    assert trades[1]["error"] == "Insufficient shares"
    assert "No price" in trades[2]["error"]
    assert out["actions"]["watchlist_changes"][0]["status"] == "failed"
    assert "already" in out["actions"]["watchlist_changes"][0]["error"]
    assert "Could not buy 100 MSFT: Insufficient cash" in out["message"]
    assert env["cash"] == 1000.0


def test_unexpected_exception_in_trade_is_contained(env, monkeypatch):
    def boom(*a):
        raise RuntimeError("db exploded")
    monkeypatch.setattr(service, "_execute_trade", boom)
    env["llm_raw"] = json.dumps({"message": "ok", "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 1}]})
    out = run(env, "buy")
    assert out["actions"]["trades"][0]["status"] == "failed"
    assert out["actions"]["trades"][0]["error"] == "db exploded"


def test_invalid_ticker_and_zero_quantity(env):
    env["llm_raw"] = json.dumps({"message": "ok", "trades": [
        {"ticker": "NOT A TICKER", "side": "buy", "quantity": 1},
        {"ticker": "AAPL", "side": "buy", "quantity": 0}]})
    out = run(env, "x")
    errs = [t["error"] for t in out["actions"]["trades"]]
    assert "Invalid ticker" in errs[0]
    assert "greater than zero" in errs[1]
    assert env["trades"] == []


def test_malformed_llm_output_returns_apology(env):
    env["llm_raw"] = "I am not JSON"
    out = run(env, "hello")
    assert out == {"message": service.APOLOGY_MESSAGE, "actions": {"trades": [], "watchlist_changes": []}}
    assert [m["role"] for m in env["messages"]] == ["user", "assistant"]


def test_llm_call_error_returns_apology(env):
    env["llm_raw"] = LLMCallError("network down")
    out = run(env, "hello")
    assert out["message"] == service.APOLOGY_MESSAGE
    assert out["actions"] == {"trades": [], "watchlist_changes": []}


def test_no_actions_stored_as_null(env):
    env["llm_raw"] = json.dumps({"message": "Your portfolio looks fine."})
    out = run(env, "how am I doing?")
    assert out["actions"] == {"trades": [], "watchlist_changes": []}
    assert env["messages"][1]["actions"] is None


def test_prompt_contains_context_and_history(env):
    env["messages"].extend([
        {"role": "user", "content": "earlier question", "actions": None},
        {"role": "assistant", "content": "earlier answer",
         "actions": json.dumps({"trades": [{"ticker": "AAPL"}], "watchlist_changes": []})},
    ])
    env["llm_raw"] = json.dumps({"message": "hi"})
    run(env, "new question")
    msgs = env["llm_calls"][0]
    assert msgs[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert msgs[1] == {"role": "user", "content": "earlier question"}
    assert msgs[2]["content"].startswith("earlier answer\n[Actions from this earlier turn - already applied")
    ctx = msgs[-2]
    assert ctx["role"] == "system" and ctx["content"].startswith("CURRENT PORTFOLIO")
    assert "Cash: $8,100.00" in ctx["content"]
    assert "AAPL | 10 | $190.00" in ctx["content"]
    assert "MSFT | $400.00" in ctx["content"]
    assert msgs[-1] == {"role": "user", "content": "new question"}


def test_mock_mode_skips_llm(env, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    out = run(env, "buy 1 AAPL and add PYPL")
    assert env["llm_calls"] == []
    assert out["actions"]["trades"][0]["status"] == "executed"
    assert out["actions"]["watchlist_changes"][0]["status"] == "executed"
    assert out["message"].startswith("Mock response:")


def test_mock_mode_default(env, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    out = run(env, "hello")
    assert out == {"message": "Mock response: I received your message.",
                   "actions": {"trades": [], "watchlist_changes": []}}


def test_mock_mode_failed_trade(env, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    out = run(env, "sell 5 AAPL")
    t = out["actions"]["trades"][0]
    assert t["status"] == "failed" and t["error"] == "Insufficient shares"


def test_get_chat_history_decodes_actions(env):
    env["llm_raw"] = json.dumps({"message": "ok", "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 1}]})
    run(env, "buy one")
    hist = service.get_chat_history()
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[0]["actions"] is None
    assert hist[1]["actions"]["trades"][0]["ticker"] == "AAPL"
    assert set(hist[0]) == {"id", "role", "content", "actions", "created_at"}


def test_build_context_handles_empty():
    ctx = build_portfolio_context({"cash_balance": 10000, "positions": []}, [])
    assert "Positions: none" in ctx and "(empty)" in ctx


def test_build_messages_truncates_history():
    hist = [{"role": "user", "content": str(i)} for i in range(50)]
    msgs = build_messages("ctx", hist, "now")
    assert len(msgs) == 2 + 20 + 1
