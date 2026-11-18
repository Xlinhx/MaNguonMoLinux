from __future__ import annotations

from pathlib import Path
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.api import create_app
from backend.app.config import Settings


def _nginx_is_up(url: str) -> bool:
    try:
        r = httpx.get(f"{url}/health", timeout=1.5)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def test_real_nginx_create_run_and_append_404(tmp_path: Path) -> None:
    cfg = Settings.from_env()
    if not _nginx_is_up(cfg.nginx_url):
        pytest.skip(f"Nginx lab is not running at {cfg.nginx_url}")

    # Isolate DB and snapshots per test run while reading the real Nginx log file
    test_cfg = Settings(
        project_root=cfg.project_root,
        runtime_dir=tmp_path / "runtime",
        db_path=tmp_path / "runtime" / "db" / "test.db",
        nginx_log_path=cfg.nginx_log_path,
        snapshots_dir=tmp_path / "runtime" / "snapshots",
        nginx_url=cfg.nginx_url,
        test_service_url=cfg.test_service_url,
        test_service_host_url=cfg.test_service_host_url,
        docker_network=cfg.docker_network,
        worker_image=cfg.worker_image,
        api_host=cfg.api_host,
        api_port=cfg.api_port,
    )
    app = create_app(settings=test_cfg)
    client = TestClient(app)

    k1 = f"int-create-{uuid.uuid4()}"
    run1 = client.post("/api/runs", headers={"Idempotency-Key": k1}).json()
    assert run1["status"] == "ready"
    assert run1["revision"] == 1
    assert run1["request_count"] == 10
    assert run1["ok_count"] == 7
    assert run1["not_found_count"] == 3

    # Replay same key -> same run, no duplicate requests
    run1_replay = client.post("/api/runs", headers={"Idempotency-Key": k1}).json()
    assert run1_replay["id"] == run1["id"]

    # Append 404 -> 11/7/4
    k_add = f"int-add-{uuid.uuid4()}"
    run1_v2 = client.post(
        f"/api/runs/{run1['id']}/requests/404",
        headers={"Idempotency-Key": k_add},
    ).json()
    assert run1_v2["revision"] == 2
    assert run1_v2["request_count"] == 11
    assert run1_v2["ok_count"] == 7
    assert run1_v2["not_found_count"] == 4

    # Create a second run -> must not mix logs with run1
    run2 = client.post("/api/runs", headers={"Idempotency-Key": f"int-create-{uuid.uuid4()}"}).json()
    assert run2["id"] != run1["id"]
    assert run2["request_count"] == 10

    detail1 = client.get(f"/api/runs/{run1['id']}").json()
    detail2 = client.get(f"/api/runs/{run2['id']}").json()
    assert len(detail1["requests"]) == 11
    assert len(detail2["requests"]) == 10
    assert {r["run_id"] for r in detail1["requests"]} == {run1["id"]}
    assert {r["run_id"] for r in detail2["requests"]} == {run2["id"]}
