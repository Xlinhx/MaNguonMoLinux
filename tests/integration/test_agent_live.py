from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from backend.app.agent import AgentService, has_consistent_404_counts, resolve_model_adapter_from_env
from backend.app.config import Settings
from backend.app.models import RequestRecord, Run
from backend.app.snapshots import create_immutable_snapshot
from backend.app.store import Store


def assert_live_agent_404_answer(
    msg,
    *,
    expected_run_id: str,
    expected_snapshot_id: str,
    expected_revision: int,
    expected_404: int,
    expected_total: int,
) -> None:
    assert msg.status == "completed"
    assert msg.run_id == expected_run_id
    assert msg.snapshot_id == expected_snapshot_id
    assert msg.revision == expected_revision
    assert len(msg.tool_calls) >= 1
    completed_calls = [tc for tc in msg.tool_calls if tc.status == "completed"]
    assert completed_calls
    count_calls = [tc for tc in completed_calls if tc.tool_name == "count_status_codes"]
    assert len(count_calls) >= 1, f"Expected count_status_codes call, got {[tc.tool_name for tc in completed_calls]}"
    for tc in completed_calls:
        assert tc.tool_name in {"count_status_codes", "top_error_paths", "recent_http_errors"}
        assert tc.run_id == expected_run_id
        assert tc.snapshot_id == expected_snapshot_id
        assert tc.revision == expected_revision
        assert isinstance(tc.payload, dict)
        if tc.tool_name == "count_status_codes":
            assert tc.payload.get("total_requests") == expected_total
            assert tc.payload.get("status_404") == expected_404
    status_200 = count_calls[-1].payload.get("status_200")
    assert has_consistent_404_counts(
        msg.content,
        status_404=expected_404,
        total_requests=expected_total,
        status_200=status_200 if isinstance(status_200, int) else None,
    ), f"Inconsistent or inverted 404/total counts in {msg.content!r}"


@pytest.mark.live_llm
def test_live_agent_with_real_mcp_and_docker_3_of_10_then_4_of_11(tmp_path: Path) -> None:
    adapter = resolve_model_adapter_from_env()
    if adapter is None:
        pytest.skip("No live LLM API key configured (DEEPSEEK_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY).")

    base = Settings.from_env()
    runtime = tmp_path / "runtime"
    cfg = Settings(
        project_root=base.project_root,
        runtime_dir=runtime,
        db_path=runtime / "db" / "live_agent.db",
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

    agent = AgentService(store=store, settings=cfg)

    msg1 = asyncio.run(agent.answer_question(run_id=run_id, question="How many 404s are in this run?"))
    assert_live_agent_404_answer(
        msg1,
        expected_run_id=run_id,
        expected_snapshot_id=snap1.id,
        expected_revision=1,
        expected_404=3,
        expected_total=10,
    )

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

    msg2 = asyncio.run(agent.answer_question(run_id=run_id, question="How many 404s are in this run?"))
    assert_live_agent_404_answer(
        msg2,
        expected_run_id=run_id,
        expected_snapshot_id=snap2.id,
        expected_revision=2,
        expected_404=4,
        expected_total=11,
    )
