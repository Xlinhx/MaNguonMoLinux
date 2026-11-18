from __future__ import annotations

import threading
import time
import uuid
from typing import Callable

import httpx

from .config import Settings
from .models import RequestRecord, Run
from .snapshots import (
    create_immutable_snapshot,
    read_run_records_from_nginx_log,
    read_snapshot_records,
)
from .store import Store

# Canonical 10-request demo pattern matching Traffic v4 (01..10):
# 04, 07, 10 are GET /missing (404); the remaining 7 are GET /demo (200).
INITIAL_BATCH_PATHS: list[tuple[str, int]] = [
    ("/demo", 200),
    ("/demo", 200),
    ("/demo", 200),
    ("/missing", 404),
    ("/demo", 200),
    ("/demo", 200),
    ("/missing", 404),
    ("/demo", 200),
    ("/demo", 200),
    ("/missing", 404),
]


class TrafficError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _is_definite_pre_send_error(exc: httpx.HTTPError) -> bool:
    """Return True only for transport errors that occur strictly before bytes reach Nginx."""
    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ProxyError,
            httpx.UnsupportedProtocol,
            httpx.LocalProtocolError,
        ),
    )


class TrafficService:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        http_client_factory: Callable[[], httpx.Client] | None = None,
        log_wait_timeout_s: float = 4.0,
        log_poll_interval_s: float = 0.08,
    ) -> None:
        self.store = store
        self.settings = settings
        self.http_client_factory = http_client_factory or (
            lambda: httpx.Client(base_url=self.settings.nginx_url, timeout=3.0)
        )
        self.log_wait_timeout_s = log_wait_timeout_s
        self.log_poll_interval_s = log_poll_interval_s
        self._locks_guard = threading.Lock()
        self._create_locks: dict[str, threading.Lock] = {}
        self._run_locks: dict[str, threading.Lock] = {}

    def _get_create_lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._create_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._create_locks[key] = lock
            return lock

    def _get_run_lock(self, run_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._run_locks.get(run_id)
            if lock is None:
                lock = threading.Lock()
                self._run_locks[run_id] = lock
            return lock

    def _collect_run_records(self, run: Run) -> list[RequestRecord]:
        by_id: dict[str, RequestRecord] = {}
        existing_snap = (
            self.store.get_snapshot(run.snapshot_id)
            if run.snapshot_id
            else self.store.get_latest_snapshot_for_run(run.id)
        )
        if existing_snap is not None:
            try:
                for rec in read_snapshot_records(existing_snap):
                    by_id[rec.id] = rec
            except FileNotFoundError:
                pass
        for rec in read_run_records_from_nginx_log(self.settings.nginx_log_path, run.id):
            if rec.id not in by_id:
                by_id[rec.id] = rec
        records = list(by_id.values())
        records.sort(key=lambda r: (r.seq, r.timestamp, r.id))
        return records

    def _wait_for_logged_requests(self, run: Run, expected_ids: set[str]) -> list[RequestRecord]:
        deadline = time.monotonic() + self.log_wait_timeout_s
        latest: list[RequestRecord] = []
        while True:
            latest = self._collect_run_records(run)
            logged_ids = {r.id for r in latest}
            if expected_ids.issubset(logged_ids):
                return latest
            if time.monotonic() >= deadline:
                return latest
            time.sleep(self.log_poll_interval_s)

    def _dispatch_missing_batch_requests(
        self,
        run: Run,
        planned: list[dict[str, object]],
    ) -> tuple[int, httpx.HTTPError | None, list[RequestRecord]]:
        records = self._collect_run_records(run)
        if not run.is_current:
            # Historical runs are strictly read-only and must never emit Nginx traffic.
            return 0, None, records
        logged_ids = {r.id for r in records}
        state_sync: dict[str, str] = {}
        to_send: list[dict[str, object]] = []
        for item in planned:
            req_id = str(item["request_id"])
            cur_state = str(item.get("dispatch_state") or "legacy_unknown")
            if req_id in logged_ids:
                if cur_state != "sent":
                    state_sync[req_id] = "sent"
                continue
            if cur_state == "not_sent":
                to_send.append(item)
        if state_sync:
            self.store.update_planned_request_states(run.id, state_sync)

        if not to_send:
            return 0, None, records

        sent_count = 0
        last_err: httpx.HTTPError | None = None
        with self.http_client_factory() as client:
            for item in to_send:
                req_id = str(item["request_id"])
                self.store.update_planned_request_state(run.id, req_id, "dispatched_uncertain")
                try:
                    client.get(
                        str(item["path"]),
                        headers={
                            "X-Sandbox-Run-Id": run.id,
                            "X-Sandbox-Request-Id": req_id,
                            "X-Sandbox-Seq": f"{int(item['seq']):02d}",
                        },
                    )
                    self.store.update_planned_request_state(run.id, req_id, "sent")
                    sent_count += 1
                except httpx.HTTPError as exc:
                    post_err_ids = {r.id for r in self._collect_run_records(run)}
                    if req_id in post_err_ids:
                        self.store.update_planned_request_state(run.id, req_id, "sent")
                        sent_count += 1
                    elif _is_definite_pre_send_error(exc):
                        self.store.update_planned_request_state(run.id, req_id, "not_sent")
                    last_err = exc
                    break
        return sent_count, last_err, records

    def _finalize_initial_batch(
        self,
        run: Run,
        planned: list[dict[str, object]],
        records: list[RequestRecord],
        dispatch_err: httpx.HTTPError | None,
    ) -> Run:
        expected_ids = {str(item["request_id"]) for item in planned}
        matched = [r for r in records if r.id in expected_ids]
        ok_count = sum(1 for r in matched if r.status == 200)
        not_found_count = sum(1 for r in matched if r.status == 404)

        if matched:
            self.store.update_planned_request_states(run.id, {r.id: "sent" for r in matched})

        if len(matched) < len(planned):
            planned_after = self.store.get_planned_requests(run.id)
            matched_ids = {r.id for r in matched}
            any_attempted = any(
                str(p.get("dispatch_state") or "legacy_unknown") != "not_sent" for p in planned_after
            )
            has_legacy_unknown = any(
                str(p.get("dispatch_state") or "legacy_unknown") == "legacy_unknown"
                and str(p["request_id"]) not in matched_ids
                for p in planned_after
            )
            run.expected_count = len(planned)
            run.logged_count = len(matched)
            run.request_count = len(matched)
            run.ok_count = ok_count
            run.not_found_count = not_found_count
            if not run.is_current and run.status == "error":
                return self.store.update_run(run)
            if dispatch_err is not None and _is_definite_pre_send_error(dispatch_err) and not any_attempted and not matched:
                run.status = "error"
                run.error_code = "NGINX_UNAVAILABLE"
                run.error_message = f"Nginx is unavailable ({dispatch_err.__class__.__name__})."
            elif dispatch_err is not None and _is_definite_pre_send_error(dispatch_err):
                run.status = "error"
                run.error_code = "NGINX_UNAVAILABLE"
                run.error_message = (
                    f"Nginx connection failed after {len(matched)} of {len(planned)} requests ({dispatch_err.__class__.__name__})."
                )
            elif has_legacy_unknown:
                run.status = "pending_log"
                run.error_code = "LEGACY_DISPATCH_UNKNOWN"
                run.error_message = (
                    f"{len(matched)} of {len(planned)} requests logged. Legacy pre-migration rows have unknown "
                    "dispatch state and were not re-sent to avoid duplicate traffic."
                )
            elif not run.is_current:
                run.status = "pending_log"
                run.error_code = "LOG_INCOMPLETE"
                run.error_message = (
                    f"Read-only historical run: {len(matched)} of {len(planned)} requests logged "
                    "(no traffic re-sent)."
                )
            else:
                run.status = "pending_log"
                run.error_code = "LOG_INCOMPLETE"
                run.error_message = f"{len(matched)} of {len(planned)} requests logged."
            return self.store.update_run(run)

        existing_snap = self.store.get_latest_snapshot_for_run(run.id)
        if existing_snap and existing_snap.request_count == len(matched):
            snap = existing_snap
        else:
            snap = create_immutable_snapshot(
                store=self.store,
                snapshots_dir=self.settings.snapshots_dir,
                run_id=run.id,
                revision=(existing_snap.revision + 1) if existing_snap else 1,
                records=matched,
            )
        self.store.complete_append_attempts_for_run(run.id, snap.id)
        run.status = "ready"
        run.revision = snap.revision
        run.snapshot_id = snap.id
        run.request_count = snap.request_count
        run.expected_count = len(planned)
        run.logged_count = snap.request_count
        run.ok_count = snap.ok_count
        run.not_found_count = snap.not_found_count
        run.error_code = None
        run.error_message = None
        return self.store.update_run(run)

    def _hydrate_from_existing_snapshot(self, run: Run, existing_snap, planned_len: int) -> Run:
        if run.status != "ready":
            run.status = "ready"
            run.revision = existing_snap.revision
            run.snapshot_id = existing_snap.id
            run.request_count = existing_snap.request_count
            run.expected_count = max(existing_snap.request_count, planned_len)
            run.logged_count = existing_snap.request_count
            run.ok_count = existing_snap.ok_count
            run.not_found_count = existing_snap.not_found_count
            run.error_code = None
            run.error_message = None
            run = self.store.update_run(run)
        self.store.complete_append_attempts_for_run(run.id, existing_snap.id)
        return run

    def _reconcile_readonly(self, run: Run, planned: list[dict[str, object]], existing_snap) -> Run:
        if existing_snap is not None and (
            (not run.is_current and run.status == "ready")
            or existing_snap.request_count >= len(planned)
        ):
            return self._hydrate_from_existing_snapshot(run, existing_snap, len(planned))
        records = self._collect_run_records(run)
        return self._finalize_initial_batch(run, planned, records, None)

    def create_run(self, idempotency_key: str | None = None) -> Run:
        lock_key = f"key:{idempotency_key}" if idempotency_key else f"anon:{uuid.uuid4()}"
        with self._get_create_lock(lock_key):
            run, _, _ = self.store.reserve_create_run(
                idempotency_key=idempotency_key,
                planned_template=INITIAL_BATCH_PATHS,
            )
            with self._get_run_lock(run.id):
                latest_run = self.store.get_run(run.id) or run
                planned = self.store.get_planned_requests(latest_run.id)
                existing_snap = (
                    self.store.get_snapshot(latest_run.snapshot_id)
                    if latest_run.snapshot_id
                    else self.store.get_latest_snapshot_for_run(latest_run.id)
                )
                if not latest_run.is_current:
                    return self._reconcile_readonly(latest_run, planned, existing_snap)
                if existing_snap is not None and (
                    latest_run.status == "ready" or existing_snap.request_count >= len(planned)
                ):
                    return self._hydrate_from_existing_snapshot(latest_run, existing_snap, len(planned))
                if latest_run.status == "ready":
                    return latest_run

                sent_count, dispatch_err, pre_records = self._dispatch_missing_batch_requests(latest_run, planned)
                expected_ids = {str(item["request_id"]) for item in planned}
                if dispatch_err is not None and _is_definite_pre_send_error(dispatch_err):
                    records = self._collect_run_records(latest_run) if sent_count > 0 else pre_records
                else:
                    records = self._wait_for_logged_requests(latest_run, expected_ids)
                return self._finalize_initial_batch(latest_run, planned, records, dispatch_err)

    def reconcile_run_logs(self, run_id: str) -> Run:
        with self._get_run_lock(run_id):
            run = self.store.get_run(run_id)
            if not run:
                raise TrafficError("RUN_NOT_FOUND", "Run was not found.", 404)
            planned = self.store.get_planned_requests(run_id)
            if not planned:
                return run

            existing_snap = (
                self.store.get_snapshot(run.snapshot_id)
                if run.snapshot_id
                else self.store.get_latest_snapshot_for_run(run_id)
            )
            # Closed/ready historical runs and ready runs whose snapshot already covers all planned requests
            # must never be downgraded when raw Nginx access logs rotate or truncate.
            if existing_snap is not None and (
                (not run.is_current and run.status == "ready")
                or existing_snap.request_count >= len(planned)
            ):
                return self._hydrate_from_existing_snapshot(run, existing_snap, len(planned))

            # Historical runs are strictly read-only: reconcile only from existing logs/snapshots, never send HTTP traffic.
            if not run.is_current:
                return self._reconcile_readonly(run, planned, existing_snap)

            # Only resume mid-batch dispatches when rev1 snapshot does not yet exist (never re-dispatch append_404).
            if existing_snap is None:
                sent_count, dispatch_err, _ = self._dispatch_missing_batch_requests(run, planned)
                expected_ids = {str(item["request_id"]) for item in planned}
                if sent_count > 0 and (dispatch_err is None or not _is_definite_pre_send_error(dispatch_err)):
                    records = self._wait_for_logged_requests(run, expected_ids)
                else:
                    records = self._collect_run_records(run)
                return self._finalize_initial_batch(run, planned, records, dispatch_err)

            expected_ids = {str(item["request_id"]) for item in planned}
            records = self._collect_run_records(run)
            matched = [r for r in records if r.id in expected_ids]
            ok_count = sum(1 for r in matched if r.status == 200)
            not_found_count = sum(1 for r in matched if r.status == 404)

            if len(matched) < len(planned):
                run.status = "pending_log"
                run.logged_count = len(matched)
                run.expected_count = len(planned)
                run.error_code = "LOG_INCOMPLETE"
                run.error_message = f"{len(matched)} of {len(planned)} requests logged."
                return self.store.update_run(run)

            snap = create_immutable_snapshot(
                store=self.store,
                snapshots_dir=self.settings.snapshots_dir,
                run_id=run.id,
                revision=existing_snap.revision + 1,
                records=matched,
            )

            self.store.complete_append_attempts_for_run(run.id, snap.id)
            run.status = "ready"
            run.revision = snap.revision
            run.snapshot_id = snap.id
            run.request_count = snap.request_count
            run.expected_count = len(planned)
            run.logged_count = snap.request_count
            run.ok_count = snap.ok_count
            run.not_found_count = snap.not_found_count
            run.error_code = None
            run.error_message = None
            return self.store.update_run(run)

    def append_404(self, run_id: str, idempotency_key: str | None = None) -> Run:
        with self._get_run_lock(run_id):
            reservation = self.store.reserve_append_404(run_id, idempotency_key)
            action = str(reservation["action"])
            if action == "not_found":
                raise TrafficError("RUN_NOT_FOUND", "Run was not found.", 404)
            if action == "read_only":
                raise TrafficError(
                    "RUN_READ_ONLY",
                    "Historical runs are read-only. Switch to the current run to add a request.",
                    409,
                )
            if action == "not_ready":
                raise TrafficError(
                    "RUN_NOT_READY",
                    "Finish or refresh the current run before adding a 404 request.",
                    409,
                )

            run: Run = reservation["run"]  # type: ignore[assignment]
            attempt: dict[str, object] = reservation["attempt"]  # type: ignore[assignment]
            planned: list[dict[str, object]] = reservation["planned"]  # type: ignore[assignment]
            scope = str(attempt["scope"])
            attempt_key = str(attempt["idempotency_key"])
            req_id = str(attempt["request_id"])
            seq = int(attempt["seq"])
            attempt_status = str(attempt["status"])

            if action == "existing" and attempt_status == "completed":
                return self.store.get_run(run.id) or run

            if action == "existing" and req_id not in {str(p["request_id"]) for p in planned}:
                self.store.add_planned_requests(
                    run.id,
                    [
                        {
                            "request_id": req_id,
                            "seq": seq,
                            "method": "GET",
                            "path": "/missing",
                            "expected_status": 404,
                        }
                    ],
                )
                planned = self.store.get_planned_requests(run.id)

            all_expected_ids = {str(p["request_id"]) for p in planned} | {req_id}
            already_logged = {r.id for r in self._collect_run_records(run)}

            # Only dispatch HTTP request if Nginx has not already logged req_id
            # (prevents duplicates on timeout / lost response retries).
            if req_id not in already_logged and attempt_status != "sent_pending_log":
                try:
                    with self.http_client_factory() as client:
                        client.get(
                            "/missing",
                            headers={
                                "X-Sandbox-Run-Id": run.id,
                                "X-Sandbox-Request-Id": req_id,
                                "X-Sandbox-Seq": f"{seq:02d}",
                            },
                        )
                except httpx.HTTPError as exc:
                    post_err_ids = {r.id for r in self._collect_run_records(run)}
                    if req_id not in post_err_ids and _is_definite_pre_send_error(exc):
                        # Confirmed pre-send error (ConnectError/ConnectTimeout): preserve ready snapshot and roll back planned row.
                        self.store.remove_planned_request(run.id, req_id)
                        self.store.update_append_attempt(scope, attempt_key, "failed_pre_send")
                        raise TrafficError(
                            "NGINX_UNAVAILABLE",
                            f"Nginx is unavailable ({exc.__class__.__name__}).",
                            503,
                        ) from exc
                    # Post-dispatch error (ReadTimeout/ReadError/RemoteProtocolError): server may have accepted it;
                    # retain reservation as sent_pending_log and reconcile from access log rather than re-sending.

            self.store.update_append_attempt(scope, attempt_key, "sent_pending_log")
            records = self._wait_for_logged_requests(run, all_expected_ids)
            matched = [r for r in records if r.id in all_expected_ids]
            if req_id not in {r.id for r in matched} or len(matched) < len(all_expected_ids):
                run.status = "pending_log"
                run.expected_count = len(all_expected_ids)
                run.logged_count = len(matched)
                run.error_code = "LOG_INCOMPLETE"
                run.error_message = f"{len(matched)} of {len(all_expected_ids)} requests logged."
                return self.store.update_run(run)

            existing_snap = self.store.get_latest_snapshot_for_run(run.id)
            next_revision = (existing_snap.revision + 1) if existing_snap else (run.revision + 1)
            snap = create_immutable_snapshot(
                store=self.store,
                snapshots_dir=self.settings.snapshots_dir,
                run_id=run.id,
                revision=next_revision,
                records=matched,
            )
            self.store.update_append_attempt(scope, attempt_key, "completed", snapshot_id=snap.id)

            run.status = "ready"
            run.revision = snap.revision
            run.snapshot_id = snap.id
            run.request_count = snap.request_count
            run.expected_count = snap.request_count
            run.logged_count = snap.request_count
            run.ok_count = snap.ok_count
            run.not_found_count = snap.not_found_count
            run.error_code = None
            run.error_message = None
            return self.store.update_run(run)
