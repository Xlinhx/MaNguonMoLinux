from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Iterable

from .models import RequestRecord, Snapshot
from .store import Store, utc_now_iso


def _status_text(code: int) -> str:
    try:
        phrase = HTTPStatus(code).phrase
        return f"{code} {phrase}"
    except ValueError:
        return str(code)


def _display_time(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return dt.strftime("%H:%M:%S")
    except ValueError:
        return iso_ts[11:19] if len(iso_ts) >= 19 else iso_ts


def parse_nginx_log_lines(lines: Iterable[str], run_id: str) -> list[RequestRecord]:
    """Parse JSON lines from Nginx access.jsonl filtered strictly to run_id and non-empty request_id.

    Deduplicates by request_id (keeping first occurrence) and orders chronologically by seq then timestamp.
    """
    seen_ids: set[str] = set()
    records: list[RequestRecord] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if str(obj.get("run_id") or "") != run_id:
            continue
        req_id = str(obj.get("request_id") or "").strip()
        if not req_id or req_id in seen_ids:
            continue
        seen_ids.add(req_id)
        try:
            seq = int(obj.get("seq") or 0)
        except (TypeError, ValueError):
            seq = len(records) + 1
        try:
            status = int(obj.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        ts = str(obj.get("timestamp") or "")
        records.append(
            RequestRecord(
                id=req_id,
                run_id=run_id,
                seq=seq,
                timestamp=ts,
                display_time=_display_time(ts),
                method=str(obj.get("method") or "GET"),
                path=str(obj.get("path") or "/"),
                status=status,
                status_text=_status_text(status),
                bytes_sent=int(obj.get("bytes_sent") or 0),
                request_time=float(obj.get("request_time") or 0.0),
            )
        )
    records.sort(key=lambda r: (r.seq, r.timestamp, r.id))
    return records


def read_run_records_from_nginx_log(nginx_log_path: Path, run_id: str) -> list[RequestRecord]:
    if not nginx_log_path.exists():
        return []
    with nginx_log_path.open("r", encoding="utf-8") as f:
        return parse_nginx_log_lines(f, run_id)


def create_immutable_snapshot(
    store: Store,
    snapshots_dir: Path,
    run_id: str,
    revision: int,
    records: list[RequestRecord],
) -> Snapshot:
    snap_dir = snapshots_dir / run_id / f"rev-{revision:04d}"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / "access.jsonl"

    ordered = sorted(records, key=lambda r: (r.seq, r.timestamp, r.id))
    payload_lines = [
        json.dumps(
            {
                "timestamp": r.timestamp,
                "display_time": r.display_time,
                "run_id": r.run_id,
                "request_id": r.id,
                "seq": r.seq,
                "method": r.method,
                "path": r.path,
                "status": r.status,
                "bytes_sent": r.bytes_sent,
                "request_time": r.request_time,
            },
            separators=(",", ":"),
        )
        for r in ordered
    ]
    content = ("\n".join(payload_lines) + "\n") if payload_lines else ""
    expected_bytes = content.encode("utf-8")
    digest = hashlib.sha256(expected_bytes).hexdigest()

    existing_db = store.get_snapshot_by_revision(run_id, revision)
    if snap_file.exists():
        disk_digest = hashlib.sha256(snap_file.read_bytes()).hexdigest()
        if existing_db is not None and existing_db.sha256 == disk_digest == digest:
            return existing_db
        if existing_db is None and disk_digest == digest:
            ok_count = sum(1 for r in ordered if r.status == 200)
            not_found_count = sum(1 for r in ordered if r.status == 404)
            snap = Snapshot(
                id=str(uuid.uuid4()),
                run_id=run_id,
                revision=revision,
                created_at=utc_now_iso(),
                sha256=disk_digest,
                request_count=len(ordered),
                ok_count=ok_count,
                not_found_count=not_found_count,
                file_path=str(snap_file.resolve()),
            )
            return store.insert_snapshot(snap)
        raise FileExistsError(f"Immutable snapshot already exists: {snap_file}")

    if existing_db is not None:
        raise FileExistsError(f"Snapshot row already exists without matching file: {snap_file}")

    tmp_file = snap_dir / f".access.jsonl.{uuid.uuid4().hex}.tmp"
    tmp_file.write_bytes(expected_bytes)
    os.replace(tmp_file, snap_file)

    ok_count = sum(1 for r in ordered if r.status == 200)
    not_found_count = sum(1 for r in ordered if r.status == 404)

    snap = Snapshot(
        id=str(uuid.uuid4()),
        run_id=run_id,
        revision=revision,
        created_at=utc_now_iso(),
        sha256=digest,
        request_count=len(ordered),
        ok_count=ok_count,
        not_found_count=not_found_count,
        file_path=str(snap_file.resolve()),
    )
    return store.insert_snapshot(snap)


def read_snapshot_records(snapshot: Snapshot) -> list[RequestRecord]:
    p = Path(snapshot.file_path)
    if not p.exists():
        raise FileNotFoundError(f"Snapshot file missing: {p}")
    with p.open("r", encoding="utf-8") as f:
        return parse_nginx_log_lines(f, snapshot.run_id)
