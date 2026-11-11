"""LLM call via LiteLLM -> OpenRouter -> Cerebras with structured outputs."""

from __future__ import annotations

import os

from .schema import LLMResponse

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))


class LLMCallError(RuntimeError):
    """Raised when the LLM call itself fails (network, auth, provider error)."""


async def call_llm(messages: list[dict]) -> str:
    """Call the model asynchronously and return the raw message content."""
    if not os.getenv("OPENROUTER_API_KEY"):
        raise LLMCallError("OPENROUTER_API_KEY is not set")
    try:
        from litellm import acompletion  # imported lazily: heavy import, unused in mock mode

        response = await acompletion(
            model=MODEL,
            messages=messages,
            response_format=LLMResponse,
            reasoning_effort="low",
            extra_body=EXTRA_BODY,
            timeout=LLM_TIMEOUT_SECONDS,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:  # litellm raises a wide variety of exception types
        raise LLMCallError(f"{type(exc).__name__}: {exc}") from exc
