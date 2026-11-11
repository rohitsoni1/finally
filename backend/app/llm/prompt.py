"""System prompt, portfolio context and message construction."""

from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """You are FinAlly, an AI trading assistant inside a simulated trading workstation.
The user trades a paper portfolio with virtual money: market orders only, instant fill at the current price, no fees.

Your job:
- Analyze portfolio composition, risk concentration and unrealized P&L using the live data provided.
- Suggest trades with brief, data-driven reasoning.
- Execute trades when the user asks for them or agrees to a suggestion, by listing them in "trades".
- Manage the watchlist proactively (add tickers the user is discussing, remove ones they no longer want) via "watchlist_changes".
- Be concise: short paragraphs or bullet points, concrete numbers, no filler.

Rules:
- Only include a trade when the user has asked for it or clearly agreed. Never invent trades.
- Quantities are share counts (> 0, fractional allowed). If the user gives a dollar amount, convert it using the current price.
- Buys need enough cash; sells need enough shares. Check the context before proposing a trade and say so if it cannot be done.
- Tickers are uppercase symbols such as AAPL.
- The CURRENT PORTFOLIO message is the only source of truth for cash, positions and quantities. Earlier trades in the conversation are already included in it; never add them again.
- Trades and watchlist changes you list are executed automatically right after your reply; describe them as being executed now. If one fails, the user is shown the error.
- Watchlist changes are applied before trades, and a ticker gets a live price as soon as it is added. So to trade a ticker that has no price in the context, add it in "watchlist_changes" and include the trade in "trades" in the same reply; it fills at the live market price. Never refuse a requested trade only because its price is missing from the context.
- You don't know the exact fill price in advance; don't state a specific fill price for tickers not in the context.
- Always respond with a single JSON object: {"message": string, "trades": [{"ticker","side":"buy"|"sell","quantity"}], "watchlist_changes": [{"ticker","action":"add"|"remove"}]}. Use empty lists when there is nothing to do.
"""

HISTORY_LIMIT = 20


def _money(x: Any) -> str:
    try:
        return f"${float(x):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _pct(x: Any) -> str:
    try:
        return f"{float(x):+.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def build_portfolio_context(portfolio: dict, watchlist: list[dict]) -> str:
    """Render portfolio + watchlist (with live prices) as compact text for the prompt.

    `portfolio` follows the GET /api/portfolio shape; `watchlist` items have
    `ticker` and `price` (PriceUpdate.to_dict() or None).
    """
    lines = [
        "CURRENT PORTFOLIO (live, authoritative; already includes every trade made earlier in this conversation)",
        f"Cash: {_money(portfolio.get('cash_balance'))}",
        f"Positions value: {_money(portfolio.get('positions_value'))}",
        f"Total value: {_money(portfolio.get('total_value'))}",
        f"Unrealized P&L: {_money(portfolio.get('unrealized_pnl'))} "
        f"({_pct(portfolio.get('unrealized_pnl_percent'))})",
    ]
    positions = portfolio.get("positions") or []
    if positions:
        lines.append("Positions (ticker | qty | avg cost | price | value | P&L | weight):")
        for p in positions:
            weight = p.get("weight")
            weight_s = f"{float(weight) * 100:.1f}%" if isinstance(weight, (int, float)) else "n/a"
            lines.append(
                f"- {p.get('ticker')} | {p.get('quantity')} | {_money(p.get('avg_cost'))} | "
                f"{_money(p.get('current_price'))} | {_money(p.get('market_value'))} | "
                f"{_money(p.get('unrealized_pnl'))} ({_pct(p.get('unrealized_pnl_percent'))}) | {weight_s}"
            )
    else:
        lines.append("Positions: none")

    lines.append("")
    lines.append("WATCHLIST (ticker | price | day change):")
    if not watchlist:
        lines.append("- (empty)")
    for w in watchlist:
        price = w.get("price") or {}
        if price:
            lines.append(
                f"- {w.get('ticker')} | {_money(price.get('price'))} | "
                f"{_pct(price.get('day_change_percent'))}"
            )
        else:
            lines.append(f"- {w.get('ticker')} | no price yet")
    return "\n".join(lines)


def _history_content(msg: dict) -> str:
    content = msg.get("content") or ""
    actions = msg.get("actions")
    if msg.get("role") == "assistant" and actions:
        if isinstance(actions, str):
            try:
                actions = json.loads(actions)
            except json.JSONDecodeError:
                actions = None
        if isinstance(actions, dict) and (actions.get("trades") or actions.get("watchlist_changes")):
            content += (
                "\n[Actions from this earlier turn - already applied and already reflected in the "
                "CURRENT PORTFOLIO; do not count them again: "
                + json.dumps(actions, separators=(",", ":")) + "]"
            )
    return content


def build_messages(context: str, history: list[dict], user_message: str) -> list[dict]:
    """Assemble the chat messages: system prompt, live context, history, new message."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history[-HISTORY_LIMIT:]:
        role = msg.get("role")
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": _history_content(msg)})
    # Live context goes right before the new message so it supersedes anything in history.
    messages.append({"role": "system", "content": context})
    messages.append({"role": "user", "content": user_message})
    return messages
