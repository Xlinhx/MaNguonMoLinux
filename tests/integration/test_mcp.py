from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.mcp_client import McpLabClient
from backend.app.models import RequestRecord, Run
from backend.app.sandbox import resolve_docker_bin
from backend.app.snapshots import create_immutable_snapshot
from backend.app.store import Store


def _docker_and_image_ready(image: str) -> bool:
    docker_bin = resolve_docker_bin()
    p1 = subprocess.run([docker_bin, "info"], capture_output=True, text=True, check=False)
    if p1.returncode != 0:
        return False
    p2 = subprocess.run([docker_bin, "image", "inspect", image], capture_output=True, text=True, check=False)
    return p2.returncode == 0


def test_real_mcp_discovery_and_all_three_tools_with_docker_worker(tmp_path: Path) -> None:
    base = Settings.from_env()
    if not _docker_and_image_ready(base.worker_image):
        pytest.skip(f"Docker or worker image '{base.worker_image}' is not available.")

    runtime = tmp_path / "runtime"
    cfg = Settings(
        project_root=base.project_root,
        runtime_dir=runtime,
        db_path=runtime / "db" / "mcp_test.db",
        nginx_log_path=runtime / "nginx-logs" / "access.jsonl",
        snapshots_dir=runtime / "snapshots",
        nginx_url=base.nginx_url,
        test_service_url=base.test_service_url,
        test_service_host_url=base.test_service_host_url,
        docker_network=base.docker_network,
        worker_image=base.worker_image,
        api_host=base.api_host,
        api_port=base.api_port,
    )
    cfg.ensure_dirs()
    store = Store(cfg.db_path)

    run_id = str(uuid.uuid4())
    run = store.insert_run(
        Run(
            id=run_id,
            display_number=1,
            label="Run 001",
            created_at="2026-11-18T11:00:00Z",
            status="ready",
            revision=1,
            request_count=10,
            ok_count=7,
            not_found_count=3,
        )
    )
    recs_v1 = [
        RequestRecord(
            id=f"req-{i:02d}",
            run_id=run_id,
            seq=i,
            timestamp=f"2026-11-18T11:00:{i:02d}Z",
            display_time=f"11:00:{i:02d}",
            method="GET",
            path="/missing" if i in (4, 7, 10) else "/demo",
            status=404 if i in (4, 7, 10) else 200,
            status_text="404 Not Found" if i in (4, 7, 10) else "200 OK",
        )
        for i in range(1, 11)
    ]
    snap1 = create_immutable_snapshot(store, cfg.snapshots_dir, run_id, 1, recs_v1)
    run.snapshot_id = snap1.id
    store.update_run(run)

    client = McpLabClient(cfg)

    async def _exercise() -> None:
        tools = await client.list_tools()
        names = [t["name"] for t in tools]
        assert set(names) == {"count_status_codes", "top_error_paths", "recent_http_errors"}

        res_count = await client.call_tool("count_status_codes", {"run_id": run_id})
        assert res_count["status"] == "completed"
        assert res_count["revision"] == 1
        assert res_count["payload"]["total_requests"] == 10
        assert res_count["payload"]["status_200"] == 7
        assert res_count["payload"]["status_404"] == 3

        res_top = await client.call_tool("top_error_paths", {"run_id": run_id, "limit": 5})
        assert res_top["status"] == "completed"
        assert res_top["payload"]["paths"] == [{"path": "/missing", "count": 3}]

        res_recent = await client.call_tool("recent_http_errors", {"run_id": run_id, "limit": 2})
        assert res_recent["status"] == "completed"
        assert [e["seq"] for e in res_recent["payload"]["errors"]] == [10, 7]

        # Advance snapshot to Revision 2 (11/7/4) and verify real MCP tool picks up 4/11
        recs_v2 = recs_v1 + [
            RequestRecord(
                id="req-11",
                run_id=run_id,
                seq=11,
                timestamp="2026-11-18T11:00:11Z",
                display_time="11:00:11",
                method="GET",
                path="/missing",
                status=404,
                status_text="404 Not Found",
            )
        ]
        snap2 = create_immutable_snapshot(store, cfg.snapshots_dir, run_id, 2, recs_v2)
        run.revision = 2
        run.snapshot_id = snap2.id
        run.request_count = 11
        run.not_found_count = 4
        store.update_run(run)

        res_count_v2 = await client.call_tool("count_status_codes", {"run_id": run_id})
        assert res_count_v2["revision"] == 2
        assert res_count_v2["payload"]["total_requests"] == 11
        assert res_count_v2["payload"]["status_404"] == 4

    asyncio.run(_exercise())
