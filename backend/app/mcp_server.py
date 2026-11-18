from __future__ import annotations

import json
import uuid
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .config import Settings
from .models import ToolResult
from .sandbox import SandboxPolicyError, execute_tool
from .store import Store, utc_now_iso

mcp = FastMCP("sandbox-lab")


def _validate_run_and_snapshot(store: Store, run_id: str, snapshot_id: str | None = None) -> tuple[str, str, int, str]:
    try:
        uuid.UUID(run_id)
    except ValueError as exc:
        raise ValueError("INVALID_RUN_ID: run_id must be a valid UUID.") from exc

    run = store.get_run(run_id)
    if not run:
        raise ValueError("RUN_NOT_FOUND: Run does not exist.")
    if run.status != "ready":
        raise ValueError("RUN_NOT_READY: Run does not have a ready log snapshot.")

    if snapshot_id:
        try:
            uuid.UUID(snapshot_id)
        except ValueError as exc:
            raise ValueError("INVALID_SNAPSHOT_ID: snapshot_id must be a valid UUID.") from exc
        snap = store.get_snapshot(snapshot_id)
        if not snap or snap.run_id != run_id:
            raise ValueError("SNAPSHOT_NOT_FOUND: Snapshot does not belong to this run.")
    else:
        snap = store.get_snapshot(run.snapshot_id) if run.snapshot_id else store.get_latest_snapshot_for_run(run_id)
        if not snap:
            raise ValueError("SNAPSHOT_NOT_FOUND: No snapshot found for this run.")
    return run.id, snap.id, snap.revision, snap.sha256


def invoke_sandbox_tool(
    *,
    tool_name: str,
    run_id: str,
    limit: int | None = None,
    snapshot_id: str | None = None,
    timeout_s: float = 10.0,
    settings: Settings | None = None,
    job_id: str | None = None,
) -> dict[str, object]:
    cfg = settings or Settings.from_env()
    cfg.ensure_dirs()
    store = Store(cfg.db_path)

    _, snap_id, revision, expected_sha = _validate_run_and_snapshot(store, run_id, snapshot_id)
    args: dict[str, object] = {"run_id": run_id}
    if limit is not None:
        args["limit"] = limit

    try:
        res = execute_tool(
            store=store,
            settings=cfg,
            action=tool_name,
            snapshot_id=snap_id,
            limit=limit,
            timeout_s=min(10.0, max(0.5, float(timeout_s))),
            job_id=job_id,
        )
    except SandboxPolicyError as exc:
        tc = ToolResult(
            call_id=str(uuid.uuid4()),
            tool_name=tool_name,
            args=args,
            run_id=run_id,
            snapshot_id=snap_id,
            revision=revision,
            payload=None,
            duration_ms=0,
            status="failed",
            error_code=exc.code,
            error_message=exc.message,
            created_at=utc_now_iso(),
        )
        store.save_tool_call(tc)
        return tc.model_dump()

    payload = res.payload
    error_code = res.error_code
    error_message = res.error_message
    if res.timed_out:
        status = "timed_out"
    elif res.infra_error or res.exit_code != 0 or payload is None or error_code is not None:
        status = "failed"
    else:
        observed_sha = payload.get("snapshot_sha256") if isinstance(payload, dict) else None
        if observed_sha != expected_sha:
            status = "failed"
            payload = None
            error_code = "SNAPSHOT_INTEGRITY_MISMATCH"
            error_message = (
                "Snapshot integrity check failed: tool output digest does not match pinned snapshot SHA-256."
            )
        else:
            status = "completed"

    tc = ToolResult(
        call_id=res.call_id,
        tool_name=tool_name,
        args=args,
        run_id=run_id,
        snapshot_id=snap_id,
        revision=revision,
        payload=payload,
        duration_ms=res.duration_ms,
        status=status,
        error_code=error_code or ("WORKER_NONZERO_EXIT" if status == "failed" and res.exit_code != 0 else None),
        error_message=error_message or (res.stderr[:400] if status == "failed" and res.stderr else None),
        created_at=utc_now_iso(),
    )
    store.save_tool_call(tc)
    return tc.model_dump()


@mcp.tool()
def count_status_codes(
    run_id: str = Field(description="UUID of the Sandbox Lab run to inspect"),
    snapshot_id: str | None = Field(default=None, description="Optional pinned snapshot UUID"),
    timeout_s: float = Field(default=10.0, ge=0.5, le=10.0),
) -> str:
    """Count HTTP status codes (including 200 and 404) in the selected run's immutable log snapshot."""
    result = invoke_sandbox_tool(
        tool_name="count_status_codes",
        run_id=run_id,
        snapshot_id=snapshot_id,
        timeout_s=timeout_s,
    )
    return json.dumps(result)


@mcp.tool()
def top_error_paths(
    run_id: str = Field(description="UUID of the Sandbox Lab run to inspect"),
    limit: int = Field(default=5, ge=1, le=20, description="Number of top error paths to return (1-20)"),
    snapshot_id: str | None = Field(default=None, description="Optional pinned snapshot UUID"),
    timeout_s: float = Field(default=10.0, ge=0.5, le=10.0),
) -> str:
    """Return the most frequent HTTP >= 400 error paths in the selected run's immutable log snapshot."""
    result = invoke_sandbox_tool(
        tool_name="top_error_paths",
        run_id=run_id,
        limit=limit,
        snapshot_id=snapshot_id,
        timeout_s=timeout_s,
    )
    return json.dumps(result)


@mcp.tool()
def recent_http_errors(
    run_id: str = Field(description="UUID of the Sandbox Lab run to inspect"),
    limit: int = Field(default=10, ge=1, le=50, description="Number of recent HTTP errors to return (1-50)"),
    snapshot_id: str | None = Field(default=None, description="Optional pinned snapshot UUID"),
    timeout_s: float = Field(default=10.0, ge=0.5, le=10.0),
) -> str:
    """Return the most recent HTTP >= 400 error entries in the selected run's immutable log snapshot."""
    result = invoke_sandbox_tool(
        tool_name="recent_http_errors",
        run_id=run_id,
        limit=limit,
        snapshot_id=snapshot_id,
        timeout_s=timeout_s,
    )
    return json.dumps(result)


if __name__ == "__main__":
    mcp.run(transport="stdio")
