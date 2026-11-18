from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.agent import AgentService, ModelTurn, OpenAICompatibleAdapter, resolve_model_adapter_from_env
from backend.app.api import create_app
from backend.app.config import Settings
from backend.app.models import RequestRecord, Run
from backend.app.snapshots import create_immutable_snapshot
from backend.app.store import Store


def _seed_agent_run(tmp_path: Path) -> tuple[Settings, Store, Run, str]:
    runtime = tmp_path / "runtime"
    cfg = Settings(
        project_root=tmp_path,
        runtime_dir=runtime,
        db_path=runtime / "db" / "agent_reg.db",
        nginx_log_path=runtime / "nginx-logs" / "access.jsonl",
        snapshots_dir=runtime / "snapshots",
        nginx_url="http://127.0.0.1:18080",
        test_service_url="http://sandbox-lab-test-service:8081/ping",
        test_service_host_url="http://127.0.0.1:18081/ping",
        docker_network="sandbox_lab_net",
        worker_image="sandbox-lab-worker:v1",
        api_host="127.0.0.1",
        api_port=18000,
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
    records = [
        RequestRecord(
            id=f"r-{i}",
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
    snap = create_immutable_snapshot(store, cfg.snapshots_dir, run_id, 1, records)
    run.snapshot_id = snap.id
    store.update_run(run)
    return cfg, store, run, snap.id


def test_agent_rejects_direct_answer_without_mcp_tool_call(tmp_path: Path) -> None:
    cfg, store, run, _ = _seed_agent_run(tmp_path)

    class DirectGuessAdapter:
        async def next_turn(self, **kwargs) -> ModelTurn:
            return ModelTurn(content="There are 999 404s (guessed without MCP).")

    agent = AgentService(
        store=store,
        settings=cfg,
        model_adapter_factory=lambda: DirectGuessAdapter(),
    )
    msg = asyncio.run(agent.answer_question(run_id=run.id, question="How many 404s are in this run?"))
    assert msg.status == "failed"
    assert msg.error_code == "MCP_TOOL_REQUIRED"
    assert msg.tool_calls == []


def test_agent_enforces_deadline_when_model_or_mcp_exceeds_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, store, run, snap_id = _seed_agent_run(tmp_path)
    cleanup_calls: list[str] = []

    monkeypatch.setattr("backend.app.agent.QUESTION_DEADLINE_S", 0.15)
    monkeypatch.setattr(
        "backend.app.agent.cleanup_job_containers",
        lambda docker_bin, job_id: cleanup_calls.append(job_id) or True,
    )

    class SlowModelAdapter:
        async def next_turn(self, **kwargs) -> ModelTurn:
            await asyncio.sleep(0.35)
            return ModelTurn(content="Late response")

    slow_model_agent = AgentService(
        store=store,
        settings=cfg,
        model_adapter_factory=lambda: SlowModelAdapter(),
        deadline_s=0.2,
    )
    msg1 = asyncio.run(slow_model_agent.answer_question(run_id=run.id, question="How many 404s?", job_id="job-slow-model"))
    assert msg1.status == "timed_out"
    assert msg1.error_code == "QUESTION_TIMEOUT"
    assert "job-slow-model" in cleanup_calls

    class ToolOnceAdapter:
        async def next_turn(self, *, history, **kwargs) -> ModelTurn:
            if not history:
                return ModelTurn(tool_calls=[{"id": "c1", "name": "count_status_codes", "arguments": {"run_id": run.id}}])
            return ModelTurn(content="Done")

    async def slow_mcp(tool_name: str, args: dict) -> dict:
        await asyncio.sleep(0.35)
        return {"call_id": "c1", "tool_name": tool_name, "args": args, "run_id": run.id, "snapshot_id": snap_id, "revision": 1, "status": "completed", "payload": {}}

    slow_mcp_agent = AgentService(
        store=store,
        settings=cfg,
        model_adapter_factory=lambda: ToolOnceAdapter(),
        mcp_caller=slow_mcp,
        deadline_s=0.2,
    )
    msg2 = asyncio.run(slow_mcp_agent.answer_question(run_id=run.id, question="How many 404s?", job_id="job-slow-mcp"))
    assert msg2.status == "timed_out"
    assert "job-slow-mcp" in cleanup_calls


def test_cancel_job_waits_for_cleanup_and_reports_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, store, run, snap_id = _seed_agent_run(tmp_path)

    cleanup_events: list[tuple[str, bool]] = []
    allow_cleanup_return = True

    def fake_cleanup(docker_bin, job_id: str) -> bool:
        cleanup_events.append((job_id, allow_cleanup_return))
        return allow_cleanup_return

    monkeypatch.setattr("backend.app.api.cleanup_job_containers", fake_cleanup)
    monkeypatch.setattr("backend.app.agent.cleanup_job_containers", fake_cleanup)

    class BlockingAdapter:
        def __init__(self) -> None:
            self.calls = 0

        async def next_turn(self, *, history, **kwargs) -> ModelTurn:
            self.calls += 1
            if "blocker" in kwargs["question"]:
                await asyncio.sleep(5.0)
            if not history:
                return ModelTurn(tool_calls=[{"id": "c1", "name": "count_status_codes", "arguments": {"run_id": run.id}}])
            return ModelTurn(content="3 of 10 requests returned HTTP 404.")

    async def ok_mcp(tool_name: str, args: dict) -> dict:
        snap_obj = store.get_snapshot(snap_id)
        assert snap_obj is not None
        return {
            "call_id": str(uuid.uuid4()),
            "tool_name": tool_name,
            "args": args,
            "run_id": run.id,
            "snapshot_id": snap_id,
            "revision": 1,
            "payload": {
                "total_requests": 10,
                "status_200": 7,
                "status_404": 3,
                "snapshot_sha256": snap_obj.sha256,
            },
            "duration_ms": 5,
            "status": "completed",
            "created_at": "2026-11-18T11:00:00Z",
        }

    agent = AgentService(
        store=store,
        settings=cfg,
        model_adapter_factory=lambda: BlockingAdapter(),
        mcp_caller=ok_mcp,
    )
    app = create_app(settings=cfg, agent_service=agent)
    with TestClient(app) as client:
        job = client.post(f"/api/runs/{run.id}/questions", json={"question": "Run blocker task"}).json()
        cancelled = client.post(f"/api/jobs/{job['job_id']}/cancel").json()
        assert cancelled["status"] == "cancelled"
        assert any(jid == job["job_id"] for jid, _ in cleanup_events)

        # Follow-up question works cleanly after cancellation
        followup = client.post(f"/api/runs/{run.id}/questions", json={"question": "How many 404s?"}).json()
        latest = client.get(f"/api/jobs/{followup['job_id']}").json()
        assert latest["status"] in ("completed", "running")

        # Simulate cleanup failure -> cancel must NOT falsely claim cancelled
        allow_cleanup_return = False
        job2 = client.post(f"/api/runs/{run.id}/questions", json={"question": "Run blocker 2"}).json()
        failed_cancel = client.post(f"/api/jobs/{job2['job_id']}/cancel").json()
        assert failed_cancel["status"] == "failed"
        assert failed_cancel["error_code"] == "CLEANUP_FAILED"


def test_deepseek_provider_selection_no_silent_fallback_and_tool_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_LAB_LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-fallback")
    assert resolve_model_adapter_from_env() is None

    captured_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_payloads.append(body)
        if len(captured_payloads) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_ds_1",
                                        "type": "function",
                                        "function": {
                                            "name": "count_status_codes",
                                            "arguments": json.dumps({"run_id": "run-1"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "3 of 10"}}]})

    orig_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "backend.app.agent.httpx.AsyncClient",
        lambda *a, **k: orig_async_client(transport=httpx.MockTransport(handler), **{k2: v2 for k2, v2 in k.items() if k2 != "transport"}),
    )

    adapter = OpenAICompatibleAdapter(
        api_key="test-key",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        extra_body={"thinking": {"type": "disabled"}},
    )
    t1 = asyncio.run(adapter.next_turn(question="How many 404s?", run_id="run-1", revision=1, history=[], timeout_s=5.0))
    assert t1.tool_calls is not None and t1.tool_calls[0]["name"] == "count_status_codes"
    assert captured_payloads[0]["model"] == "deepseek-flash"
    assert captured_payloads[0]["thinking"] == {"type": "disabled"}

    t2 = asyncio.run(
        adapter.next_turn(
            question="How many 404s?",
            run_id="run-1",
            revision=1,
            history=[
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_ds_1",
                            "type": "function",
                            "function": {
                                "name": "count_status_codes",
                                "arguments": json.dumps({"run_id": "run-1"}),
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_ds_1", "name": "count_status_codes", "content": json.dumps({"payload": {"status_404": 3}})},
            ],
            timeout_s=5.0,
        )
    )
    assert t2.content == "3 of 10"


def test_agent_prevents_inverted_numbers_and_enforces_count_tool(tmp_path: Path) -> None:
    from backend.app.agent import has_consistent_404_counts
    from tests.integration.test_agent_live import assert_live_agent_404_answer

    cfg, store, run, snap_id = _seed_agent_run(tmp_path)
    snap = store.get_snapshot(snap_id)
    assert snap is not None

    def make_mcp(tool_override: str | None = None):
        async def fake_mcp(tool_name: str, args: dict):
            chosen = tool_override or tool_name
            if chosen == "count_status_codes":
                payload = {"total_requests": 10, "status_200": 7, "status_404": 3, "snapshot_sha256": snap.sha256}
            else:
                payload = {"limit": 5, "paths": [{"path": "/missing", "count": 3}], "errors": [{"seq": 10, "path": "/missing", "status": 404}], "snapshot_sha256": snap.sha256}
            return {
                "call_id": str(uuid.uuid4()),
                "tool_name": chosen,
                "args": args,
                "run_id": run.id,
                "snapshot_id": snap.id,
                "revision": 1,
                "payload": payload,
                "duration_ms": 8,
                "status": "completed",
                "created_at": "2026-11-19T00:00:00Z",
            }
        return fake_mcp

    # 1. Inverted model text "10 out of 3 requests returned HTTP 404." must NEVER reach saved completed answer!
    class InvertedAdapter:
        async def next_turn(self, *, history, **kwargs) -> ModelTurn:
            if not history:
                return ModelTurn(tool_calls=[{"id": "c1", "name": "count_status_codes", "arguments": {"run_id": run.id}}])
            return ModelTurn(content="10 out of 3 requests returned HTTP 404.")

    agent_inv = AgentService(store=store, settings=cfg, model_adapter_factory=lambda: InvertedAdapter(), mcp_caller=make_mcp())
    msg_inv = asyncio.run(agent_inv.answer_question(run_id=run.id, question="How many 404s are in this run?"))
    assert msg_inv.status == "completed"
    assert "10 out of 3" not in msg_inv.content
    assert "3 of 10" in msg_inv.content
    assert has_consistent_404_counts(msg_inv.content, status_404=3, total_requests=10, status_200=7)
    assert_live_agent_404_answer(
        msg_inv,
        expected_run_id=run.id,
        expected_snapshot_id=snap.id,
        expected_revision=1,
        expected_404=3,
        expected_total=10,
    )

    # 2. Valid natural phrasings remain intact (including markdown bold & reversed "out of 10 ... 3")
    for valid_phrase in (
        "There are **3** 404s in this run (out of 10 total, Revision 1).",
        "Out of 10 requests, 3 returned 404 and 7 returned 200.",
    ):
        class ValidAdapter:
            def __init__(self, text: str) -> None:
                self.text = text
            async def next_turn(self, *, history, **kwargs) -> ModelTurn:
                if not history:
                    return ModelTurn(tool_calls=[{"id": "c2", "name": "count_status_codes", "arguments": {"run_id": run.id}}])
                return ModelTurn(content=self.text)

        ag_ok = AgentService(store=store, settings=cfg, model_adapter_factory=lambda t=valid_phrase: ValidAdapter(t), mcp_caller=make_mcp())
        res_ok = asyncio.run(ag_ok.answer_question(run_id=run.id, question="How many 404s are in this run?"))
        assert res_ok.content == valid_phrase
        assert_live_agent_404_answer(
            res_ok,
            expected_run_id=run.id,
            expected_snapshot_id=snap.id,
            expected_revision=1,
            expected_404=3,
            expected_total=10,
        )

    # 3. Aggregate count question calling ONLY recent_http_errors -> fail closed (MCP_COUNT_TOOL_REQUIRED)
    class WrongToolAdapter:
        async def next_turn(self, *, history, **kwargs) -> ModelTurn:
            if not history:
                return ModelTurn(tool_calls=[{"id": "c3", "name": "recent_http_errors", "arguments": {"run_id": run.id, "limit": 5}}])
            return ModelTurn(content="3 of 10 requests returned HTTP 404.")

    ag_wrong_tool = AgentService(store=store, settings=cfg, model_adapter_factory=lambda: WrongToolAdapter(), mcp_caller=make_mcp())
    res_wrong = asyncio.run(ag_wrong_tool.answer_question(run_id=run.id, question="How many 404s are in this run?"))
    assert res_wrong.status == "failed" and res_wrong.error_code == "MCP_COUNT_TOOL_REQUIRED"

    # 4. Open error-path question using top_error_paths works without count_status_codes
    class PathAdapter:
        async def next_turn(self, *, history, **kwargs) -> ModelTurn:
            if not history:
                return ModelTurn(tool_calls=[{"id": "c4", "name": "top_error_paths", "arguments": {"run_id": run.id, "limit": 5}}])
            return ModelTurn(content="Top error path is /missing with 3 errors.")

    ag_path = AgentService(store=store, settings=cfg, model_adapter_factory=lambda: PathAdapter(), mcp_caller=make_mcp())
    res_path = asyncio.run(ag_path.answer_question(run_id=run.id, question="Which paths have the highest error count?"))
    assert res_path.status == "completed" and "/missing" in res_path.content

    # 5. Live matcher explicitly rejects "10 out of 3", "10 of the 3 requests", and "Out of 10 404s, 3 total."
    for bad_phrase in (
        "10 out of 3 requests returned HTTP 404.",
        "10 of the 3 requests returned HTTP 404.",
        "Out of 10 404s, 3 total.",
    ):
        bad_msg = msg_inv.model_copy(update={"content": bad_phrase})
        with pytest.raises(AssertionError):
            assert_live_agent_404_answer(
                bad_msg,
                expected_run_id=run.id,
                expected_snapshot_id=snap.id,
                expected_revision=1,
                expected_404=3,
                expected_total=10,
            )
