#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT_DIR/runtime/nginx-logs" "$ROOT_DIR/runtime/db" "$ROOT_DIR/runtime/snapshots" "$ROOT_DIR/runtime/pids"

DOCKER_BIN="${SANDBOX_LAB_DOCKER_BIN:-docker}"
if [ -z "${SANDBOX_LAB_DOCKER_BIN:-}" ] && [ ! -S /var/run/docker.sock ] && command -v docker.exe >/dev/null 2>&1; then
  DOCKER_BIN="docker.exe"
fi

# Resolve Python interpreter cleanly (system python3 or .venv-linux bootstrap)
PYTHON_BIN=""
if [ -x "$ROOT_DIR/.venv-linux/bin/python" ] && "$ROOT_DIR/.venv-linux/bin/python" -c "import fastapi, uvicorn, pydantic, httpx, mcp, dotenv" 2>/dev/null; then
  PYTHON_BIN="$ROOT_DIR/.venv-linux/bin/python"
elif python3 -c "import fastapi, uvicorn, pydantic, httpx, mcp, dotenv" 2>/dev/null; then
  PYTHON_BIN="$(command -v python3)"
else
  VENV_DIR="$ROOT_DIR/.venv-linux"
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "Bootstrapping Linux Python venv at $VENV_DIR ..."
    python3 -m venv "$VENV_DIR"
  fi
  if ! "$VENV_DIR/bin/python" -c "import fastapi, uvicorn, pydantic, httpx, mcp, dotenv" 2>/dev/null; then
    echo "Installing backend/requirements.txt into $VENV_DIR ..."
    "$VENV_DIR/bin/python" -m pip install --upgrade pip
    "$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/backend/requirements.txt"
  fi
  PYTHON_BIN="$VENV_DIR/bin/python"
fi

HELPER="$ROOT_DIR/scripts/_proc_helper.py"

# Verify Rollup native binary for Linux when running Vite inside Linux/WSL2
if [ "$(uname -s)" = "Linux" ]; then
  if [ ! -d "$ROOT_DIR/web/node_modules" ]; then
    echo "ERROR: web/node_modules missing. Run '(cd \"$ROOT_DIR/web\" && npm ci)' before starting." >&2
    exit 1
  fi
  if [ ! -d "$ROOT_DIR/web/node_modules/@rollup/rollup-linux-x64-gnu" ] && [ ! -d "$ROOT_DIR/web/node_modules/@rollup/rollup-linux-arm64-gnu" ]; then
    echo "NOTE: web/node_modules lacks Linux @rollup native package (likely installed on Windows)." >&2
    echo "Run '(cd \"$ROOT_DIR/web\" && npm ci)' inside Linux/WSL2 before running Linux Vite/build." >&2
    exit 1
  fi
fi

# Verify worker image exists
if ! "$DOCKER_BIN" image inspect "${SANDBOX_LAB_WORKER_IMAGE:-sandbox-lab-worker:v1}" >/dev/null 2>&1; then
  echo "Building worker image ${SANDBOX_LAB_WORKER_IMAGE:-sandbox-lab-worker:v1} ..."
  WORKER_DIR="$ROOT_DIR/worker"
  if [ "$DOCKER_BIN" = "docker.exe" ] && command -v wslpath >/dev/null 2>&1; then
    WORKER_DIR="$(wslpath -w "$WORKER_DIR")"
  fi
  "$DOCKER_BIN" build -t "${SANDBOX_LAB_WORKER_IMAGE:-sandbox-lab-worker:v1}" "$WORKER_DIR"
fi

COMPOSE_FILE="$ROOT_DIR/infra/compose.yaml"
if [ "$DOCKER_BIN" = "docker.exe" ] && command -v wslpath >/dev/null 2>&1; then
  COMPOSE_FILE="$(wslpath -w "$COMPOSE_FILE")"
fi

API_HOST="${SANDBOX_LAB_API_HOST:-127.0.0.1}"
API_PORT="${SANDBOX_LAB_API_PORT:-18000}"
WEB_PORT="${SANDBOX_LAB_WEB_PORT:-5173}"
NGINX_HOST="${SANDBOX_LAB_NGINX_HOST:-127.0.0.1}"
NGINX_PORT="${SANDBOX_LAB_NGINX_PORT:-18080}"
export VITE_API_ORIGIN="http://${API_HOST}:${API_PORT}"

API_PID_FILE="$ROOT_DIR/runtime/pids/api.pid"
API_META_FILE="$ROOT_DIR/runtime/pids/api.meta"
WEB_PID_FILE="$ROOT_DIR/runtime/pids/web.pid"
WEB_META_FILE="$ROOT_DIR/runtime/pids/web.meta"

check_existing_pid() {
  local name="$1"
  local pidfile="$2"
  local metafile="$3"
  if [ -f "$pidfile" ] || [ -f "$metafile" ]; then
    "$PYTHON_BIN" "$HELPER" check-existing "$name" "$pidfile" "$metafile" || exit 1
  fi
}

check_existing_pid "API" "$API_PID_FILE" "$API_META_FILE"
check_existing_pid "Web" "$WEB_PID_FILE" "$WEB_META_FILE"

# Fail fast if target ports already have active listeners before spawning anything
"$PYTHON_BIN" "$HELPER" port-free "$API_HOST" "$API_PORT"
"$PYTHON_BIN" "$HELPER" port-free "127.0.0.1" "$WEB_PORT"

STARTED_API=0
STARTED_WEB=0
START_COMPLETED=0

rollback_on_failure() {
  if [ "$START_COMPLETED" -eq 1 ]; then
    return 0
  fi
  echo "Startup failed; rolling back newly spawned local services..." >&2
  if [ "$STARTED_WEB" -eq 1 ]; then
    "$PYTHON_BIN" "$HELPER" kill-pgid "$WEB_PID_FILE" "$WEB_META_FILE" >/dev/null 2>&1 || true
  fi
  if [ "$STARTED_API" -eq 1 ]; then
    "$PYTHON_BIN" "$HELPER" kill-pgid "$API_PID_FILE" "$API_META_FILE" >/dev/null 2>&1 || true
  fi
}
trap rollback_on_failure EXIT

echo "Starting Nginx lab and test-service via $DOCKER_BIN compose..."
"$DOCKER_BIN" compose -f "$COMPOSE_FILE" up -d

API_INSTANCE_TOKEN="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(12))')"

echo "Starting Sandbox Lab API on $VITE_API_ORIGIN ..."
(
  cd "$ROOT_DIR"
  SANDBOX_LAB_INSTANCE_TOKEN="$API_INSTANCE_TOKEN" \
    setsid "$PYTHON_BIN" -m uvicorn backend.app.api:app --host "$API_HOST" --port "$API_PORT" \
      >"$ROOT_DIR/runtime/pids/api.log" 2>&1 < /dev/null &
  echo $! >"$API_PID_FILE"
)
STARTED_API=1
API_PID="$(cat "$API_PID_FILE")"
"$PYTHON_BIN" "$HELPER" write-meta "$API_PID" "$API_META_FILE" \
  --name "API" --port "$API_PORT" --host "$API_HOST" --root "$ROOT_DIR" \
  --tokens "uvicorn,backend.app.api" --token "$API_INSTANCE_TOKEN"

echo "Starting Sandbox Lab Web on http://127.0.0.1:${WEB_PORT} (proxying /api -> $VITE_API_ORIGIN) ..."
(
  cd "$ROOT_DIR/web"
  setsid npm run dev -- --host 127.0.0.1 --port "$WEB_PORT" --strictPort \
    >"$ROOT_DIR/runtime/pids/web.log" 2>&1 < /dev/null &
  echo $! >"$WEB_PID_FILE"
)
STARTED_WEB=1
WEB_PID="$(cat "$WEB_PID_FILE")"
"$PYTHON_BIN" "$HELPER" write-meta "$WEB_PID" "$WEB_META_FILE" \
  --name "Web" --port "$WEB_PORT" --host "127.0.0.1" --root "$ROOT_DIR" \
  --tokens "npm,vite,node"

"$PYTHON_BIN" "$HELPER" verify-ready "$API_META_FILE" \
  --url "${VITE_API_ORIGIN}/api/health" --require-token --require-socket --settle-ms 250 --timeout 15

"$PYTHON_BIN" "$HELPER" verify-ready "$WEB_META_FILE" \
  --url "http://127.0.0.1:${WEB_PORT}" --require-socket --settle-ms 350 --timeout 15

wait_nginx_ready() {
  local url="http://${NGINX_HOST}:${NGINX_PORT}/health"
  local i=0
  while [ "$i" -lt 40 ]; do
    if "$PYTHON_BIN" -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('$url', timeout=1.5).status == 200 else 1)" 2>/dev/null; then
      return 0
    fi
    i=$((i + 1))
    sleep 0.25
  done
  echo "ERROR: Nginx did not become ready at $url" >&2
  return 1
}

wait_nginx_ready
START_COMPLETED=1

echo "Sandbox Lab ready: Web=http://127.0.0.1:${WEB_PORT} API=${VITE_API_ORIGIN}"
