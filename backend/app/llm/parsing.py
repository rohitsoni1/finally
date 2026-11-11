"""Robust parsing of raw LLM output into an LLMResponse.

The model is asked for structured output, but we never trust it blindly:
code fences, surrounding prose, invalid individual items and odd casing are
all tolerated. Only a response with no usable `message` is rejected.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from .schema import LLMResponse, TradeInstruction, WatchlistChange

logger = logging.getLogger(__name__)

TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


class LLMParseError(ValueError):
    """Raised when the LLM output cannot be turned into a usable response."""


def normalize_ticker(raw: Any) -> str | None:
    """Uppercase/strip a ticker; return None if it is not a valid symbol."""
    if not isinstance(raw, str):
        return None
    ticker = raw.strip().upper()
    return ticker if TICKER_RE.match(ticker) else None


def _extract_json_object(text: str) -> dict:
    text = text.strip()
    fence = _FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMParseError("LLM output contained no JSON object") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMParseError(f"LLM output was not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise LLMParseError("LLM output JSON was not an object")
    return data


def _parse_items(raw_items: Any, model: type, kind: str) -> list:
    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        logger.warning("Ignoring non-list %s in LLM output: %r", kind, raw_items)
        return []
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            logger.warning("Ignoring malformed %s item: %r", kind, raw)
            continue
        cleaned = dict(raw)
        for key in ("side", "action"):
            if isinstance(cleaned.get(key), str):
                cleaned[key] = cleaned[key].strip().lower()
        if isinstance(cleaned.get("ticker"), str):
            cleaned["ticker"] = cleaned["ticker"].strip().upper()
        try:
            items.append(model.model_validate(cleaned))
        except ValidationError as exc:
            logger.warning("Ignoring invalid %s item %r: %s", kind, raw, exc)
    return items


def parse_llm_output(raw: str | None) -> LLMResponse:
    """Parse raw model text into an LLMResponse, tolerating minor malformations."""
    if not raw or not raw.strip():
        raise LLMParseError("LLM returned an empty response")
    data = _extract_json_object(raw)
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        raise LLMParseError("LLM response is missing a 'message' string")
    return LLMResponse(
        message=message.strip(),
        trades=_parse_items(data.get("trades"), TradeInstruction, "trade"),
        watchlist_changes=_parse_items(
            data.get("watchlist_changes"), WatchlistChange, "watchlist change"
        ),
    )
