# Sandbox Lab — Secure Agentic AI Tool Execution using Linux Sandbox

Topic 17 · Linux OSS 2026 (VKU, Group of 3)

Sandbox Lab is an end-to-end engineering lab demonstrating how an AI Agent inspects real Nginx access logs through the **Model Context Protocol (MCP)** while every tool execution is isolated inside an ephemeral, non-root, read-only, network-disabled Linux Docker worker container.

## 1. Architecture Summary

- **Web Operator UI (`web/`)**: React 19 + TypeScript + Vite (`http://127.0.0.1:5173`) with four screens: **Traffic**, **Agent**, **Checks**, and **Access log**.
- **Backend & Trusted Runner (`backend/app/`)**: FastAPI + Pydantic + SQLite WAL (`http://127.0.0.1:18000`), immutable log snapshots (`runtime/snapshots/`), MCP server/client (`mcp==1.28.1` over stdio), and sandbox policy runner.
- **Nginx & Control Service (`infra/`)**: `nginx:1.27-alpine` on `127.0.0.1:18080` writing JSON lines to `runtime/nginx-logs/access.jsonl`, plus `sandbox-lab-test-service` on `127.0.0.1:18081` (`sandbox_lab_net`) for network isolation controls.
- **Worker Image (`worker/`)**: `sandbox-lab-worker:v1` (`alpine:3.21` + `python3`, UID/GID `10001:10001`), running strictly typed actions (`count_status_codes`, `top_error_paths`, `recent_http_errors`, and harness checks).
- **Scripts & Release (`scripts/`, `scripts/release/`)**: Runtime lifecycle (`preflight.sh`, `start.sh`, `stop.sh`, `verify.sh`) and strict deliverable packaging (`build_release.py`, `scripts/release/*`).
- **Deliverables (`deliverables/`)**: Final `report.docx` / `report.pdf` and `slides.pptx` / `slides.pdf`, verified figures in `deliverables/assets/`, and provenance + raw JUnit XMLs organized under `deliverables/evidence/`.

## 2. Prerequisites & Environment Notes

- **Docker Desktop** with Linux containers (`desktop-linux` context) and Docker Compose v2+.
  - *WSL2 Linux vs Windows `/mnt/<drive>` paths*:
    - **WSL Integration ON (`/var/run/docker.sock` present in Ubuntu-24.04)**: `docker` and bind mounts work from both Linux ext4 (`~/...`, `/tmp`) and Windows mounts (`/mnt/p/...`).
    - **WSL Integration OFF (`docker.exe` + `wslpath -w` fallback)**: When `/var/run/docker.sock` is not attached in WSL2, scripts and `backend/app/sandbox.py` automatically invoke Windows `docker.exe`. Because `docker.exe` cannot mount `\\wsl.localhost\Ubuntu-24.04\...` UNC paths (`no such file or directory` on `distro-services` socket), keep `project_root`, `SANDBOX_LAB_RUNTIME_DIR`, and pytest `--basetemp` on a Windows-backed mount such as `/mnt/p/MaNguonMoLinux` (keeping Linux `TMPDIR=/tmp` for pytest capture files; handled automatically by `scripts/verify.sh`).
  - *Node / Vite on Linux vs Windows*: When sharing the same repo folder across Windows and WSL2, Windows `web/node_modules` installs Windows Rollup binaries, whereas Linux Vite builds require `@rollup/rollup-linux-x64-gnu` (installed via `npm ci` inside Linux or in an isolated Linux copy).
- **Python 3.12+**: `python -m pip install -r backend/requirements.txt` (includes FastAPI, Uvicorn, Pydantic, HTTPX, `mcp==1.28.1`, Pytest, Playwright, and deliverable generators).
- **Node.js 18+/24+** and `npm`.
- **Optional live LLM key** (exported in the terminal environment running the **FastAPI backend only**; `backend/app/config.py::Settings.from_env()` reads process environment variables directly and does **not** auto-load `.env` files — never commit real keys):
  - `DEEPSEEK_API_KEY` (`SANDBOX_LAB_LLM_PROVIDER=deepseek`, `SANDBOX_LAB_MODEL=deepseek-flash`, `DEEPSEEK_BASE_URL=https://api.deepseek.com`), or
  - `OPENAI_API_KEY` (with optional `OPENAI_BASE_URL` and `SANDBOX_LAB_MODEL`), or
  - `GEMINI_API_KEY` (`SANDBOX_LAB_MODEL=gemini-2.5-flash`), or
  - `ANTHROPIC_API_KEY` (`SANDBOX_LAB_MODEL=claude-3-5-sonnet-latest`).
  - **Key isolation**: `McpLabClient` explicitly strips all LLM provider secret variables (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`) before spawning `backend.app.mcp_server` over stdio, and never mounts credentials into the Docker worker.
  - Without a model API key, **Traffic**, **Checks**, **Access log**, and all deterministic **MCP/Docker** tests still work; **Agent** reports `Model unavailable` unless the target provider key is configured.

## 3. Quick Start from Clean Checkout

1. **Preflight check** (from WSL2 or Bash):
   ```bash
   bash scripts/preflight.sh
   ```
2. **Build the worker image** (from Windows PowerShell or WSL2):
   ```powershell
   docker build -t sandbox-lab-worker:v1 .\worker
   ```
3. **Install dependencies**:
   ```powershell
   python -m pip install -r .\backend\requirements.txt
   cd .\web ; npm install ; cd ..
   ```
4. **Start Nginx, test-service, FastAPI backend, and Web UI**:
   - Using `scripts/start.sh` in WSL2/Bash (auto-bootstraps `.venv-linux` if needed, exports `VITE_API_ORIGIN`, and waits for health endpoints), or in PowerShell:
     ```powershell
     docker compose -f .\infra\compose.yaml up -d
     python -m uvicorn backend.app.api:app --host 127.0.0.1 --port 18000
     # In another terminal:
     cd .\web ; npm run dev
     ```
5. **Open the Web UI**:
   - Live mode: `http://127.0.0.1:5173`
   - Mockup fixture preview (in-memory only, zero backend mutations): `http://127.0.0.1:5173/?fixture=mockup`

## 4. Verification Commands

Run unit, real Docker/Nginx/MCP integration, and Playwright E2E tests:

```powershell
python -m pytest tests/unit tests/integration tests/e2e -v -rs
cd .\web ; npm run typecheck ; npm run build
```

Or run the unified verification script in WSL2/Bash:
```bash
# Development / local run (writes JUnit XMLs to runtime/exports/, never clobbers deliverables/evidence/junit/, reports PARTIAL COVERAGE if skipped)
bash scripts/verify.sh

# Isolated clean WSL2 /mnt/<drive> test sequence when Docker Desktop WSL Integration socket is absent (docker.exe mode):
#   1. Copy repo cleanly onto /mnt/p/... (not \\wsl.localhost\...), cd into that folder
#   2. python3 -m venv .venv-linux && .venv-linux/bin/pip install -r backend/requirements.txt
#   3. (cd web && npm ci && npm run typecheck && npm run build)
#   4. mkdir -p "$PWD/runtime/.wsl-tmp" && docker.exe compose -f infra/compose.yaml up -d
#   5. .venv-linux/bin/python -m pytest tests/integration/test_mcp.py -v --basetemp="$PWD/runtime/.wsl-tmp/pytest"

# Strict release verification (requires E2E=1, FRONTEND=1, live LLM key, 0 skipped, then syncs deliverables/evidence/junit/)
SANDBOX_LAB_RELEASE_STRICT=1 DEEPSEEK_API_KEY="<your-key>" bash scripts/verify.sh

# Offline release build (requires tracked deliverables/evidence/{junit/*.xml,linux_wsl_receipt.json,evidence_bundle.json,test_summary.json} to match source fingerprint)
python scripts/build_release.py
```

Note on provenance: `deliverables/evidence/evidence_bundle.json` and the Web UI screenshots (`Run 012`) preserve an audited live runtime capture alongside the verified Linux WSL2 MCP receipt (`deliverables/evidence/linux_wsl_receipt.json`) and tracked raw JUnit test results (`deliverables/evidence/junit/junit_{unit,integration,e2e}.xml`). Export any live session bundle via:
`GET http://127.0.0.1:18000/api/runs/{run_id}/export`

## 5. Stop Lab Services

```bash
bash scripts/stop.sh
```
Or in PowerShell:
```powershell
docker compose -f .\infra\compose.yaml down --remove-orphans
```
