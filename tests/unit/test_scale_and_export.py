from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api import create_app
from backend.app.config import Settings
from backend.app.models import RequestRecord, Run
from backend.app.snapshots import create_immutable_snapshot
from backend.app.store import Store


def test_scale_100_runs_10000_log_rows_and_300_char_path(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    cfg = Settings(
        project_root=tmp_path,
        runtime_dir=runtime,
        db_path=runtime / "db" / "scale.db",
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

    # Seed 100 runs
    last_run_id = ""
    for i in range(1, 101):
        rid = str(uuid.uuid4())
        last_run_id = rid
        store.insert_run(
            Run(
                id=rid,
                display_number=i,
                label=f"Run {i:03d}",
                created_at="2026-11-18T11:00:00Z",
                status="ready",
                revision=1,
                request_count=10000 if i == 100 else 10,
                ok_count=9000 if i == 100 else 7,
                not_found_count=1000 if i == 100 else 3,
            )
        )

    # Seed 10,000 log entries on Run 100 including a 300-character path
    long_path = "/missing/" + ("segment-" * 36) + "end"
    assert len(long_path) >= 300
    records = [
        RequestRecord(
            id=f"req-{seq:05d}",
            run_id=last_run_id,
            seq=seq,
            timestamp=f"2026-11-18T11:{(seq // 3600):02d}:{((seq // 60) % 60):02d}Z",
            display_time=f"11:{(seq // 3600):02d}:{((seq // 60) % 60):02d}",
            method="GET",
            path=long_path if seq == 9999 else ("/missing" if seq % 10 == 0 else "/demo"),
            status=404 if (seq == 9999 or seq % 10 == 0) else 200,
            status_text="404 Not Found" if (seq == 9999 or seq % 10 == 0) else "200 OK",
        )
        for seq in range(1, 10001)
    ]
    snap = create_immutable_snapshot(store, cfg.snapshots_dir, last_run_id, 1, records)
    run_100 = store.get_run(last_run_id)
    assert run_100 is not None
    run_100.snapshot_id = snap.id
    store.update_run(run_100)

    app = create_app(settings=cfg)
    client = TestClient(app)

    # 1. Run list pagination across 100 runs
    page1 = client.get("/api/runs", params={"page": 1, "page_size": 3}).json()
    assert page1["total"] == 100
    assert [r["label"] for r in page1["items"]] == ["Run 100", "Run 099", "Run 098"]
    search_res = client.get("/api/runs", params={"search": "Run 042", "page": 1, "page_size": 3}).json()
    assert search_res["total"] == 1
    assert search_res["items"][0]["label"] == "Run 042"

    # 2. Cursor pagination across 10,000 log rows (25 per page, never returning all 10,000)
    log_p1 = client.get(f"/api/runs/{last_run_id}/logs", params={"limit": 25}).json()
    assert log_p1["total_in_snapshot"] == 10000
    assert len(log_p1["items"]) == 25
    assert log_p1["items"][0]["seq"] == 10000
    assert log_p1["items"][1]["path"] == long_path
    assert log_p1["next_cursor"] == "25"

    log_p2 = client.get(
        f"/api/runs/{last_run_id}/logs",
        params={"limit": 25, "cursor": log_p1["next_cursor"]},
    ).json()
    assert len(log_p2["items"]) == 25
    assert log_p2["items"][0]["seq"] == 9975
    assert log_p2["prev_cursor"] == "0"
