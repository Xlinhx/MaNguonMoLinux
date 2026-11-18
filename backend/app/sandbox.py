from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from .config import Settings
from .models import Snapshot
from .store import Store

MAX_OUTPUT_BYTES = 65536
DEFAULT_TIMEOUT_S = 10.0

TOOL_ACTIONS = frozenset({"count_status_codes", "top_error_paths", "recent_http_errors"})
CHECK_ACTIONS = frozenset(
    {
        "check_read_log",
        "check_write_log",
        "check_network",
        "check_outside_mount",
        "check_sleep",
        "check_memory",
    }
)
ALL_ACTIONS = TOOL_ACTIONS | CHECK_ACTIONS


class SandboxPolicyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ControlOverride:
    """Internal harness override for control comparisons only; never exposed to agent/MCP."""

    readonly_mount_path: Path | None = None
    writable_mount_path: Path | None = None
    network_name: str | None = None
    retain_for_inspect: bool = False


@dataclass
class WorkerExecutionResult:
    call_id: str
    container_name: str
    action: str
    args: dict[str, object]
    snapshot_id: str | None
    exit_code: int | None
    stdout: str
    stderr: str
    payload: dict[str, object] | None
    duration_ms: int
    timed_out: bool
    infra_error: bool
    cleanup_ok: bool
    error_code: str | None = None
    error_message: str | None = None
    argv: list[str] | None = None
    oom_killed: bool | None = None


def resolve_docker_bin() -> str:
    env_bin = os.getenv("SANDBOX_DOCKER_BIN")
    if env_bin:
        return env_bin
    if os.name != "nt" and not Path("/var/run/docker.sock").exists() and shutil.which("docker.exe"):
        return "docker.exe"
    if shutil.which("docker"):
        return "docker"
    if shutil.which("docker.exe"):
        return "docker.exe"
    return "docker"


def _to_docker_host_path(path: Path, docker_bin: str) -> str:
    resolved = path.resolve()
    if os.name != "nt" and docker_bin.endswith(".exe") and shutil.which("wslpath"):
        proc = subprocess.run(
            ["wslpath", "-w", str(resolved)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return str(resolved)


def validate_action_and_args(
    action: str,
    limit: int | None = None,
    url: str | None = None,
    seconds: float | None = None,
    mb: int | None = None,
    marker_path: str | None = None,
) -> list[str]:
    if action not in ALL_ACTIONS:
        raise SandboxPolicyError("UNSUPPORTED_ACTION", f"Action '{action}' is not in the worker allowlist.")

    action_argv: list[str] = [action]
    if action == "top_error_paths":
        val = 5 if limit is None else int(limit)
        if val < 1 or val > 20:
            raise SandboxPolicyError("INVALID_ARGS", "top_error_paths limit must be between 1 and 20.")
        action_argv.extend(["--limit", str(val)])
    elif action == "recent_http_errors":
        val = 10 if limit is None else int(limit)
        if val < 1 or val > 50:
            raise SandboxPolicyError("INVALID_ARGS", "recent_http_errors limit must be between 1 and 50.")
        action_argv.extend(["--limit", str(val)])
    elif action == "check_network":
        if not url:
            raise SandboxPolicyError("INVALID_ARGS", "check_network requires a fixed lab URL.")
        action_argv.extend(["--url", url])
    elif action == "check_outside_mount":
        if marker_path:
            action_argv.extend(["--path", marker_path])
    elif action == "check_sleep":
        val_s = 15.0 if seconds is None else float(seconds)
        if val_s <= 0 or val_s > 60:
            raise SandboxPolicyError("INVALID_ARGS", "check_sleep seconds out of bounds.")
        action_argv.extend(["--seconds", str(val_s)])
    elif action == "check_memory":
        val_mb = 200 if mb is None else int(mb)
        if val_mb < 1 or val_mb > 512:
            raise SandboxPolicyError("INVALID_ARGS", "check_memory mb out of bounds.")
        action_argv.extend(["--mb", str(val_mb)])
    return action_argv


def build_docker_run_argv(
    *,
    docker_bin: str,
    worker_image: str,
    container_name: str,
    call_id: str,
    mount_source: Path | None,
    action_argv: list[str],
    control: ControlOverride | None = None,
    job_id: str | None = None,
) -> list[str]:
    network = control.network_name if (control and control.network_name) else "none"
    readonly_mount = not (control and control.writable_mount_path is not None)

    argv = [
        docker_bin,
        "run",
        "--name",
        container_name,
        "--label",
        "sandbox-lab.managed=true",
        "--label",
        f"sandbox-lab.call-id={call_id}",
    ]
    if job_id:
        argv.extend(["--label", f"sandbox-lab.job-id={job_id}"])

    argv.extend(
        [
            "--user",
            "10001:10001",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--network",
            network,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "64",
            "--memory",
            "128m",
            "--memory-swap",
            "128m",
            "--cpus",
            "0.5",
        ]
    )
    if mount_source is not None:
        host_src = _to_docker_host_path(mount_source, docker_bin)
        mount_spec = f"type=bind,src={host_src},dst=/input/access.jsonl"
        if readonly_mount:
            mount_spec += ",readonly"
        argv.extend(["--mount", mount_spec])

    argv.append(worker_image)
    argv.extend(action_argv)
    return argv


def _read_pipe_capped(pipe, max_bytes: int, holder: list[bytes]) -> None:
    """Read subprocess pipe incrementally up to max_bytes, draining excess without retaining unbounded memory."""
    buf = bytearray()
    truncated = False
    try:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                break
            if len(buf) < max_bytes:
                room = max_bytes - len(buf)
                buf.extend(chunk[:room])
                if len(chunk) > room:
                    truncated = True
            else:
                truncated = True
    except OSError:
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass
    if truncated:
        buf.extend(b"\n...[truncated]")
    holder.append(bytes(buf))


def _classify_docker_infra_error(exit_code: int | None, stderr: str) -> tuple[bool, str | None, str | None]:
    low = stderr.lower()
    if exit_code == 125 or "cannot connect to the docker daemon" in low or "is the docker daemon running" in low:
        return True, "DOCKER_UNAVAILABLE", "Docker daemon is unavailable."
    if "no such image" in low or "pull access denied" in low or "unable to find image" in low:
        return True, "WORKER_IMAGE_MISSING", "Worker image is missing."
    if "error response from daemon" in low and ("mount" in low or "bind" in low or "no such file" in low):
        return True, "MOUNT_ERROR", "Docker failed to bind mount the snapshot."
    return False, None, None


def cleanup_container(docker_bin: str, container_name: str, call_id: str) -> bool:
    """Explicitly kill/remove the worker container by name and verify none remain for call_id."""
    try:
        rm_proc = subprocess.run(
            [docker_bin, "rm", "-f", container_name],
            capture_output=True,
            text=True,
            shell=False,
            timeout=10.0,
            check=False,
        )
        ps = subprocess.run(
            [docker_bin, "ps", "-a", "-q", "--filter", f"label=sandbox-lab.call-id={call_id}"],
            capture_output=True,
            text=True,
            shell=False,
            timeout=5.0,
            check=False,
        )
        if ps.returncode != 0:
            return False
        if ps.stdout.strip() != "":
            return False
        # rm_proc may say "No such container" if daemon failed before create; ps clean confirms zero left
        _ = rm_proc
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def cleanup_job_containers(docker_bin: str | None, job_id: str) -> bool:
    """Force-remove any worker containers tagged with sandbox-lab.job-id=<job_id> and verify clean."""
    resolved = docker_bin or resolve_docker_bin()
    try:
        ps = subprocess.run(
            [resolved, "ps", "-a", "-q", "--filter", f"label=sandbox-lab.job-id={job_id}"],
            capture_output=True,
            text=True,
            shell=False,
            timeout=5.0,
            check=False,
        )
        if ps.returncode != 0:
            return False
        ids = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
        if ids:
            subprocess.run(
                [resolved, "rm", "-f", *ids],
                capture_output=True,
                text=True,
                shell=False,
                timeout=10.0,
                check=False,
            )
            ps_after = subprocess.run(
                [resolved, "ps", "-a", "-q", "--filter", f"label=sandbox-lab.job-id={job_id}"],
                capture_output=True,
                text=True,
                shell=False,
                timeout=5.0,
                check=False,
            )
            return ps_after.returncode == 0 and ps_after.stdout.strip() == ""
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def inspect_worker_container(docker_bin: str, container_name: str) -> dict[str, object] | None:
    try:
        proc = subprocess.run(
            [docker_bin, "inspect", container_name],
            capture_output=True,
            text=True,
            shell=False,
            timeout=5.0,
            check=False,
        )
        if proc.returncode != 0:
            return None
        items = json.loads(proc.stdout)
        return items[0] if items else None
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return None


def _resolve_snapshot(store: Store, settings: Settings, snapshot_id: str) -> Snapshot:
    try:
        uuid.UUID(snapshot_id)
    except ValueError as exc:
        raise SandboxPolicyError("INVALID_SNAPSHOT_ID", "Snapshot ID must be a valid UUID.") from exc

    snap = store.get_snapshot(snapshot_id)
    if not snap:
        raise SandboxPolicyError("SNAPSHOT_NOT_FOUND", "Snapshot was not found.")

    snap_path = Path(snap.file_path).resolve()
    snapshots_root = settings.snapshots_dir.resolve()
    try:
        snap_path.relative_to(snapshots_root)
    except ValueError as exc:
        raise SandboxPolicyError("INVALID_SNAPSHOT_PATH", "Snapshot path escapes snapshot root.") from exc

    if not snap_path.is_file():
        raise SandboxPolicyError("SNAPSHOT_FILE_MISSING", "Snapshot file is missing on disk.")
    return snap


def run_worker_action(
    *,
    store: Store,
    settings: Settings,
    action: str,
    snapshot_id: str | None,
    limit: int | None = None,
    url: str | None = None,
    seconds: float | None = None,
    mb: int | None = None,
    marker_path: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    control: ControlOverride | None = None,
    docker_bin: str | None = None,
    job_id: str | None = None,
) -> WorkerExecutionResult:
    action_argv = validate_action_and_args(
        action,
        limit=limit,
        url=url,
        seconds=seconds,
        mb=mb,
        marker_path=marker_path,
    )
    mount_source: Path | None = None
    if control and control.writable_mount_path is not None:
        mount_source = control.writable_mount_path.resolve()
        if not mount_source.is_file():
            raise SandboxPolicyError("CONTROL_FILE_MISSING", "Control writable file is missing.")
    elif control and control.readonly_mount_path is not None:
        mount_source = control.readonly_mount_path.resolve()
        if not mount_source.is_file():
            raise SandboxPolicyError("CONTROL_FILE_MISSING", "Readonly check copy file is missing.")
    elif snapshot_id is not None:
        snap = _resolve_snapshot(store, settings, snapshot_id)
        mount_source = Path(snap.file_path).resolve()

    call_id = str(uuid.uuid4())
    container_name = f"sandbox-lab-worker-{call_id[:12]}"
    resolved_docker = docker_bin or resolve_docker_bin()
    effective_timeout = max(0.5, min(DEFAULT_TIMEOUT_S, float(timeout_s)))

    argv = build_docker_run_argv(
        docker_bin=resolved_docker,
        worker_image=settings.worker_image,
        container_name=container_name,
        call_id=call_id,
        mount_source=mount_source,
        action_argv=action_argv,
        control=control,
        job_id=job_id or os.getenv("SANDBOX_LAB_JOB_ID") or None,
    )

    started = time.monotonic()
    timed_out = False
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    infra_error = False
    err_code: str | None = None
    err_msg: str | None = None
    oom_killed: bool | None = None
    cleanup_ok = True

    proc: subprocess.Popen[bytes] | None = None
    out_holder: list[bytes] = []
    err_holder: list[bytes] = []

    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        t_out = threading.Thread(target=_read_pipe_capped, args=(proc.stdout, MAX_OUTPUT_BYTES, out_holder), daemon=True)
        t_err = threading.Thread(target=_read_pipe_capped, args=(proc.stderr, MAX_OUTPUT_BYTES, err_holder), daemon=True)
        t_out.start()
        t_err.start()

        try:
            exit_code = proc.wait(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=2.0)
            except (subprocess.SubprocessError, OSError):
                pass
            err_code = "WORKER_TIMEOUT"
            err_msg = f"Worker exceeded {effective_timeout:.1f}s timeout and was terminated."

        t_out.join(timeout=1.5)
        t_err.join(timeout=1.5)
        stdout = (out_holder[0] if out_holder else b"").decode("utf-8", errors="replace")
        stderr = (err_holder[0] if err_holder else b"").decode("utf-8", errors="replace")

        if not timed_out:
            infra_error, infra_code, infra_msg = _classify_docker_infra_error(exit_code, stderr)
            if infra_error:
                err_code = infra_code
                err_msg = infra_msg

        if control and control.retain_for_inspect:
            info = inspect_worker_container(resolved_docker, container_name)
            if info and isinstance(info.get("State"), dict):
                oom_killed = bool(info["State"].get("OOMKilled"))
    except FileNotFoundError:
        infra_error = True
        err_code = "DOCKER_UNAVAILABLE"
        err_msg = f"Docker executable '{resolved_docker}' not found."
    except (subprocess.SubprocessError, OSError) as exc:
        infra_error = True
        err_code = "DOCKER_UNAVAILABLE"
        err_msg = f"Failed to execute Docker ({exc})."
    finally:
        cleanup_ok = cleanup_container(resolved_docker, container_name, call_id)
        if not cleanup_ok:
            infra_error = True
            err_code = err_code or "CLEANUP_FAILED"
            err_msg = err_msg or f"Worker container {container_name} could not be cleaned up."

    duration_ms = int((time.monotonic() - started) * 1000)
    payload: dict[str, object] | None = None
    if stdout.strip():
        try:
            parsed = json.loads(stdout.strip().splitlines()[-1])
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = None

    return WorkerExecutionResult(
        call_id=call_id,
        container_name=container_name,
        action=action,
        args={
            k: v
            for k, v in {
                "limit": limit,
                "url": url,
                "seconds": seconds,
                "mb": mb,
                "marker_path": marker_path,
            }.items()
            if v is not None
        },
        snapshot_id=snapshot_id,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        payload=payload,
        duration_ms=duration_ms,
        timed_out=timed_out,
        infra_error=infra_error,
        cleanup_ok=cleanup_ok,
        error_code=err_code,
        error_message=err_msg,
        argv=argv,
        oom_killed=oom_killed,
    )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def execute_tool(
    *,
    store: Store,
    settings: Settings,
    action: str,
    snapshot_id: str,
    limit: int | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    job_id: str | None = None,
) -> WorkerExecutionResult:
    """Public typed entry point used by MCP tools. Never accepts control overrides or arbitrary paths."""
    if action not in TOOL_ACTIONS:
        raise SandboxPolicyError("FORBIDDEN_TOOL_ACTION", f"'{action}' is not a permitted MCP tool action.")
    snap = _resolve_snapshot(store, settings, snapshot_id)
    snap_path = Path(snap.file_path).resolve()
    if _sha256_file(snap_path) != snap.sha256:
        raise SandboxPolicyError(
            "SNAPSHOT_INTEGRITY_MISMATCH",
            "Snapshot integrity check failed: snapshot file digest on disk does not match stored SHA-256.",
        )

    res = run_worker_action(
        store=store,
        settings=settings,
        action=action,
        snapshot_id=snapshot_id,
        limit=limit,
        timeout_s=timeout_s,
        control=None,
        job_id=job_id,
    )
    if not res.timed_out and not res.infra_error and res.exit_code == 0:
        worker_sha = res.payload.get("snapshot_sha256") if isinstance(res.payload, dict) else None
        disk_sha = _sha256_file(snap_path) if snap_path.is_file() else None
        if worker_sha != snap.sha256 or disk_sha != snap.sha256:
            return WorkerExecutionResult(
                call_id=res.call_id,
                container_name=res.container_name,
                action=res.action,
                args=res.args,
                snapshot_id=res.snapshot_id,
                exit_code=res.exit_code,
                stdout=res.stdout,
                stderr=res.stderr,
                payload=None,
                duration_ms=res.duration_ms,
                timed_out=False,
                infra_error=False,
                cleanup_ok=res.cleanup_ok,
                error_code="SNAPSHOT_INTEGRITY_MISMATCH",
                error_message="Snapshot integrity check failed: worker snapshot SHA-256 does not match pinned snapshot.",
                argv=res.argv,
                oom_killed=res.oom_killed,
            )
    return res
