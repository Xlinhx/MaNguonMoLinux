from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .config import Settings
from .models import CheckResult, Snapshot
from .sandbox import ControlOverride, run_worker_action
from .store import Store, utc_now_iso


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def run_read_log_check(store: Store, settings: Settings, snap: Snapshot) -> CheckResult:
    res = run_worker_action(
        store=store,
        settings=settings,
        action="check_read_log",
        snapshot_id=snap.id,
    )
    if res.infra_error or not res.cleanup_ok or res.timed_out:
        return CheckResult(
            check_id=str(uuid.uuid4()),
            run_id=snap.run_id,
            snapshot_id=snap.id,
            revision=snap.revision,
            name="read_log",
            title="Read log",
            policy_version=settings.policy_version,
            expected="Allowed",
            observed="Error",
            verdict="error",
            control_result={"kind": "snapshot_sha256_match", "expected_sha256": snap.sha256},
            evidence={
                "error_code": res.error_code or ("WORKER_TIMEOUT" if res.timed_out else "INFRA_ERROR"),
                "error_message": res.error_message or "Worker execution failed.",
                "summary": res.error_message or "Worker execution failed.",
            },
            exit_code=res.exit_code,
            stderr_snippet=res.stderr[:1000],
            created_at=utc_now_iso(),
        )

    observed_sha = res.payload.get("sha256") if res.payload else None
    read_ok = res.exit_code == 0 and res.payload is not None and observed_sha == snap.sha256
    observed = "Allowed" if read_ok else "Blocked"
    verdict = "pass" if read_ok else "fail"
    summary = (
        "Worker read snapshot with matching SHA-256."
        if read_ok
        else f"Read check did not match snapshot (exit={res.exit_code}, observed_sha={observed_sha})."
    )
    return CheckResult(
        check_id=str(uuid.uuid4()),
        run_id=snap.run_id,
        snapshot_id=snap.id,
        revision=snap.revision,
        name="read_log",
        title="Read log",
        policy_version=settings.policy_version,
        expected="Allowed",
        observed=observed,
        verdict=verdict,
        control_result={
            "kind": "host_snapshot_digest",
            "expected_sha256": snap.sha256,
            "observed_sha256": observed_sha,
            "lines": res.payload.get("lines") if res.payload else None,
        },
        evidence={
            "uid": res.payload.get("uid") if res.payload else None,
            "gid": res.payload.get("gid") if res.payload else None,
            "sha256": observed_sha,
            "summary": summary,
        },
        exit_code=res.exit_code,
        stderr_snippet=res.stderr[:1000],
        created_at=utc_now_iso(),
    )


def run_write_log_check(store: Store, settings: Settings, snap: Snapshot) -> CheckResult:
    # NEVER mount the source snapshot to check_write_log.
    # Use two isolated temp copies with identical 0666 POSIX permissions so UID 10001
    # can write on the writable control copy while the readonly mount rejects writes.
    snap_path = Path(snap.file_path)
    source_sha_before = _sha256_file(snap_path)

    with tempfile.TemporaryDirectory(dir=str(settings.runtime_dir)) as tmp_dir:
        tmp_path = Path(tmp_dir)
        os.chmod(tmp_path, 0o755)
        ro_copy = tmp_path / "ro-access.jsonl"
        rw_copy = tmp_path / "rw-access.jsonl"
        shutil.copy2(snap_path, ro_copy)
        shutil.copy2(snap_path, rw_copy)
        os.chmod(ro_copy, 0o666)
        os.chmod(rw_copy, 0o666)
        ro_sha_before = _sha256_file(ro_copy)

        # Sandbox run (isolated read-only copy, identical sandbox flags)
        sandbox_res = run_worker_action(
            store=store,
            settings=settings,
            action="check_write_log",
            snapshot_id=snap.id,
            control=ControlOverride(readonly_mount_path=ro_copy),
        )
        # Control run (isolated writable copy, same image + UID 10001 + check_write_log)
        control_res = run_worker_action(
            store=store,
            settings=settings,
            action="check_write_log",
            snapshot_id=None,
            control=ControlOverride(writable_mount_path=rw_copy),
        )
        ro_sha_after = _sha256_file(ro_copy)
        rw_text = rw_copy.read_text(encoding="utf-8")
        marker_found = "SANDBOX_WRITE_MARKER" in rw_text

    source_sha_after = _sha256_file(snap_path)
    source_untouched = source_sha_before == source_sha_after == snap.sha256

    if (
        sandbox_res.infra_error
        or control_res.infra_error
        or sandbox_res.timed_out
        or control_res.timed_out
        or not sandbox_res.cleanup_ok
        or not control_res.cleanup_ok
    ):
        err_code = (
            sandbox_res.error_code
            or control_res.error_code
            or ("WORKER_TIMEOUT" if (sandbox_res.timed_out or control_res.timed_out) else "INFRA_ERROR")
        )
        err_msg = sandbox_res.error_message or control_res.error_message or "Worker or cleanup error."
        return CheckResult(
            check_id=str(uuid.uuid4()),
            run_id=snap.run_id,
            snapshot_id=snap.id,
            revision=snap.revision,
            name="write_log",
            title="Write log",
            policy_version=settings.policy_version,
            expected="Blocked",
            observed="Error",
            verdict="error",
            control_result={
                "kind": "writable_copy_control",
                "control_exit_code": control_res.exit_code,
                "marker_found": marker_found,
                "error_code": err_code,
                "error_message": err_msg,
                "summary": err_msg,
            },
            evidence={
                "error_code": err_code,
                "error_message": err_msg,
                "source_snapshot_untouched": source_untouched,
            },
            exit_code=sandbox_res.exit_code,
            stderr_snippet=(sandbox_res.stderr or control_res.stderr)[:1000],
            created_at=utc_now_iso(),
        )

    control_ok = control_res.exit_code == 0 and marker_found
    if not control_ok:
        return CheckResult(
            check_id=str(uuid.uuid4()),
            run_id=snap.run_id,
            snapshot_id=snap.id,
            revision=snap.revision,
            name="write_log",
            title="Write log",
            policy_version=settings.policy_version,
            expected="Blocked",
            observed="Error",
            verdict="error",
            control_result={
                "kind": "writable_copy_control",
                "control_exit_code": control_res.exit_code,
                "marker_found": marker_found,
                "summary": "Writable control failed to write marker; cannot validate read-only isolation.",
            },
            evidence={
                "sandbox_payload": sandbox_res.payload,
                "source_snapshot_untouched": source_untouched,
                "error_message": "Writable control did not write marker.",
            },
            exit_code=sandbox_res.exit_code,
            stderr_snippet=control_res.stderr[:1000],
            created_at=utc_now_iso(),
        )

    sb_payload = sandbox_res.payload or {}
    sandbox_blocked = (
        sandbox_res.exit_code != 0
        and sb_payload.get("write_succeeded") is False
        and sb_payload.get("hash_unchanged") is True
        and ro_sha_before == ro_sha_after == snap.sha256
        and source_untouched
    )
    observed = "Blocked" if sandbox_blocked else "Allowed"
    verdict = "pass" if sandbox_blocked else "fail"
    return CheckResult(
        check_id=str(uuid.uuid4()),
        run_id=snap.run_id,
        snapshot_id=snap.id,
        revision=snap.revision,
        name="write_log",
        title="Write log",
        policy_version=settings.policy_version,
        expected="Blocked",
        observed=observed,
        verdict=verdict,
        control_result={
            "kind": "writable_copy_control",
            "write_succeeded": True,
            "marker_found": True,
            "summary": "Write succeeded · Marker found",
        },
        evidence={
            "read_only_mount": "Write rejected · Hash unchanged" if sandbox_blocked else "Write unexpectedly allowed",
            "writable_control": "Write succeeded · Marker found",
            "sha256_before": ro_sha_before,
            "sha256_after": ro_sha_after,
            "source_snapshot_untouched": source_untouched,
            "error": sb_payload.get("error"),
        },
        exit_code=sandbox_res.exit_code,
        stderr_snippet=sandbox_res.stderr[:1000],
        created_at=utc_now_iso(),
    )


def _is_valid_network_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if not isinstance(payload.get("http_response_received"), bool):
        return False
    if not isinstance(payload.get("reachable"), bool):
        return False
    status_code = payload.get("status_code")
    if status_code is not None and (not isinstance(status_code, int) or isinstance(status_code, bool)):
        return False
    return True


def _is_valid_ping_control(payload: dict[str, object]) -> tuple[bool, str]:
    status_code = payload.get("status_code")
    if payload.get("protocol_error") is True:
        return False, f"Control hit protocol error: {payload.get('error') or 'unknown error'}"
    if payload.get("http_response_received") is not True or status_code != 200:
        detail = f"HTTP {status_code}" if status_code is not None else (payload.get("error") or "unreachable")
        return False, f"Control did not return HTTP 200 /ping ({detail})."
    raw_body = payload.get("body")
    if not isinstance(raw_body, str) or not raw_body.strip():
        return False, "Control returned HTTP 200 with empty body instead of /ping JSON."
    try:
        parsed = json.loads(raw_body)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False, "Control returned non-JSON body on HTTP 200 /ping."
    if parsed != {"service": "sandbox-test-service", "status": "ok"}:
        return False, f"Control returned unexpected /ping payload (HTTP {status_code})."
    return True, "Control reached /ping (HTTP 200)"


def run_network_check(store: Store, settings: Settings, snap: Snapshot) -> CheckResult:
    url = settings.test_service_url
    sandbox_res = run_worker_action(
        store=store,
        settings=settings,
        action="check_network",
        snapshot_id=snap.id,
        url=url,
    )
    control_res = run_worker_action(
        store=store,
        settings=settings,
        action="check_network",
        snapshot_id=snap.id,
        url=url,
        control=ControlOverride(network_name=settings.docker_network),
    )

    if (
        sandbox_res.infra_error
        or control_res.infra_error
        or sandbox_res.timed_out
        or control_res.timed_out
        or not sandbox_res.cleanup_ok
        or not control_res.cleanup_ok
    ):
        err_code = sandbox_res.error_code or control_res.error_code or "INFRA_ERROR"
        err_msg = sandbox_res.error_message or control_res.error_message or "Worker or cleanup error."
        return CheckResult(
            check_id=str(uuid.uuid4()),
            run_id=snap.run_id,
            snapshot_id=snap.id,
            revision=snap.revision,
            name="reach_test_service",
            title="Reach test service",
            policy_version=settings.policy_version,
            expected="Blocked",
            observed="Error",
            verdict="error",
            control_result={
                "kind": "network_control",
                "url": url,
                "network": settings.docker_network,
                "status_code": None,
                "error_code": err_code,
                "error_message": err_msg,
                "summary": err_msg,
            },
            evidence={
                "error_code": err_code,
                "error_message": err_msg,
                "summary": err_msg,
            },
            exit_code=sandbox_res.exit_code,
            stderr_snippet=(sandbox_res.stderr or control_res.stderr)[:1000],
            created_at=utc_now_iso(),
        )

    if (
        not _is_valid_network_payload(sandbox_res.payload)
        or not _is_valid_network_payload(control_res.payload)
        or sandbox_res.payload.get("protocol_error") is True  # type: ignore[union-attr]
    ):
        err_msg = (
            (sandbox_res.payload.get("error") if isinstance(sandbox_res.payload, dict) else None)
            or sandbox_res.error_message
            or control_res.error_message
            or "Worker returned invalid or missing network measurement payload."
        )
        ctrl_status = control_res.payload.get("status_code") if isinstance(control_res.payload, dict) else None
        return CheckResult(
            check_id=str(uuid.uuid4()),
            run_id=snap.run_id,
            snapshot_id=snap.id,
            revision=snap.revision,
            name="reach_test_service",
            title="Reach test service",
            policy_version=settings.policy_version,
            expected="Blocked",
            observed="Error",
            verdict="error",
            control_result={
                "kind": "network_control",
                "url": url,
                "reachable": bool(control_res.payload.get("reachable")) if isinstance(control_res.payload, dict) else False,
                "status_code": ctrl_status,
                "summary": f"Measurement error: {err_msg}",
            },
            evidence={
                "error_code": "WORKER_PAYLOAD_INVALID",
                "error_message": str(err_msg),
                "summary": str(err_msg),
                "sandbox_payload": sandbox_res.payload,
            },
            exit_code=sandbox_res.exit_code,
            stderr_snippet=(sandbox_res.stderr or control_res.stderr)[:1000],
            created_at=utc_now_iso(),
        )

    assert sandbox_res.payload is not None and control_res.payload is not None
    sb_payload = sandbox_res.payload
    ctrl_payload = control_res.payload
    sb_expected_exit = 0 if sb_payload.get("http_response_received") is True else 1
    ctrl_expected_exit = 0 if ctrl_payload.get("http_response_received") is True else 1
    if sandbox_res.exit_code != sb_expected_exit or control_res.exit_code != ctrl_expected_exit:
        reasons: list[str] = []
        if sandbox_res.exit_code != sb_expected_exit:
            reasons.append(
                f"sandbox worker exited abnormally (exit code {sandbox_res.exit_code}, expected {sb_expected_exit})"
            )
        if control_res.exit_code != ctrl_expected_exit:
            reasons.append(
                f"control worker exited abnormally (exit code {control_res.exit_code}, expected {ctrl_expected_exit})"
            )
        reason_msg = "; ".join(reasons) + "."
        ctrl_status = ctrl_payload.get("status_code")
        return CheckResult(
            check_id=str(uuid.uuid4()),
            run_id=snap.run_id,
            snapshot_id=snap.id,
            revision=snap.revision,
            name="reach_test_service",
            title="Reach test service",
            policy_version=settings.policy_version,
            expected="Blocked",
            observed="Error",
            verdict="error",
            control_result={
                "kind": "network_control",
                "url": url,
                "reachable": bool(ctrl_payload.get("reachable")),
                "http_response_received": bool(ctrl_payload.get("http_response_received")),
                "status_code": ctrl_status,
                "exit_code": control_res.exit_code,
                "summary": f"Abnormal worker exit: {reason_msg}",
            },
            evidence={
                "sandbox_network": "none",
                "sandbox_http_response_received": bool(sb_payload.get("http_response_received")),
                "sandbox_status_code": sb_payload.get("status_code"),
                "sandbox_exit_code": sandbox_res.exit_code,
                "control_network": settings.docker_network,
                "control_status": ctrl_status,
                "control_exit_code": control_res.exit_code,
                "error_code": "WORKER_EXIT_ABNORMAL",
                "error_message": reason_msg,
                "summary": reason_msg,
            },
            exit_code=sandbox_res.exit_code,
            stderr_snippet=(sandbox_res.stderr or control_res.stderr)[:1000],
            created_at=utc_now_iso(),
        )

    ctrl_status = ctrl_payload.get("status_code")
    ctrl_ok, ctrl_summary = _is_valid_ping_control(ctrl_payload)
    if not ctrl_ok:
        return CheckResult(
            check_id=str(uuid.uuid4()),
            run_id=snap.run_id,
            snapshot_id=snap.id,
            revision=snap.revision,
            name="reach_test_service",
            title="Reach test service",
            policy_version=settings.policy_version,
            expected="Blocked",
            observed="Error",
            verdict="error",
            control_result={
                "kind": "network_control",
                "url": url,
                "reachable": bool(ctrl_payload.get("reachable")),
                "http_response_received": bool(ctrl_payload.get("http_response_received")),
                "status_code": ctrl_status,
                "summary": ctrl_summary,
                "error": ctrl_payload.get("error"),
            },
            evidence={
                "sandbox_network": "none",
                "sandbox_http_response_received": bool(sb_payload.get("http_response_received")),
                "sandbox_status_code": sb_payload.get("status_code"),
                "control_network": settings.docker_network,
                "control_status": ctrl_status,
                "error_code": "CONTROL_CHECK_INVALID",
                "error_message": ctrl_summary,
                "summary": ctrl_summary,
            },
            exit_code=sandbox_res.exit_code,
            stderr_snippet=control_res.stderr[:1000],
            created_at=utc_now_iso(),
        )

    sb_http_received = sb_payload.get("http_response_received") is True
    sb_status = sb_payload.get("status_code")
    sandbox_reached = (
        sandbox_res.exit_code == 0
        or sb_http_received
        or sb_payload.get("reachable") is True
        or sb_status is not None
    )
    sandbox_blocked = not sandbox_reached and sandbox_res.exit_code != 0
    observed = "Blocked" if sandbox_blocked else "Allowed"
    verdict = "pass" if sandbox_blocked else "fail"
    sb_summary = (
        "Blocked under --network none (no HTTP response)"
        if sandbox_blocked
        else (
            f"Sandbox reached test endpoint unexpectedly (HTTP {sb_status})"
            if sb_status is not None
            else "Sandbox unexpectedly reported reachable network"
        )
    )
    return CheckResult(
        check_id=str(uuid.uuid4()),
        run_id=snap.run_id,
        snapshot_id=snap.id,
        revision=snap.revision,
        name="reach_test_service",
        title="Reach test service",
        policy_version=settings.policy_version,
        expected="Blocked",
        observed=observed,
        verdict=verdict,
        control_result={
            "kind": "network_control",
            "url": url,
            "reachable": True,
            "http_response_received": True,
            "status_code": ctrl_status,
            "service_verified": True,
            "summary": ctrl_summary,
        },
        evidence={
            "sandbox_network": "none",
            "sandbox_blocked": sandbox_blocked,
            "sandbox_http_response_received": sb_http_received,
            "sandbox_status_code": sb_status,
            "sandbox_error": sb_payload.get("error"),
            "control_network": settings.docker_network,
            "control_status": ctrl_status,
            "summary": sb_summary,
        },
        exit_code=sandbox_res.exit_code,
        stderr_snippet=sandbox_res.stderr[:1000],
        created_at=utc_now_iso(),
    )


def run_all_demo_checks(store: Store, settings: Settings, run_id: str) -> list[CheckResult]:
    snap = store.get_latest_snapshot_for_run(run_id)
    if not snap:
        raise ValueError("No ready snapshot found for run.")
    results = [
        run_read_log_check(store, settings, snap),
        run_write_log_check(store, settings, snap),
        run_network_check(store, settings, snap),
    ]
    for item in results:
        store.save_check(item)
    return results
