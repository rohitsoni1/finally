# FinAlly — AI Trading Workstation

An AI-powered trading terminal: live market prices, a simulated $10,000 portfolio, and an LLM assistant that analyzes positions and executes trades through chat. Built entirely by coding agents as the capstone of an agentic AI coding course.

> **Status:** Specification stage. The full plan lives in `planning/PLAN.md`; application code is still to be built.

## Quick Start (once built)

```bash
cp .env.example .env        # add your OPENROUTER_API_KEY
./scripts/start_mac.sh      # Windows: ./scripts/start_windows.ps1
```

Open http://localhost:8000. Stop with `./scripts/stop_mac.sh`. Data persists in the `finally-data` Docker volume.

## Environment

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Required for AI chat |
| `MASSIVE_API_KEY` | Optional; real market data (simulator used if unset) |
| `LLM_MOCK` | Optional; `true` for deterministic LLM responses in tests |

## Stack

Next.js + TypeScript (static export) · FastAPI + `uv` · SQLite · SSE · LiteLLM → OpenRouter (Cerebras) · single Docker container on port 8000

## Layout

```
frontend/   Next.js app
backend/    FastAPI: API, SSE, market data, LLM, DB
planning/   Specs and agent docs (start with PLAN.md)
scripts/    Docker start/stop scripts
test/       Playwright E2E tests
db/         SQLite volume mount
```

## License

See `LICENSE`.
