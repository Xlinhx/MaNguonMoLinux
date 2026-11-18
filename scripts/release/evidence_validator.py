from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

MANIFEST_SINGLE_FILES = (
    "README.md",
    ".env.example",
    ".gitignore",
    "pytest.ini",
    "backend/requirements.txt",
    "web/package.json",
    "web/package-lock.json",
    "web/vite.config.ts",
    "web/tsconfig.json",
    "web/index.html",
)

MANIFEST_TREE_DIRS = (
    "backend/app",
    "infra",
    "scripts",
    "tests",
    "web/src",
    "worker",
)

REQUIRED_ASSETS = (
    "architecture.png",
    "mcp-sequence.png",
    "live-traffic-verified.png",
    "live-checks-verified.png",
    "live-agent-verified.png",
)

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


class ValidationError(ValueError):
    """Raised when deliverable evidence, test provenance, or assets fail strict validation."""


def iter_manifest_paths(root: Path) -> list[str]:
    rels: list[str] = []
    for rel in MANIFEST_SINGLE_FILES:
        candidate = root / rel
        if candidate.is_file():
            rels.append(rel)
    for tree in MANIFEST_TREE_DIRS:
        base = root / tree
        if not base.is_dir():
            continue
        for item in base.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(root).as_posix()
            parts = rel.split("/")
            if "__pycache__" in parts or rel.endswith(".pyc") or rel.endswith(".png"):
                continue
            rels.append(rel)
    return sorted(set(rels))


def compute_source_fingerprint(root: Path) -> tuple[str, int, list[str]]:
    rels = iter_manifest_paths(root)
    outer = hashlib.sha256()
    for rel in rels:
        digest = hashlib.sha256((root / rel).read_bytes()).digest()
        outer.update(rel.encode("utf-8"))
        outer.update(b"\x00")
        outer.update(digest)
    return outer.hexdigest(), len(rels), rels


def _scan_no_secrets(label: str, raw_text: str) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(raw_text):
            raise ValidationError(f"Secret-like credential pattern found in {label}")


def _is_sha256_hex(val: Any) -> bool:
    return isinstance(val, str) and bool(re.fullmatch(r"[0-9a-f]{64}", val))


def validate_evidence_bundle(bundle: dict[str, Any], raw_text: str | None = None) -> None:
    if raw_text is not None:
        _scan_no_secrets("evidence_bundle.json", raw_text)

    for key in ("run", "snapshot", "checks", "check_history", "messages"):
        if key not in bundle:
            raise ValidationError(f"Missing required key '{key}' in evidence_bundle.json")

    run_obj = bundle["run"]
    snap_obj = bundle["snapshot"]
    checks = bundle["checks"]
    check_history = bundle["check_history"]
    messages = bundle["messages"]

    if not isinstance(run_obj, dict) or not isinstance(snap_obj, dict):
        raise ValidationError("run and snapshot must be objects")
    if run_obj.get("status") != "ready":
        raise ValidationError(f"Expected ready run status in evidence bundle, got {run_obj.get('status')!r}")

    run_id = run_obj.get("id")
    snap_id = snap_obj.get("id")
    if not run_id or not snap_id:
        raise ValidationError("run.id and snapshot.id must be non-empty")
    if run_obj.get("snapshot_id") != snap_id:
        raise ValidationError(f"run.snapshot_id ({run_obj.get('snapshot_id')}) != snapshot.id ({snap_id})")
    if snap_obj.get("run_id") != run_id:
        raise ValidationError(f"snapshot.run_id ({snap_obj.get('run_id')}) != run.id ({run_id})")
    if snap_obj.get("revision") != run_obj.get("revision"):
        raise ValidationError(f"snapshot.revision ({snap_obj.get('revision')}) != run.revision ({run_obj.get('revision')})")

    req_n = run_obj.get("request_count")
    if not isinstance(req_n, int) or req_n <= 0:
        raise ValidationError("run.request_count must be positive int")
    if run_obj.get("expected_count") != req_n or run_obj.get("logged_count") != req_n:
        raise ValidationError("run expected_count and logged_count must equal request_count")
    for field in ("request_count", "ok_count", "not_found_count"):
        if snap_obj.get(field) != run_obj.get(field):
            raise ValidationError(f"snapshot.{field} ({snap_obj.get(field)}) != run.{field} ({run_obj.get(field)})")
    if (run_obj.get("ok_count", 0) + run_obj.get("not_found_count", 0)) > req_n:
        raise ValidationError("ok_count + not_found_count exceeds request_count")

    snap_sha = snap_obj.get("sha256")
    if not _is_sha256_hex(snap_sha):
        raise ValidationError(f"Invalid snapshot.sha256: {snap_sha!r}")

    if not isinstance(checks, list) or not isinstance(check_history, list):
        raise ValidationError("checks and check_history must be arrays")
    check_names = {c.get("name") for c in checks}
    if check_names != {"read_log", "write_log", "reach_test_service"} or len(checks) != 3:
        raise ValidationError(f"checks must contain exactly the 3 expected checks, got {[c.get('name') for c in checks]}")

    for chk in checks:
        if chk.get("run_id") != run_id:
            raise ValidationError(f"Check {chk.get('name')} run_id mismatch")
        if chk.get("snapshot_id") != snap_id or chk.get("revision") != snap_obj.get("revision"):
            raise ValidationError(f"Check {chk.get('name')} not pinned to active snapshot/revision")
        verdict = chk.get("verdict")
        if verdict not in ("pass", "fail", "error"):
            raise ValidationError(f"Invalid check verdict {verdict!r} for {chk.get('name')}")

        ctrl = chk.get("control_result") or {}
        ev = chk.get("evidence") or {}
        if verdict == "pass":
            if chk["name"] == "read_log":
                if ev.get("sha256") != snap_sha or ctrl.get("observed_sha256") != snap_sha or ctrl.get("expected_sha256") != snap_sha:
                    raise ValidationError("read_log pass check SHA-256 does not match snapshot.sha256")
                if ctrl.get("lines") != req_n:
                    raise ValidationError(f"read_log lines ({ctrl.get('lines')}) != request_count ({req_n})")
            elif chk["name"] == "write_log":
                if ev.get("sha256_before") != snap_sha or ev.get("sha256_after") != snap_sha or ev.get("source_snapshot_untouched") is not True:
                    raise ValidationError("write_log pass check source snapshot integrity mismatch")
                if ctrl.get("write_succeeded") is not True or ctrl.get("marker_found") is not True:
                    raise ValidationError("write_log pass check missing positive writable control proof")
            elif chk["name"] == "reach_test_service":
                if (
                    chk.get("observed") != "Blocked"
                    or ev.get("sandbox_blocked") is not True
                    or ev.get("sandbox_status_code") is not None
                    or ev.get("sandbox_http_response_received") is True
                    or ("exit_code" in chk and chk.get("exit_code") in (0, None))
                    or ctrl.get("reachable") is not True
                    or ctrl.get("status_code") != 200
                ):
                    raise ValidationError("reach_test_service pass check missing paired HTTP 200 control & blocked sandbox proof")
        elif verdict == "fail":
            if chk["name"] == "reach_test_service":
                if chk.get("observed") != "Allowed" or ev.get("sandbox_blocked") is True:
                    raise ValidationError("reach_test_service fail check must observe Allowed with sandbox_blocked!=True")
                if ctrl.get("status_code") != 200:
                    raise ValidationError("reach_test_service fail check requires valid HTTP 200 control")
        elif verdict == "error" and chk["name"] == "reach_test_service":
            if ctrl and ctrl.get("status_code") != 200:
                if "HTTP 200" in str(ctrl.get("summary") or "") or ev.get("control_status") == 200:
                    raise ValidationError("reach_test_service error check falsely claims HTTP 200 control")

    history_ids = {h.get("check_id") for h in check_history}
    for chk in checks:
        if chk.get("check_id") not in history_ids:
            raise ValidationError(f"Current check {chk.get('check_id')} missing from check_history")
    for hist in check_history:
        if hist.get("run_id") != run_id:
            raise ValidationError("check_history contains entry for a different run_id")
        if hist.get("verdict") not in ("pass", "fail", "error"):
            raise ValidationError(f"Invalid historical check verdict {hist.get('verdict')!r}")

    if not isinstance(messages, list):
        raise ValidationError("messages must be an array")
    for msg in messages:
        if msg.get("run_id") != run_id:
            raise ValidationError("Message belongs to different run_id")
        if msg.get("role") == "assistant" and msg.get("status") == "completed":
            tc_ids = msg.get("tool_call_ids") or []
            tool_calls = msg.get("tool_calls") or []
            if not tc_ids or not tool_calls:
                raise ValidationError(f"Completed assistant message {msg.get('id')} lacks MCP tool_calls")
            for tc in tool_calls:
                if tc.get("call_id") not in tc_ids:
                    raise ValidationError(f"Tool call {tc.get('call_id')} absent from tool_call_ids")
                if tc.get("run_id") != run_id or tc.get("snapshot_id") != msg.get("snapshot_id") or tc.get("revision") != msg.get("revision"):
                    raise ValidationError(f"Tool call {tc.get('call_id')} snapshot/revision/run mismatch")
                if tc.get("status") != "completed":
                    raise ValidationError(f"Tool call {tc.get('call_id')} status is {tc.get('status')!r}, expected completed")
                payload = tc.get("payload") or {}
                if msg.get("revision") == snap_obj.get("revision"):
                    if msg.get("snapshot_id") != snap_id:
                        raise ValidationError("Current-revision assistant message snapshot_id mismatch")
                    if "snapshot_sha256" in payload and payload["snapshot_sha256"] != snap_sha:
                        raise ValidationError("Current-revision tool payload snapshot_sha256 mismatch")
                    if tc.get("tool_name") == "count_status_codes":
                        if (
                            payload.get("total_requests") != req_n
                            or payload.get("status_200") != run_obj.get("ok_count")
                            or payload.get("status_404") != run_obj.get("not_found_count")
                        ):
                            raise ValidationError("Current-revision count_status_codes payload counts do not match run counts")


def validate_test_summary(
    summary: dict[str, Any],
    raw_text: str | None = None,
    *,
    expected_fingerprint: str | None = None,
    require_zero_skips: bool = False,
    release_strict: bool = False,
) -> None:
    if raw_text is not None:
        _scan_no_secrets("test_summary.json", raw_text)

    suites = summary.get("suites")
    totals = summary.get("totals")
    if not isinstance(suites, dict) or not isinstance(totals, dict):
        raise ValidationError("test_summary.json missing suites or totals")

    enforce_strict = require_zero_skips or release_strict
    sum_tests = sum_passed = sum_failed = sum_errors = sum_skipped = 0
    for suite_name in ("unit", "integration", "e2e"):
        suite = suites.get(suite_name)
        if not isinstance(suite, dict):
            raise ValidationError(f"Missing suite {suite_name!r} in test_summary.json")
        t = int(suite.get("tests", -1))
        p = int(suite.get("passed", -1))
        f = int(suite.get("failed", -1))
        e = int(suite.get("errors", -1))
        s = int(suite.get("skipped", -1))
        cases = suite.get("cases")
        if t < 0 or p < 0 or f < 0 or e < 0 or s < 0 or p + f + e + s != t:
            raise ValidationError(f"Suite {suite_name} counts do not sum to total tests")
        if not isinstance(cases, list) or len(cases) != t:
            raise ValidationError(f"Suite {suite_name} cases length ({len(cases) if isinstance(cases, list) else None}) != tests ({t})")
        if enforce_strict and t <= 0:
            raise ValidationError(f"Refusing strict release with empty test suite {suite_name!r} (tests={t})")
        case_counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
        for c in cases:
            st = c.get("status")
            if st not in case_counts:
                raise ValidationError(f"Invalid case status {st!r} in suite {suite_name}")
            case_counts[st] += 1
        if case_counts["passed"] != p or case_counts["failed"] != f or case_counts["error"] != e or case_counts["skipped"] != s:
            raise ValidationError(f"Suite {suite_name} case status counts do not match header counts")
        sum_tests += t
        sum_passed += p
        sum_failed += f
        sum_errors += e
        sum_skipped += s

    if (
        totals.get("tests") != sum_tests
        or totals.get("passed") != sum_passed
        or totals.get("failed") != sum_failed
        or totals.get("errors") != sum_errors
        or totals.get("skipped") != sum_skipped
    ):
        raise ValidationError("test_summary totals do not equal sum of suites")
    if sum_failed > 0 or sum_errors > 0:
        raise ValidationError(f"Refusing to build deliverables with failing/errored tests (failed={sum_failed}, errors={sum_errors})")
    if enforce_strict and (sum_tests <= 0 or sum_passed != sum_tests or sum_skipped > 0):
        raise ValidationError(
            f"Refusing strict release deliverables with skipped or non-passing tests "
            f"(tests={sum_tests}, passed={sum_passed}, skipped={sum_skipped})"
        )
    if enforce_strict:
        int_cases = suites["integration"]["cases"]
        live_cases = [
            c
            for c in int_cases
            if "test_agent_live" in str(c.get("classname") or "") or "test_live_agent" in str(c.get("name") or "")
        ]
        if not live_cases or any(c.get("status") != "passed" for c in live_cases):
            raise ValidationError(
                "Refusing strict release without a passed live LLM integration test (test_agent_live)"
            )

    if expected_fingerprint is not None:
        prov = summary.get("provenance") or {}
        got_fp = prov.get("source_fingerprint")
        if got_fp != expected_fingerprint:
            raise ValidationError(
                f"test_summary provenance.source_fingerprint ({got_fp}) != current source ({expected_fingerprint})"
            )


def parse_junit_xml_suite(xml_path: Path) -> dict[str, Any]:
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        raise ValidationError(f"Malformed JUnit XML {xml_path}: {exc}") from exc
    cases: list[dict[str, str]] = []
    passed = failed = errors = skipped = 0
    for tc in tree.findall(".//testcase"):
        cn = tc.attrib.get("classname", "")
        nm = tc.attrib.get("name", "")
        if tc.find("failure") is not None:
            st = "failed"
            failed += 1
        elif tc.find("error") is not None:
            st = "error"
            errors += 1
        elif tc.find("skipped") is not None:
            st = "skipped"
            skipped += 1
        else:
            st = "passed"
            passed += 1
        cases.append({"classname": cn, "name": nm, "status": st})
    return {
        "tests": len(cases),
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "cases": cases,
    }


def validate_junit_sync(summary: dict[str, Any], exports_dir: Path, *, require_all: bool = False) -> None:
    if not exports_dir.is_dir():
        if require_all:
            raise ValidationError(f"Missing JUnit exports directory: {exports_dir}")
        return
    present = {s: exports_dir / f"junit_{s}.xml" for s in ("unit", "integration", "e2e")}
    existing = [s for s, p in present.items() if p.is_file()]
    if not existing:
        if require_all:
            raise ValidationError(f"Missing junit_*.xml files in {exports_dir}")
        return
    if len(existing) != 3:
        missing = sorted(set(present) - set(existing))
        raise ValidationError(f"Incomplete JUnit XML exports in {exports_dir}; missing suites {missing}")
    for suite_name, xml_path in present.items():
        parsed = parse_junit_xml_suite(xml_path)
        expected = summary["suites"][suite_name]
        for field in ("tests", "passed", "failed", "errors", "skipped"):
            if parsed[field] != int(expected[field]):
                raise ValidationError(
                    f"JUnit {xml_path.name} {field}={parsed[field]} != test_summary {field}={expected[field]}"
                )
        parsed_cases = sorted((c["classname"], c["name"], c["status"]) for c in parsed["cases"])
        summary_cases = sorted((c["classname"], c["name"], c["status"]) for c in expected["cases"])
        if parsed_cases != summary_cases:
            raise ValidationError(f"JUnit {xml_path.name} test cases do not match test_summary suites.{suite_name}.cases")


def validate_assets_provenance(assets_dir: Path, bundle: dict[str, Any]) -> dict[str, bytes]:
    prov = bundle.get("provenance") or {}
    assets_meta = prov.get("assets")
    if not isinstance(assets_meta, dict):
        raise ValidationError("evidence_bundle.json missing provenance.assets integrity manifest")

    expected_run_id = bundle["run"]["id"]
    expected_snap_id = bundle["snapshot"]["id"]
    verified_bytes: dict[str, bytes] = {}
    seen_hashes: set[str] = set()

    for asset in REQUIRED_ASSETS:
        p = assets_dir / asset
        if not p.is_file() or p.stat().st_size == 0:
            raise ValidationError(f"Missing or empty deliverable visual asset: {p}")
        entry = assets_meta.get(asset)
        if not isinstance(entry, dict):
            raise ValidationError(f"provenance.assets missing entry for '{asset}'")
        exp_sha = entry.get("sha256")
        exp_size = entry.get("bytes")
        if not _is_sha256_hex(exp_sha):
            raise ValidationError(f"Invalid SHA-256 in provenance.assets['{asset}']: {exp_sha!r}")
        if not isinstance(exp_size, int) or exp_size <= 0:
            raise ValidationError(f"Invalid byte size in provenance.assets['{asset}']: {exp_size!r}")
        raw = p.read_bytes()
        actual_sha = hashlib.sha256(raw).hexdigest()
        if len(raw) != exp_size or actual_sha != exp_sha:
            raise ValidationError(
                f"Asset hash/size mismatch for '{asset}': got sha256={actual_sha} ({len(raw)} B), "
                f"expected sha256={exp_sha} ({exp_size} B)"
            )
        if actual_sha in seen_hashes:
            raise ValidationError(f"Duplicate asset SHA-256 detected for '{asset}'")
        seen_hashes.add(actual_sha)
        if asset.startswith("live-"):
            if entry.get("role") != "live_runtime_screenshot":
                raise ValidationError(f"Live screenshot '{asset}' missing role='live_runtime_screenshot'")
            if entry.get("run_id") != expected_run_id or entry.get("snapshot_id") != expected_snap_id:
                raise ValidationError(
                    f"Live screenshot '{asset}' provenance ({entry.get('run_id')}, {entry.get('snapshot_id')}) "
                    f"does not match bundle ({expected_run_id}, {expected_snap_id})"
                )
        verified_bytes[asset] = raw
    return verified_bytes


def prepare_verified_image_stream(
    verified_assets: dict[str, bytes],
    bundle: dict[str, Any],
    asset_name: str,
    *,
    crop_bottom_ratio: float | None = None,
    crop_box: tuple[float, float, float, float] | None = None,
    add_border: bool = False,
    preview_dir: Path | None = None,
) -> BytesIO:
    if asset_name not in verified_assets:
        raise ValidationError(f"Asset '{asset_name}' has not been verified")
    raw = verified_assets[asset_name]
    expected_sha = ((bundle.get("provenance") or {}).get("assets") or {}).get(asset_name, {}).get("sha256")
    actual_sha = hashlib.sha256(raw).hexdigest()
    if not expected_sha or actual_sha != expected_sha:
        raise ValidationError(f"In-memory verified buffer mismatch for '{asset_name}'")
    if crop_box is None and crop_bottom_ratio is None and not add_border:
        return BytesIO(raw)
    from PIL import Image, ImageOps

    im = Image.open(BytesIO(raw))
    if crop_box is not None:
        l_r, t_r, r_r, b_r = crop_box
        box = (int(im.width * l_r), int(im.height * t_r), int(im.width * r_r), int(im.height * b_r))
        im = im.crop(box)
    elif crop_bottom_ratio is not None:
        box = (0, 0, im.width, int(im.height * crop_bottom_ratio))
        im = im.crop(box)
    if add_border:
        im = ImageOps.expand(im, border=1, fill="#CBD5E1")
    out = BytesIO()
    im.save(out, format="PNG")
    png_bytes = out.getvalue()
    if preview_dir is not None:
        preview_dir.mkdir(parents=True, exist_ok=True)
        (preview_dir / f"cropped_{asset_name}").write_bytes(png_bytes)
    return BytesIO(png_bytes)


def validate_and_load_deliverables(
    root: Path,
    evidence_path: Path | None = None,
    summary_path: Path | None = None,
    *,
    check_assets: bool = True,
    check_fingerprint: bool = True,
    require_junit: bool = False,
    junit_dir: Path | None = None,
    release_strict: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, bytes]]:
    ev_path = evidence_path or (root / "deliverables" / "evidence" / "evidence_bundle.json")
    ts_path = summary_path or (root / "deliverables" / "evidence" / "test_summary.json")
    if not ev_path.is_file():
        raise ValidationError(f"Missing evidence bundle: {ev_path}")
    if not ts_path.is_file():
        raise ValidationError(f"Missing test summary: {ts_path}")

    ev_raw = ev_path.read_text(encoding="utf-8")
    ts_raw = ts_path.read_text(encoding="utf-8")
    bundle = json.loads(ev_raw)
    summary = json.loads(ts_raw)

    fp_hex, _, _ = compute_source_fingerprint(root)
    validate_evidence_bundle(bundle, raw_text=ev_raw)
    validate_test_summary(
        summary,
        raw_text=ts_raw,
        expected_fingerprint=(fp_hex if check_fingerprint else None),
        require_zero_skips=release_strict,
        release_strict=release_strict,
    )

    if check_fingerprint:
        ev_fp = (bundle.get("provenance") or {}).get("source_fingerprint")
        if ev_fp != fp_hex:
            raise ValidationError(
                f"evidence_bundle provenance.source_fingerprint ({ev_fp}) != current source ({fp_hex})"
            )

    if junit_dir is not None:
        effective_junit_dir = junit_dir
    elif (root / "deliverables" / "evidence" / "junit").is_dir() or release_strict:
        effective_junit_dir = root / "deliverables" / "evidence" / "junit"
    else:
        effective_junit_dir = root / "runtime" / "exports"

    validate_junit_sync(summary, effective_junit_dir, require_all=(require_junit or release_strict))

    verified_assets: dict[str, bytes] = {}
    if check_assets:
        verified_assets = validate_assets_provenance(root / "deliverables" / "assets", bundle)

    return bundle, summary, fp_hex, verified_assets


def validate_deliverable_inputs(
    root: Path,
    evidence_path: Path | None = None,
    summary_path: Path | None = None,
    *,
    check_assets: bool = True,
    check_fingerprint: bool = True,
    require_junit: bool = False,
    junit_dir: Path | None = None,
    release_strict: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    bundle, summary, fp_hex, _ = validate_and_load_deliverables(
        root,
        evidence_path=evidence_path,
        summary_path=summary_path,
        check_assets=check_assets,
        check_fingerprint=check_fingerprint,
        require_junit=require_junit,
        junit_dir=junit_dir,
        release_strict=release_strict,
    )
    return bundle, summary, fp_hex
