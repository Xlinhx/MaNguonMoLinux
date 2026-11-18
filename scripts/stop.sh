#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HELPER="$ROOT_DIR/scripts/_proc_helper.py"

DOCKER_BIN="${SANDBOX_LAB_DOCKER_BIN:-docker}"
if [ -z "${SANDBOX_LAB_DOCKER_BIN:-}" ] && [ ! -S /var/run/docker.sock ] && command -v docker.exe >/dev/null 2>&1; then
  DOCKER_BIN="docker.exe"
fi

PYTHON_BIN="python3"
if [ -x "$ROOT_DIR/.venv-linux/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv-linux/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  echo "ERROR: python3 is required by stop.sh for process identity verification." >&2
  exit 1
fi

echo "Stopping Sandbox Lab local servers and Compose services..."
STATUS=0

if ! "$PYTHON_BIN" "$HELPER" stop-service "API" "$ROOT_DIR/runtime/pids/api.pid" "$ROOT_DIR/runtime/pids/api.meta"; then
  STATUS=1
fi

if ! "$PYTHON_BIN" "$HELPER" stop-service "Web" "$ROOT_DIR/runtime/pids/web.pid" "$ROOT_DIR/runtime/pids/web.meta"; then
  STATUS=1
fi

COMPOSE_FILE="$ROOT_DIR/infra/compose.yaml"
if [ "$DOCKER_BIN" = "docker.exe" ] && command -v wslpath >/dev/null 2>&1; then
  COMPOSE_FILE="$(wslpath -w "$COMPOSE_FILE")"
fi

if ! "$DOCKER_BIN" compose -f "$COMPOSE_FILE" down --remove-orphans; then
  STATUS=1
fi

if [ "$STATUS" -ne 0 ]; then
  echo "Sandbox Lab stop encountered errors (see above); check preserved .pid/.meta files." >&2
  exit "$STATUS"
fi

echo "Sandbox Lab stopped cleanly."
