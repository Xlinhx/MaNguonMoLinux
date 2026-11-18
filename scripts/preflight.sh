#!/usr/bin/env bash
set -euo pipefail

echo "=== Sandbox Lab Preflight ==="
uname -a
if [ -f /etc/os-release ]; then
  grep -E '^(NAME|VERSION)=' /etc/os-release || true
fi

if [ -S /var/run/docker.sock ] && command -v docker >/dev/null 2>&1; then
  DOCKER_BIN="docker"
  echo "docker: $(command -v docker)"
elif command -v docker.exe >/dev/null 2>&1; then
  DOCKER_BIN="docker.exe"
  echo "docker.exe (Windows Docker Desktop fallback): $(command -v docker.exe)"
  echo "NOTE: Native Linux /var/run/docker.sock is not mounted in this WSL distro; enable Docker Desktop -> Settings -> Resources -> WSL Integration if needed."
elif command -v docker >/dev/null 2>&1; then
  DOCKER_BIN="docker"
  echo "docker: $(command -v docker)"
else
  echo "ERROR: Neither docker nor docker.exe found." >&2
  exit 1
fi

"$DOCKER_BIN" context show
"$DOCKER_BIN" info --format 'Engine={{.ServerVersion}} OS={{.OSType}} Arch={{.Architecture}}'
"$DOCKER_BIN" compose version

command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found." >&2; exit 1; }
command -v node >/dev/null 2>&1 || { echo "ERROR: node not found." >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "ERROR: npm not found." >&2; exit 1; }

python3 --version
node -v
npm -v

if ! python3 -m venv --help >/dev/null 2>&1 && ! python3 -m pip --version >/dev/null 2>&1; then
  echo "ERROR: Neither python3-venv nor pip is available." >&2
  exit 1
fi

echo "Preflight complete."
