from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.api import create_app
from backend.app.config import Settings
from backend.app.snapshots import parse_nginx_log_lines
from backend.app.store import Store
from backend.app.traffic import INITIAL_BATCH_PATHS, TrafficService


def _make_settings(tmp_path: Path) -> Settings:
    runtime_dir = tmp_path / "runtime"
    return Settings(
        project_root=tmp_path,
        runtime_dir=runtime_dir,
        db_path=runtime_dir / "db" / "sandbox_lab.db",
        nginx_log_path=runtime_dir / "nginx-logs" / "access.jsonl",
        snapshots_dir=runtime_dir / "snapshots",
        nginx_url="http://127.0.0.1:18080",
        test_service_url="http://sandbox-lab-test-service:8081/ping",
        test_service_host_url="http://127.0.0.1:18081/ping",
        docker_network="sandbox_lab_net",
        worker_image="sandbox-lab-worker:v1",
        api_host="127.0.0.1",
        api_port=18000,
    )


def test_parse_nginx_log_filters_run_and_dedupes(tmp_path: Path) -> None:
    lines = [
        "not-json\n",
        json.dumps({"timestamp": "2026-11-18T11:00:00Z", "run_id": "", "request_id": "", "seq": "", "method": "GET", "path": "/health", "status": 200}),
        json.dumps({"timestamp": "2026-11-18T11:00:01Z", "run_id": "other-run", "request_id": "r-other", "seq": "01", "method": "GET", "path": "/demo", "status": 200}),
        json.dumps({"timestamp": "2026-11-18T11:00:02Z", "run_id": "run-1", "request_id": "r2", "seq": "02", "method": "GET", "path": "/missing", "status": 404}),
        json.dumps({"timestamp": "2026-11-18T11:00:01Z", "run_id": "run-1", "request_id": "r1", "seq": "01", "method": "GET", "path": "/demo", "status": 200}),
        json.dumps({"timestamp": "2026-11-18T11:00:02Z", "run_id": "run-1", "request_id": "r2", "seq": "02", "method": "GET", "path": "/missing", "status": 404}),
    ]
    parsed = parse_nginx_log_lines(lines, "run-1")
    assert [r.id for r in parsed] == ["r1", "r2"]
    assert [r.seq for r in parsed] == [1, 2]
    assert parsed[0].status == 200
    assert parsed[1].status == 404


def test_create_run_partial_log_stays_pending_and_reconciles(tmp_path: Path) -> None:
    cfg = _make_settings(tmp_path)
    cfg.ensure_dirs()
    store = Store(cfg.db_path)

    captured_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hdrs = {k.lower(): v for k, v in request.headers.items()}
        captured_headers.append(hdrs)
        seq = int(hdrs["x-sandbox-seq"])
        # Only write first 8 entries initially to simulate log lag
        if seq <= 8:
            entry = {
                "timestamp": f"2026-11-18T11:00:{seq:02d}+00:00",
                "run_id": hdrs["x-sandbox-run-id"],
                "request_id": hdrs["x-sandbox-request-id"],
                "seq": hdrs["x-sandbox-seq"],
                "method": "GET",
                "path": str(request.url.path),
                "status": 404 if request.url.path == "/missing" else 200,
                "bytes_sent": 30,
                "request_time": 0.001,
            }
            with cfg.nginx_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        return httpx.Response(404 if request.url.path == "/missing" else 200)

    transport = httpx.MockTransport(handler)
    service = TrafficService(
        store=store,
        settings=cfg,
        http_client_factory=lambda: httpx.Client(transport=transport, base_url="http://nginx"),
        log_wait_timeout_s=0.15,
        log_poll_interval_s=0.05,
    )

    run = service.create_run(idempotency_key="k1")
    assert run.status == "pending_log"
    assert run.logged_count == 8
    assert run.revision == 0
    assert run.snapshot_id is None

    # Now append remaining 2 entries and reconcile
    for hdrs in captured_headers[8:]:
        seq = int(hdrs["x-sandbox-seq"])
        path = INITIAL_BATCH_PATHS[seq - 1][0]
        entry = {
            "timestamp": f"2026-11-18T11:00:{seq:02d}+00:00",
            "run_id": hdrs["x-sandbox-run-id"],
            "request_id": hdrs["x-sandbox-request-id"],
            "seq": hdrs["x-sandbox-seq"],
            "method": "GET",
            "path": path,
            "status": 404 if path == "/missing" else 200,
            "bytes_sent": 30,
            "request_time": 0.001,
        }
        with cfg.nginx_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    reconciled = service.reconcile_run_logs(run.id)
    assert reconciled.status == "ready"
    assert reconciled.revision == 1
    assert reconciled.request_count == 10
    assert reconciled.ok_count == 7
    assert reconciled.not_found_count == 3
    assert reconciled.snapshot_id is not None


def test_idempotency_and_read_only_history_and_log_pagination(tmp_path: Path) -> None:
    cfg = _make_settings(tmp_path)
    cfg.ensure_dirs()
    store = Store(cfg.db_path)

    def handler(request: httpx.Request) -> httpx.Response:
        hdrs = {k.lower(): v for k, v in request.headers.items()}
        seq = int(hdrs["x-sandbox-seq"])
        status = 404 if request.url.path == "/missing" else 200
        entry = {
            "timestamp": f"2026-11-18T11:01:{seq:02d}+00:00",
            "run_id": hdrs["x-sandbox-run-id"],
            "request_id": hdrs["x-sandbox-request-id"],
            "seq": hdrs["x-sandbox-seq"],
            "method": "GET",
            "path": str(request.url.path),
            "status": status,
            "bytes_sent": 30,
            "request_time": 0.001,
        }
        with cfg.nginx_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return httpx.Response(status)

    transport = httpx.MockTransport(handler)
    service = TrafficService(
        store=store,
        settings=cfg,
        http_client_factory=lambda: httpx.Client(transport=transport, base_url="http://nginx"),
        log_wait_timeout_s=0.5,
    )
    app = create_app(settings=cfg, traffic_service=service)
    client = TestClient(app)

    # Idempotent create_run
    r1 = client.post("/api/runs", headers={"Idempotency-Key": "create-1"}).json()
    r1_dup = client.post("/api/runs", headers={"Idempotency-Key": "create-1"}).json()
    assert r1["id"] == r1_dup["id"]
    assert r1["request_count"] == 10
    assert r1["ok_count"] == 7
    assert r1["not_found_count"] == 3

    # Idempotent append_404 -> 11/7/4
    r1_add = client.post(f"/api/runs/{r1['id']}/requests/404", headers={"Idempotency-Key": "add-1"}).json()
    r1_add_dup = client.post(f"/api/runs/{r1['id']}/requests/404", headers={"Idempotency-Key": "add-1"}).json()
    assert r1_add["request_count"] == 11
    assert r1_add["ok_count"] == 7
    assert r1_add["not_found_count"] == 4
    assert r1_add["revision"] == 2
    assert r1_add_dup["revision"] == 2
    assert r1_add_dup["request_count"] == 11

    # Second run makes first run historical read-only
    r2 = client.post("/api/runs", headers={"Idempotency-Key": "create-2"}).json()
    assert r2["id"] != r1["id"]
    assert r2["is_current"] is True

    ro_resp = client.post(f"/api/runs/{r1['id']}/requests/404", headers={"Idempotency-Key": "add-ro"})
    assert ro_resp.status_code == 409
    assert ro_resp.json()["code"] == "RUN_READ_ONLY"

    # Log query newest to oldest + filter + locate_request_id hidden by filter
    logs = client.get(f"/api/runs/{r1['id']}/logs", params={"limit": 5}).json()
    assert logs["total_in_snapshot"] == 11
    assert [item["seq"] for item in logs["items"]] == [11, 10, 9, 8, 7]

    req10_id = logs["items"][1]["id"]
    hidden = client.get(
        f"/api/runs/{r1['id']}/logs",
        params={"status": "200", "locate_request_id": req10_id, "limit": 5},
    ).json()
    assert hidden["located_hidden_by_filter"] is True
    assert hidden["located_request"]["id"] == req10_id
