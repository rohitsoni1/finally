# Change review

Reviewed the working-tree changes against `HEAD` on 2026-11-07.

## Findings

### P1 — The change removes the entire market-data backend and its tests

The diff deletes `backend/app/market/` (the source interface, simulator, Massive client, cache, models, factory, and SSE stream), `backend/market_data_demo.py`, the backend dependency/lock files, and all backend market tests. No replacement implementation appears in the working tree. The README and `planning/PLAN.md` still describe a working FastAPI backend and streaming market data, so this leaves the documented application without its backend subsystem. If this is an intentional reset, the repository documentation and scope need to say so; otherwise restore or replace these files.

### P1 — README quick start points to files absent from the repository

The revised quick start requires `.env.example` and `scripts/start_mac.sh`, and mentions `scripts/start_windows.ps1` and `scripts/stop_mac.sh`. None of these paths exists in the current tree. As written, setup fails at `cp .env.example .env` before the app can start. Restore the referenced files or make the instructions use files that exist (the prior Docker build/run commands are also not currently runnable because the backend and Dockerfile are absent).

### P2 — Stop hook launches a recursive review on every stop

`.claude/settings.json` adds a `Stop` command that runs `codex exec` with a request to review changes and write `feedback/Changes.md`. Since this hook runs when the assistant stops, the launched Codex session can itself trigger the same hook, causing repeated nested reviews or runaway execution. Add a recursion guard (for example, a hook-specific environment flag) or use a mechanism that does not invoke another stop-hook-enabled session.

### P2 — Stop hook configuration is difficult to maintain and has no failure handling

The new JSON is valid but unusually indented, and the hook has no timeout/failure behavior or guard for a missing `codex` executable. A missing executable or failed review can make the stop hook fail repeatedly. Format the JSON and define a bounded failure strategy so the hook cannot trap the session.

## Summary

The README now describes a shorter setup, but the working tree removes the backend that README and the plan still describe, while the new setup commands reference absent files. The stop hook also risks recursively invoking itself. No tests were run; this was a source review.
