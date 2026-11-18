from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import sys
import time
from typing import Any
import urllib.error
import urllib.request


def _read_proc_stat(pid: int) -> dict[str, Any] | None:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        raw = stat_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rparen = raw.rfind(")")
    if rparen == -1:
        return None
    tail = raw[rparen + 2 :].split()
    if len(tail) < 20:
        return None
    try:
        cmdline_raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        cmdline = cmdline_raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        cmdline = ""
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        cwd = ""
    return {
        "pid": pid,
        "state": tail[0],
        "ppid": int(tail[1]),
        "pgid": int(tail[2]),
        "session": int(tail[3]),
        "start_ticks": int(tail[19]),
        "cmdline": cmdline,
        "cwd": cwd,
    }


def _list_pgid_members(pgid: int, *, min_start_ticks: int | None = None) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    proc_dir = Path("/proc")
    if not proc_dir.is_dir():
        return members
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        info = _read_proc_stat(int(entry.name))
        if not info or info["state"] == "Z" or info["pgid"] != pgid:
            continue
        if min_start_ticks is not None and info["start_ticks"] < min_start_ticks - 2:
            continue
        members.append(info)
    return members


def _listening_inodes_for_port(port: int) -> set[str]:
    inodes: set[str] = set()
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[1:]:
            cols = line.split()
            if len(cols) < 10:
                continue
            local_addr, state, inode = cols[1], cols[3], cols[9]
            if state != "0A":  # TCP_LISTEN
                continue
            _, _, hex_port = local_addr.rpartition(":")
            if hex_port and int(hex_port, 16) == port:
                inodes.add(f"socket:[{inode}]")
    return inodes


def _pgid_owns_listening_port(pgid: int, port: int, *, min_start_ticks: int) -> bool:
    inodes = _listening_inodes_for_port(port)
    if not inodes:
        return False
    for member in _list_pgid_members(pgid, min_start_ticks=min_start_ticks):
        fd_dir = Path(f"/proc/{member['pid']}/fd")
        try:
            for fd_entry in fd_dir.iterdir():
                try:
                    target = os.readlink(fd_entry)
                except OSError:
                    continue
                if target in inodes:
                    return True
        except OSError:
            continue
    return False


def cmd_port_free(args: argparse.Namespace) -> int:
    host = args.host
    port = int(args.port)
    if _listening_inodes_for_port(port):
        print(f"ERROR: Port {port} already has a listening socket.", file=sys.stderr)
        return 1
    for addr in {host, "127.0.0.1"}:
        try:
            with socket.create_connection((addr, port), timeout=0.2):
                print(f"ERROR: Port {port} on {addr} is already accepting connections.", file=sys.stderr)
                return 1
        except OSError:
            pass
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        finally:
            sock.close()
    except OSError as exc:
        print(f"ERROR: Port {port} on {host} cannot be bound ({exc}).", file=sys.stderr)
        return 1
    return 0


def cmd_write_meta(args: argparse.Namespace) -> int:
    pid = int(args.pid)
    deadline = time.monotonic() + 2.0
    info: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        info = _read_proc_stat(pid)
        if info and info["cmdline"]:
            break
        time.sleep(0.02)
    if not info:
        print(f"ERROR: Spawned {args.name} process {pid} vanished before metadata capture.", file=sys.stderr)
        return 1
    tokens = [tok.strip() for tok in args.tokens.split(",") if tok.strip()]
    meta = {
        "name": args.name,
        "pid": pid,
        "pgid": info["pgid"],
        "session": info["session"],
        "start_ticks": info["start_ticks"],
        "cmdline_tokens": tokens,
        "cmdline_sample": info["cmdline"],
        "port": int(args.port),
        "host": args.host,
        "instance_token": args.token or "",
        "root_dir": str(Path(args.root).resolve()),
        "env_kind": "linux",
    }
    meta_path = Path(args.meta_path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return 0


def cmd_verify_ready(args: argparse.Namespace) -> int:
    meta_path = Path(args.meta_path)
    if not meta_path.is_file():
        print(f"ERROR: Missing metadata file {meta_path}", file=sys.stderr)
        return 1
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    pid = int(meta["pid"])
    pgid = int(meta["pgid"])
    start_ticks = int(meta["start_ticks"])
    port = int(meta["port"])

    def _is_alive_and_matching() -> bool:
        info = _read_proc_stat(pid)
        return bool(info and info["state"] != "Z" and abs(info["start_ticks"] - start_ticks) <= 2)

    deadline = time.monotonic() + float(args.timeout)
    last_err = "timed out"
    while time.monotonic() < deadline:
        if not _is_alive_and_matching():
            print(f"ERROR: {meta['name']} exited prematurely (pid {pid}).", file=sys.stderr)
            return 2
        try:
            with urllib.request.urlopen(args.url, timeout=1.5) as resp:
                status = resp.status
                body = resp.read()
            if status == 200:
                if args.require_token:
                    payload = json.loads(body.decode("utf-8"))
                    if payload.get("instance_token") != meta.get("instance_token"):
                        last_err = (
                            f"instance_token mismatch on {args.url} "
                            f"(got={payload.get('instance_token')!r})"
                        )
                        time.sleep(0.25)
                        continue
                if args.require_socket and not _pgid_owns_listening_port(pgid, port, min_start_ticks=start_ticks):
                    last_err = f"port {port} listener does not belong to PGID {pgid}"
                    time.sleep(0.25)
                    continue
                if args.settle_ms > 0:
                    time.sleep(args.settle_ms / 1000.0)
                    if not _is_alive_and_matching():
                        print(
                            f"ERROR: {meta['name']} exited during post-bind settle check (pid {pid}).",
                            file=sys.stderr,
                        )
                        return 2
                    if args.require_socket and not _pgid_owns_listening_port(pgid, port, min_start_ticks=start_ticks):
                        print(
                            f"ERROR: {meta['name']} lost ownership of port {port} during settle.",
                            file=sys.stderr,
                        )
                        return 2
                return 0
        except (urllib.error.URLError, json.JSONDecodeError, OSError, TimeoutError) as exc:
            last_err = str(exc)
        time.sleep(0.25)

    print(f"ERROR: {meta['name']} did not become ready at {args.url}: {last_err}", file=sys.stderr)
    return 1


def _matches_meta_identity(info: dict[str, Any], meta: dict[str, Any]) -> bool:
    if abs(int(info["start_ticks"]) - int(meta["start_ticks"])) > 2:
        return False
    tokens = meta.get("cmdline_tokens") or []
    if tokens and not any(tok in info["cmdline"] for tok in tokens):
        return False
    return True


def _signal_pgid(pgid: int, members: list[dict[str, Any]], sig: signal.Signals) -> None:
    try:
        os.killpg(pgid, sig)
    except OSError:
        pass
    for member in members:
        try:
            os.kill(int(member["pid"]), sig)
        except OSError:
            pass


def _inspect_pid_meta_state(name: str, pid_path: Path, meta_path: Path, *, action_label: str) -> dict[str, Any]:
    if not pid_path.exists() and not meta_path.exists():
        return {"state": "absent", "exit_code": 0}

    pid: int | None = None
    if pid_path.exists():
        raw_pid = pid_path.read_text(encoding="utf-8", errors="replace").strip()
        if raw_pid:
            try:
                pid = int(raw_pid)
            except ValueError:
                print(f"ERROR: Invalid PID '{raw_pid}' in {pid_path}; keeping files.", file=sys.stderr)
                return {"state": "error", "exit_code": 1}

    meta: dict[str, Any] | None = None
    if meta_path.exists():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and {"pid", "pgid", "start_ticks"} <= set(loaded):
                meta = loaded
        except (OSError, json.JSONDecodeError):
            meta = None

    if pid is None and meta is not None:
        pid = int(meta["pid"])

    if pid is None:
        pid_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return {"state": "cleaned", "exit_code": 0}

    if meta is None or int(meta["pid"]) != pid:
        live = _read_proc_stat(pid)
        if live and live["state"] != "Z":
            print(
                f"ERROR: Refusing to {action_label} {name} PID {pid}: missing or mismatched .meta identity record; leaving live process untouched.",
                file=sys.stderr,
            )
            return {"state": "error", "exit_code": 1}
        pid_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return {"state": "cleaned", "exit_code": 0}

    pgid = int(meta["pgid"])
    start_ticks = int(meta["start_ticks"])
    meta_session = int(meta["session"]) if meta.get("session") is not None else None

    def _pgid_group_survivors() -> list[dict[str, Any]]:
        members = _list_pgid_members(pgid, min_start_ticks=start_ticks)
        if meta_session is not None:
            members = [m for m in members if m.get("session") == meta_session]
        return members

    live_parent = _read_proc_stat(pid)
    if live_parent and live_parent["state"] != "Z":
        if not _matches_meta_identity(live_parent, meta):
            print(
                f"ERROR: Refusing to {action_label} {name} PID {pid}: live process identity mismatch "
                f"(cmd='{live_parent['cmdline']}', start={live_parent['start_ticks']} != expected={start_ticks}).",
                file=sys.stderr,
            )
            return {"state": "error", "exit_code": 1}
        survivors = _pgid_group_survivors()
        if live_parent["pid"] not in {m["pid"] for m in survivors}:
            survivors.append(live_parent)
    else:
        survivors = _pgid_group_survivors()
        if not survivors:
            pid_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            return {"state": "cleaned", "exit_code": 0}

    return {
        "state": "alive",
        "exit_code": 0,
        "pid": pid,
        "pgid": pgid,
        "meta": meta,
        "survivors": survivors,
        "pgid_group_survivors_fn": _pgid_group_survivors,
    }


def cmd_check_existing(args: argparse.Namespace) -> int:
    name = args.name
    pid_path = Path(args.pid_path)
    meta_path = Path(args.meta_path)
    inspected = _inspect_pid_meta_state(name, pid_path, meta_path, action_label="replace")
    if inspected["state"] in ("absent", "cleaned"):
        return 0
    if inspected["state"] == "error":
        return int(inspected["exit_code"])
    pgid = int(inspected["pgid"])
    survivors = inspected["survivors"]
    pids_str = ", ".join(str(m["pid"]) for m in survivors)
    print(
        f"ERROR: {name} already has live process(es) [{pids_str}] in PGID {pgid}; keeping pid/meta files. Run scripts/stop.sh first.",
        file=sys.stderr,
    )
    return 1


def cmd_stop_service(args: argparse.Namespace) -> int:
    name = args.name
    pid_path = Path(args.pid_path)
    meta_path = Path(args.meta_path)
    inspected = _inspect_pid_meta_state(name, pid_path, meta_path, action_label="stop")
    if inspected["state"] in ("absent", "cleaned"):
        return 0
    if inspected["state"] == "error":
        return int(inspected["exit_code"])

    pid = int(inspected["pid"])
    pgid = int(inspected["pgid"])
    meta = inspected["meta"]
    survivors = inspected["survivors"]
    _pgid_group_survivors = inspected["pgid_group_survivors_fn"]

    def _current_survivors() -> list[dict[str, Any]]:
        live_members = _pgid_group_survivors()
        parent = _read_proc_stat(pid)
        if parent and parent["state"] != "Z" and _matches_meta_identity(parent, meta):
            if parent["pid"] not in {m["pid"] for m in live_members}:
                live_members.append(parent)
        return live_members

    _signal_pgid(pgid, survivors, signal.SIGTERM)
    term_deadline = time.monotonic() + float(args.term_wait)
    while time.monotonic() < term_deadline:
        survivors = _current_survivors()
        if not survivors:
            break
        time.sleep(0.1)

    survivors = _current_survivors()
    if survivors:
        _signal_pgid(pgid, survivors, signal.SIGKILL)
        kill_deadline = time.monotonic() + float(args.kill_wait)
        while time.monotonic() < kill_deadline:
            survivors = _current_survivors()
            if not survivors:
                break
            time.sleep(0.1)

    survivors = _current_survivors()
    if survivors:
        pids_str = ", ".join(str(m["pid"]) for m in survivors)
        print(
            f"ERROR: {name} PGID {pgid} still has live process(es) [{pids_str}]; keeping pid/meta files.",
            file=sys.stderr,
        )
        return 1

    pid_path.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)
    return 0


def cmd_kill_pgid(args: argparse.Namespace) -> int:
    meta_path = Path(args.meta_path)
    pid_path = Path(args.pid_path)
    if meta_path.is_file():
        return cmd_stop_service(
            argparse.Namespace(
                name=meta_path.stem,
                pid_path=str(pid_path),
                meta_path=str(meta_path),
                term_wait=1.5,
                kill_wait=1.0,
            )
        )
    pid_path.unlink(missing_ok=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sandbox Lab Linux process lifecycle helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_free = sub.add_parser("port-free")
    p_free.add_argument("host")
    p_free.add_argument("port", type=int)

    p_meta = sub.add_parser("write-meta")
    p_meta.add_argument("pid", type=int)
    p_meta.add_argument("meta_path")
    p_meta.add_argument("--name", required=True)
    p_meta.add_argument("--port", type=int, required=True)
    p_meta.add_argument("--host", required=True)
    p_meta.add_argument("--root", required=True)
    p_meta.add_argument("--tokens", required=True)
    p_meta.add_argument("--token", default="")

    p_ready = sub.add_parser("verify-ready")
    p_ready.add_argument("meta_path")
    p_ready.add_argument("--url", required=True)
    p_ready.add_argument("--require-token", action="store_true")
    p_ready.add_argument("--require-socket", action="store_true")
    p_ready.add_argument("--settle-ms", type=int, default=300)
    p_ready.add_argument("--timeout", type=float, default=12.0)

    p_check = sub.add_parser("check-existing")
    p_check.add_argument("name")
    p_check.add_argument("pid_path")
    p_check.add_argument("meta_path")

    p_stop = sub.add_parser("stop-service")
    p_stop.add_argument("name")
    p_stop.add_argument("pid_path")
    p_stop.add_argument("meta_path")
    p_stop.add_argument("--term-wait", type=float, default=3.0)
    p_stop.add_argument("--kill-wait", type=float, default=1.5)

    p_kill = sub.add_parser("kill-pgid")
    p_kill.add_argument("pid_path")
    p_kill.add_argument("meta_path")

    args = parser.parse_args()
    dispatch = {
        "port-free": cmd_port_free,
        "write-meta": cmd_write_meta,
        "verify-ready": cmd_verify_ready,
        "check-existing": cmd_check_existing,
        "stop-service": cmd_stop_service,
        "kill-pgid": cmd_kill_pgid,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
