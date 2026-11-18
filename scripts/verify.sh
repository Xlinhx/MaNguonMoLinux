#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN=""
if [ -x "$ROOT_DIR/.venv-linux/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv-linux/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  echo "ERROR: Python 3 interpreter not found. Install Python 3.12+ and bootstrap .venv-linux." >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c "import fastapi, uvicorn, pydantic, httpx, mcp, pytest" 2>/dev/null; then
  echo "ERROR: Missing backend Python dependencies in $PYTHON_BIN." >&2
  echo "Bootstrap with: python3 -m venv .venv-linux && .venv-linux/bin/pip install -r backend/requirements.txt" >&2
  exit 1
fi

RELEASE_STRICT="${SANDBOX_LAB_RELEASE_STRICT:-0}"
RUN_E2E="${SANDBOX_LAB_RUN_E2E:-1}"
RUN_FRONTEND="${SANDBOX_LAB_RUN_FRONTEND:-1}"
if [ "$RELEASE_STRICT" = "1" ]; then
  if [ "$RUN_E2E" != "1" ] || [ "$RUN_FRONTEND" != "1" ]; then
    echo "ERROR: SANDBOX_LAB_RELEASE_STRICT=1 requires SANDBOX_LAB_RUN_E2E=1 and SANDBOX_LAB_RUN_FRONTEND=1." >&2
    exit 1
  fi
fi

# Always write pytest JUnit XMLs to staging first so non-strict/partial runs never clobber deliverables/junit/*.xml
if [ -n "${SANDBOX_LAB_JUNIT_DIR:-}" ]; then
  JUNIT_DIR="$SANDBOX_LAB_JUNIT_DIR"
elif [ "$RELEASE_STRICT" = "1" ]; then
  JUNIT_DIR="$ROOT_DIR/runtime/exports/release_staging_$$"
else
  JUNIT_DIR="$ROOT_DIR/runtime/exports"
fi
mkdir -p "$JUNIT_DIR"

WSL_TMP_CREATED=""
PYTEST_BASETEMP_ARGS=()
STAGING_TMP_CREATED=""
if [ "$RELEASE_STRICT" = "1" ] && [ -z "${SANDBOX_LAB_JUNIT_DIR:-}" ]; then
  STAGING_TMP_CREATED="$JUNIT_DIR"
fi
cleanup_verify() {
  if [ -n "$WSL_TMP_CREATED" ] && [ -d "$WSL_TMP_CREATED" ]; then
    rm -rf "$WSL_TMP_CREATED"
  fi
  if [ -n "$STAGING_TMP_CREATED" ] && [ -d "$STAGING_TMP_CREATED" ]; then
    rm -rf "$STAGING_TMP_CREATED"
  fi
}
trap cleanup_verify EXIT

if [ "$(uname -s)" = "Linux" ] && [ ! -S /var/run/docker.sock ] && command -v docker.exe >/dev/null 2>&1; then
  case "$ROOT_DIR" in
    /mnt/*)
      # Keep Linux TMPDIR on ext4/tmpfs for pytest capture tempfiles while placing --basetemp on Windows-backed /mnt/<drive> for docker.exe bind mounts.
      WSL_TMP_CREATED="$ROOT_DIR/runtime/.wsl-verify-tmp-$$"
      mkdir -p "$WSL_TMP_CREATED"
      PYTEST_BASETEMP_ARGS=(--basetemp="$WSL_TMP_CREATED/pytest")
      ;;
  esac
fi

OMITTED=()
UNIT_XML="$JUNIT_DIR/junit_unit.xml"
INT_XML="$JUNIT_DIR/junit_integration.xml"
E2E_XML="$JUNIT_DIR/junit_e2e.xml"

echo "Running unit tests with $PYTHON_BIN ..."
"$PYTHON_BIN" -m pytest tests/unit -q -rs --junitxml="$UNIT_XML" "${PYTEST_BASETEMP_ARGS[@]}"

echo "Running integration tests with $PYTHON_BIN ..."
"$PYTHON_BIN" -m pytest tests/integration -q -rs --junitxml="$INT_XML" "${PYTEST_BASETEMP_ARGS[@]}"

ACTIVE_XMLS=("$UNIT_XML" "$INT_XML")

if [ "$RUN_E2E" = "1" ]; then
  if ! "$PYTHON_BIN" -c "import playwright" 2>/dev/null; then
    echo "ERROR: Playwright is required for E2E verification (SANDBOX_LAB_RUN_E2E=1)." >&2
    echo "Install with: \"$PYTHON_BIN\" -m pip install playwright && \"$PYTHON_BIN\" -m playwright install chromium" >&2
    exit 1
  fi
  echo "Running E2E Playwright tests..."
  "$PYTHON_BIN" -m pytest tests/e2e -q -rs --junitxml="$E2E_XML" "${PYTEST_BASETEMP_ARGS[@]}"
  ACTIVE_XMLS+=("$E2E_XML")
else
  echo "NOTE: E2E tests omitted because SANDBOX_LAB_RUN_E2E=${RUN_E2E}." >&2
  OMITTED+=("e2e (SANDBOX_LAB_RUN_E2E=${RUN_E2E})")
fi

if [ "$RUN_FRONTEND" = "1" ]; then
  if [ ! -d "$ROOT_DIR/web/node_modules" ]; then
    echo "ERROR: Missing web/node_modules. Run '(cd \"$ROOT_DIR/web\" && npm ci)' before verifying." >&2
    exit 1
  fi
  if [ "$(uname -s)" = "Linux" ]; then
    if [ ! -d "$ROOT_DIR/web/node_modules/@rollup/rollup-linux-x64-gnu" ] && [ ! -d "$ROOT_DIR/web/node_modules/@rollup/rollup-linux-arm64-gnu" ]; then
      echo "ERROR: web/node_modules lacks Linux @rollup native package (likely installed on Windows)." >&2
      echo "Run '(cd \"$ROOT_DIR/web\" && npm ci)' inside Linux/WSL2." >&2
      exit 1
    fi
  fi
  echo "Running TypeScript check and Vite build..."
  (cd "$ROOT_DIR/web" && npm run typecheck && npm run build)
else
  echo "NOTE: Frontend typecheck and build omitted because SANDBOX_LAB_RUN_FRONTEND=${RUN_FRONTEND}." >&2
  OMITTED+=("frontend (SANDBOX_LAB_RUN_FRONTEND=${RUN_FRONTEND})")
fi

KEY_PRESENT="no"
for K in DEEPSEEK_API_KEY OPENAI_API_KEY GEMINI_API_KEY ANTHROPIC_API_KEY; do
  if [ -n "${!K:-}" ]; then
    KEY_PRESENT="yes"
    break
  fi
done

TOTAL_SKIPPED="$(VERIFY_KEY_PRESENT="$KEY_PRESENT" "$PYTHON_BIN" - "${ACTIVE_XMLS[@]}" <<'PYEOF'
import os, sys, xml.etree.ElementTree as ET
from pathlib import Path

root = Path.cwd()
provider = os.getenv("SANDBOX_LAB_LLM_PROVIDER", "auto")
model = os.getenv("SANDBOX_LAB_MODEL", "default")
fp = "unavailable"
if (root / "scripts" / "evidence_validator.py").is_file():
    sys.path.insert(0, str(root))
    from scripts.evidence_validator import compute_source_fingerprint
    fp, file_count, _ = compute_source_fingerprint(root)
    print(f"[verify] provenance: files={file_count} source_fingerprint={fp}", file=sys.stderr)
print(f"[verify] llm_config: provider={provider} model={model} api_key_present={os.environ.get('VERIFY_KEY_PRESENT', 'no')}", file=sys.stderr)

total_skipped = 0
for raw_path in sys.argv[1:]:
    p = Path(raw_path)
    if not p.is_file():
        print(f"ERROR: Missing JUnit XML {p}", file=sys.stderr)
        sys.exit(2)
    tree = ET.parse(p)
    t = failed = err = sk = passed = 0
    skips = []
    for tc in tree.findall(".//testcase"):
        t += 1
        sk_el = tc.find("skipped")
        if tc.find("failure") is not None:
            failed += 1
        elif tc.find("error") is not None:
            err += 1
        elif sk_el is not None:
            sk += 1
            reason = (sk_el.attrib.get("message") or sk_el.text or "").strip()
            skips.append(f"{tc.attrib.get('name', '?')}: {reason}")
        else:
            passed += 1
    total_skipped += sk
    print(f"[verify] {p.name}: tests={t} passed={passed} failed={failed} errors={err} skipped={sk}", file=sys.stderr)
    for item in skips:
        print(f"[verify]   skipped -> {item}", file=sys.stderr)
print(total_skipped)
PYEOF
)"
echo "JUnit reports verified in staging: $JUNIT_DIR (skipped=$TOTAL_SKIPPED, api_key_present=$KEY_PRESENT)"

if [ "$RELEASE_STRICT" = "1" ]; then
  if [ "$TOTAL_SKIPPED" -ne 0 ]; then
    echo "ERROR: SANDBOX_LAB_RELEASE_STRICT=1 refuses release when skipped=$TOTAL_SKIPPED > 0 (ensure live API key, Docker, and dependencies are present)." >&2
    exit 1
  fi
  "$PYTHON_BIN" - "$ROOT_DIR" "$JUNIT_DIR" <<'PYEOF'
import sys
from pathlib import Path
root = Path(sys.argv[1])
junit_dir = Path(sys.argv[2])
sys.path.insert(0, str(root))
from scripts.evidence_validator import validate_and_load_deliverables
validate_and_load_deliverables(
    root,
    check_assets=True,
    check_fingerprint=True,
    require_junit=True,
    junit_dir=junit_dir,
    release_strict=True,
)
print("[verify] Strict release deliverables validation PASSED.")
PYEOF
  if [ "$JUNIT_DIR" != "$ROOT_DIR/deliverables/junit" ]; then
    mkdir -p "$ROOT_DIR/deliverables/junit"
    cp "$UNIT_XML" "$INT_XML" "$E2E_XML" "$ROOT_DIR/deliverables/junit/"
    echo "[verify] Synced verified JUnit XMLs to deliverables/junit/."
  fi
fi

if [ "${#OMITTED[@]}" -gt 0 ] || [ "$TOTAL_SKIPPED" -ne 0 ]; then
  OMIT_TEXT="none"
  if [ "${#OMITTED[@]}" -gt 0 ]; then
    OMIT_TEXT="${OMITTED[*]}"
  fi
  echo "Verification finished with PARTIAL COVERAGE (skipped=${TOTAL_SKIPPED}; omitted: ${OMIT_TEXT})."
else
  echo "Verification finished (FULL COVERAGE passed, 0 skipped)."
fi
