"""Structured-output schema for the FinAlly chat assistant."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TradeInstruction(BaseModel):
    """A market order the assistant wants to execute."""

    ticker: str = Field(description="Ticker symbol, e.g. AAPL")
    side: Literal["buy", "sell"]
    quantity: float = Field(description="Number of shares (> 0, fractional allowed)")


class WatchlistChange(BaseModel):
    """A watchlist modification the assistant wants to make."""

    ticker: str = Field(description="Ticker symbol, e.g. PYPL")
    action: Literal["add", "remove"]


class LLMResponse(BaseModel):
    """The JSON object the LLM must return on every turn."""

    message: str = Field(description="Conversational reply shown to the user")
    trades: list[TradeInstruction] = Field(
        default_factory=list, description="Trades to execute now; empty if none"
    )
    watchlist_changes: list[WatchlistChange] = Field(
        default_factory=list, description="Watchlist changes to make now; empty if none"
    )
