"""Chat API: POST /api/chat and GET /api/chat/history."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.llm import get_chat_history, handle_chat

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@router.post("")
async def post_chat(body: ChatRequest, request: Request) -> dict:
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message must not be empty")
    state = request.app.state
    return await handle_chat(
        body.message,
        getattr(state, "price_cache", None),
        getattr(state, "market", None),
    )


@router.get("/history")
def chat_history(limit: int = 200) -> dict:
    limit = max(1, min(limit, 500))
    return {"messages": get_chat_history(limit)}
