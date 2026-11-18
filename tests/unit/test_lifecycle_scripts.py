from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run_linux_bash(script: str, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    if sys.platform != "win32":
        return subprocess.run(
            ["bash", "-lc", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    wsl = shutil.which("wsl")
    if not wsl:
        pytest.skip("WSL not available on Windows host")
    probe = subprocess.run([wsl, "-d", "Ubuntu-24.04", "-e", "true"], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("Ubuntu-24.04 WSL distro not available")
    return subprocess.run(
        [wsl, "-d", "Ubuntu-24.04", "-e", "bash", "-lc", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _wsl_root_expr() -> str:
    if sys.platform != "win32":
        return ROOT.as_posix()
    drive = ROOT.drive.rstrip(":").lower()
    rest = ROOT.as_posix().split(":", 1)[1]
    return f"/mnt/{drive}{rest}"


def test_start_script_uses_strict_port_and_lf_line_endings() -> None:
    for name in ("preflight.sh", "start.sh", "stop.sh", "verify.sh"):
        raw = (ROOT / "scripts" / name).read_bytes()
        assert b"\r\n" not in raw, f"{name} must use LF line endings"
    start_text = (ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")
    assert "--strictPort" in start_text
    assert "SANDBOX_LAB_INSTANCE_TOKEN" in start_text


def test_stop_script_refuses_pid_reuse_cleans_stale_and_kills_pgid_sigterm_ignorer() -> None:
    src_root = _wsl_root_expr()
    bash_code = f"""
set -euo pipefail
TMP="$(mktemp -d /tmp/sandbox-stop-test-XXXXXX)"
cleanup() {{
  rm -rf "$TMP"
}}
trap cleanup EXIT

mkdir -p "$TMP/scripts" "$TMP/infra" "$TMP/runtime/pids"
cp "{src_root}/scripts/stop.sh" "$TMP/scripts/stop.sh"
cp "{src_root}/scripts/_proc_helper.py" "$TMP/scripts/_proc_helper.py"
touch "$TMP/infra/compose.yaml"
cat <<'EOF' > "$TMP/docker_shim"
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMP/docker_shim" "$TMP/scripts/stop.sh"

# 1. Stale dead PID + meta -> stop.sh exits 0 and removes both files
echo "999999" > "$TMP/runtime/pids/api.pid"
echo '{{"pid":999999,"pgid":999999,"start_ticks":1,"cmdline_tokens":["uvicorn"]}}' > "$TMP/runtime/pids/api.meta"
SANDBOX_LAB_DOCKER_BIN="$TMP/docker_shim" bash "$TMP/scripts/stop.sh"
test ! -f "$TMP/runtime/pids/api.pid"
test ! -f "$TMP/runtime/pids/api.meta"

# 2. Unrelated live PID without matching .meta -> refuses without killing sleep
setsid /bin/sleep 60 >/dev/null 2>&1 < /dev/null &
UNREL_PID=$!
echo "$UNREL_PID" > "$TMP/runtime/pids/web.pid"
set +e
OUT=$(SANDBOX_LAB_DOCKER_BIN="$TMP/docker_shim" bash "$TMP/scripts/stop.sh" 2>&1)
RC=$?
set -e
if [ "$RC" -eq 0 ]; then
  kill -KILL "$UNREL_PID" 2>/dev/null || true
  echo "Expected non-zero exit on missing .meta live PID" >&2
  exit 1
fi
kill -0 "$UNREL_PID"
kill -KILL "$UNREL_PID" 2>/dev/null || true
rm -f "$TMP/runtime/pids/web.pid"

# 3. Unrelated live PID WITH stale/mismatched .meta -> refuses without killing sleep
setsid /bin/sleep 60 >/dev/null 2>&1 < /dev/null &
REUSED_PID=$!
echo "$REUSED_PID" > "$TMP/runtime/pids/web.pid"
python3 "$TMP/scripts/_proc_helper.py" write-meta "$REUSED_PID" "$TMP/runtime/pids/web.meta" \
  --name Web --port 5199 --host 127.0.0.1 --root "$TMP" --tokens "npm,vite"
# Tamper cmdline/start_ticks to simulate PID reuse
python3 -c 'import json,pathlib; p=pathlib.Path("'$TMP'/runtime/pids/web.meta"); d=json.loads(p.read_text()); d["start_ticks"]=1; p.write_text(json.dumps(d))'
set +e
OUT2=$(SANDBOX_LAB_DOCKER_BIN="$TMP/docker_shim" bash "$TMP/scripts/stop.sh" 2>&1)
RC2=$?
set -e
if [ "$RC2" -eq 0 ]; then
  kill -KILL "$REUSED_PID" 2>/dev/null || true
  echo "Expected non-zero exit on mismatched live PID reuse" >&2
  exit 1
fi
kill -0 "$REUSED_PID"
kill -KILL "$REUSED_PID" 2>/dev/null || true
rm -f "$TMP/runtime/pids/web.pid" "$TMP/runtime/pids/web.meta"

# 4. Parent dies on SIGTERM while PGID child in cwd='/' with non-matching command ignores SIGTERM -> escalates SIGKILL
setsid python3 -c '
import os, signal, subprocess, sys, time
child = subprocess.Popen(["/bin/sh", "-c", "trap \\"\\" TERM; exec /bin/sleep 60"], cwd="/")
open("'$TMP'/child.pid", "w").write(str(child.pid))
def on_term(signum, frame):
    os._exit(0)
signal.signal(signal.SIGTERM, on_term)
while True:
    time.sleep(0.2)
' --uvicorn-fake >/dev/null 2>&1 < /dev/null &
PAR_PID=$!
sleep 0.3
CHILD_PID=$(cat "$TMP/child.pid")
echo "$PAR_PID" > "$TMP/runtime/pids/api.pid"
python3 "$TMP/scripts/_proc_helper.py" write-meta "$PAR_PID" "$TMP/runtime/pids/api.meta" \
  --name API --port 18199 --host 127.0.0.1 --root "$TMP" --tokens "python3,--uvicorn-fake"
SANDBOX_LAB_DOCKER_BIN="$TMP/docker_shim" bash "$TMP/scripts/stop.sh"
if kill -0 "$CHILD_PID" 2>/dev/null; then
  kill -KILL "$CHILD_PID" 2>/dev/null || true
  echo "Child in cwd='/' ignoring SIGTERM survived stop.sh!" >&2
  exit 1
fi
test ! -f "$TMP/runtime/pids/api.pid"
test ! -f "$TMP/runtime/pids/api.meta"

# 5. Parent ALREADY exited before stop.sh runs, child in same PGID with cwd='/' and different cmd survives -> still cleaned
setsid python3 -c '
import os, subprocess
child = subprocess.Popen(["/bin/sh", "-c", "trap \\"\\" TERM; exec /bin/sleep 60"], cwd="/")
open("'$TMP'/child2.pid", "w").write(str(child.pid))
open("'$TMP'/par2_pgid.txt", "w").write(str(os.getpgrp()))
' --uvicorn-fake >/dev/null 2>&1 < /dev/null &
PAR2_PID=$!
wait "$PAR2_PID" || true
sleep 0.2
CHILD2_PID=$(cat "$TMP/child2.pid")
PAR2_PGID=$(cat "$TMP/par2_pgid.txt")
C2_START=$(python3 -c 'from pathlib import Path; raw=Path("/proc/'$CHILD2_PID'/stat").read_text(); print(raw[raw.rfind(")")+2:].split()[19])')
echo "$PAR2_PID" > "$TMP/runtime/pids/api.pid"
python3 -c 'import json,pathlib; pathlib.Path("'$TMP'/runtime/pids/api.meta").write_text(json.dumps({{"pid":'$PAR2_PID',"pgid":'$PAR2_PGID',"start_ticks":int("'$C2_START'")-1,"cmdline_tokens":["--uvicorn-fake"],"root_dir":"'$TMP'"}}))'
SANDBOX_LAB_DOCKER_BIN="$TMP/docker_shim" bash "$TMP/scripts/stop.sh"
if kill -0 "$CHILD2_PID" 2>/dev/null; then
  kill -KILL "$CHILD2_PID" 2>/dev/null || true
  echo "Orphan PGID child in cwd='/' survived stop.sh!" >&2
  exit 1
fi
test ! -f "$TMP/runtime/pids/api.pid"
test ! -f "$TMP/runtime/pids/api.meta"
echo "STOP_PGID_OK"
"""
    res = _run_linux_bash(bash_code, timeout=45)
    assert res.returncode == 0, f"stdout={res.stdout}\nstderr={res.stderr}"
    assert "STOP_PGID_OK" in res.stdout


def test_start_port_collision_and_verify_missing_frontend_fail_loudly() -> None:
    src_root = _wsl_root_expr()
    bash_code = f"""
set -euo pipefail
TMP="$(mktemp -d /tmp/sandbox-r6r7-test-XXXXXX)"
cleanup() {{
  if [ -f "$TMP/dummy.pid" ]; then kill -KILL "$(cat "$TMP/dummy.pid")" 2>/dev/null || true; fi
  if [ -f "$TMP/api.pid" ]; then kill -KILL "$(cat "$TMP/api.pid")" 2>/dev/null || true; fi
  rm -rf "$TMP"
}}
trap cleanup EXIT

mkdir -p "$TMP/scripts" "$TMP/tests/unit" "$TMP/tests/integration" "$TMP/web"
cp "{src_root}/scripts/_proc_helper.py" "$TMP/scripts/_proc_helper.py"
cp "{src_root}/scripts/verify.sh" "$TMP/scripts/verify.sh"
chmod +x "$TMP/scripts/verify.sh"

# 1. R6: Unrelated listener holds port -> port-free fails AND verify-ready --require-socket rejects wrong PGID
setsid python3 -c '
import http.server, socketserver, threading
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{{\\"status\\":\\"ok\\"}}")
    def log_message(self, *a): pass
s = socketserver.TCPServer(("127.0.0.1", 0), H)
open("'$TMP'/port.txt", "w").write(str(s.server_address[1]))
s.serve_forever()
' >/dev/null 2>&1 < /dev/null &
DUMMY_PID=$!
echo "$DUMMY_PID" > "$TMP/dummy.pid"
for _ in $(seq 1 30); do [ -f "$TMP/port.txt" ] && break; sleep 0.05; done
PORT=$(cat "$TMP/port.txt")

set +e
python3 "$TMP/scripts/_proc_helper.py" port-free 127.0.0.1 "$PORT" 2>/dev/null
RC_FREE=$?
set -e
test "$RC_FREE" -ne 0

# Spawn unrelated sleep on same fake meta pointing to PORT; verify-ready --require-socket MUST fail
setsid /bin/sleep 30 >/dev/null 2>&1 < /dev/null &
OTHER_PID=$!
echo "$OTHER_PID" > "$TMP/api.pid"
python3 "$TMP/scripts/_proc_helper.py" write-meta "$OTHER_PID" "$TMP/api.meta" \
  --name Web --port "$PORT" --host 127.0.0.1 --root "$TMP" --tokens "sleep"
set +e
python3 "$TMP/scripts/_proc_helper.py" verify-ready "$TMP/api.meta" \
  --url "http://127.0.0.1:$PORT" --require-socket --settle-ms 100 --timeout 1.0 2>/dev/null
RC_READY=$?
set -e
test "$RC_READY" -ne 0

# 2. R7: verify.sh fails loudly when backend Python dependencies are missing
cat <<'EOF' > "$TMP/tests/unit/test_ok.py"
def test_ok():
    assert True
EOF
set +e
VOUT_PY=$(cd "$TMP" && SANDBOX_LAB_RUN_E2E=0 SANDBOX_LAB_RUN_FRONTEND=1 bash "$TMP/scripts/verify.sh" 2>&1)
VRC_PY=$?
set -e
test "$VRC_PY" -ne 0
echo "$VOUT_PY" | grep -q "backend/requirements.txt"

# Provide an isolated mock .venv-linux/bin/python in TMP so verify.sh reaches Playwright/Frontend gates
mkdir -p "$TMP/.venv-linux/bin"
cat <<'EOF' > "$TMP/.venv-linux/bin/python"
#!/usr/bin/env bash
if [ "${1:-}" = "-c" ]; then
  if [ "${{FAIL_PLAYWRIGHT:-1}}" = "1" ] && echo "${2:-}" | grep -q "playwright"; then
    exit 1
  fi
  exit 0
fi
if [ "${{1:-}}" = "-m" ] && [ "${{2:-}}" = "pytest" ]; then
  JUNIT_OUT=""
  for arg in "$@"; do
    case "$arg" in
      --junitxml=*) JUNIT_OUT="${{arg#--junitxml=}}" ;;
    esac
  done
  if [ -n "$JUNIT_OUT" ]; then
    mkdir -p "$(dirname "$JUNIT_OUT")"
    if [ "${{MOCK_SKIP_ONE:-0}}" = "1" ] && [[ "$JUNIT_OUT" == *junit_integration.xml ]]; then
      cat <<'XML' > "$JUNIT_OUT"
<testsuite tests="1" skipped="1"><testcase classname="test_agent_live" name="test_live_agent"><skipped message="DEEPSEEK_API_KEY not set"/></testcase></testsuite>
XML
    else
      cat <<'XML' > "$JUNIT_OUT"
<testsuite tests="1"><testcase classname="test_ok" name="test_ok"/></testsuite>
XML
    fi
  fi
  exit 0
fi
exec python3 "$@"
EOF
chmod +x "$TMP/.venv-linux/bin/python"

# Missing Playwright fails loudly when SANDBOX_LAB_RUN_E2E=1
set +e
VOUT_PW=$(cd "$TMP" && SANDBOX_LAB_RUN_E2E=1 SANDBOX_LAB_RUN_FRONTEND=0 bash "$TMP/scripts/verify.sh" 2>&1)
VRC_PW=$?
set -e
test "$VRC_PW" -ne 0
echo "$VOUT_PW" | grep -q "Playwright"

# Missing web/node_modules fails loudly (never silent skip)
set +e
VOUT=$(cd "$TMP" && SANDBOX_LAB_RUN_E2E=0 SANDBOX_LAB_RUN_FRONTEND=1 bash "$TMP/scripts/verify.sh" 2>&1)
VRC=$?
set -e
test "$VRC" -ne 0
echo "$VOUT" | grep -q "web/node_modules"

# Empty web/node_modules without @rollup/rollup-linux-x64-gnu ALSO fails loudly on Linux
mkdir -p "$TMP/web/node_modules"
set +e
VOUT2=$(cd "$TMP" && SANDBOX_LAB_RUN_E2E=0 SANDBOX_LAB_RUN_FRONTEND=1 bash "$TMP/scripts/verify.sh" 2>&1)
VRC2=$?
set -e
test "$VRC2" -ne 0
echo "$VOUT2" | grep -q "rollup"

# Explicit opt-out reports PARTIAL COVERAGE
VOUT3=$(cd "$TMP" && SANDBOX_LAB_RUN_E2E=0 SANDBOX_LAB_RUN_FRONTEND=0 bash "$TMP/scripts/verify.sh" 2>&1)
echo "$VOUT3" | grep -q "PARTIAL COVERAGE"

# Skipped pytest case MUST NEVER report FULL COVERAGE, and release strict mode MUST fail non-zero
mkdir -p "$TMP/web/node_modules/@rollup/rollup-linux-x64-gnu" "$TMP/web"
cat <<'EOF' > "$TMP/web/package.json"
{{"name":"mock","scripts":{{"typecheck":"true","build":"true"}}}}
EOF
VOUT_SKIP=$(cd "$TMP" && FAIL_PLAYWRIGHT=0 MOCK_SKIP_ONE=1 SANDBOX_LAB_RUN_E2E=1 SANDBOX_LAB_RUN_FRONTEND=1 PATH="$TMP/fakebin:$PATH" bash -c 'mkdir -p "$0/fakebin" && printf "#!/usr/bin/env bash\nexit 0\n" > "$0/fakebin/npm" && chmod +x "$0/fakebin/npm" && PATH="$0/fakebin:$PATH" bash "$0/scripts/verify.sh"' "$TMP" 2>&1)
echo "$VOUT_SKIP" | grep -q "PARTIAL COVERAGE"
if echo "$VOUT_SKIP" | grep -q "FULL COVERAGE"; then
  echo "ERROR: verify.sh printed FULL COVERAGE despite skipped test!" >&2
  exit 1
fi
set +e
VOUT_REL=$(cd "$TMP" && FAIL_PLAYWRIGHT=0 SANDBOX_LAB_RELEASE_STRICT=1 MOCK_SKIP_ONE=1 SANDBOX_LAB_RUN_E2E=1 SANDBOX_LAB_RUN_FRONTEND=1 PATH="$TMP/fakebin:$PATH" bash "$TMP/scripts/verify.sh" 2>&1)
RC_REL=$?
set -e
test "$RC_REL" -ne 0
echo "$VOUT_REL" | grep -q "SANDBOX_LAB_RELEASE_STRICT=1"

# 3. R6: Real start.sh failure rolls back newly spawned API process cleanly
cp "{src_root}/scripts/start.sh" "$TMP/scripts/start.sh"
chmod +x "$TMP/scripts/start.sh"
mkdir -p "$TMP/web/node_modules/@rollup/rollup-linux-x64-gnu" "$TMP/infra" "$TMP/worker" "$TMP/fakebin"
touch "$TMP/infra/compose.yaml"
cat <<'EOF' > "$TMP/docker_shim"
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMP/docker_shim"
cat <<'EOF' > "$TMP/.venv-linux/bin/python"
#!/usr/bin/env bash
if [ "${{1:-}}" = "-c" ] && echo "${{2:-}}" | grep -q "import secrets"; then exec python3 "$@"; fi
if [ "${{1:-}}" = "-c" ]; then exit 0; fi
if [ "${{1:-}}" = "-m" ] && [ "${{2:-}}" = "uvicorn" ]; then
  exec python3 -c 'import sys,time; time.sleep(30)' "$@"
fi
exec python3 "$@"
EOF
chmod +x "$TMP/.venv-linux/bin/python"
cat <<'EOF' > "$TMP/fakebin/npm"
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$TMP/fakebin/npm"
R6_API_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
R6_WEB_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
set +e
R6OUT=$(PATH="$TMP/fakebin:$PATH" SANDBOX_LAB_DOCKER_BIN="$TMP/docker_shim" SANDBOX_LAB_API_PORT="$R6_API_PORT" SANDBOX_LAB_WEB_PORT="$R6_WEB_PORT" bash "$TMP/scripts/start.sh" 2>&1)
R6RC=$?
set -e
test "$R6RC" -ne 0
test ! -f "$TMP/runtime/pids/api.pid"
test ! -f "$TMP/runtime/pids/web.pid"
echo "R6_R7_OK"
"""
    res = _run_linux_bash(bash_code, timeout=45)
    assert res.returncode == 0, f"stdout={res.stdout}\nstderr={res.stderr}"
    assert "R6_R7_OK" in res.stdout


def test_start_refuses_when_parent_dead_but_pgid_child_alive() -> None:
    src_root = _wsl_root_expr()
    bash_code = f"""
set -euo pipefail
TMP="$(mktemp -d /tmp/sandbox-start-pgid-test-XXXXXX)"
cleanup() {{
  rm -rf "$TMP"
}}
trap cleanup EXIT

mkdir -p "$TMP/scripts" "$TMP/runtime/pids" "$TMP/infra" "$TMP/worker" "$TMP/web/node_modules/@rollup/rollup-linux-x64-gnu"
cp "{src_root}/scripts/start.sh" "$TMP/scripts/start.sh"
cp "{src_root}/scripts/_proc_helper.py" "$TMP/scripts/_proc_helper.py"
touch "$TMP/infra/compose.yaml"
cat <<'EOF' > "$TMP/docker_shim"
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMP/docker_shim" "$TMP/scripts/start.sh"

# Spawn a session leader that launches a sleeping child in the same PGID and then exits immediately
setsid bash -c 'sleep 30 >/dev/null 2>&1 & CHILD=$!; echo "$CHILD" > "'"$TMP"'/child.pid"; exit 0' >/dev/null 2>&1 < /dev/null &
sleep 0.3
CHILD_PID="$(cat "$TMP/child.pid")"
PGID="$(ps -o pgid= -p "$CHILD_PID" | tr -d ' ')"
SESS="$(ps -o sess= -p "$CHILD_PID" | tr -d ' ')"
TICKS="$(awk '{{print $22}}' /proc/"$CHILD_PID"/stat)"
FAKE_PARENT=999998

echo "$FAKE_PARENT" > "$TMP/runtime/pids/api.pid"
cat <<EOF > "$TMP/runtime/pids/api.meta"
{{"name":"API","pid":$FAKE_PARENT,"pgid":$PGID,"session":$SESS,"start_ticks":1,"cmdline_tokens":["python3"],"port":18099,"host":"127.0.0.1","root_dir":"$TMP","env_kind":"linux"}}
EOF

# 1. check-existing & start.sh MUST refuse while PGID child lives and MUST NOT delete pid/meta files
set +e
OUT=$(SANDBOX_LAB_DOCKER_BIN="$TMP/docker_shim" bash "$TMP/scripts/start.sh" 2>&1)
RC=$?
set -e
test "$RC" -ne 0
echo "$OUT" | grep -qi "PGID"
test -f "$TMP/runtime/pids/api.pid"
test -f "$TMP/runtime/pids/api.meta"

# 2. Once child exits, check-existing cleans up stale pid/meta cleanly and exits 0
kill -KILL "$CHILD_PID" 2>/dev/null || true
wait "$CHILD_PID" 2>/dev/null || true
sleep 0.1
python3 "$TMP/scripts/_proc_helper.py" check-existing API "$TMP/runtime/pids/api.pid" "$TMP/runtime/pids/api.meta"
test ! -f "$TMP/runtime/pids/api.pid"
test ! -f "$TMP/runtime/pids/api.meta"
echo "PGID_START_GUARD_OK"
"""
    res = _run_linux_bash(bash_code, timeout=45)
    assert res.returncode == 0, f"stdout={res.stdout}\nstderr={res.stderr}"
    assert "PGID_START_GUARD_OK" in res.stdout
