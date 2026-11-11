"""Chat orchestration: context -> LLM (or mock) -> parse -> auto-execute -> persist.

Integration points with other modules are isolated in the small `_` adapter
functions below so they can be swapped/mocked in tests and adjusted in one
place if a teammate's interface changes.
"""

from __future__ import annotations

import logging
from typing import Any

from .client import LLMCallError, call_llm
from .mock import is_mock_enabled, mock_response
from .parsing import LLMParseError, normalize_ticker, parse_llm_output
from .prompt import HISTORY_LIMIT, build_messages, build_portfolio_context
from .schema import LLMResponse, TradeInstruction, WatchlistChange

logger = logging.getLogger(__name__)

APOLOGY_MESSAGE = (
    "Sorry, I couldn't process that request right now. Please try again in a moment."
)


# --------------------------------------------------------------------------- #
# Adapters to other backend modules (imported lazily)
# --------------------------------------------------------------------------- #


def _get_portfolio(price_cache) -> dict:
    from app.portfolio import get_portfolio

    return get_portfolio(price_cache)


def _get_watchlist_with_prices(price_cache) -> list[dict]:
    from app.portfolio import list_watchlist

    return list_watchlist(price_cache)


def _execute_trade(ticker: str, side: str, quantity: float, price_cache) -> dict:
    from app.portfolio import execute_trade

    return execute_trade(ticker, side, quantity, price_cache)


def _trade_error_types() -> tuple[type[BaseException], ...]:
    try:
        from app.portfolio import TradeError

        return (TradeError,)
    except ImportError:  # pragma: no cover - defensive
        return ()


async def _add_to_watchlist(ticker: str, price_cache, market_source) -> Any:
    from app.portfolio import add_to_watchlist

    return await add_to_watchlist(ticker, market_source)


async def _remove_from_watchlist(ticker: str, price_cache, market_source) -> Any:
    from app.portfolio import remove_from_watchlist

    return await remove_from_watchlist(ticker, market_source)


def _load_history(limit: int) -> list[dict]:
    from app import db

    return list(db.list_chat_messages(limit=limit))


def _save_message(role: str, content: str, actions: dict | None) -> None:
    from app import db

    db.insert_chat_message(role=role, content=content, actions=actions)


# --------------------------------------------------------------------------- #
# Action execution
# --------------------------------------------------------------------------- #


def _error_text(exc: BaseException) -> str:
    detail = getattr(exc, "detail", None) or getattr(exc, "message", None)
    return str(detail or exc) or type(exc).__name__


def _fmt_qty(q: float) -> str:
    return str(int(q)) if float(q).is_integer() else f"{q:g}"


def _run_trade(t: TradeInstruction, price_cache) -> dict:
    result = {"ticker": t.ticker, "side": t.side, "quantity": t.quantity,
              "status": "failed", "price": None, "error": None}
    ticker = normalize_ticker(t.ticker)
    if ticker is None:
        result["error"] = f"Invalid ticker '{t.ticker}'"
        return result
    result["ticker"] = ticker
    if not t.quantity or t.quantity <= 0:
        result["error"] = "Quantity must be greater than zero"
        return result
    try:
        out = _execute_trade(ticker, t.side, t.quantity, price_cache)
    except Exception as exc:  # TradeError or anything unexpected: report, don't crash
        if not isinstance(exc, _trade_error_types()):
            logger.exception("Unexpected error executing LLM trade %s", t)
        result["error"] = _error_text(exc)
        return result
    trade = out.get("trade", out) if isinstance(out, dict) else {}
    result["status"] = "executed"
    result["price"] = trade.get("price") if isinstance(trade, dict) else None
    return result


async def _run_watchlist_change(c: WatchlistChange, price_cache, market_source) -> dict:
    result = {"ticker": c.ticker, "action": c.action, "status": "failed", "error": None}
    ticker = normalize_ticker(c.ticker)
    if ticker is None:
        result["error"] = f"Invalid ticker '{c.ticker}'"
        return result
    result["ticker"] = ticker
    try:
        if c.action == "add":
            await _add_to_watchlist(ticker, price_cache, market_source)
        else:
            await _remove_from_watchlist(ticker, price_cache, market_source)
    except Exception as exc:
        logger.info("Watchlist %s %s failed: %s", c.action, ticker, exc)
        result["error"] = _error_text(exc)
        return result
    result["status"] = "executed"
    return result


async def execute_actions(response: LLMResponse, price_cache, market_source) -> dict:
    """Execute watchlist changes first (so newly added tickers get prices), then trades."""
    changes = [await _run_watchlist_change(c, price_cache, market_source)
               for c in response.watchlist_changes]
    trades = [_run_trade(t, price_cache) for t in response.trades]
    return {"trades": trades, "watchlist_changes": changes}


def _failure_notes(actions: dict) -> str:
    notes = []
    for t in actions["trades"]:
        if t["status"] == "failed":
            notes.append(f"- Could not {t['side']} {_fmt_qty(t['quantity'])} {t['ticker']}: {t['error']}")
    for c in actions["watchlist_changes"]:
        if c["status"] == "failed":
            verb = "add" if c["action"] == "add" else "remove"
            prep = "to" if verb == "add" else "from"
            notes.append(f"- Could not {verb} {c['ticker']} {prep} the watchlist: {c['error']}")
    return ("\n\nSome actions failed:\n" + "\n".join(notes)) if notes else ""


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def _empty_actions() -> dict:
    return {"trades": [], "watchlist_changes": []}


async def _get_llm_response(message: str, price_cache) -> LLMResponse:
    if is_mock_enabled():
        return mock_response(message)
    try:
        history = _load_history(HISTORY_LIMIT)
    except Exception:
        logger.exception("Failed to load chat history; continuing without it")
        history = []
    try:
        context = build_portfolio_context(
            _get_portfolio(price_cache), _get_watchlist_with_prices(price_cache)
        )
    except Exception:
        logger.exception("Failed to build portfolio context")
        context = "CURRENT PORTFOLIO: unavailable"
    raw = await call_llm(build_messages(context, history, message))
    return parse_llm_output(raw)


async def handle_chat(message: str, price_cache, market_source) -> dict:
    """Process one user chat message and return the /api/chat response body."""
    message = (message or "").strip()
    try:
        llm_response = await _get_llm_response(message, price_cache)
    except (LLMCallError, LLMParseError) as exc:
        logger.warning("LLM failure: %s", exc)
        llm_response = None
    except Exception:
        logger.exception("Unexpected error during LLM call")
        llm_response = None

    if llm_response is None:
        reply, actions = APOLOGY_MESSAGE, _empty_actions()
    else:
        actions = await execute_actions(llm_response, price_cache, market_source)
        reply = llm_response.message + _failure_notes(actions)

    has_actions = bool(actions["trades"] or actions["watchlist_changes"])
    try:
        _save_message("user", message, None)
        _save_message("assistant", reply, actions if has_actions else None)
    except Exception:
        logger.exception("Failed to persist chat messages")

    return {"message": reply, "actions": actions}


def get_chat_history(limit: int = 200) -> list[dict]:
    """Return stored chat messages, ascending by time, in the API shape."""
    import json

    out = []
    for m in _load_history(limit):
        actions = m.get("actions")
        if isinstance(actions, str):
            try:
                actions = json.loads(actions)
            except json.JSONDecodeError:
                actions = None
        out.append({
            "id": m.get("id"),
            "role": m.get("role"),
            "content": m.get("content"),
            "actions": actions,
            "created_at": m.get("created_at"),
        })
    return out
