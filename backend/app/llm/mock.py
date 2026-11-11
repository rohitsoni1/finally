"""Deterministic mock LLM used when LLM_MOCK=true (E2E tests, offline dev).

Rules (see planning/API_CONTRACT.md §4), case-insensitive, all matches honoured
in order of appearance:
  buy <qty> <TICKER>   -> buy trade
  sell <qty> <TICKER>  -> sell trade
  add <TICKER>         -> watchlist add
  remove <TICKER>      -> watchlist remove
Anything else -> "Mock response: I received your message." with no actions.
"""

from __future__ import annotations

import os
import re

from .schema import LLMResponse, TradeInstruction, WatchlistChange

DEFAULT_MOCK_MESSAGE = "Mock response: I received your message."

_TICKER = r"([A-Za-z0-9.\-]{1,10})"
_QTY = r"(\d+(?:\.\d+)?)"
_PATTERN = re.compile(
    rf"\b(?:(?P<side>buy|sell)\s+{_QTY}\s+{_TICKER}|(?P<action>add|remove)\s+{_TICKER})(?![A-Za-z0-9.\-])",
    re.IGNORECASE,
)


def is_mock_enabled() -> bool:
    return os.getenv("LLM_MOCK", "false").strip().lower() in {"1", "true", "yes", "on"}


def _fmt_qty(qty: float) -> str:
    return str(int(qty)) if qty == int(qty) else str(qty)


def mock_response(user_message: str) -> LLMResponse:
    trades: list[TradeInstruction] = []
    changes: list[WatchlistChange] = []
    parts: list[str] = []
    for m in _PATTERN.finditer(user_message or ""):
        if m.group("side"):
            side = m.group("side").lower()
            qty = float(m.group(2))
            ticker = m.group(3).upper()
            if qty <= 0:
                continue
            trades.append(TradeInstruction(ticker=ticker, side=side, quantity=qty))
            parts.append(f"{side} {_fmt_qty(qty)} {ticker}")
        else:
            action = m.group("action").lower()
            ticker = m.group(5).upper()
            changes.append(WatchlistChange(ticker=ticker, action=action))
            parts.append(f"{action} {ticker} {'to' if action == 'add' else 'from'} watchlist")
    if not parts:
        return LLMResponse(message=DEFAULT_MOCK_MESSAGE)
    return LLMResponse(
        message="Mock response: " + "; ".join(parts) + ".",
        trades=trades,
        watchlist_changes=changes,
    )
