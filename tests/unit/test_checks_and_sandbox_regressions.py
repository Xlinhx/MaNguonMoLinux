from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api import create_app
from backend.app.checks import run_read_log_check, run_write_log_check
from backend.app.config import Settings
from backend.app.models import RequestRecord, Run
from backend.app.sandbox import (
    ControlOverride,
    MAX_OUTPUT_BYTES,
    WorkerExecutionResult,
    _read_pipe_capped,
    run_worker_action,
)
from backend.app.snapshots import create_immutable_snapshot
from backend.app.store import Store


def _setup_snapshot(tmp_path: Path) -> tuple[Settings, Store, Run, str]:
    runtime = tmp_path / "runtime"
    cfg = Settings(
        project_root=tmp_path,
        runtime_dir=runtime,
        db_path=runtime / "db" / "checks_reg.db",
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
            request_count=1,
            expected_count=1,
            logged_count=1,
            ok_count=1,
            not_found_count=0,
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
                timestamp="2026-11-18T11:00:01Z",
                display_time="11:00:01",
                method="GET",
                path="/demo",
                status=200,
                status_text="200 OK",
            )
        ],
    )
    run.snapshot_id = snap.id
    store.update_run(run)
    return cfg, store, run, snap.id


def test_write_log_check_uses_isolated_copies_and_protects_source_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, store, _, snap_id = _setup_snapshot(tmp_path)
    snap = store.get_snapshot(snap_id)
    assert snap is not None
    source_bytes_before = Path(snap.file_path).read_bytes()
    mounted_paths: list[Path] = []

    def fake_worker(**kwargs) -> WorkerExecutionResult:
        ctrl: ControlOverride | None = kwargs.get("control")
        assert ctrl is not None
        if ctrl.readonly_mount_path is not None:
            mounted_paths.append(ctrl.readonly_mount_path)
            # Even if a broken read-only policy let the worker write to ro_copy,
            # the original immutable snapshot file must remain untouched.
            assert ctrl.readonly_mount_path.resolve() != Path(snap.file_path).resolve()
            assert (ctrl.readonly_mount_path.stat().st_mode & 0o666) == 0o666
            ctrl.readonly_mount_path.write_text("CORRUPTED_BY_BUGGY_RO", encoding="utf-8")
            return WorkerExecutionResult(
                call_id=str(uuid.uuid4()),
                container_name="fake-ro",
                action="check_write_log",
                args={},
                snapshot_id=snap.id,
                exit_code=0,
                stdout="",
                stderr="",
                payload={"written": True},
                duration_ms=5,
                timed_out=False,
                cleanup_ok=True,
                infra_error=False,
            )
        assert ctrl.writable_mount_path is not None
        mounted_paths.append(ctrl.writable_mount_path)
        assert ctrl.writable_mount_path.resolve() != Path(snap.file_path).resolve()
        ctrl.writable_mount_path.write_text("control\nSANDBOX_WRITE_MARKER\n", encoding="utf-8")
        return WorkerExecutionResult(
            call_id=str(uuid.uuid4()),
            container_name="fake-rw",
            action="check_write_log",
            args={},
            snapshot_id=snap.id,
            exit_code=0,
            stdout="",
            stderr="",
            payload={"written": True},
            duration_ms=5,
            timed_out=False,
            cleanup_ok=True,
            infra_error=False,
        )

    monkeypatch.setattr("backend.app.checks.run_worker_action", fake_worker)
    chk = run_write_log_check(store, cfg, snap)
    assert chk.verdict == "fail"
    assert Path(snap.file_path).read_bytes() == source_bytes_before
    assert len(mounted_paths) == 2
    assert len({p.resolve() for p in mounted_paths}) == 2


def test_read_log_timeout_classifies_as_error_with_honest_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, store, _, snap_id = _setup_snapshot(tmp_path)
    snap = store.get_snapshot(snap_id)
    assert snap is not None

    monkeypatch.setattr(
        "backend.app.checks.run_worker_action",
        lambda **kwargs: WorkerExecutionResult(
            call_id=str(uuid.uuid4()),
            container_name="fake-timeout",
            action="check_read_log",
            args={},
            snapshot_id=snap.id,
            exit_code=None,
            stdout="",
            stderr="Worker timed out after 10s.",
            payload=None,
            duration_ms=10000,
            timed_out=True,
            cleanup_ok=True,
            infra_error=True,
            error_code="WORKER_TIMEOUT",
            error_message="Worker timed out after 10s.",
        ),
    )

    chk = run_read_log_check(store, cfg, snap)
    assert chk.verdict == "error"
    assert chk.observed == "Error"
    assert "timed out" in str(chk.evidence.get("summary", "")).lower()


def test_sandbox_pipe_cap_and_cleanup_error_do_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, store, _, snap_id = _setup_snapshot(tmp_path)

    # 1. Verify _read_pipe_capped caps oversized stream without crashing
    import io

    big_stream = io.BytesIO(b"A" * (MAX_OUTPUT_BYTES + 12345))
    holder: list[bytes] = []
    _read_pipe_capped(big_stream, MAX_OUTPUT_BYTES, holder)
    assert len(holder[0]) <= MAX_OUTPUT_BYTES + len(b"\n...[truncated]")
    assert b"...[truncated]" in holder[0]

    # 2. Verify Popen OS error and cleanup failure stay inside structured SandboxRunResult
    monkeypatch.setattr(
        "backend.app.sandbox.subprocess.Popen",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("docker missing")),
    )
    monkeypatch.setattr("backend.app.sandbox.cleanup_container", lambda *a, **k: False)
    res = run_worker_action(store=store, settings=cfg, action="check_read_log", snapshot_id=snap_id)
    assert res.infra_error is True
    assert res.cleanup_ok is False
    assert res.error_code == "DOCKER_UNAVAILABLE"


def test_checks_pinned_per_snapshot_and_export_preserves_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, store, run, snap1_id = _setup_snapshot(tmp_path)

    def fake_checks(store: Store, settings: Settings, run_id: str):
        cur_run = store.get_run(run_id)
        assert cur_run and cur_run.snapshot_id
        snap = store.get_snapshot(cur_run.snapshot_id)
        assert snap is not None
        from backend.app.models import CheckResult

        chk = CheckResult(
            check_id=str(uuid.uuid4()),
            run_id=run_id,
            snapshot_id=snap.id,
            revision=snap.revision,
            name="read_log",
            title="Read log",
            policy_version=settings.policy_version,
            expected="Allowed",
            observed="Allowed",
            verdict="pass",
            created_at="2026-11-18T11:00:05Z",
        )
        store.save_check(chk)
        return [chk]

    monkeypatch.setattr("backend.app.api.run_all_demo_checks", fake_checks)
    app = create_app(settings=cfg)
    client = TestClient(app)

    # Execute checks on rev1 -> Pass
    res1 = client.post(f"/api/runs/{run.id}/checks").json()
    assert len(res1) == 1 and res1[0]["revision"] == 1

    # Bump run to rev2 snapshot -> GET /checks must return [] (Not run), NOT rev1 checks!
    snap2 = create_immutable_snapshot(
        store,
        cfg.snapshots_dir,
        run.id,
        2,
        [
            RequestRecord(
                id="r1",
                run_id=run.id,
                seq=1,
                timestamp="2026-11-18T11:00:01Z",
                display_time="11:00:01",
                method="GET",
                path="/demo",
                status=200,
                status_text="200 OK",
            ),
            RequestRecord(
                id="r2",
                run_id=run.id,
                seq=2,
                timestamp="2026-11-18T11:00:02Z",
                display_time="11:00:02",
                method="GET",
                path="/missing",
                status=404,
                status_text="404 Not Found",
            ),
        ],
    )
    run.revision = 2
    run.snapshot_id = snap2.id
    run.request_count = 2
    store.update_run(run)

    assert client.get(f"/api/runs/{run.id}/checks").json() == []
    exp_before_rev2 = client.get(f"/api/runs/{run.id}/export").json()
    assert exp_before_rev2["checks"] == []
    assert len(exp_before_rev2["check_history"]) == 1

    # Run checks on rev2 -> export contains current rev2 in checks and both rev1+rev2 in check_history
    client.post(f"/api/runs/{run.id}/checks")
    exp_after = client.get(f"/api/runs/{run.id}/export").json()
    assert len(exp_after["checks"]) == 1 and exp_after["checks"][0]["revision"] == 2
    assert [c["revision"] for c in exp_after["check_history"]] == [1, 2]
