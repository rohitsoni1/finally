#!/usr/bin/env bash
# Stop and remove the FinAlly container (macOS/Linux). Data volume is kept.
set -euo pipefail

CONTAINER="${FINALLY_CONTAINER:-finally}"
VOLUME="${FINALLY_VOLUME:-finally-data}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running; nothing to stop."
  exit 0
fi

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  docker rm -f "$CONTAINER" >/dev/null
  echo "Stopped and removed container $CONTAINER."
else
  echo "Container $CONTAINER is not running."
fi
echo "Data volume $VOLUME was kept (remove it with: docker volume rm $VOLUME)."
