from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest

from backend.app.agent import AgentService, ModelTurn
from backend.app.mcp_server import invoke_sandbox_tool
from backend.app.models import RequestRecord, Run
from backend.app.snapshots import create_immutable_snapshot
from backend.app.store import Store
from backend.app.config import Settings
from worker.main import act_recent_http_errors, act_top_error_paths


def _make_settings(tmp_path: Path) -> Settings:
    runtime = tmp_path / "runtime"
    cfg = Settings(
        project_root=tmp_path,
        runtime_dir=runtime,
        db_path=runtime / "db" / "test.db",
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
    return cfg


def test_mcp_tool_validation_rejects_bad_inputs(tmp_path: Path) -> None:
    cfg = _make_settings(tmp_path)
    with pytest.raises(ValueError, match="INVALID_RUN_ID"):
        invoke_sandbox_tool(tool_name="count_status_codes", run_id="../etc/passwd", settings=cfg)

    with pytest.raises(ValueError, match="RUN_NOT_FOUND"):
        invoke_sandbox_tool(tool_name="count_status_codes", run_id=str(uuid.uuid4()), settings=cfg)

    # Create ready run and verify invalid snapshot_id or unknown action fails safely
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
        )
    )
    snap = create_immutable_snapshot(
        store,
        cfg.snapshots_dir,
        run_id,
        1,
        [
            RequestRecord(
                id="r1",
                run_id=run_id,
                seq=1,
                timestamp="2026-11-18T11:00:00Z",
                display_time="11:00:00",
                method="GET",
                path="/missing?q=$(echo PWNED)",
                status=404,
                status_text="404 Not Found",
            )
        ],
    )
    run.snapshot_id = snap.id
    store.update_run(run)

    with pytest.raises(ValueError, match="INVALID_SNAPSHOT_ID"):
        invoke_sandbox_tool(
            tool_name="count_status_codes",
            run_id=run_id,
            snapshot_id="../../secret",
            settings=cfg,
        )

    res = invoke_sandbox_tool(
        tool_name="rm_rf_root",
        run_id=run_id,
        snapshot_id=snap.id,
        settings=cfg,
    )
    assert res["status"] == "failed"
    assert res["error_code"] == "FORBIDDEN_TOOL_ACTION"


def test_worker_treats_shell_metacharacters_in_logs_as_plain_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log_file = tmp_path / "access.jsonl"
    log_file.write_text(
        json.dumps(
            {
                "seq": 1,
                "request_id": "id-1",
                "method": "GET",
                "path": "/missing?cmd=$(id);`uname -a`",
                "status": 404,
                "timestamp": "2026-11-18T11:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("worker.main.INPUT_LOG", log_file)
    assert act_top_error_paths(5) == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1["paths"][0]["path"] == "/missing?cmd=$(id);`uname -a`"

    assert act_recent_http_errors(5) == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["errors"][0]["path"] == "/missing?cmd=$(id);`uname -a`"


def test_agent_loop_snapshot_pinning_max_calls_and_missing_key(tmp_path: Path) -> None:
    cfg = _make_settings(tmp_path)
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
    snap1 = create_immutable_snapshot(store, cfg.snapshots_dir, run_id, 1, recs_v1)
    run.snapshot_id = snap1.id
    store.update_run(run)

    # 1. Missing credential must return model_unavailable without fabricating answer
    no_key_agent = AgentService(store=store, settings=cfg, model_adapter_factory=lambda: None)
    ans_no_key = asyncio.run(no_key_agent.answer_question(run_id=run_id, question="How many 404s?"))
    assert ans_no_key.status == "model_unavailable"
    assert ans_no_key.error_code == "MODEL_UNAVAILABLE"

    # 2. Fake unit-test adapter exercising tool call + snapshot pinning across revision 1 -> 2
    class FakeAdapter:
        async def next_turn(self, *, question: str, run_id: str, revision: int, history: list, timeout_s: float) -> ModelTurn:
            if not history:
                return ModelTurn(
                    tool_calls=[{"id": "tc-1", "name": "count_status_codes", "arguments": {"run_id": run_id}}]
                )
            tool_payload = json.loads(history[-1]["content"])["payload"]
            return ModelTurn(
                content=f"{tool_payload['status_404']} of {tool_payload['total_requests']} requests returned HTTP 404."
            )

    async def fake_mcp(tool_name: str, args: dict) -> dict:
        snap_obj = store.get_snapshot(args["snapshot_id"])
        assert snap_obj is not None
        lines = [json.loads(line) for line in Path(snap_obj.file_path).read_text(encoding="utf-8").splitlines() if line]
        n404 = sum(1 for r in lines if r["status"] == 404)
        n200 = sum(1 for r in lines if r["status"] == 200)
        return {
            "call_id": str(uuid.uuid4()),
            "tool_name": tool_name,
            "args": args,
            "run_id": run_id,
            "snapshot_id": snap_obj.id,
            "revision": snap_obj.revision,
            "payload": {
                "total_requests": len(lines),
                "status_200": n200,
                "status_404": n404,
                "snapshot_sha256": snap_obj.sha256,
            },
            "duration_ms": 12,
            "status": "completed",
            "created_at": "2026-11-18T11:00:00Z",
        }

    agent = AgentService(
        store=store,
        settings=cfg,
        model_adapter_factory=lambda: FakeAdapter(),
        mcp_caller=fake_mcp,
    )
    msg1 = asyncio.run(agent.answer_question(run_id=run_id, question="How many 404s are in this run?"))
    assert msg1.status == "completed"
    assert msg1.revision == 1
    assert "3 of 10" in msg1.content

    # Advance run to Revision 2 (11/7/4)
    recs_v2 = recs_v1 + [
        RequestRecord(
            id="r-11",
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
    assert msg2.status == "completed"
    assert msg2.revision == 2
    assert "4 of 11" in msg2.content
    # Old message remains pinned to Revision 1
    history_msgs = store.list_agent_messages(run_id)
    assistant_msgs = [m for m in history_msgs if m.role == "assistant" and m.status == "completed"]
    assert assistant_msgs[0].revision == 1 and assistant_msgs[0].snapshot_id == snap1.id
    assert assistant_msgs[1].revision == 2 and assistant_msgs[1].snapshot_id == snap2.id

    # 3. Max 4 tool calls guardrail
    class LoopingAdapter:
        async def next_turn(self, *, question: str, run_id: str, revision: int, history: list, timeout_s: float) -> ModelTurn:
            return ModelTurn(tool_calls=[{"id": str(uuid.uuid4()), "name": "count_status_codes", "arguments": {"run_id": run_id}}])

    loop_agent = AgentService(
        store=store,
        settings=cfg,
        model_adapter_factory=lambda: LoopingAdapter(),
        mcp_caller=fake_mcp,
    )
    msg_loop = asyncio.run(loop_agent.answer_question(run_id=run_id, question="Loop forever"))
    assert msg_loop.status == "failed"
    assert len(msg_loop.tool_calls) == 4


def test_mcp_and_agent_reject_tampered_snapshot_digest_for_all_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.agent import AgentService, ModelTurn
    from backend.app.mcp_server import invoke_sandbox_tool
    from backend.app.sandbox import WorkerExecutionResult

    cfg = _make_settings(tmp_path)
    cfg.ensure_dirs()
    store = Store(cfg.db_path)
    run_id = str(uuid.uuid4())
    store.insert_run(Run(id=run_id, display_number=1, label="Run 001", created_at="2026-11-19T00:00:00Z", status="ready", revision=0))
    recs = [
        RequestRecord(id=f"r-{i}", run_id=run_id, seq=i, timestamp="2026-11-19T00:00:00Z", display_time="00:00:00", method="GET", path="/missing" if i <= 3 else "/demo", status=404 if i <= 3 else 200, status_text="404 Not Found" if i <= 3 else "200 OK")
        for i in range(1, 11)
    ]
    snap = create_immutable_snapshot(store, cfg.snapshots_dir, run_id, 1, recs)
    run = store.get_run(run_id)
    assert run is not None
    run.revision = 1
    run.snapshot_id = snap.id
    run.request_count = 10
    run.ok_count = 7
    run.not_found_count = 3
    store.update_run(run)

    # 1. Tamper snapshot on disk -> invoke_sandbox_tool must fail with SNAPSHOT_INTEGRITY_MISMATCH
    snap_path = Path(snap.file_path)
    orig_text = snap_path.read_text(encoding="utf-8")
    tampered_text = orig_text.replace('"status":200', '"status":404', 1)
    assert tampered_text != orig_text
    snap_path.write_text(tampered_text, encoding="utf-8")
    for tool_name in ("count_status_codes", "top_error_paths", "recent_http_errors"):
        out = invoke_sandbox_tool(tool_name=tool_name, run_id=run_id, snapshot_id=snap.id, settings=cfg)
        assert out["status"] == "failed"
        assert out["error_code"] == "SNAPSHOT_INTEGRITY_MISMATCH"
        assert out["payload"] is None

    # Restore disk snapshot, but simulate worker/execute_tool returning tampered digest payload -> rejected for all 3 tools
    snap_path.write_text(orig_text, encoding="utf-8")
    for tool_name in ("count_status_codes", "top_error_paths", "recent_http_errors"):
        monkeypatch.setattr(
            "backend.app.mcp_server.execute_tool",
            lambda **kw: WorkerExecutionResult(
                call_id=str(uuid.uuid4()),
                container_name="w",
                action=kw["action"],
                args={},
                snapshot_id=snap.id,
                exit_code=0,
                stdout="{}",
                stderr="",
                payload={"total_requests": 10, "status_404": 4, "snapshot_sha256": "0" * 64},
                duration_ms=5,
                timed_out=False,
                infra_error=False,
                cleanup_ok=True,
            ),
        )
        out = invoke_sandbox_tool(tool_name=tool_name, run_id=run_id, snapshot_id=snap.id, settings=cfg)
        assert out["status"] == "failed"
        assert out["error_code"] == "SNAPSHOT_INTEGRITY_MISMATCH"
        assert out["payload"] is None

    # 2. AgentService also rejects mismatched digest from mcp_caller (never publishes verified answer)
    class ProbeAdapter:
        async def next_turn(self, *, history, **kwargs) -> ModelTurn:
            if not history:
                return ModelTurn(tool_calls=[{"id": "c1", "name": "count_status_codes", "arguments": {"run_id": run_id}}])
            return ModelTurn(content="4 of 10 requests returned 404")

    async def tampered_mcp(tool_name: str, args: dict) -> dict:
        return {
            "call_id": str(uuid.uuid4()),
            "tool_name": tool_name,
            "args": args,
            "run_id": run_id,
            "snapshot_id": snap.id,
            "revision": 1,
            "payload": {"total_requests": 10, "status_200": 6, "status_404": 4, "snapshot_sha256": "bad" + "0" * 61},
            "duration_ms": 5,
            "status": "completed",
            "created_at": "2026-11-19T00:00:00Z",
        }

    agent = AgentService(store=store, settings=cfg, model_adapter_factory=lambda: ProbeAdapter(), mcp_caller=tampered_mcp)
    msg = asyncio.run(agent.answer_question(run_id=run_id, question="How many 404s?"))
    assert msg.status == "failed"
    assert msg.error_code == "TOOL_OR_AGENT_FAILED"
    persisted = [tc for m in store.list_agent_messages(run_id) for tc in m.tool_calls if tc.status == "completed"]
    assert all(isinstance(tc.payload, dict) and tc.payload.get("snapshot_sha256") == snap.sha256 for tc in persisted)


def test_live_answer_matcher_rejects_partial_number_counterexample() -> None:
    from backend.app.models import AgentMessage, ToolResult
    from tests.integration.test_agent_live import assert_live_agent_404_answer

    run_id = str(uuid.uuid4())
    snap_id = str(uuid.uuid4())
    tc = ToolResult(
        call_id=str(uuid.uuid4()),
        tool_name="count_status_codes",
        args={"run_id": run_id},
        run_id=run_id,
        snapshot_id=snap_id,
        revision=1,
        payload={"total_requests": 10, "status_200": 7, "status_404": 3},
        duration_ms=5,
        status="completed",
        created_at="2026-11-19T00:00:00Z",
    )
    bad_msg = AgentMessage(
        id=str(uuid.uuid4()),
        run_id=run_id,
        role="assistant",
        content="34 out of 110 requests returned 404.",
        snapshot_id=snap_id,
        revision=1,
        tool_call_ids=[tc.call_id],
        tool_calls=[tc],
        status="completed",
        created_at="2026-11-19T00:00:00Z",
    )
    with pytest.raises(AssertionError):
        assert_live_agent_404_answer(
            bad_msg,
            expected_run_id=run_id,
            expected_snapshot_id=snap_id,
            expected_revision=1,
            expected_404=3,
            expected_total=10,
        )

    good_msg = bad_msg.model_copy(update={"content": "3 of 10 requests returned HTTP 404."})
    assert_live_agent_404_answer(
        good_msg,
        expected_run_id=run_id,
        expected_snapshot_id=snap_id,
        expected_revision=1,
        expected_404=3,
        expected_total=10,
    )


def test_mcp_client_strips_llm_api_keys_from_stdio_subprocess_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.mcp_client import LLM_SECRET_ENV_KEYS, McpLabClient

    cfg = _make_settings(tmp_path)
    sentinels = {
        "DEEPSEEK_API_KEY": "sk-sentinel-deepseek-secret-value-1234",
        "OPENAI_API_KEY": "sk-sentinel-openai-secret-value-5678",
        "GEMINI_API_KEY": "AIzaSentinelGeminiKey1234567890",
        "ANTHROPIC_API_KEY": "sk-ant-sentinel-key-9999",
        "OPENROUTER_API_KEY": "sk-or-sentinel-key-0000",
    }
    for k, v in sentinels.items():
        monkeypatch.setenv(k, v)

    client = McpLabClient(cfg)
    params = client._server_params(job_id="job-env-check")
    assert params.env is not None
    assert params.env.get("SANDBOX_LAB_JOB_ID") == "job-env-check"
    assert params.env.get("SANDBOX_LAB_RUNTIME_DIR") == str(cfg.runtime_dir)
    for k in LLM_SECRET_ENV_KEYS:
        assert k not in params.env
    serialized = json.dumps(params.env)
    for v in sentinels.values():
        assert v not in serialized
