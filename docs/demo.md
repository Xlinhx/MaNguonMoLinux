# Sandbox Lab — 3-Member Demo & Defense Script

## Pre-Demo Checklist
1. Ensure Docker Desktop (`desktop-linux` context) is running (`docker info` succeeds) and `sandbox-lab-worker:v1` is built.
2. Start lab services (`infra/compose.yaml`, FastAPI on `:18000`, Web UI on `:5173`) — in WSL2 use `bash scripts/start.sh` (or Windows PowerShell uvicorn + Vite).
3. Verify `http://127.0.0.1:5173` loads cleanly (or `?fixture=mockup` for non-mutating visual preview).
4. Keep one backup terminal ready to inspect `runtime/nginx-logs/access.jsonl`, `GET /api/runs/{id}/export`, and `deliverables/evidence/linux_wsl_receipt.json`.
5. **Architecture boundary summary to explain during defense**:
   - **Host Python boundary**: FastAPI runs `AgentService` and spawns `backend.app.mcp_server` over MCP `stdio` via `McpLabClient` (**explicitly stripping** `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, and `OPENROUTER_API_KEY` before launching the MCP server).
   - **Trusted runner -> Linux container**: `mcp_server.py` calls `sandbox.execute_tool(...)`, which verifies snapshot SHA-256 on disk, then launches an ephemeral `sandbox-lab-worker:v1` Linux container inside Docker Desktop's `desktop-linux` VM (controlled via Docker CLI / daemon) with `--user 10001:10001`, `--read-only`, `--network none`, `--cap-drop ALL`, `--pids-limit 64`, `--memory 128m --memory-swap 128m`, `--cpus 0.5`, and single-snapshot `--mount ...,readonly`.
   - **Data visibility & kernel sharing**: The external LLM API sees only the user question + bounded JSON tool results (never host paths outside snapshots or secrets). Worker containers share the Linux kernel inside the Docker VM (OS-level isolation, not a hardware microVM).

## Division of Roles (3 Students)

### Part 1 — Member A (Linux, Nginx, Worker Policy & Isolation)
- Open **Traffic** (`http://127.0.0.1:5173`) and click **Create run**.
- Explain that the backend sends 10 real HTTP requests (`7x GET /demo` = 200, `3x GET /missing` = 404) with `X-Sandbox-Run-Id` and `X-Sandbox-Request-Id` headers to Nginx in Docker, tracks per-request dispatch state (`not_sent` / `dispatched_uncertain` / `sent` for mid-batch recovery), waits for JSON log confirmation in `runtime/nginx-logs/access.jsonl`, and freezes **Snapshot Revision 1**.
- Select Request `04` (`404`) and click **Open in access log →** to show `run_id` and `request_id` preservation against the raw JSONL entry in terminal (`runtime/nginx-logs/access.jsonl`).
- Open **Checks** and click **Run checks**. Explain the 3 behavioral checks + paired controls (`Read log`, `Write log` with isolated `ro_copy` + writable control copy proving host snapshot SHA-256 unchanged, `Reach test service` `--network none` vs `sandbox_lab_net` control returning HTTP 200 JSON) and inspect **Control evidence**. Highlight runtime OOM enforcement (`OOMKilled=True`, exit 137) vs config-inspected limits (`--cpus 0.5`, `--pids-limit 64`).

### Part 2 — Member B (MCP Protocol, Agent Loop & Snapshot Pinning)
- **Live-Model Branch (when `DEEPSEEK_API_KEY` / provider key is exported in the backend terminal)**:
  - Open **Agent** and run two real queries on **Revision 1**:
    1. `"How many 404s are in this run?"` -> invokes **`count_status_codes`** via MCP stdio -> answers `3 of 10 requests returned HTTP 404.` (with verified `Tool counts: 3 404 / 10 total` pill). Click **View tool call →** to inspect `tool_name`, `run_id`, `Revision 1`, SHA-256 digest verification, and JSON output (`200: 7 · 404: 3`).
    2. `"Which requests returned 404 most recently?"` -> invokes **`recent_http_errors`** via MCP stdio -> lists recent `seq`, `request_id`, and `/missing` entries, proving direct correlation with **Access log** and raw JSONL.
  - Switch back to **Traffic** and click **Add 404** -> total updates to `11 requests (7 successful · 4 not found)` on **Revision 2** (Checks reset to `Not run` for the new snapshot, while previously selected Request `04` stays preserved).
  - Return to **Agent**: show the amber banner on the previous Revision 1 answer (`Answer uses revision 1 · Run now has 11 requests`) and click **Ask again** -> new answer returns `4 of 11` on **Revision 2**.
- **No-Key / Offline Branch (no LLM API key in backend environment)**:
  - Submitting a question in **Agent** cleanly reports `Model unavailable` (`Traffic and Checks are available.`); do **not** run **Ask again** in offline mode.
  - Prove the exact same real MCP stdio + Docker worker execution deterministically across revisions via `python -m pytest tests/integration/test_mcp.py -v`, inspect the verified WSL2 receipt (`deliverables/evidence/linux_wsl_receipt.json`), and cite historical `Run 012` artifacts (`deliverables/evidence/evidence_bundle.json`, captured `2026-11-18`) honestly as pre-recorded audited captures alongside final verification.

### Part 3 — Member C (UI Craft, Verification & Error Classification)
- Demonstrate mobile responsiveness (`360px` / `390px`) including the visible **Access log** summary (`11 requests · Snapshot 2`), shared accessible bottom sheets (`Navigation`, `Switch run`, `Tool call`, `Control evidence`, `Request detail`) with `inert` + Tab focus trapping and `44px+` touch targets, and preserved Agent job tracking when switching tabs.
- Open **Checks** / slides to explain **Pass vs. Fail vs. Error**:
  - **Pass**: Sandbox blocks forbidden write/network AND paired control succeeds (`200` + `{"service":"sandbox-test-service","status":"ok"}`).
  - **Fail**: Sandbox unexpectedly receives an HTTP response (including `503`/`404`/`3xx`) or writes marker while control succeeds.
  - **Error (never Pass/Fail)**: Unavailable control (`/ping` down or returning `204`), worker crash, snapshot SHA-256 mismatch, or timeout (`10s` abort + `docker rm -f` cleanup).
- Summarize full verification (`tests/unit`, `tests/integration`, `tests/e2e`, `scripts/verify.sh`), `<2 KB` run-detail metadata (`?include_requests=false`) vs `10,000-row` cursor pagination, and the shared-kernel trust boundary.
