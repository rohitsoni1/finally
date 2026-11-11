#!/usr/bin/env bash
# Start FinAlly in Docker (macOS/Linux). Idempotent: safe to run repeatedly.
#   ./scripts/start_mac.sh            build image if missing, (re)start container
#   ./scripts/start_mac.sh --build    force an image rebuild
#   ./scripts/start_mac.sh --no-open  don't open the browser
set -euo pipefail

IMAGE="${FINALLY_IMAGE:-finally:latest}"
CONTAINER="${FINALLY_CONTAINER:-finally}"
VOLUME="${FINALLY_VOLUME:-finally-data}"
PORT="${FINALLY_PORT:-8000}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BUILD=0
OPEN=1
for arg in "$@"; do
  case "$arg" in
    --build|-b) BUILD=1 ;;
    --no-open) OPEN=0 ;;
    -h|--help)
      sed -n '2,5p' "$ROOT_DIR/scripts/start_mac.sh" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Error: the Docker daemon is not running. Start Docker Desktop and try again." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    echo "No .env found; creating one from .env.example (add your OPENROUTER_API_KEY to enable AI chat)."
    cp .env.example .env
  else
    touch .env
  fi
fi

if [[ "$BUILD" -eq 1 ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building image $IMAGE ..."
  docker build -t "$IMAGE" .
fi

# Remove any existing container (running or stopped) so the run below is idempotent.
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "Replacing existing container $CONTAINER ..."
  docker rm -f "$CONTAINER" >/dev/null
fi

docker volume create "$VOLUME" >/dev/null

# Docker's --env-file is stricter than python-dotenv: no spaces around '=', no
# quotes, no 'export'. Normalise .env into a temp file so either style works.
ENV_FILE="$(mktemp -t finally-env.XXXXXX)"
trap 'rm -f "$ENV_FILE"' EXIT
sed -E \
  -e 's/\r$//' \
  -e '/^[[:space:]]*(#|$)/d' \
  -e 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*/\2=/' \
  -e 's/[[:space:]]+$//' \
  -e "s/^([A-Za-z_][A-Za-z0-9_]*)=\"(.*)\"$/\1=\2/" \
  -e "s/^([A-Za-z_][A-Za-z0-9_]*)='(.*)'$/\1=\2/" \
  .env | grep -E '^[A-Za-z_][A-Za-z0-9_]*=' > "$ENV_FILE" || true

docker run -d \
  --name "$CONTAINER" \
  -p "${PORT}:8000" \
  -v "${VOLUME}:/app/db" \
  --env-file "$ENV_FILE" \
  --restart unless-stopped \
  "$IMAGE" >/dev/null

URL="http://localhost:${PORT}"
echo -n "Waiting for FinAlly to become healthy"
for _ in $(seq 1 60); do
  if curl -fsS "${URL}/api/health" >/dev/null 2>&1; then
    echo " ok"
    break
  fi
  echo -n "."
  sleep 1
done
if ! curl -fsS "${URL}/api/health" >/dev/null 2>&1; then
  echo
  echo "Warning: health check did not pass yet. Check logs with: docker logs -f $CONTAINER" >&2
fi

echo "FinAlly is running at ${URL}"
echo "Logs: docker logs -f ${CONTAINER}   Stop: ./scripts/stop_mac.sh"

if [[ "$OPEN" -eq 1 ]]; then
  if command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  fi
fi
