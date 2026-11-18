from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

INPUT_LOG = Path("/input/access.jsonl")
OUTSIDE_MARKER = Path("/host_marker/secret.txt")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _read_sha256_and_records(path: Path) -> tuple[str, list[dict]]:
    h = hashlib.sha256()
    records: list[dict] = []
    with path.open("rb") as f:
        for raw_bytes in f:
            h.update(raw_bytes)
            line = raw_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
    return h.hexdigest(), records


def act_count_status_codes() -> int:
    digest, records = _read_sha256_and_records(INPUT_LOG)
    counts: Counter[str] = Counter()
    for rec in records:
        counts[str(rec.get("status", "unknown"))] += 1
    payload = {
        "total_requests": len(records),
        "status_200": counts.get("200", 0),
        "status_404": counts.get("404", 0),
        "by_status": dict(sorted(counts.items())),
        "snapshot_sha256": digest,
    }
    print(json.dumps(payload))
    return 0


def act_top_error_paths(limit: int) -> int:
    digest, records = _read_sha256_and_records(INPUT_LOG)
    counter: Counter[str] = Counter()
    for rec in records:
        status = int(rec.get("status") or 0)
        if status >= 400:
            counter[str(rec.get("path") or "/")] += 1
    items = [{"path": p, "count": c} for p, c in counter.most_common(limit)]
    print(json.dumps({"limit": limit, "paths": items, "snapshot_sha256": digest}))
    return 0


def act_recent_http_errors(limit: int) -> int:
    digest, records = _read_sha256_and_records(INPUT_LOG)
    errors = [rec for rec in records if int(rec.get("status") or 0) >= 400]
    # Preserve reverse chronological order
    errors.sort(key=lambda r: (int(r.get("seq") or 0), str(r.get("timestamp") or "")), reverse=True)
    items = [
        {
            "seq": int(r.get("seq") or 0),
            "request_id": str(r.get("request_id") or ""),
            "method": str(r.get("method") or "GET"),
            "path": str(r.get("path") or "/"),
            "status": int(r.get("status") or 0),
            "timestamp": str(r.get("timestamp") or ""),
        }
        for r in errors[:limit]
    ]
    print(json.dumps({"limit": limit, "errors": items, "snapshot_sha256": digest}))
    return 0


def act_check_read_log() -> int:
    digest, records = _read_sha256_and_records(INPUT_LOG)
    print(
        json.dumps(
            {
                "ok": True,
                "uid": os.getuid(),
                "gid": os.getgid(),
                "lines": len(records),
                "sha256": digest,
            }
        )
    )
    return 0


def act_check_write_log() -> int:
    before = _sha256(INPUT_LOG)
    write_succeeded = False
    error_msg = ""
    try:
        with INPUT_LOG.open("a", encoding="utf-8") as f:
            f.write('{"probe":"SANDBOX_WRITE_MARKER"}\n')
        write_succeeded = True
    except OSError as exc:
        error_msg = f"{exc.__class__.__name__}: {exc}"
    after = _sha256(INPUT_LOG)
    print(
        json.dumps(
            {
                "uid": os.getuid(),
                "gid": os.getgid(),
                "write_succeeded": write_succeeded,
                "sha256_before": before,
                "sha256_after": after,
                "hash_unchanged": before == after,
                "error": error_msg,
            }
        )
    )
    return 0 if write_succeeded else 1


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _safe_read_body(stream) -> str:
    try:
        return stream.read(512).decode("utf-8", errors="replace")
    except (OSError, http.client.HTTPException, TimeoutError, ValueError):
        return ""


def act_check_network(url: str) -> int:
    reachable = False
    http_response_received = False
    protocol_error = False
    status_code: int | None = None
    body = ""
    error_msg = ""
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        req = urllib.request.Request(url, method="GET")
        with opener.open(req, timeout=2.5) as resp:
            http_response_received = True
            reachable = True
            status_code = int(resp.status)
            body = _safe_read_body(resp)
    except urllib.error.HTTPError as exc:
        # HTTP 3xx/4xx/5xx still proves TCP+HTTP reached a responding endpoint rather than --network none.
        http_response_received = True
        reachable = True
        status_code = int(exc.code)
        body = _safe_read_body(exc)
        error_msg = f"HTTPError {exc.code}: {exc.reason}"
        try:
            exc.close()
        except OSError:
            pass
    except (http.client.HTTPException, ValueError) as exc:
        protocol_error = True
        error_msg = f"{exc.__class__.__name__}: {exc}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        error_msg = f"{exc.__class__.__name__}: {exc}"
    print(
        json.dumps(
            {
                "uid": os.getuid(),
                "gid": os.getgid(),
                "url": url,
                "reachable": reachable,
                "http_response_received": http_response_received,
                "protocol_error": protocol_error,
                "status_code": status_code,
                "body": body,
                "error": error_msg,
            }
        )
    )
    return 0 if http_response_received else 1


def act_check_outside_mount(marker_path: str | None = None) -> int:
    target = Path(marker_path) if marker_path else OUTSIDE_MARKER
    found = False
    content = ""
    error_msg = ""
    try:
        content = target.read_text(encoding="utf-8")
        found = True
    except OSError as exc:
        error_msg = f"{exc.__class__.__name__}: {exc}"
    print(
        json.dumps(
            {
                "uid": os.getuid(),
                "gid": os.getgid(),
                "path": str(target),
                "found": found,
                "content": content,
                "error": error_msg,
            }
        )
    )
    return 0 if found else 1


def act_check_sleep(seconds: float) -> int:
    time.sleep(seconds)
    print(json.dumps({"slept_seconds": seconds}))
    return 0


def act_check_memory(mb: int) -> int:
    # Touch 4 KiB pages so Linux RSS commits memory deterministically
    buf = bytearray(mb * 1024 * 1024)
    for offset in range(0, len(buf), 4096):
        buf[offset] = 1
    print(json.dumps({"allocated_mb": mb}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sandbox Lab fixed worker actions")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("count_status_codes")

    p_top = sub.add_parser("top_error_paths")
    p_top.add_argument("--limit", type=int, default=5)

    p_rec = sub.add_parser("recent_http_errors")
    p_rec.add_argument("--limit", type=int, default=10)

    sub.add_parser("check_read_log")
    sub.add_parser("check_write_log")

    p_net = sub.add_parser("check_network")
    p_net.add_argument("--url", type=str, required=True)

    p_out = sub.add_parser("check_outside_mount")
    p_out.add_argument("--path", type=str, default=str(OUTSIDE_MARKER))

    p_sleep = sub.add_parser("check_sleep")
    p_sleep.add_argument("--seconds", type=float, default=15.0)

    p_mem = sub.add_parser("check_memory")
    p_mem.add_argument("--mb", type=int, default=200)

    args = parser.parse_args()

    if args.action == "count_status_codes":
        return act_count_status_codes()
    if args.action == "top_error_paths":
        return act_top_error_paths(max(1, min(20, args.limit)))
    if args.action == "recent_http_errors":
        return act_recent_http_errors(max(1, min(50, args.limit)))
    if args.action == "check_read_log":
        return act_check_read_log()
    if args.action == "check_write_log":
        return act_check_write_log()
    if args.action == "check_network":
        return act_check_network(args.url)
    if args.action == "check_outside_mount":
        return act_check_outside_mount(args.path)
    if args.action == "check_sleep":
        return act_check_sleep(args.seconds)
    if args.action == "check_memory":
        return act_check_memory(args.mb)
    return 2


if __name__ == "__main__":
    sys.exit(main())
