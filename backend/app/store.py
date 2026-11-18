from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import (
    AgentMessage,
    CheckResult,
    JobRecord,
    RequestRecord,
    Run,
    Snapshot,
    ToolResult,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _immediate_conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("BEGIN IMMEDIATE;")
        try:
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    display_number INTEGER NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    snapshot_id TEXT,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    expected_count INTEGER NOT NULL DEFAULT 10,
                    logged_count INTEGER NOT NULL DEFAULT 0,
                    ok_count INTEGER NOT NULL DEFAULT 0,
                    not_found_count INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS run_planned_requests (
                    request_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    expected_status INTEGER NOT NULL,
                    dispatch_state TEXT NOT NULL DEFAULT 'not_sent',
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    request_count INTEGER NOT NULL,
                    ok_count INTEGER NOT NULL,
                    not_found_count INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    UNIQUE (run_id, revision),
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (scope, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS append_attempts (
                    scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    snapshot_id TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (scope, idempotency_key),
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                    call_id TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_json TEXT,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS agent_messages (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    snapshot_id TEXT,
                    revision INTEGER,
                    tool_call_ids_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'completed',
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS checks (
                    check_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    expected TEXT NOT NULL,
                    observed TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    control_result_json TEXT,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    exit_code INTEGER,
                    stderr_snippet TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    run_id TEXT,
                    status TEXT NOT NULL,
                    progress_message TEXT NOT NULL DEFAULT '',
                    result_id TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(run_planned_requests)").fetchall()}
            if "dispatch_state" not in cols:
                # Pre-migration rows have unknown dispatch history; never mark them safe to re-dispatch.
                conn.execute(
                    "ALTER TABLE run_planned_requests ADD COLUMN dispatch_state TEXT NOT NULL DEFAULT 'legacy_unknown'"
                )

    def _latest_display_number(self, conn: sqlite3.Connection) -> int | None:
        row = conn.execute("SELECT MAX(display_number) AS max_num FROM runs").fetchone()
        return row["max_num"] if row and row["max_num"] is not None else None

    def _row_to_run(self, row: sqlite3.Row, max_num: int | None) -> Run:
        return Run(
            id=row["id"],
            display_number=row["display_number"],
            label=row["label"],
            created_at=row["created_at"],
            status=row["status"],
            revision=row["revision"],
            snapshot_id=row["snapshot_id"],
            request_count=row["request_count"],
            expected_count=row["expected_count"],
            logged_count=row["logged_count"],
            ok_count=row["ok_count"],
            not_found_count=row["not_found_count"],
            is_current=(max_num is not None and row["display_number"] == max_num),
            error_code=row["error_code"],
            error_message=row["error_message"],
        )

    def next_display_number(self) -> int:
        with self._conn() as conn:
            cur = self._latest_display_number(conn)
            return 1 if cur is None else cur + 1

    def get_idempotency_resource(self, scope: str, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT resource_id FROM idempotency_keys WHERE scope = ? AND idempotency_key = ?",
                (scope, key),
            ).fetchone()
            return str(row["resource_id"]) if row else None

    def save_idempotency_resource(self, scope: str, key: str, resource_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO idempotency_keys (scope, idempotency_key, resource_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (scope, key, resource_id, utc_now_iso()),
            )

    def reserve_create_run(
        self,
        idempotency_key: str | None,
        planned_template: list[tuple[str, int]],
    ) -> tuple[Run, list[dict[str, object]], bool]:
        with self._immediate_conn() as conn:
            if idempotency_key:
                row = conn.execute(
                    "SELECT resource_id FROM idempotency_keys WHERE scope = 'create_run' AND idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if row:
                    existing_row = conn.execute(
                        "SELECT * FROM runs WHERE id = ?", (str(row["resource_id"]),)
                    ).fetchone()
                    if existing_row:
                        max_num = self._latest_display_number(conn)
                        planned_rows = conn.execute(
                            "SELECT * FROM run_planned_requests WHERE run_id = ? ORDER BY seq ASC",
                            (str(existing_row["id"]),),
                        ).fetchall()
                        return self._row_to_run(existing_row, max_num), [dict(r) for r in planned_rows], False

            cur_num = self._latest_display_number(conn)
            num = 1 if cur_num is None else cur_num + 1
            run_id = str(uuid.uuid4())
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO runs (
                    id, display_number, label, created_at, status, revision, snapshot_id,
                    request_count, expected_count, logged_count, ok_count, not_found_count,
                    error_code, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    num,
                    f"Run {num:03d}",
                    now,
                    "creating",
                    0,
                    None,
                    0,
                    len(planned_template),
                    0,
                    0,
                    0,
                    None,
                    None,
                ),
            )
            if idempotency_key:
                conn.execute(
                    """
                    INSERT INTO idempotency_keys (scope, idempotency_key, resource_id, created_at)
                    VALUES ('create_run', ?, ?, ?)
                    """,
                    (idempotency_key, run_id, now),
                )

            planned = [
                {
                    "request_id": str(uuid.uuid4()),
                    "seq": idx,
                    "method": "GET",
                    "path": path,
                    "expected_status": expected_status,
                    "dispatch_state": "not_sent",
                }
                for idx, (path, expected_status) in enumerate(planned_template, start=1)
            ]
            conn.executemany(
                """
                INSERT INTO run_planned_requests (request_id, run_id, seq, method, path, expected_status, dispatch_state)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(item["request_id"]),
                        run_id,
                        int(item["seq"]),
                        str(item["method"]),
                        str(item["path"]),
                        int(item["expected_status"]),
                        str(item["dispatch_state"]),
                    )
                    for item in planned
                ],
            )
            created_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return self._row_to_run(created_row, num), planned, True

    def reserve_append_404(
        self,
        run_id: str,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        scope = f"append_404:{run_id}"
        with self._immediate_conn() as conn:
            max_num = self._latest_display_number(conn)
            run_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not run_row:
                return {"action": "not_found"}
            run = self._row_to_run(run_row, max_num)
            if not run.is_current:
                return {"action": "read_only", "run": run}

            planned_rows = conn.execute(
                "SELECT * FROM run_planned_requests WHERE run_id = ? ORDER BY seq ASC",
                (run_id,),
            ).fetchall()
            planned = [dict(r) for r in planned_rows]

            if idempotency_key:
                attempt = conn.execute(
                    "SELECT * FROM append_attempts WHERE scope = ? AND idempotency_key = ?",
                    (scope, idempotency_key),
                ).fetchone()
                if attempt:
                    return {
                        "action": "existing",
                        "run": run,
                        "attempt": dict(attempt),
                        "planned": planned,
                    }

            if run.status != "ready":
                return {"action": "not_ready", "run": run}

            next_seq = (max(int(p["seq"]) for p in planned) + 1) if planned else (run.request_count + 1)
            new_req_id = str(uuid.uuid4())
            now = utc_now_iso()
            new_item = {
                "request_id": new_req_id,
                "seq": next_seq,
                "method": "GET",
                "path": "/missing",
                "expected_status": 404,
                "dispatch_state": "managed_by_append",
            }
            conn.execute(
                """
                INSERT INTO run_planned_requests (request_id, run_id, seq, method, path, expected_status, dispatch_state)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (new_req_id, run_id, next_seq, "GET", "/missing", 404, "managed_by_append"),
            )
            planned.append({"run_id": run_id, **new_item})

            effective_key = idempotency_key or f"auto:{new_req_id}"
            conn.execute(
                """
                INSERT INTO append_attempts (scope, idempotency_key, run_id, request_id, seq, status, snapshot_id, created_at)
                VALUES (?, ?, ?, ?, ?, 'reserved', NULL, ?)
                """,
                (scope, effective_key, run_id, new_req_id, next_seq, now),
            )
            attempt_row = conn.execute(
                "SELECT * FROM append_attempts WHERE scope = ? AND idempotency_key = ?",
                (scope, effective_key),
            ).fetchone()
            return {
                "action": "reserved",
                "run": run,
                "attempt": dict(attempt_row),
                "planned": planned,
            }

    def update_append_attempt(
        self,
        scope: str,
        idempotency_key: str,
        status: str,
        snapshot_id: str | None = None,
    ) -> None:
        with self._immediate_conn() as conn:
            conn.execute(
                """
                UPDATE append_attempts
                SET status = ?, snapshot_id = COALESCE(?, snapshot_id)
                WHERE scope = ? AND idempotency_key = ?
                """,
                (status, snapshot_id, scope, idempotency_key),
            )

    def remove_planned_request(self, run_id: str, request_id: str) -> None:
        with self._immediate_conn() as conn:
            conn.execute(
                "DELETE FROM run_planned_requests WHERE run_id = ? AND request_id = ?",
                (run_id, request_id),
            )

    def complete_append_attempts_for_run(self, run_id: str, snapshot_id: str) -> None:
        with self._immediate_conn() as conn:
            conn.execute(
                """
                UPDATE append_attempts
                SET status = 'completed', snapshot_id = ?
                WHERE run_id = ? AND status IN ('reserved', 'sent_pending_log')
                """,
                (snapshot_id, run_id),
            )

    def insert_run(self, run: Run) -> Run:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    id, display_number, label, created_at, status, revision, snapshot_id,
                    request_count, expected_count, logged_count, ok_count, not_found_count,
                    error_code, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.display_number,
                    run.label,
                    run.created_at,
                    run.status,
                    run.revision,
                    run.snapshot_id,
                    run.request_count,
                    run.expected_count,
                    run.logged_count,
                    run.ok_count,
                    run.not_found_count,
                    run.error_code,
                    run.error_message,
                ),
            )
            max_num = self._latest_display_number(conn)
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run.id,)).fetchone()
            return self._row_to_run(row, max_num)

    def update_run(self, run: Run) -> Run:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?, revision = ?, snapshot_id = ?, request_count = ?,
                    expected_count = ?, logged_count = ?, ok_count = ?, not_found_count = ?,
                    error_code = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    run.status,
                    run.revision,
                    run.snapshot_id,
                    run.request_count,
                    run.expected_count,
                    run.logged_count,
                    run.ok_count,
                    run.not_found_count,
                    run.error_code,
                    run.error_message,
                    run.id,
                ),
            )
            max_num = self._latest_display_number(conn)
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run.id,)).fetchone()
            return self._row_to_run(row, max_num)

    def get_run(self, run_id: str) -> Run | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                return None
            max_num = self._latest_display_number(conn)
            return self._row_to_run(row, max_num)

    def list_runs(self, search: str = "", page: int = 1, page_size: int = 10) -> tuple[list[Run], int, str | None]:
        with self._conn() as conn:
            max_num = self._latest_display_number(conn)
            current_row = (
                conn.execute("SELECT id FROM runs WHERE display_number = ?", (max_num,)).fetchone()
                if max_num is not None
                else None
            )
            current_run_id = str(current_row["id"]) if current_row else None

            q = search.strip().lower()
            if q:
                like = f"%{q}%"
                total_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM runs WHERE LOWER(label) LIKE ? OR LOWER(id) LIKE ?",
                    (like, like),
                ).fetchone()
                rows = conn.execute(
                    """
                    SELECT * FROM runs
                    WHERE LOWER(label) LIKE ? OR LOWER(id) LIKE ?
                    ORDER BY display_number DESC
                    LIMIT ? OFFSET ?
                    """,
                    (like, like, page_size, (page - 1) * page_size),
                ).fetchall()
            else:
                total_row = conn.execute("SELECT COUNT(*) AS cnt FROM runs").fetchone()
                rows = conn.execute(
                    """
                    SELECT * FROM runs
                    ORDER BY display_number DESC
                    LIMIT ? OFFSET ?
                    """,
                    (page_size, (page - 1) * page_size),
                ).fetchall()
            total = int(total_row["cnt"]) if total_row else 0
            return [self._row_to_run(r, max_num) for r in rows], total, current_run_id

    def add_planned_requests(self, run_id: str, items: list[dict[str, object]]) -> None:
        with self._conn() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO run_planned_requests (
                    request_id, run_id, seq, method, path, expected_status, dispatch_state
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(item["request_id"]),
                        run_id,
                        int(item["seq"]),
                        str(item["method"]),
                        str(item["path"]),
                        int(item["expected_status"]),
                        str(item.get("dispatch_state") or "not_sent"),
                    )
                    for item in items
                ],
            )

    def update_planned_request_state(self, run_id: str, request_id: str, state: str) -> None:
        with self._immediate_conn() as conn:
            conn.execute(
                "UPDATE run_planned_requests SET dispatch_state = ? WHERE run_id = ? AND request_id = ?",
                (state, run_id, request_id),
            )

    def update_planned_request_states(self, run_id: str, updates: dict[str, str]) -> None:
        if not updates:
            return
        with self._immediate_conn() as conn:
            conn.executemany(
                "UPDATE run_planned_requests SET dispatch_state = ? WHERE run_id = ? AND request_id = ?",
                [(state, run_id, req_id) for req_id, state in updates.items()],
            )

    def get_planned_requests(self, run_id: str) -> list[dict[str, object]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM run_planned_requests WHERE run_id = ? ORDER BY seq ASC",
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def insert_snapshot(self, snap: Snapshot) -> Snapshot:
        try:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO snapshots (
                        id, run_id, revision, created_at, sha256, request_count, ok_count, not_found_count, file_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snap.id,
                        snap.run_id,
                        snap.revision,
                        snap.created_at,
                        snap.sha256,
                        snap.request_count,
                        snap.ok_count,
                        snap.not_found_count,
                        snap.file_path,
                    ),
                )
            return snap
        except sqlite3.IntegrityError:
            existing = self.get_snapshot_by_revision(snap.run_id, snap.revision)
            if (
                existing is not None
                and existing.sha256 == snap.sha256
                and existing.request_count == snap.request_count
            ):
                return existing
            raise

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
            return Snapshot(**dict(row)) if row else None

    def get_snapshot_by_revision(self, run_id: str, revision: int) -> Snapshot | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE run_id = ? AND revision = ? LIMIT 1",
                (run_id, revision),
            ).fetchone()
            return Snapshot(**dict(row)) if row else None

    def get_latest_snapshot_for_run(self, run_id: str) -> Snapshot | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE run_id = ? ORDER BY revision DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            return Snapshot(**dict(row)) if row else None

    def save_tool_call(self, tc: ToolResult) -> ToolResult:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tool_calls (
                    call_id, tool_name, args_json, run_id, snapshot_id, revision,
                    payload_json, duration_ms, status, error_code, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tc.call_id,
                    tc.tool_name,
                    json.dumps(tc.args),
                    tc.run_id,
                    tc.snapshot_id,
                    tc.revision,
                    json.dumps(tc.payload) if tc.payload is not None else None,
                    tc.duration_ms,
                    tc.status,
                    tc.error_code,
                    tc.error_message,
                    tc.created_at,
                ),
            )
        return tc

    def get_tool_call(self, call_id: str) -> ToolResult | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tool_calls WHERE call_id = ?", (call_id,)).fetchone()
            if not row:
                return None
            return ToolResult(
                call_id=row["call_id"],
                tool_name=row["tool_name"],
                args=json.loads(row["args_json"]),
                run_id=row["run_id"],
                snapshot_id=row["snapshot_id"],
                revision=row["revision"],
                payload=json.loads(row["payload_json"]) if row["payload_json"] else None,
                duration_ms=row["duration_ms"],
                status=row["status"],
                error_code=row["error_code"],
                error_message=row["error_message"],
                created_at=row["created_at"],
            )

    def save_agent_message(self, msg: AgentMessage) -> AgentMessage:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_messages (
                    id, run_id, role, content, snapshot_id, revision, tool_call_ids_json,
                    status, error_code, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.id,
                    msg.run_id,
                    msg.role,
                    msg.content,
                    msg.snapshot_id,
                    msg.revision,
                    json.dumps(msg.tool_call_ids),
                    msg.status,
                    msg.error_code,
                    msg.error_message,
                    msg.created_at,
                ),
            )
        return msg

    def list_agent_messages(self, run_id: str) -> list[AgentMessage]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_messages WHERE run_id = ? ORDER BY created_at ASC, rowid ASC",
                (run_id,),
            ).fetchall()
        messages: list[AgentMessage] = []
        for row in rows:
            call_ids = json.loads(row["tool_call_ids_json"])
            tcs: list[ToolResult] = []
            for cid in call_ids:
                tc = self.get_tool_call(cid)
                if tc:
                    tcs.append(tc)
            messages.append(
                AgentMessage(
                    id=row["id"],
                    run_id=row["run_id"],
                    role=row["role"],
                    content=row["content"],
                    snapshot_id=row["snapshot_id"],
                    revision=row["revision"],
                    tool_call_ids=call_ids,
                    tool_calls=tcs,
                    status=row["status"],
                    error_code=row["error_code"],
                    error_message=row["error_message"],
                    created_at=row["created_at"],
                )
            )
        return messages

    def save_check(self, check: CheckResult) -> CheckResult:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checks (
                    check_id, run_id, snapshot_id, revision, name, title, policy_version,
                    expected, observed, verdict, control_result_json, evidence_json,
                    exit_code, stderr_snippet, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    check.check_id,
                    check.run_id,
                    check.snapshot_id,
                    check.revision,
                    check.name,
                    check.title,
                    check.policy_version,
                    check.expected,
                    check.observed,
                    check.verdict,
                    json.dumps(check.control_result) if check.control_result is not None else None,
                    json.dumps(check.evidence),
                    check.exit_code,
                    check.stderr_snippet,
                    check.created_at,
                ),
            )
        return check

    def get_check(self, check_id: str) -> CheckResult | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM checks WHERE check_id = ?", (check_id,)).fetchone()
            if not row:
                return None
            return self._row_to_check(row)

    def list_checks_for_run(self, run_id: str, snapshot_id: str | None = None) -> list[CheckResult]:
        with self._conn() as conn:
            target_snapshot_id = snapshot_id
            if target_snapshot_id is None:
                run_row = conn.execute(
                    "SELECT snapshot_id FROM runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if run_row and run_row["snapshot_id"]:
                    target_snapshot_id = str(run_row["snapshot_id"])

            if target_snapshot_id is not None:
                rows = conn.execute(
                    "SELECT * FROM checks WHERE run_id = ? AND snapshot_id = ? ORDER BY created_at DESC, rowid DESC",
                    (run_id, target_snapshot_id),
                ).fetchall()
            else:
                rows = []

            if not rows:
                return []
            by_name: dict[str, CheckResult] = {}
            for r in rows:
                item = self._row_to_check(r)
                if item.name not in by_name:
                    by_name[item.name] = item
            order = {"read_log": 0, "write_log": 1, "reach_test_service": 2}
            results = list(by_name.values())
            results.sort(key=lambda c: order.get(c.name, 99))
            return results

    def list_all_checks_for_run(self, run_id: str) -> list[CheckResult]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM checks WHERE run_id = ? ORDER BY revision ASC, created_at ASC, rowid ASC",
                (run_id,),
            ).fetchall()
            return [self._row_to_check(r) for r in rows]

    def _row_to_check(self, row: sqlite3.Row) -> CheckResult:
        return CheckResult(
            check_id=row["check_id"],
            run_id=row["run_id"],
            snapshot_id=row["snapshot_id"],
            revision=row["revision"],
            name=row["name"],
            title=row["title"],
            policy_version=row["policy_version"],
            expected=row["expected"],
            observed=row["observed"],
            verdict=row["verdict"],
            control_result=json.loads(row["control_result_json"]) if row["control_result_json"] else None,
            evidence=json.loads(row["evidence_json"]),
            exit_code=row["exit_code"],
            stderr_snippet=row["stderr_snippet"],
            created_at=row["created_at"],
        )

    def save_job(self, job: JobRecord) -> JobRecord:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, kind, run_id, status, progress_message, result_id,
                    error_code, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    progress_message = excluded.progress_message,
                    result_id = excluded.result_id,
                    error_code = excluded.error_code,
                    error_message = excluded.error_message,
                    updated_at = excluded.updated_at
                """,
                (
                    job.job_id,
                    job.kind,
                    job.run_id,
                    job.status,
                    job.progress_message,
                    job.result_id,
                    job.error_code,
                    job.error_message,
                    job.created_at,
                    job.updated_at,
                ),
            )
        return job

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            return JobRecord(**dict(row)) if row else None

    def get_latest_job_for_run(self, run_id: str, kind: str = "agent_question") -> JobRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE run_id = ? AND kind = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (run_id, kind),
            ).fetchone()
            return JobRecord(**dict(row)) if row else None
