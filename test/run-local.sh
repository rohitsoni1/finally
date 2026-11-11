#!/usr/bin/env bash
# Run the E2E suite locally (no Docker):
#   1. build the Next.js static export (skip with SKIP_FRONTEND_BUILD=1)
#   2. start the backend with LLM_MOCK=true, a throwaway SQLite DB, and FINALLY_STATIC_DIR -> frontend/out
#   3. run Playwright against it, then stop the server.
# Extra args are passed to `playwright test` (e.g. ./run-local.sh e2e/04-trading.spec.ts).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PORT="${PORT:-8000}"
TMP="$HERE/.tmp"
mkdir -p "$TMP"

if curl -fsS "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
  echo "Something is already listening on :$PORT - stop it first (the suite needs a fresh DB)." >&2
  exit 1
fi

if [[ "${SKIP_FRONTEND_BUILD:-0}" != "1" ]]; then
  echo "==> Building frontend static export"
  (cd "$ROOT/frontend" && { [[ -d node_modules ]] || npm install --no-audit --no-fund; } && npm run build)
fi
STATIC_DIR="${FINALLY_STATIC_DIR:-$ROOT/frontend/out}"
[[ -f "$STATIC_DIR/index.html" ]] || { echo "No index.html in $STATIC_DIR" >&2; exit 1; }

DB="$TMP/finally-e2e-$$.db"
rm -f "$DB"
echo "==> Starting backend on :$PORT (db=$DB, static=$STATIC_DIR)"
(
  cd "$ROOT/backend"
  LLM_MOCK=true MASSIVE_API_KEY= FINALLY_DB_PATH="$DB" FINALLY_STATIC_DIR="$STATIC_DIR" \
    exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
) >"$TMP/server.log" 2>&1 &
SERVER_PID=$!
cleanup() { kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; rm -f "$DB"; }
trap cleanup EXIT

for _ in $(seq 1 60); do
  curl -fsS "http://localhost:$PORT/api/health" >/dev/null 2>&1 && break
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "Backend exited:"; cat "$TMP/server.log"; exit 1; }
  sleep 0.5
done
curl -fsS "http://localhost:$PORT/api/health" >/dev/null || { echo "Backend not healthy"; cat "$TMP/server.log"; exit 1; }

cd "$HERE"
[[ -d node_modules ]] || npm install --no-audit --no-fund
BASE_URL="http://localhost:$PORT" npx playwright test "$@"
