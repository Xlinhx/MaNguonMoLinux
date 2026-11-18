from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from backend.app.config import Settings
from backend.app.store import Store
from backend.app.traffic import INITIAL_BATCH_PATHS, TrafficError, TrafficService


def _make_cfg(tmp_path: Path) -> Settings:
    runtime = tmp_path / "runtime"
    cfg = Settings(
        project_root=tmp_path,
        runtime_dir=runtime,
        db_path=runtime / "db" / "traffic_reg.db",
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


def test_concurrent_idempotency_for_create_and_append_404(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    store = Store(cfg.db_path)
    sent_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent_count
        sent_count += 1
        hdrs = {k.lower(): v for k, v in request.headers.items()}
        status = 404 if request.url.path == "/missing" else 200
        entry = {
            "timestamp": "2026-11-18T11:00:01+00:00",
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

    with ThreadPoolExecutor(max_workers=5) as pool:
        runs = list(pool.map(lambda _: service.create_run("same-create-key"), range(5)))
    assert len({r.id for r in runs}) == 1
    assert sent_count == 10
    run_id = runs[0].id

    with ThreadPoolExecutor(max_workers=5) as pool:
        appends = list(pool.map(lambda _: service.append_404(run_id, "same-add-key"), range(5)))
    assert sent_count == 11
    assert {r.revision for r in appends} == {2}
    assert {r.request_count for r in appends} == {11}


def test_append_404_preserves_ready_snapshot_on_pre_send_error_and_recovers_on_same_key_retry(
    tmp_path: Path,
) -> None:
    cfg = _make_cfg(tmp_path)
    store = Store(cfg.db_path)
    nginx_down = False
    sent_requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if nginx_down:
            raise httpx.ConnectError("Nginx refused connection", request=request)
        hdrs = {k.lower(): v for k, v in request.headers.items()}
        sent_requests.append(hdrs["x-sandbox-request-id"])
        status = 404 if request.url.path == "/missing" else 200
        entry = {
            "timestamp": "2026-11-18T11:00:01+00:00",
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
        log_wait_timeout_s=0.3,
    )

    r1 = service.create_run("c1")
    assert r1.status == "ready" and r1.revision == 1
    snap1_id = r1.snapshot_id

    # Simulate Nginx outage before sending the 11th request
    nginx_down = True
    with pytest.raises(TrafficError) as exc_info:
        service.append_404(r1.id, "add-retry-key")
    assert exc_info.value.status_code == 503

    # Existing ready snapshot must be completely intact
    intact = store.get_run(r1.id)
    assert intact is not None
    assert intact.status == "ready"
    assert intact.revision == 1
    assert intact.snapshot_id == snap1_id
    assert intact.request_count == 10

    # Refresh while still intact stays ready (10 requests)
    refreshed = service.reconcile_run_logs(r1.id)
    assert refreshed.status == "ready" and refreshed.request_count == 10

    # Bring Nginx back and retry with SAME idempotency_key -> clean recovery to 11 (rev 2)
    nginx_down = False
    recovered = service.append_404(r1.id, "add-retry-key")
    assert recovered.status == "ready"
    assert recovered.revision == 2
    assert recovered.request_count == 11
    assert recovered.not_found_count == 4


def test_append_404_post_send_timeout_recovers_without_duplicate_and_rotation_safe_reconcile(
    tmp_path: Path,
) -> None:
    cfg = _make_cfg(tmp_path)
    store = Store(cfg.db_path)
    drop_response = False
    http_404_dispatches = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_404_dispatches
        hdrs = {k.lower(): v for k, v in request.headers.items()}
        if int(hdrs["x-sandbox-seq"]) == 11:
            http_404_dispatches += 1
        status = 404 if request.url.path == "/missing" else 200
        entry = {
            "timestamp": "2026-11-18T11:00:01+00:00",
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
        if drop_response and int(hdrs["x-sandbox-seq"]) == 11:
            raise httpx.ReadTimeout("Lost response after Nginx logged", request=request)
        return httpx.Response(status)

    transport = httpx.MockTransport(handler)
    service = TrafficService(
        store=store,
        settings=cfg,
        http_client_factory=lambda: httpx.Client(transport=transport, base_url="http://nginx"),
        log_wait_timeout_s=0.3,
    )

    r1 = service.create_run("c1")
    drop_response = True
    # Nginx logged req #11 right before response timed out -> append_404 reconciles from log!
    r1_v2 = service.append_404(r1.id, "k-lost")
    assert r1_v2.revision == 2 and r1_v2.request_count == 11

    # Retrying with SAME key returns rev2 without sending a 12th request
    r1_v2_again = service.append_404(r1.id, "k-lost")
    assert r1_v2_again.revision == 2 and r1_v2_again.request_count == 11
    assert http_404_dispatches == 1

    # Create Run 002 so Run 001 becomes historical, then truncate/rotate raw access.jsonl
    r2 = service.create_run("c2")
    assert r2.is_current is True
    cfg.nginx_log_path.write_text("", encoding="utf-8")

    # Reconcile on Run 001 after log rotation must preserve its ready rev2 snapshot (11 requests)
    r1_after_rotate = service.reconcile_run_logs(r1.id)
    assert r1_after_rotate.status == "ready"
    assert r1_after_rotate.request_count == 11
    assert r1_after_rotate.revision == 2


def test_append_404_read_timeout_with_delayed_log_no_duplicate_on_same_key_retry(
    tmp_path: Path,
) -> None:
    cfg = _make_cfg(tmp_path)
    store = Store(cfg.db_path)
    delayed: list[dict[str, object]] = []
    dispatches: list[int] = []
    lose = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lose
        hdrs = {k.lower(): v for k, v in request.headers.items()}
        seq = int(hdrs["x-sandbox-seq"])
        dispatches.append(seq)
        status = 404 if request.url.path == "/missing" else 200
        rec = {
            "timestamp": "2026-11-18T11:00:01+00:00",
            "run_id": hdrs["x-sandbox-run-id"],
            "request_id": hdrs["x-sandbox-request-id"],
            "seq": seq,
            "method": "GET",
            "path": str(request.url.path),
            "status": status,
            "bytes_sent": 30,
            "request_time": 0.001,
        }
        if seq == 11 and lose:
            lose = False
            delayed.append(rec)
            raise httpx.ReadTimeout("response lost; server received request, log delayed", request=request)
        with cfg.nginx_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return httpx.Response(status)

    transport = httpx.MockTransport(handler)
    service = TrafficService(
        store=store,
        settings=cfg,
        http_client_factory=lambda: httpx.Client(transport=transport, base_url="http://nginx"),
        log_wait_timeout_s=0.02,
        log_poll_interval_s=0.002,
    )

    run = service.create_run("create-delayed")
    # 1. Server received seq 11 but ReadTimeout occurred before log became visible
    first = service.append_404(run.id, "one-intent")
    assert first.status == "pending_log"
    assert first.expected_count == 11
    assert first.logged_count == 10
    assert dispatches.count(11) == 1

    # 2. Retry same idempotency_key BEFORE delayed log flushes -> must NOT re-dispatch HTTP!
    second = service.append_404(run.id, "one-intent")
    assert second.status == "pending_log"
    assert dispatches.count(11) == 1

    # 3. Delayed log flushes to access.jsonl -> reconcile / retry promotes cleanly to 11/rev2 with 1 HTTP dispatch total
    with cfg.nginx_log_path.open("a", encoding="utf-8") as f:
        for rec in delayed:
            f.write(json.dumps(rec) + "\n")
    final = service.reconcile_run_logs(run.id)
    assert final.status == "ready"
    assert final.revision == 2
    assert final.request_count == 11
    assert final.not_found_count == 4
    assert dispatches.count(11) == 1
    assert len(cfg.nginx_log_path.read_text(encoding="utf-8").splitlines()) == 11

    # Same-key replay after completion also sends zero extra requests
    replay = service.append_404(run.id, "one-intent")
    assert replay.revision == 2
    assert dispatches.count(11) == 1


def test_create_run_mid_batch_read_timeout_recovers_via_refresh_and_same_key_retry_without_duplicate(
    tmp_path: Path,
) -> None:
    cfg = _make_cfg(tmp_path)
    store = Store(cfg.db_path)

    def _log_record(run_id: str, rid: str, seq: int, path: str, status: int) -> dict:
        return {
            "timestamp": "2026-11-19T00:00:00+00:00",
            "run_id": run_id,
            "request_id": rid,
            "seq": str(seq),
            "method": "GET",
            "path": path,
            "status": status,
            "bytes_sent": 30,
            "request_time": 0.001,
        }

    # Case 1: Req 1-3 succeed, req 4 logged by Nginx then ReadTimeout, req 5-10 never sent -> Refresh resumes 5-10
    dispatches_1: list[tuple[int, str]] = []
    delayed_req4_1: list[dict] = []

    class Case1Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, path: str, headers: dict[str, str]):
            seq = int(headers["X-Sandbox-Seq"])
            rid = headers["X-Sandbox-Request-Id"]
            dispatches_1.append((seq, rid))
            rec = _log_record(headers["X-Sandbox-Run-Id"], rid, seq, path, 404 if "/missing" in path else 200)
            if seq == 4 and len([s for s, _ in dispatches_1 if s == 4]) == 1:
                with cfg.nginx_log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
                raise httpx.ReadTimeout("Lost HTTP response after req 4 logged")
            with cfg.nginx_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            return httpx.Response(rec["status"], request=httpx.Request("GET", f"http://localhost{path}"))

    svc1 = TrafficService(store=store, settings=cfg, http_client_factory=lambda: Case1Client(), log_wait_timeout_s=0.15, log_poll_interval_s=0.02)
    r1 = svc1.create_run("batch-1")
    assert r1.status == "pending_log"
    assert r1.logged_count == 4
    assert r1.expected_count == 10
    assert [s for s, _ in dispatches_1] == [1, 2, 3, 4]

    # Refresh (reconcile_run_logs) must NOT resend 1-4; resumes strictly 5-10 -> 10 unique IDs, 7/3, 1 snapshot
    r1_done = svc1.reconcile_run_logs(r1.id)
    assert r1_done.status == "ready"
    assert r1_done.revision == 1
    assert r1_done.request_count == 10
    assert r1_done.ok_count == 7 and r1_done.not_found_count == 3
    assert [s for s, _ in dispatches_1] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert len({rid for _, rid in dispatches_1}) == 10
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM snapshots WHERE run_id = ?", (r1.id,)).fetchone()[0] == 1
    _ = delayed_req4_1

    # Case 2: Req 4 ReadTimeout BEFORE delayed log flushes + same-key retry + near-simultaneous create/refresh
    dispatches_2: list[tuple[int, str]] = []
    req4_holder: list[dict] = []

    class Case2Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, path: str, headers: dict[str, str]):
            seq = int(headers["X-Sandbox-Seq"])
            rid = headers["X-Sandbox-Request-Id"]
            dispatches_2.append((seq, rid))
            rec = _log_record(headers["X-Sandbox-Run-Id"], rid, seq, path, 404 if "/missing" in path else 200)
            if seq == 4:
                req4_holder.append(rec)
                raise httpx.ReadTimeout("Response lost and log delayed")
            with cfg.nginx_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            return httpx.Response(rec["status"], request=httpx.Request("GET", f"http://localhost{path}"))

    svc2 = TrafficService(store=store, settings=cfg, http_client_factory=lambda: Case2Client(), log_wait_timeout_s=0.15, log_poll_interval_s=0.02)
    r2 = svc2.create_run("batch-2")
    assert r2.status == "pending_log"
    assert r2.logged_count == 3
    assert [s for s, _ in dispatches_2] == [1, 2, 3, 4]

    # Same-key retry before req 4 log flushes -> sends strictly 5-10, NEVER resends req 4!
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_retry = pool.submit(svc2.create_run, "batch-2")
        f_ref = pool.submit(svc2.reconcile_run_logs, r2.id)
        r2_mid_a = f_retry.result()
        r2_mid_b = f_ref.result()
    assert r2_mid_a.status == "pending_log" and r2_mid_b.status == "pending_log"
    assert sorted(s for s, _ in dispatches_2) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert len({rid for _, rid in dispatches_2}) == 10

    # Flush delayed log for req 4 -> Refresh completes cleanly with 10/10 and single snapshot rev1
    with cfg.nginx_log_path.open("a", encoding="utf-8") as f:
        for rec in req4_holder:
            f.write(json.dumps(rec) + "\n")
    r2_done = svc2.reconcile_run_logs(r2.id)
    assert r2_done.status == "ready"
    assert r2_done.revision == 1
    assert r2_done.request_count == 10
    assert r2_done.ok_count == 7 and r2_done.not_found_count == 3
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM snapshots WHERE run_id = ?", (r2.id,)).fetchone()[0] == 1
    assert len(dispatches_2) == 10

    # Even if append_404 enters pending_log (rev1 snapshot exists, len(planned)==11), replaying create_run's key sends ZERO extra requests!
    def _pending_append_client():
        class C:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get(self, path, headers):
                dispatches_2.append((int(headers["X-Sandbox-Seq"]), headers["X-Sandbox-Request-Id"]))
                raise httpx.ReadTimeout("Append 11 delayed")
        return C()

    svc2_pending = TrafficService(store=store, settings=cfg, http_client_factory=_pending_append_client, log_wait_timeout_s=0.1, log_poll_interval_s=0.02)
    r2_pend = svc2_pending.append_404(r2.id, "append-during-create-replay")
    assert r2_pend.status == "pending_log"
    before_replay = len(dispatches_2)
    r2_replay = svc2_pending.create_run("batch-2")
    assert r2_replay.id == r2.id
    assert len(dispatches_2) == before_replay


def test_historical_readonly_never_dispatches_and_legacy_db_never_duplicates(tmp_path: Path) -> None:
    import sqlite3
    import uuid
    from backend.app.snapshots import create_immutable_snapshot
    from backend.app.models import RequestRecord, Run

    def _log_record(run_id: str, rid: str, seq: int, path: str, status: int) -> dict:
        return {
            "timestamp": "2026-11-19T00:00:00+00:00",
            "run_id": run_id,
            "request_id": rid,
            "seq": str(seq),
            "method": "GET",
            "path": path,
            "status": status,
            "bytes_sent": 30,
            "request_time": 0.001,
        }

    cfg = _make_cfg(tmp_path)
    store = Store(cfg.db_path)

    # 1. Historical run stopped at #04 -> Run B created -> Refresh A & replay key A send 0 HTTP requests!
    sent_a: list[int] = []
    sent_b: list[int] = []

    class PartialAClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, path, headers):
            seq = int(headers["X-Sandbox-Seq"])
            sent_a.append(seq)
            if seq == 4:
                raise httpx.ConnectError("Nginx down mid-batch")
            rec = _log_record(headers["X-Sandbox-Run-Id"], headers["X-Sandbox-Request-Id"], seq, path, 200)
            with cfg.nginx_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            return httpx.Response(200)

    class OkBClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, path, headers):
            seq = int(headers["X-Sandbox-Seq"])
            sent_b.append(seq)
            rec = _log_record(headers["X-Sandbox-Run-Id"], headers["X-Sandbox-Request-Id"], seq, path, 404 if "/missing" in path else 200)
            with cfg.nginx_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            return httpx.Response(rec["status"])

    svc_a = TrafficService(store=store, settings=cfg, http_client_factory=lambda: PartialAClient(), log_wait_timeout_s=0.1, log_poll_interval_s=0.02)
    run_a = svc_a.create_run("run-a-key")
    assert run_a.status == "error" and sent_a == [1, 2, 3, 4]

    svc_b = TrafficService(store=store, settings=cfg, http_client_factory=lambda: OkBClient(), log_wait_timeout_s=0.2, log_poll_interval_s=0.02)
    run_b = svc_b.create_run("run-b-key")
    assert run_b.status == "ready" and len(sent_b) == 10

    class ForbiddenClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, path, headers):
            raise AssertionError(f"Historical run must never dispatch HTTP traffic! Got {path}")

    svc_ro = TrafficService(store=store, settings=cfg, http_client_factory=lambda: ForbiddenClient(), log_wait_timeout_s=0.05, log_poll_interval_s=0.01)
    run_a_after_ref = svc_ro.reconcile_run_logs(run_a.id)
    assert run_a_after_ref.status == "error"
    run_a_after_key = svc_ro.create_run("run-a-key")
    assert run_a_after_key.id == run_a.id and run_a_after_key.status == "error"
    assert sent_a == [1, 2, 3, 4]  # Zero extra requests sent!

    # 2. Pre-migration legacy DB (no dispatch_state column): interrupted at #04 must NEVER resend #04 or append duplicate!
    legacy_cfg = _make_cfg(tmp_path / "legacy_case")
    legacy_run_id = str(uuid.uuid4())
    with sqlite3.connect(legacy_cfg.db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                display_number INTEGER NOT NULL UNIQUE,
                label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                snapshot_id TEXT,
                request_count INTEGER NOT NULL DEFAULT 0,
                expected_count INTEGER NOT NULL DEFAULT 0,
                logged_count INTEGER NOT NULL DEFAULT 0,
                ok_count INTEGER NOT NULL DEFAULT 0,
                not_found_count INTEGER NOT NULL DEFAULT 0,
                idempotency_key TEXT UNIQUE,
                error_code TEXT,
                error_message TEXT
            );
            CREATE TABLE run_planned_requests (
                run_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                expected_status INTEGER NOT NULL,
                PRIMARY KEY (run_id, seq)
            );
            """
        )
        conn.execute(
            "INSERT INTO runs (id, display_number, label, created_at, status, expected_count, logged_count, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (legacy_run_id, 1, "Run 001", "2026-11-18T10:00:00Z", "pending_log", 10, 3, "legacy-k1"),
        )
        req_ids: list[str] = []
        for idx, (path, exp_st) in enumerate(INITIAL_BATCH_PATHS, start=1):
            rid = str(uuid.uuid4())
            req_ids.append(rid)
            conn.execute(
                "INSERT INTO run_planned_requests (run_id, request_id, seq, method, path, expected_status) VALUES (?, ?, ?, 'GET', ?, ?)",
                (legacy_run_id, rid, idx, path, exp_st),
            )
            if idx <= 3:
                rec = _log_record(legacy_run_id, rid, idx, path, exp_st)
                with legacy_cfg.nginx_log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")

    legacy_store = Store(legacy_cfg.db_path)
    svc_legacy = TrafficService(store=legacy_store, settings=legacy_cfg, http_client_factory=lambda: ForbiddenClient(), log_wait_timeout_s=0.05, log_poll_interval_s=0.01)
    res_mid = svc_legacy.reconcile_run_logs(legacy_run_id)
    assert res_mid.status == "pending_log" and res_mid.error_code == "LEGACY_DISPATCH_UNKNOWN"
    assert len(legacy_cfg.nginx_log_path.read_text(encoding="utf-8").splitlines()) == 3

    # When delayed #04-#10 flush cleanly later, Refresh seals exactly 10 lines (0 duplicate #04)
    for idx, ((path, exp_st), rid) in enumerate(zip(INITIAL_BATCH_PATHS, req_ids), start=1):
        if idx >= 4:
            with legacy_cfg.nginx_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(_log_record(legacy_run_id, rid, idx, path, exp_st)) + "\n")
    res_done = svc_legacy.reconcile_run_logs(legacy_run_id)
    assert res_done.status == "ready" and res_done.request_count == 10
    assert len(legacy_cfg.nginx_log_path.read_text(encoding="utf-8").splitlines()) == 10

    # 3. Crash after snapshot file/DB write before run status update -> Refresh & key replay recover cleanly with 0 requests
    crash_cfg = _make_cfg(tmp_path / "crash_case")
    crash_store = Store(crash_cfg.db_path)
    run_c, planned_c, _ = crash_store.reserve_create_run("crash-k1", INITIAL_BATCH_PATHS)
    recs_c = [
        RequestRecord(
            id=str(p["request_id"]),
            run_id=run_c.id,
            seq=int(p["seq"]),
            timestamp="2026-11-18T12:00:00Z",
            display_time="12:00:00",
            method="GET",
            path=str(p["path"]),
            status=int(p["expected_status"]),
            status_text=str(p["expected_status"]),
        )
        for p in planned_c
    ]
    snap_c = create_immutable_snapshot(crash_store, crash_cfg.snapshots_dir, run_c.id, 1, recs_c)
    # Also simulate disk-first recovery when snapshot file already matches on disk without a new FileExistsError
    svc_crash = TrafficService(store=crash_store, settings=crash_cfg, http_client_factory=lambda: ForbiddenClient(), log_wait_timeout_s=0.05, log_poll_interval_s=0.01)
    recovered = svc_crash.reconcile_run_logs(run_c.id)
    assert recovered.status == "ready" and recovered.snapshot_id == snap_c.id and recovered.request_count == 10

    # And calling create_immutable_snapshot again with identical records succeeds idempotently instead of FileExistsError
    snap_c_again = create_immutable_snapshot(crash_store, crash_cfg.snapshots_dir, run_c.id, 1, recs_c)
    assert snap_c_again.id == snap_c.id

    # And disk-only crash (access.jsonl written before DB row): adopting matching digest succeeds without FileExistsError
    run_d, planned_d, _ = crash_store.reserve_create_run("crash-k2", INITIAL_BATCH_PATHS)
    recs_d = [
        RequestRecord(
            id=str(p["request_id"]),
            run_id=run_d.id,
            seq=int(p["seq"]),
            timestamp="2026-11-18T12:00:00Z",
            display_time="12:00:00",
            method="GET",
            path=str(p["path"]),
            status=int(p["expected_status"]),
            status_text=str(p["expected_status"]),
        )
        for p in planned_d
    ]
    snap_d1 = create_immutable_snapshot(crash_store, crash_cfg.snapshots_dir, run_d.id, 1, recs_d)
    with crash_store._conn() as conn:
        conn.execute("DELETE FROM snapshots WHERE id = ?", (snap_d1.id,))
    snap_d_adopted = create_immutable_snapshot(crash_store, crash_cfg.snapshots_dir, run_d.id, 1, recs_d)
    assert snap_d_adopted.sha256 == snap_d1.sha256
