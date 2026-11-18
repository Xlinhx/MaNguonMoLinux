from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest

from backend.app.checks import (
    run_network_check,
    run_read_log_check,
    run_write_log_check,
)
from backend.app.config import Settings
from backend.app.models import RequestRecord, Run, Snapshot
from backend.app.sandbox import (
    ControlOverride,
    build_docker_run_argv,
    cleanup_container,
    execute_tool,
    resolve_docker_bin,
    run_worker_action,
)
from backend.app.snapshots import create_immutable_snapshot
from backend.app.store import Store


def _docker_and_image_ready(image: str) -> bool:
    docker_bin = resolve_docker_bin()
    p1 = subprocess.run([docker_bin, "info"], capture_output=True, text=True, check=False)
    if p1.returncode != 0:
        return False
    p2 = subprocess.run([docker_bin, "image", "inspect", image], capture_output=True, text=True, check=False)
    return p2.returncode == 0


def _make_isolated_settings(tmp_path: Path, base: Settings) -> Settings:
    runtime = tmp_path / "runtime"
    cfg = Settings(
        project_root=base.project_root,
        runtime_dir=runtime,
        db_path=runtime / "db" / "test.db",
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
    return cfg


def _seed_run_with_snapshot(store: Store, cfg: Settings) -> tuple[Run, Snapshot]:
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
            expected_count=10,
            logged_count=10,
            ok_count=7,
            not_found_count=3,
        )
    )
    records = [
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
    snap = create_immutable_snapshot(store, cfg.snapshots_dir, run_id, 1, records)
    run.snapshot_id = snap.id
    store.update_run(run)
    return run, snap


def test_real_docker_sandbox_isolation_and_controls(tmp_path: Path) -> None:
    base = Settings.from_env()
    if not _docker_and_image_ready(base.worker_image):
        pytest.skip(f"Docker or worker image '{base.worker_image}' is not available.")

    cfg = _make_isolated_settings(tmp_path, base)
    store = Store(cfg.db_path)
    _, snap = _seed_run_with_snapshot(store, cfg)

    # 1. Read log check + tool execution (10/7/3)
    read_chk = run_read_log_check(store, cfg, snap)
    assert read_chk.verdict == "pass"
    assert read_chk.observed == "Allowed"

    tool_res = execute_tool(store=store, settings=cfg, action="count_status_codes", snapshot_id=snap.id)
    assert tool_res.exit_code == 0
    assert tool_res.payload is not None
    assert tool_res.payload["total_requests"] == 10
    assert tool_res.payload["status_200"] == 7
    assert tool_res.payload["status_404"] == 3

    # 2. Write log check (read-only rejected + hash unchanged; writable copy control writes marker)
    snap_bytes_before = Path(snap.file_path).read_bytes()
    write_chk = run_write_log_check(store, cfg, snap)
    assert write_chk.verdict == "pass", f"Write check failed/errored: {write_chk}"
    assert write_chk.observed == "Blocked"
    assert write_chk.control_result and write_chk.control_result["marker_found"] is True
    assert Path(snap.file_path).read_bytes() == snap_bytes_before

    # 3. Network check (verify host reaches test-service, control container reaches it, sandbox --network none blocked)
    try:
        host_ping = httpx.get(cfg.test_service_host_url, timeout=2.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"test-service not running at {cfg.test_service_host_url}: {exc}")
    assert host_ping.status_code == 200

    net_chk = run_network_check(store, cfg, snap)
    assert net_chk.verdict == "pass", f"Network check failed/errored: {net_chk}"
    assert net_chk.observed == "Blocked"
    assert net_chk.control_result and net_chk.control_result["reachable"] is True

    # 4. Outside-mount check: create harmless host marker and pass its host path into the worker
    host_marker = tmp_path / "host_secret_marker.txt"
    host_marker.write_text("LAB_OUTSIDE_MARKER_42", encoding="utf-8")
    assert "LAB_OUTSIDE_MARKER_42" in host_marker.read_text(encoding="utf-8")
    outside_res = run_worker_action(
        store=store,
        settings=cfg,
        action="check_outside_mount",
        snapshot_id=snap.id,
        marker_path=str(host_marker),
    )
    assert outside_res.exit_code != 0
    assert outside_res.payload and outside_res.payload["found"] is False

    # 5. Timeout + real container cleanup + subsequent read still works
    sleep_res = run_worker_action(
        store=store,
        settings=cfg,
        action="check_sleep",
        snapshot_id=snap.id,
        seconds=15.0,
        timeout_s=1.5,
    )
    assert sleep_res.timed_out is True
    assert sleep_res.cleanup_ok is True
    assert sleep_res.error_code == "WORKER_TIMEOUT"

    docker_bin = resolve_docker_bin()
    leftover = subprocess.run(
        [docker_bin, "ps", "-a", "-q", "--filter", f"label=sandbox-lab.call-id={sleep_res.call_id}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert leftover.stdout.strip() == ""

    followup = execute_tool(store=store, settings=cfg, action="count_status_codes", snapshot_id=snap.id)
    assert followup.exit_code == 0

    # 6. Resource limits: inspect container created from build_docker_run_argv + verify OOMKilled=True
    inspect_name = f"sandbox-lab-inspect-{uuid.uuid4().hex[:8]}"
    inspect_call_id = str(uuid.uuid4())
    prod_argv = build_docker_run_argv(
        docker_bin=docker_bin,
        worker_image=cfg.worker_image,
        container_name=inspect_name,
        call_id=inspect_call_id,
        mount_source=Path(snap.file_path).resolve(),
        action_argv=["check_read_log"],
    )
    create_argv = [prod_argv[0], "create", *[a for a in prod_argv[2:] if a != "--rm"]]
    try:
        cproc = subprocess.run(create_argv, capture_output=True, text=True, check=True)
        assert cproc.returncode == 0
        iproc = subprocess.run([docker_bin, "inspect", inspect_name], capture_output=True, text=True, check=True)
        info = json.loads(iproc.stdout)[0]
        host_cfg = info["HostConfig"]
        assert info["Config"]["User"] == "10001:10001"
        assert host_cfg["ReadonlyRootfs"] is True
        assert host_cfg["NetworkMode"] == "none"
        assert host_cfg["CapDrop"] == ["ALL"]
        assert "no-new-privileges:true" in host_cfg["SecurityOpt"]
        assert host_cfg["PidsLimit"] == 64
        assert host_cfg["Memory"] == 128 * 1024 * 1024
        assert host_cfg["MemorySwap"] == 128 * 1024 * 1024
        assert host_cfg["NanoCpus"] == 500_000_000
    finally:
        assert cleanup_container(docker_bin, inspect_name, inspect_call_id) is True

    oom_res = run_worker_action(
        store=store,
        settings=cfg,
        action="check_memory",
        snapshot_id=snap.id,
        mb=200,
        control=ControlOverride(retain_for_inspect=True),
    )
    assert oom_res.exit_code == 137
    assert oom_res.oom_killed is True
    assert oom_res.cleanup_ok is True

    # 7. Infrastructure error must classify as verdict='error', never 'pass' or 'blocked'
    bad_img_cfg = Settings(**{**cfg.__dict__, "worker_image": "nonexistent-sandbox-image:missing"})
    err_chk = run_read_log_check(store, bad_img_cfg, snap)
    assert err_chk.verdict == "error"

    bad_net_cfg = Settings(**{**cfg.__dict__, "test_service_url": "http://127.0.0.1:59999/unreachable"})
    net_err_chk = run_network_check(store, bad_net_cfg, snap)
    assert net_err_chk.verdict == "error"
