from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.models import RequestRecord, Run
from backend.app.sandbox import (
    ControlOverride,
    SandboxPolicyError,
    build_docker_run_argv,
    execute_tool,
    validate_action_and_args,
)
from backend.app.snapshots import create_immutable_snapshot
from backend.app.store import Store


def _make_settings(tmp_path: Path) -> Settings:
    runtime = tmp_path / "runtime"
    return Settings(
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


def test_build_docker_run_argv_enforces_policy(tmp_path: Path) -> None:
    snap_file = tmp_path / "access.jsonl"
    snap_file.write_text("{}\n", encoding="utf-8")
    argv = build_docker_run_argv(
        docker_bin="docker",
        worker_image="sandbox-lab-worker:v1",
        container_name="sandbox-lab-worker-abc",
        call_id="call-1",
        mount_source=snap_file,
        action_argv=["count_status_codes"],
    )
    joined = " ".join(argv)
    assert "--user 10001:10001" in joined
    assert "--read-only" in joined
    assert "--network none" in joined
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges:true" in joined
    assert "--pids-limit 64" in joined
    assert "--memory 128m" in joined
    assert "--cpus 0.5" in joined
    assert "readonly" in joined
    assert ":ro" not in joined
    assert "/var/run/docker.sock" not in joined


def test_validate_rejects_unknown_action_and_out_of_range_limits() -> None:
    with pytest.raises(SandboxPolicyError) as exc:
        validate_action_and_args("bash")
    assert exc.value.code == "UNSUPPORTED_ACTION"

    with pytest.raises(SandboxPolicyError):
        validate_action_and_args("top_error_paths", limit=21)

    with pytest.raises(SandboxPolicyError):
        validate_action_and_args("recent_http_errors", limit=0)


def test_execute_tool_rejects_check_actions_and_bad_snapshot(tmp_path: Path) -> None:
    cfg = _make_settings(tmp_path)
    cfg.ensure_dirs()
    store = Store(cfg.db_path)

    with pytest.raises(SandboxPolicyError) as exc:
        execute_tool(store=store, settings=cfg, action="check_write_log", snapshot_id=str(uuid.uuid4()))
    assert exc.value.code == "FORBIDDEN_TOOL_ACTION"

    with pytest.raises(SandboxPolicyError) as exc2:
        execute_tool(store=store, settings=cfg, action="count_status_codes", snapshot_id="../etc/passwd")
    assert exc2.value.code == "INVALID_SNAPSHOT_ID"

    # Snapshot path escaping snapshots_dir is rejected
    run_id = str(uuid.uuid4())
    store.insert_run(
        Run(
            id=run_id,
            display_number=1,
            label="Run 001",
            created_at="2026-11-18T11:00:00Z",
            status="ready",
            revision=1,
        )
    )
    evil_file = tmp_path / "outside.jsonl"
    evil_file.write_text("{}", encoding="utf-8")
    snap = create_immutable_snapshot(
        store,
        cfg.snapshots_dir,
        run_id,
        1,
        [
            RequestRecord(
                id="req-1",
                run_id=run_id,
                seq=1,
                timestamp="2026-11-18T11:00:00Z",
                display_time="11:00:00",
                method="GET",
                path="/demo",
                status=200,
                status_text="200 OK",
            )
        ],
    )
    # Tamper stored snapshot path in SQLite to simulate path escape
    with store._conn() as conn:
        conn.execute("UPDATE snapshots SET file_path = ? WHERE id = ?", (str(evil_file), snap.id))
    with pytest.raises(SandboxPolicyError) as exc3:
        execute_tool(store=store, settings=cfg, action="count_status_codes", snapshot_id=snap.id)
    assert exc3.value.code == "INVALID_SNAPSHOT_PATH"


def test_checks_classify_policy_violation_as_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.checks import run_network_check, run_write_log_check
    from backend.app.sandbox import WorkerExecutionResult

    cfg = _make_settings(tmp_path)
    cfg.ensure_dirs()
    store = Store(cfg.db_path)
    run_id = str(uuid.uuid4())
    store.insert_run(
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
                id="req-1",
                run_id=run_id,
                seq=1,
                timestamp="2026-11-18T11:00:00Z",
                display_time="11:00:00",
                method="GET",
                path="/demo",
                status=200,
                status_text="200 OK",
            )
        ],
    )

    def _fake_worker(**kwargs) -> WorkerExecutionResult:
        action = kwargs["action"]
        control = kwargs.get("control")
        if action == "check_write_log":
            if control and control.writable_mount_path is not None:
                control.writable_mount_path.write_text('{"probe":"SANDBOX_WRITE_MARKER"}\n', encoding="utf-8")
            # Deliberately simulate policy failure where sandbox ALSO succeeded writing
            return WorkerExecutionResult(
                call_id="c1",
                container_name="w1",
                action=action,
                args={},
                snapshot_id=snap.id,
                exit_code=0,
                stdout='{"write_succeeded": true, "hash_unchanged": false}',
                stderr="",
                payload={"write_succeeded": True, "hash_unchanged": False, "sha256_after": "changed"},
                duration_ms=5,
                timed_out=False,
                infra_error=False,
                cleanup_ok=True,
            )
        # Deliberately simulate policy failure where sandbox reached network
        return WorkerExecutionResult(
            call_id="c2",
            container_name="w2",
            action=action,
            args={},
            snapshot_id=snap.id,
            exit_code=0,
            stdout='{"reachable": true, "http_response_received": true, "status_code": 200, "body": "{\\"service\\":\\"sandbox-test-service\\",\\"status\\":\\"ok\\"}"}',
            stderr="",
            payload={
                "reachable": True,
                "http_response_received": True,
                "protocol_error": False,
                "status_code": 200,
                "body": '{"service":"sandbox-test-service","status":"ok"}',
            },
            duration_ms=5,
            timed_out=False,
            infra_error=False,
            cleanup_ok=True,
        )

    monkeypatch.setattr("backend.app.checks.run_worker_action", _fake_worker)
    w_res = run_write_log_check(store, cfg, snap)
    assert w_res.verdict == "fail"
    assert w_res.observed == "Allowed"

    n_res = run_network_check(store, cfg, snap)
    assert n_res.verdict == "fail"
    assert n_res.observed == "Allowed"


def test_network_check_and_worker_separate_http_503_404_control_204_and_worker_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import http.server
    import socketserver
    import threading
    from backend.app.checks import run_network_check
    from backend.app.models import RequestRecord, Run
    from backend.app.sandbox import WorkerExecutionResult
    from backend.app.snapshots import create_immutable_snapshot
    from worker.main import act_check_network

    monkeypatch.setattr(os, "getuid", lambda: 10001, raising=False)
    monkeypatch.setattr(os, "getgid", lambda: 10001, raising=False)

    # 1. Verify worker/main.py::act_check_network distinguishes HTTP 503/404/204 from network block
    class MultiStatusHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/ping-503":
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"Service Unavailable")
            elif self.path == "/ping-404":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")
            elif self.path == "/ping-204":
                self.send_response(204)
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"service":"sandbox-test-service","status":"ok"}')

        def log_message(self, *args) -> None:
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), MultiStatusHandler) as srv:
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            import io, contextlib

            def _call(path: str) -> tuple[int, dict]:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = act_check_network(f"http://127.0.0.1:{port}{path}")
                return rc, json.loads(buf.getvalue().strip())

            rc503, p503 = _call("/ping-503")
            assert rc503 == 0 and p503["http_response_received"] is True and p503["status_code"] == 503
            rc404, p404 = _call("/ping-404")
            assert rc404 == 0 and p404["http_response_received"] is True and p404["status_code"] == 404
            rc204, p204 = _call("/ping-204")
            assert rc204 == 0 and p204["http_response_received"] is True and p204["status_code"] == 204
            rc200, p200 = _call("/ping")
            assert rc200 == 0 and p200["status_code"] == 200
        finally:
            srv.shutdown()

    # 2. Verify run_network_check verdicts against mocked worker results
    cfg = _make_settings(tmp_path)
    cfg.ensure_dirs()
    store = Store(cfg.db_path)
    run_id = str(uuid.uuid4())
    store.insert_run(Run(id=run_id, display_number=1, label="Run 001", created_at="2026-11-19T00:00:00Z", status="ready", revision=1))
    snap = create_immutable_snapshot(
        store,
        cfg.snapshots_dir,
        run_id,
        1,
        [RequestRecord(id="r1", run_id=run_id, seq=1, timestamp="2026-11-19T00:00:00Z", display_time="00:00:00", method="GET", path="/demo", status=200, status_text="200 OK")],
    )

    good_ctrl = {"uid": 10001, "gid": 10001, "reachable": True, "http_response_received": True, "protocol_error": False, "status_code": 200, "body": '{"service":"sandbox-test-service","status":"ok"}', "error": ""}

    def _run_case(sb_exit: int, sb_payload: dict | None, ctrl_exit: int = 0, ctrl_payload: dict | None = None, stderr: str = ""):
        def _fake(**kwargs):
            is_ctrl = kwargs.get("control") is not None
            pl = (good_ctrl if ctrl_payload is None else ctrl_payload) if is_ctrl else sb_payload
            return WorkerExecutionResult(
                call_id=str(uuid.uuid4()),
                container_name="w",
                action="check_network",
                args={},
                snapshot_id=snap.id,
                exit_code=ctrl_exit if is_ctrl else sb_exit,
                stdout="",
                stderr="" if is_ctrl else stderr,
                payload=pl,
                duration_ms=5,
                timed_out=False,
                infra_error=False,
                cleanup_ok=True,
            )

        monkeypatch.setattr("backend.app.checks.run_worker_action", _fake)
        return run_network_check(store, cfg, snap)

    # Sandbox HTTP 503/404 with valid control -> FAIL (reached endpoint), keeping real HTTP code
    f503 = _run_case(0, {"reachable": True, "http_response_received": True, "protocol_error": False, "status_code": 503, "body": "unavailable"})
    assert f503.verdict == "fail" and f503.observed == "Allowed" and f503.evidence["sandbox_status_code"] == 503
    f404 = _run_case(0, {"reachable": True, "http_response_received": True, "protocol_error": False, "status_code": 404, "body": "missing"})
    assert f404.verdict == "fail" and f404.observed == "Allowed" and f404.evidence["sandbox_status_code"] == 404

    # Control returns HTTP 204 or non-ping body -> ERROR (never claims HTTP 200 Allowed)
    e204 = _run_case(1, {"reachable": False, "http_response_received": False, "protocol_error": False, "status_code": None}, 0, {"reachable": True, "http_response_received": True, "protocol_error": False, "status_code": 204, "body": ""})
    assert e204.verdict == "error" and e204.control_result["status_code"] == 204 and not str(e204.control_result["summary"]).startswith("Allowed (HTTP 200")

    # Worker crash (payload=None) -> ERROR, never Pass or Allowed
    ec = _run_case(1, None, stderr="Traceback: worker crashed")
    assert ec.verdict == "error" and ec.observed == "Error"

    # True --network none block + valid HTTP 200 /ping control -> PASS
    p_ok = _run_case(1, {"reachable": False, "http_response_received": False, "protocol_error": False, "status_code": None, "error": "URLError: blocked"})
    assert p_ok.verdict == "pass" and p_ok.observed == "Blocked" and p_ok.evidence["sandbox_blocked"] is True

    # Abnormal exit code (e.g., SIGKILL 137) even with valid-looking payload -> ERROR (never Pass · Blocked)
    ab_sb = _run_case(137, {"reachable": False, "http_response_received": False, "protocol_error": False, "status_code": None, "error": "killed"})
    assert ab_sb.verdict == "error" and ab_sb.observed == "Error" and ab_sb.evidence["error_code"] == "WORKER_EXIT_ABNORMAL"
    ab_ctrl = _run_case(1, {"reachable": False, "http_response_received": False, "protocol_error": False, "status_code": None, "error": "URLError: blocked"}, ctrl_exit=137, ctrl_payload=good_ctrl)
    assert ab_ctrl.verdict == "error" and ab_ctrl.observed == "Error" and ab_ctrl.evidence["error_code"] == "WORKER_EXIT_ABNORMAL"


def test_worker_single_pass_hash_and_records_parity(tmp_path: Path, monkeypatch) -> None:
    import contextlib
    import io
    import worker.main as wm

    sample = tmp_path / "access.jsonl"
    raw_bytes = (
        b'{"seq":1,"path":"/demo","status":200,"timestamp":"2026-11-18T11:00:00Z"}\r\n'
        b'\r\n'
        b'not-json-line\r\n'
        b'{"seq":2,"request_id":"req-2","method":"GET","path":"/missing","status":404,"timestamp":"2026-11-18T11:00:01Z"}\n'
    )
    sample.write_bytes(raw_bytes)
    expected_sha = wm._sha256(sample)

    open_calls = 0
    real_open = Path.open

    def counted_open(self: Path, *args, **kwargs):
        nonlocal open_calls
        if self == sample:
            open_calls += 1
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(wm, "INPUT_LOG", sample)
    monkeypatch.setattr(wm.os, "getuid", lambda: 10001, raising=False)
    monkeypatch.setattr(wm.os, "getgid", lambda: 10001, raising=False)
    monkeypatch.setattr(Path, "open", counted_open)

    for fn in (wm.act_count_status_codes, lambda: wm.act_top_error_paths(5), lambda: wm.act_recent_http_errors(5), wm.act_check_read_log):
        open_calls = 0
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fn()
        assert rc == 0
        assert open_calls == 1
        out = json.loads(buf.getvalue().strip())
        digest_key = "sha256" if "sha256" in out else "snapshot_sha256"
        assert out[digest_key] == expected_sha
