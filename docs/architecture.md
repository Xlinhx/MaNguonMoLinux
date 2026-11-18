# Sandbox Lab — System Architecture & Security Boundaries

## 1. Component Topology & Trust Boundaries

1. **Untrusted / External Inputs**:
   - **Web Operator UI** (`127.0.0.1:5173`): Sends typed API requests (`create_run`, `append_404`, `questions`, `checks`, log queries).
   - **LLM Model Provider**: Receives user question + MCP tool schemas; emits tool-call proposals (`count_status_codes`, `top_error_paths`, `recent_http_errors`) and `run_id`/`limit` arguments.
   - **Raw Nginx Access Logs** (`runtime/nginx-logs/access.jsonl`): Treated strictly as untrusted data (e.g., query strings containing shell metacharacters are parsed by `json.loads()` inside Python, never evaluated by a shell).
2. **Trusted Host Boundary (`backend/app/`)**:
   - **Snapshot Service (`snapshots.py`)**: Extracts entries matching `run_id` + confirmed `request_id`s, freezes them into `runtime/snapshots/<run_id>/rev-XXXX/access.jsonl`, and computes SHA-256.
   - **MCP Server (`mcp_server.py`)**: Exposes strictly 3 typed tools over `stdio` (`mcp==1.28.1`). Validates UUIDs, integer bounds (`limit` 1–20 / 1–50), and snapshot ownership *before* invoking the runner.
   - **Trusted Policy Runner (`sandbox.py`)**: Holds Docker daemon access. Constructs `docker run` argument vectors (`shell=False`), never accepts arbitrary images, mounts, network flags, or shell commands from MCP or the UI.
3. **Isolated Worker Boundary (`worker/`)**:
   - Ephemeral container `sandbox-lab-worker:v1` (`alpine:3.21` + `python3`), running `worker/main.py` as UID/GID `10001:10001`.

## 2. Linux Container Isolation Policy (`sandbox-policy-v1`)

Every MCP tool call and sandbox check container is launched with:

| Control | Flag / Setting | Purpose | Verification Method |
|---|---|---|---|
| Non-root user | `--user 10001:10001` | Prevents root UID 0 inside container | `docker inspect` + `os.getuid()` in worker |
| Read-only rootfs | `--read-only` | Prevents modifying `/app` or system files | `docker inspect` (`ReadonlyRootfs: true`) |
| Bounded `/tmp` | `--tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m` | Allows minimal scratch without exec/suid | `build_docker_run_argv` + container run |
| Read-only snapshot | `--mount type=bind,src=...,dst=/input/access.jsonl,readonly` | Worker cannot alter immutable snapshot | **Behavioral + writable copy control** |
| Network disabled | `--network none` | Blocks TCP/UDP/DNS egress and lab services | **Behavioral + `sandbox_lab_net` control** |
| Capabilities dropped | `--cap-drop ALL` | Removes Linux capabilities (`CAP_NET_RAW`, etc.) | `docker inspect` (`CapDrop: ["ALL"]`) |
| No privilege escalation | `--security-opt no-new-privileges:true` | Blocks setuid/file-cap privilege gain | `docker inspect` |
| PID & CPU limits | `--pids-limit 64`, `--cpus 0.5` | Mitigates fork proliferation and CPU starvation | `docker inspect` (`PidsLimit=64`, `NanoCpus=5e8`) |
| Memory cap | `--memory 128m --memory-swap 128m` | Enforces 128 MiB cgroup memory ceiling | `docker inspect` + **200 MiB OOM kill (`exit 137`)** |
| Execution timeout | `min(10s, deadline)` + `docker rm -f` | Kills hung workers and removes containers | **Behavioral 15s sleep terminated + `ps -a` empty** |
| Output cap | 64 KiB stdout/stderr truncation | Protects API/LLM context from log floods | Unit/runner truncation |

## 3. Control Philosophy & Known Limitations

- **Why controls are mandatory**: Observing a failed write or failed HTTP request alone can be a false positive (e.g., missing file or bad URL). Therefore, **Write log** runs the identical image/UID/command against an isolated writable copy (`SANDBOX_WRITE_MARKER` found on host), and **Reach test service** runs the identical image/UID/command on `sandbox_lab_net` (HTTP 200 confirmed). If a control fails or Docker breaks, verdict is **Error**, never **Pass**.
- **Limitations**: Containers share the host Linux kernel (under Docker Desktop's Linux VM on Windows/WSL2). This is OS-level isolation with defense-in-depth, not hardware virtualization or protection against kernel zero-days.
