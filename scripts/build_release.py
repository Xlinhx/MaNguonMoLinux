from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.evidence_validator import ValidationError, validate_and_load_deliverables
from scripts.release import build_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build Sandbox Lab report deliverables (report.docx, report.pdf) after strict provenance gating.",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Allow non-strict draft build (skips requiring zero-skip release_strict JUnit/live gate, though evidence/assets/fingerprint still must validate).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root containing deliverables/ (default: project root).",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    release_strict = not args.draft

    # 1. Fail-closed gate BEFORE building or modifying any deliverable file.
    deliv = root / "deliverables"
    ev_dir = deliv / "evidence"
    bundle, summary, fp_hex, _ = validate_and_load_deliverables(
        root,
        evidence_path=ev_dir / "evidence_bundle.json",
        summary_path=ev_dir / "test_summary.json",
        check_assets=True,
        check_fingerprint=True,
        require_junit=True,
        junit_dir=ev_dir / "junit",
        release_strict=release_strict,
    )

    if release_strict:
        receipt_path = ev_dir / "linux_wsl_receipt.json"
        if not receipt_path.is_file():
            raise ValidationError(f"Missing Linux WSL2 receipt required for strict release: {receipt_path}")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("source_fingerprint") != fp_hex:
            raise ValidationError(
                f"linux_wsl_receipt.json source_fingerprint ({receipt.get('source_fingerprint')}) != current source ({fp_hex})"
            )

    # 2. Point report builder at target root cleanly and render DOCX + PDF.
    build_report.ROOT = root
    build_report.DELIV = deliv
    build_report.ASSETS = deliv / "assets"
    build_report.EVIDENCE_JSON = ev_dir / "evidence_bundle.json"
    build_report.TEST_SUMMARY_JSON = ev_dir / "test_summary.json"

    report_docx, report_pdf = build_report.build_report_with_real_toc()

    print(
        f"[build_release] Built deliverables cleanly (strict={release_strict}, fp={fp_hex[:12]}...):\n"
        f"  - {report_docx}\n"
        f"  - {report_pdf}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as exc:
        print(f"RELEASE GATE REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(1)
