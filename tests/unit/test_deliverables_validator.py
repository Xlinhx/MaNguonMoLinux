from __future__ import annotations

import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import zipfile

from docx import Document
from pptx import Presentation
import pytest

from scripts.release import build_report as report_mod
from scripts.release import build_slides as slides_mod
from scripts.release.evidence_validator import (
    REQUIRED_ASSETS,
    ValidationError,
    compute_source_fingerprint,
    validate_deliverable_inputs,
    validate_evidence_bundle,
    validate_test_summary,
)

ROOT = Path(__file__).resolve().parents[2]


def _sample_valid_bundle(source_fp: str) -> dict:
    sha = "a" * 64
    run_id = "11111111-1111-4111-8111-111111111111"
    snap_id = "22222222-2222-4222-8222-222222222222"
    c1, c2, c3 = "chk-1", "chk-2", "chk-3"
    call_id = "call-1"
    checks = [
        {
            "check_id": c1,
            "run_id": run_id,
            "snapshot_id": snap_id,
            "revision": 1,
            "name": "read_log",
            "title": "Read log",
            "policy_version": "sandbox-policy-v1",
            "expected": "Allowed",
            "observed": "Allowed",
            "verdict": "pass",
            "control_result": {"expected_sha256": sha, "observed_sha256": sha, "lines": 10},
            "evidence": {"uid": 10001, "sha256": sha, "summary": "Read OK"},
        },
        {
            "check_id": c2,
            "run_id": run_id,
            "snapshot_id": snap_id,
            "revision": 1,
            "name": "write_log",
            "title": "Write log",
            "policy_version": "sandbox-policy-v1",
            "expected": "Blocked",
            "observed": "Blocked",
            "verdict": "pass",
            "control_result": {"write_succeeded": True, "marker_found": True, "summary": "Control write OK"},
            "evidence": {"read_only_mount": "Write rejected", "sha256_before": sha, "sha256_after": sha, "source_snapshot_untouched": True},
        },
        {
            "check_id": c3,
            "run_id": run_id,
            "snapshot_id": snap_id,
            "revision": 1,
            "name": "reach_test_service",
            "title": "Reach test service",
            "policy_version": "sandbox-policy-v1",
            "expected": "Blocked",
            "observed": "Blocked",
            "verdict": "pass",
            "control_result": {"reachable": True, "status_code": 200, "summary": "Control HTTP 200"},
            "evidence": {"sandbox_blocked": True},
        },
    ]
    return {
        "provenance": {"source_fingerprint": source_fp},
        "environment": {"os": "Linux", "python": "3.12", "worker_image": "sandbox-lab-worker:v1", "policy_version": "sandbox-policy-v1"},
        "run": {
            "id": run_id,
            "display_number": 1,
            "label": "Run 001",
            "status": "ready",
            "revision": 1,
            "snapshot_id": snap_id,
            "request_count": 10,
            "expected_count": 10,
            "logged_count": 10,
            "ok_count": 7,
            "not_found_count": 3,
        },
        "snapshot": {
            "id": snap_id,
            "run_id": run_id,
            "revision": 1,
            "sha256": sha,
            "request_count": 10,
            "ok_count": 7,
            "not_found_count": 3,
        },
        "checks": checks,
        "check_history": [dict(c) for c in checks],
        "messages": [
            {
                "id": "msg-1",
                "run_id": run_id,
                "role": "assistant",
                "content": "3 of 10 requests returned 404.",
                "snapshot_id": snap_id,
                "revision": 1,
                "tool_call_ids": [call_id],
                "tool_calls": [
                    {
                        "call_id": call_id,
                        "tool_name": "count_status_codes",
                        "run_id": run_id,
                        "snapshot_id": snap_id,
                        "revision": 1,
                        "status": "completed",
                        "payload": {"total_requests": 10, "status_200": 7, "status_404": 3, "snapshot_sha256": sha},
                    }
                ],
                "status": "completed",
            }
        ],
    }


def _sample_valid_summary(source_fp: str, *, int_skipped: int = 0) -> dict:
    int_cases = [
        {"classname": "tests.integration.test_nginx_runs", "name": "test_nginx", "status": "passed"},
        {"classname": "tests.integration.test_sandbox", "name": "test_sandbox", "status": "passed"},
        {"classname": "tests.integration.test_mcp", "name": "test_mcp", "status": "passed"},
        {
            "classname": "tests.integration.test_agent_live",
            "name": "test_live",
            "status": "skipped" if int_skipped else "passed",
        },
    ]
    int_passed = 4 - int_skipped
    return {
        "provenance": {"source_fingerprint": source_fp},
        "suites": {
            "unit": {"tests": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0, "cases": [{"classname": "u", "name": "t1", "status": "passed"}]},
            "integration": {"tests": 4, "passed": int_passed, "failed": 0, "errors": 0, "skipped": int_skipped, "cases": int_cases},
            "e2e": {"tests": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0, "cases": [{"classname": "e", "name": "t2", "status": "passed"}]},
        },
        "totals": {
            "tests": 6,
            "passed": 2 + int_passed,
            "failed": 0,
            "errors": 0,
            "skipped": int_skipped,
        },
    }


def _make_fake_repo(tmp_path: Path, bundle: dict, summary: dict) -> Path:
    deliv = tmp_path / "deliverables"
    ev_dir = deliv / "evidence"
    assets = deliv / "assets"
    assets.mkdir(parents=True)
    ev_dir.mkdir(parents=True)
    # Copy real valid PNG assets and record exact provenance hashes/run_id/snapshot_id
    asset_manifest: dict[str, dict] = {}
    for name in REQUIRED_ASSETS:
        dst = assets / name
        shutil.copy2(ROOT / "deliverables" / "assets" / name, dst)
        raw = dst.read_bytes()
        asset_manifest[name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "role": "live_runtime_screenshot" if name.startswith("live-") else "diagram",
            "run_id": bundle["run"]["id"] if name.startswith("live-") else None,
            "snapshot_id": bundle["snapshot"]["id"] if name.startswith("live-") else None,
        }
    bundle.setdefault("provenance", {})["assets"] = asset_manifest
    (ev_dir / "evidence_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    (ev_dir / "test_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    # Put one deterministic tracked file so compute_source_fingerprint is well-defined if needed
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "__init__.py").write_text("", encoding="utf-8")
    fp, _, _ = compute_source_fingerprint(tmp_path)
    bundle["provenance"]["source_fingerprint"] = fp
    summary["provenance"]["source_fingerprint"] = fp
    (ev_dir / "evidence_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    (ev_dir / "test_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "mutator",
    [
        lambda b: b["run"].__setitem__("request_count", 999),
        lambda b: b["snapshot"].__setitem__("id", "wrong-snap"),
        lambda b: b["snapshot"].__setitem__("sha256", "badhash"),
        lambda b: b["checks"][0].__setitem__("snapshot_id", "stale-snap"),
        lambda b: b["checks"][1].__setitem__("check_id", "not-in-history"),
        lambda b: b["messages"][0].__setitem__("tool_calls", []),
        lambda b: b["messages"][0]["tool_calls"][0]["payload"].__setitem__("status_404", 99),
    ],
)
def test_validator_rejects_corrupted_evidence_invariants(mutator) -> None:
    bundle = _sample_valid_bundle("0" * 64)
    mutator(bundle)
    with pytest.raises(ValidationError):
        validate_evidence_bundle(bundle)


def test_validator_rejects_secrets_mismatched_totals_stale_fingerprint_and_missing_asset(tmp_path: Path) -> None:
    bundle = _sample_valid_bundle("0" * 64)
    summary = _sample_valid_summary("0" * 64)
    repo = _make_fake_repo(tmp_path, bundle, summary)

    # Valid case succeeds
    validate_deliverable_inputs(repo)

    # Secret rejection
    bad_ev = copy.deepcopy(bundle)
    bad_ev["messages"][0]["content"] = "key sk-secret1234567890abcdef"
    with pytest.raises(ValidationError, match="Secret-like"):
        validate_evidence_bundle(bad_ev, raw_text=json.dumps(bad_ev))

    # Mismatched totals
    bad_sum = copy.deepcopy(summary)
    bad_sum["totals"]["passed"] = 999
    with pytest.raises(ValidationError):
        validate_test_summary(bad_sum)

    # Stale fingerprint
    stale_sum = copy.deepcopy(summary)
    stale_sum["provenance"]["source_fingerprint"] = "f" * 64
    (repo / "deliverables" / "evidence" / "test_summary.json").write_text(json.dumps(stale_sum), encoding="utf-8")
    with pytest.raises(ValidationError, match="source_fingerprint"):
        validate_deliverable_inputs(repo)

    # Missing asset
    (repo / "deliverables" / "evidence" / "test_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (repo / "deliverables" / "assets" / "live-agent-verified.png").unlink()
    with pytest.raises(ValidationError, match="Missing"):
        validate_deliverable_inputs(repo)


def test_builders_refuse_corrupt_bundle_and_render_non_pass_check_verdicts_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _sample_valid_bundle("0" * 64)
    # Force reach_test_service to verdict="error" and integration live test to skipped!
    bundle["checks"][2]["verdict"] = "error"
    bundle["checks"][2]["observed"] = "Error"
    bundle["checks"][2]["control_result"] = None
    bundle["checks"][2]["evidence"] = {"summary": "Worker image missing on host."}
    bundle["check_history"][2] = dict(bundle["checks"][2])

    summary = _sample_valid_summary("0" * 64, int_skipped=1)
    repo = _make_fake_repo(tmp_path, bundle, summary)

    # Monkeypatch module globals in both builders cleanly
    for mod in (report_mod, slides_mod):
        monkeypatch.setattr(mod, "ROOT", repo)
        monkeypatch.setattr(mod, "DELIV", repo / "deliverables")
        monkeypatch.setattr(mod, "ASSETS", repo / "deliverables" / "assets")
        monkeypatch.setattr(mod, "EVIDENCE_JSON", repo / "deliverables" / "evidence" / "evidence_bundle.json")
        monkeypatch.setattr(mod, "TEST_SUMMARY_JSON", repo / "deliverables" / "evidence" / "test_summary.json")

    docx_path = report_mod.build_report()
    pptx_path = slides_mod.build_slides()

    doc = Document(str(docx_path))
    doc_text = "\n".join(p.text for p in doc.paragraphs)
    for tbl in doc.tables:
        for row in tbl.rows:
            doc_text += "\n" + " | ".join(c.text for c in row.cells)
    assert "all 3 control-paired Pass verdicts" not in doc_text
    assert "passed all 3 current checks" not in doc_text
    assert "2/3" in doc_text
    assert "Control not verified" in doc_text
    assert "1 skipped" in doc_text

    prs = Presentation(str(pptx_path))
    slide_text = "\n".join(
        shape.text for slide in prs.slides for shape in slide.shapes if hasattr(shape, "text")
    )
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_table:
                for row in shape.table.rows:
                    slide_text += "\n" + " | ".join(c.text for c in row.cells)
    assert "5/6 Tests Passed (1 Skipped)" in slide_text
    assert "2/3 paired checks Pass" in slide_text
    assert "Live skipped" in slide_text

    # Corrupt request_count -> both builders MUST refuse cleanly
    bundle["run"]["request_count"] = 999
    (repo / "deliverables" / "evidence" / "evidence_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValidationError):
        report_mod.build_report()
    with pytest.raises(ValidationError):
        slides_mod.build_slides()


def test_r8_rejects_swapped_png_wrong_run_asset_corrupt_bytes_and_stale_junit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _sample_valid_bundle("0" * 64)
    summary = _sample_valid_summary("0" * 64)
    repo = _make_fake_repo(tmp_path, bundle, summary)

    for mod in (report_mod, slides_mod):
        monkeypatch.setattr(mod, "ROOT", repo)
        monkeypatch.setattr(mod, "DELIV", repo / "deliverables")
        monkeypatch.setattr(mod, "ASSETS", repo / "deliverables" / "assets")
        monkeypatch.setattr(mod, "EVIDENCE_JSON", repo / "deliverables" / "evidence" / "evidence_bundle.json")
        monkeypatch.setattr(mod, "TEST_SUMMARY_JSON", repo / "deliverables" / "evidence" / "test_summary.json")

    # Verify embedded media in valid DOCX/PPTX came from verified assets (not stale cropped cache)
    # Plant a fake stale cropped_live-traffic-verified.png on disk first -> builders must ignore it
    (repo / "deliverables" / "assets" / "cropped_live-traffic-verified.png").write_bytes(b"NOT_A_REAL_CROP")
    out_docx = report_mod.build_report()
    out_pptx = slides_mod.build_slides()
    with zipfile.ZipFile(out_docx) as zdoc:
        media_docs = [zdoc.read(n) for n in zdoc.namelist() if n.startswith("word/media/")]
        assert all(m != b"NOT_A_REAL_CROP" for m in media_docs)
    with zipfile.ZipFile(out_pptx) as zppt:
        media_ppts = [zppt.read(n) for n in zppt.namelist() if n.startswith("ppt/media/")]
        assert all(m != b"NOT_A_REAL_CROP" for m in media_ppts)

    # 1. Swap live-traffic-verified.png with valid mcp-sequence.png -> validator AND builders refuse
    saved_traffic = (repo / "deliverables" / "assets" / "live-traffic-verified.png").read_bytes()
    shutil.copy2(repo / "deliverables" / "assets" / "mcp-sequence.png", repo / "deliverables" / "assets" / "live-traffic-verified.png")
    with pytest.raises(ValidationError, match="hash/size|Duplicate"):
        validate_deliverable_inputs(repo)
    with pytest.raises(ValidationError):
        report_mod.build_report()
    with pytest.raises(ValidationError):
        slides_mod.build_slides()

    # Restore traffic PNG, corrupt 1 byte -> refuses
    (repo / "deliverables" / "assets" / "live-traffic-verified.png").write_bytes(saved_traffic[:-1] + b"\x00")
    with pytest.raises(ValidationError, match="hash/size"):
        validate_deliverable_inputs(repo)
    (repo / "deliverables" / "assets" / "live-traffic-verified.png").write_bytes(saved_traffic)

    # 2. Asset provenance bound to different snapshot/run -> refuses
    bad_meta = copy.deepcopy(bundle)
    bad_meta["provenance"]["assets"]["live-checks-verified.png"]["snapshot_id"] = "other-run-snapshot"
    (repo / "deliverables" / "evidence" / "evidence_bundle.json").write_text(json.dumps(bad_meta), encoding="utf-8")
    with pytest.raises(ValidationError, match="does not match bundle"):
        validate_deliverable_inputs(repo)
    (repo / "deliverables" / "evidence" / "evidence_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")

    # 3. Stale runtime/exports/junit_*.xml disagreeing with test_summary.json -> refuses
    exports = repo / "runtime" / "exports"
    exports.mkdir(parents=True)
    (exports / "junit_unit.xml").write_text('<testsuite><testcase classname="u" name="stale_1"/><testcase classname="u" name="stale_2"/></testsuite>', encoding="utf-8")
    (exports / "junit_integration.xml").write_text('<testsuite></testsuite>', encoding="utf-8")
    (exports / "junit_e2e.xml").write_text('<testsuite><testcase classname="e" name="t2"/></testsuite>', encoding="utf-8")
    with pytest.raises(ValidationError, match="JUnit"):
        validate_deliverable_inputs(repo)


def test_release_strict_rejects_skipped_and_missing_tracked_junit(tmp_path: Path) -> None:
    bundle = _sample_valid_bundle("0" * 64)
    summary_skip = _sample_valid_summary("0" * 64, int_skipped=1)
    repo = _make_fake_repo(tmp_path, bundle, summary_skip)
    jdir = repo / "deliverables" / "evidence" / "junit"
    jdir.mkdir(parents=True)
    (jdir / "junit_unit.xml").write_text('<testsuite><testcase classname="u" name="t1"/></testsuite>', encoding="utf-8")
    (jdir / "junit_integration.xml").write_text(
        '<testsuite>'
        '<testcase classname="tests.integration.test_nginx_runs" name="test_nginx"/>'
        '<testcase classname="tests.integration.test_sandbox" name="test_sandbox"/>'
        '<testcase classname="tests.integration.test_mcp" name="test_mcp"/>'
        '<testcase classname="tests.integration.test_agent_live" name="test_live"><skipped message="no key"/></testcase>'
        '</testsuite>',
        encoding="utf-8",
    )
    (jdir / "junit_e2e.xml").write_text('<testsuite><testcase classname="e" name="t2"/></testsuite>', encoding="utf-8")

    # Non-strict accepts valid bundle with skipped summary & tracked junit
    validate_deliverable_inputs(repo, require_junit=True, release_strict=False)
    # Strict release rejects skipped > 0
    with pytest.raises(ValidationError, match="skipped"):
        validate_deliverable_inputs(repo, release_strict=True)

    # Strict release with 0 skips STILL requires deliverables/evidence/junit/*.xml
    real_fp = compute_source_fingerprint(repo)[0]
    summary_ok = _sample_valid_summary(real_fp, int_skipped=0)
    (repo / "deliverables" / "evidence" / "test_summary.json").write_text(json.dumps(summary_ok), encoding="utf-8")
    shutil.rmtree(jdir)
    with pytest.raises(ValidationError, match="JUnit"):
        validate_deliverable_inputs(repo, release_strict=True)


def test_build_release_strict_rejects_empty_xml_missing_live_and_swapped_png_without_touching_outputs(
    tmp_path: Path,
) -> None:
    from scripts import build_release

    real_fp_init = "0" * 64
    bundle = _sample_valid_bundle(real_fp_init)
    summary = _sample_valid_summary(real_fp_init, int_skipped=0)
    repo = _make_fake_repo(tmp_path, bundle, summary)
    real_fp = compute_source_fingerprint(repo)[0]

    # Sync bundle, summary, and linux_wsl_receipt with real repo fingerprint
    ev_dir = repo / "deliverables" / "evidence"
    bundle["provenance"]["source_fingerprint"] = real_fp
    (ev_dir / "evidence_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    summary["provenance"]["source_fingerprint"] = real_fp
    (ev_dir / "test_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (ev_dir / "linux_wsl_receipt.json").write_text(
        json.dumps({"source_fingerprint": real_fp, "command": "test"}),
        encoding="utf-8",
    )

    jdir = ev_dir / "junit"
    jdir.mkdir(parents=True)
    # Seed sentinel outputs that MUST remain untouched whenever build_release refuses!
    sentinel_docx = repo / "deliverables" / "report.docx"
    sentinel_docx.write_bytes(b"SENTINEL_UNTOUCHED")

    # 1. Empty suite (tests=0) -> strict gate refuses
    empty_summary = _sample_valid_summary(real_fp, int_skipped=0)
    empty_summary["suites"]["e2e"] = {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "cases": []}
    empty_summary["totals"]["tests"] -= 1
    empty_summary["totals"]["passed"] -= 1
    (ev_dir / "test_summary.json").write_text(json.dumps(empty_summary), encoding="utf-8")
    (jdir / "junit_unit.xml").write_text('<testsuite><testcase classname="u" name="t1"/></testsuite>', encoding="utf-8")
    (jdir / "junit_integration.xml").write_text(
        '<testsuite>'
        '<testcase classname="tests.integration.test_nginx_runs" name="test_nginx"/>'
        '<testcase classname="tests.integration.test_sandbox" name="test_sandbox"/>'
        '<testcase classname="tests.integration.test_mcp" name="test_mcp"/>'
        '<testcase classname="tests.integration.test_agent_live" name="test_live"/>'
        '</testsuite>',
        encoding="utf-8",
    )
    (jdir / "junit_e2e.xml").write_text("<testsuite></testsuite>", encoding="utf-8")
    with pytest.raises(ValidationError, match="empty"):
        build_release.main(["--root", str(repo)])
    assert sentinel_docx.read_bytes() == b"SENTINEL_UNTOUCHED"

    # 2. Missing live LLM integration test -> strict gate refuses
    no_live_summary = _sample_valid_summary(real_fp, int_skipped=0)
    no_live_summary["suites"]["integration"]["cases"] = no_live_summary["suites"]["integration"]["cases"][:3]
    no_live_summary["suites"]["integration"]["tests"] = 3
    no_live_summary["suites"]["integration"]["passed"] = 3
    no_live_summary["totals"]["tests"] = 5
    no_live_summary["totals"]["passed"] = 5
    (ev_dir / "test_summary.json").write_text(json.dumps(no_live_summary), encoding="utf-8")
    (jdir / "junit_integration.xml").write_text(
        '<testsuite>'
        '<testcase classname="tests.integration.test_nginx_runs" name="test_nginx"/>'
        '<testcase classname="tests.integration.test_sandbox" name="test_sandbox"/>'
        '<testcase classname="tests.integration.test_mcp" name="test_mcp"/>'
        '</testsuite>',
        encoding="utf-8",
    )
    (jdir / "junit_e2e.xml").write_text('<testsuite><testcase classname="e" name="t2"/></testsuite>', encoding="utf-8")
    with pytest.raises(ValidationError, match="live LLM"):
        build_release.main(["--root", str(repo)])
    assert sentinel_docx.read_bytes() == b"SENTINEL_UNTOUCHED"

    # 3. Restore valid summary & XML, swap PNG -> strict gate refuses BEFORE touching report.docx
    (ev_dir / "test_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (jdir / "junit_integration.xml").write_text(
        '<testsuite>'
        '<testcase classname="tests.integration.test_nginx_runs" name="test_nginx"/>'
        '<testcase classname="tests.integration.test_sandbox" name="test_sandbox"/>'
        '<testcase classname="tests.integration.test_mcp" name="test_mcp"/>'
        '<testcase classname="tests.integration.test_agent_live" name="test_live"/>'
        '</testsuite>',
        encoding="utf-8",
    )
    shutil.copy2(repo / "deliverables" / "assets" / "mcp-sequence.png", repo / "deliverables" / "assets" / "live-agent-verified.png")
    with pytest.raises(ValidationError):
        build_release.main(["--root", str(repo)])
    assert sentinel_docx.read_bytes() == b"SENTINEL_UNTOUCHED"


def test_report_two_sections_page1_at_chapter1_and_table_4_1_na(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docx.oxml.ns import qn

    bundle = _sample_valid_bundle("0" * 64)
    summary = _sample_valid_summary("0" * 64, int_skipped=0)
    repo = _make_fake_repo(tmp_path, bundle, summary)
    for mod in (report_mod,):
        monkeypatch.setattr(mod, "ROOT", repo)
        monkeypatch.setattr(mod, "DELIV", repo / "deliverables")
        monkeypatch.setattr(mod, "ASSETS", repo / "deliverables" / "assets")
        monkeypatch.setattr(mod, "EVIDENCE_JSON", repo / "deliverables" / "evidence" / "evidence_bundle.json")
        monkeypatch.setattr(mod, "TEST_SUMMARY_JSON", repo / "deliverables" / "evidence" / "test_summary.json")

    docx_path = report_mod.build_report()
    doc = Document(str(docx_path))
    assert len(doc.sections) == 2
    # Section 0 has no page footer; Section 1 resets page numbering to 1
    assert doc.sections[0].footer.paragraphs[0].text == ""
    sec2 = doc.sections[1]
    assert sec2.different_first_page_header_footer is False
    assert sec2.footer.is_linked_to_previous is False
    pg_types = sec2._sectPr.findall(qn("w:pgNumType"))
    assert len(pg_types) == 1 and pg_types[0].get(qn("w:start")) == "1"
    assert "Page" in sec2.footer.paragraphs[0].text

    all_tables_text = "\n".join(
        " | ".join(c.text for c in r.cells) for t in doc.tables for r in t.rows
    )
    assert "Outside mount marker" in all_tables_text
    assert "Test: Pass | App: N/A (worker probe)" in all_tables_text
    assert "Chapter 1. TECHNICAL BACKGROUND" in all_tables_text
