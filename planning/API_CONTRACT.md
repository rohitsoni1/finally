# FinAlly — Team Contract (API shapes + file ownership)

Shared contract for the agent team. PLAN.md is the spec; this file pins down the details
agents need to work in parallel. If you need to change anything here, message the owner
and the agents who consume it, then update this file.

## 1. File ownership

| Agent | Owns | Notes |
|---|---|---|
| `backend` | `backend/pyproject.toml`, `backend/uv.lock`, `backend/app/main.py`, `backend/app/market/`, `backend/app/api/` (except `chat.py`), `backend/app/portfolio/`, `backend/tests/` (except `db/`, `llm/`) | Wires routers, lifespan, static serving. Only agent that edits `pyproject.toml` — others ask it to add deps (or run `uv add` and tell it). |
| `database` | `backend/app/db/`, `backend/tests/db/` | Schema SQL, lazy init + seed, connection handling, repository functions. |
| `llm` | `backend/app/llm/`, `backend/app/api/chat.py`, `backend/tests/llm/` | Uses the `cerebras` skill (LiteLLM → OpenRouter → Cerebras, structured outputs). Mock mode. |
| `frontend` | `frontend/` | Next.js + TS static export, Tailwind, unit tests. |
| `devops` | `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `scripts/`, `.env.example`, `db/.gitkeep`, root `.gitignore` | |
| `integration` | `test/` | Playwright E2E + `test/docker-compose.test.yml`. Reports bugs to owners; does not fix others' code. |

## 2. Backend module boundaries (Python, package root `backend/app`)

- `app.market` — `PriceCache`, `PriceUpdate`, `MarketDataSource`, `create_market_data_source` (see MARKET_INTERFACE.md / MARKET_DATA_DESIGN.md).
- `app.db` — must expose at least:
  - `init_db(path: str | None = None)`: idempotent; creates tables + seeds if missing. Default path from env `FINALLY_DB_PATH`, else `<repo>/db/finally.db` (in Docker: `/app/db/finally.db`).
  - `get_connection()` (context manager or equivalent; sqlite3, safe for use from async FastAPI handlers).
  - Repository functions (sync is fine), all taking `user_id="default"`:
    `get_cash`, `set_cash`, `get_positions`, `get_position`, `upsert_position`, `delete_position`,
    `insert_trade`, `list_trades`, `get_watchlist`, `add_watchlist_ticker`, `remove_watchlist_ticker`,
    `insert_snapshot`, `list_snapshots`, `insert_chat_message`, `list_chat_messages(limit)`.
  - A way to run a trade's cash + position + trade-row writes in one transaction.
- `app.portfolio` (backend) — `execute_trade(ticker, side, quantity, price_cache) -> dict` raising `TradeError(message)` on validation failure; `get_portfolio(price_cache) -> dict`; `record_snapshot(price_cache)`. **The LLM agent calls `execute_trade` and the watchlist helpers; it does not reimplement them.**
- `app.llm` (llm) — `async handle_chat(message: str, price_cache, market_source) -> dict` returning the `/api/chat` response below.

Ticker normalisation: uppercase, strip whitespace, 1–10 chars `[A-Z0-9.\-]`.

## 3. REST API (all JSON, same origin)

Errors: non-2xx with `{"detail": "<human readable message>"}` (FastAPI default). Trade validation failures → 400.

### `GET /api/health` → `{"status": "ok"}`

### `GET /api/stream/prices` (SSE)
Unnamed events, ~500 ms. `data:` is an object keyed by ticker, values are `PriceUpdate.to_dict()`:
```json
{"AAPL": {"ticker":"AAPL","price":190.12,"previous_price":190.07,"reference_price":189.70,
          "change":0.05,"change_percent":0.0263,"day_change":0.42,"day_change_percent":0.2211,
          "direction":"up","timestamp":1762540800.12}}
```
`direction` ∈ `"up" | "down" | "flat"`. `timestamp` is Unix seconds (float). Percentages are already ×100.

### `GET /api/portfolio`
```json
{
  "cash_balance": 8100.0,
  "positions_value": 1905.0,
  "total_value": 10005.0,
  "total_cost": 1900.0,
  "unrealized_pnl": 5.0,
  "unrealized_pnl_percent": 0.263,
  "positions": [
    {"ticker":"AAPL","quantity":10,"avg_cost":190.0,"current_price":190.5,
     "market_value":1905.0,"cost_basis":1900.0,"unrealized_pnl":5.0,"unrealized_pnl_percent":0.263,
     "weight":0.1904}
  ]
}
```
`weight` = market_value / total_value (0–1). If a ticker has no cached price, fall back to `avg_cost`.

### `POST /api/portfolio/trade`
Request `{"ticker":"AAPL","quantity":10,"side":"buy"}` (`quantity` > 0, fractional OK; `side` ∈ buy|sell).
Response 200:
```json
{"trade":{"id":"…","ticker":"AAPL","side":"buy","quantity":10,"price":190.0,"executed_at":"2026-11-11T12:00:00+00:00"},
 "portfolio": { …same shape as GET /api/portfolio… }}
```
400 on insufficient cash / insufficient shares / no price available / bad input. Selling the full position deletes the row. A snapshot is recorded after every successful trade. Buying a ticker not on the watchlist is allowed only if a price exists in the cache.

### `GET /api/portfolio/history`
`{"snapshots":[{"total_value":10000.0,"recorded_at":"2026-11-11T12:00:00+00:00"}, …]}` ascending by time.

### `GET /api/watchlist`
`{"tickers":[{"ticker":"AAPL","added_at":"…","price":{…PriceUpdate.to_dict() or null…}}, …]}`

### `POST /api/watchlist` — `{"ticker":"PYPL"}` → 201 `{"ticker":"PYPL","added_at":"…"}`; 409 if already present; 400 if invalid. Also calls `market_source.add_ticker`.
### `DELETE /api/watchlist/{ticker}` → 200 `{"ticker":"PYPL","removed":true}`; 404 if absent. Calls `market_source.remove_ticker` (unless an open position still needs the price — keep it tracked then).

### `POST /api/chat`
Request `{"message":"Buy 5 AAPL"}`. Response 200:
```json
{
  "message": "Done — bought 5 AAPL at $190.12.",
  "actions": {
    "trades": [{"ticker":"AAPL","side":"buy","quantity":5,"status":"executed","price":190.12,"error":null}],
    "watchlist_changes": [{"ticker":"PYPL","action":"add","status":"executed","error":null}]
  }
}
```
`status` ∈ `executed | failed`; failed items carry `error`. LLM failures return 200 with an apologetic `message` and empty actions (never a 500 for a bad LLM output).

### `GET /api/chat/history` → `{"messages":[{"id","role","content","actions","created_at"}, …]}` ascending (extra endpoint, owned by `llm`, so the panel can restore history on reload).

## 4. LLM mock mode (`LLM_MOCK=true`) — deterministic, used by E2E
- Message containing `buy <qty> <TICKER>` (case-insensitive) → executes that buy.
- `sell <qty> <TICKER>` → executes that sell.
- `add <TICKER>` / `remove <TICKER>` → watchlist change.
- Anything else → `{"message": "Mock response: I received your message.", no actions}`.

## 5. Frontend test hooks (for Playwright)
Stable `data-testid`s: `watchlist`, `watchlist-row-<TICKER>`, `price-<TICKER>`, `connection-status` (with `data-status="connected|reconnecting|disconnected"`), `cash-balance`, `total-value`, `trade-ticker`, `trade-quantity`, `trade-buy`, `trade-sell`, `trade-error`, `positions-table`, `position-row-<TICKER>`, `heatmap`, `pnl-chart`, `main-chart`, `watchlist-add-input`, `watchlist-add-button`, `watchlist-remove-<TICKER>`, `chat-panel`, `chat-input`, `chat-send`, `chat-message` (each message, with `data-role`), `chat-loading`, `chat-action`.

## 6. Runtime layout
- Backend serves the Next.js static export from `backend/static/` (env `FINALLY_STATIC_DIR` overrides). `/api/*` routes take precedence; unknown non-API paths fall back to `index.html`.
- Local dev: `cd backend && uv run uvicorn app.main:app --port 8000`; frontend dev server may proxy `/api` to 8000.
- `.env` is read from the repo root (`python-dotenv`, `find_dotenv` or explicit path).
