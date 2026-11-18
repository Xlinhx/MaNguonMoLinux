from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release.evidence_validator import ValidationError, validate_and_load_deliverables
from scripts.release import build_report, build_slides


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build Sandbox Lab deliverables (report.docx, report.pdf, slides.pptx, slides.pdf) after strict provenance gating.",
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

    # 2. Point builders at target root cleanly and render all 4 artifacts.
    for mod in (build_report, build_slides):
        mod.ROOT = root
        mod.DELIV = deliv
        mod.ASSETS = deliv / "assets"
        mod.EVIDENCE_JSON = ev_dir / "evidence_bundle.json"
        mod.TEST_SUMMARY_JSON = ev_dir / "test_summary.json"

    report_docx, report_pdf = build_report.build_report_with_real_toc()
    slides_pptx = build_slides.build_slides()
    slides_pdf = build_report._convert_docx_to_pdf(slides_pptx, deliv)

    print(
        f"[build_release] Built deliverables cleanly (strict={release_strict}, fp={fp_hex[:12]}...):\n"
        f"  - {report_docx}\n"
        f"  - {report_pdf}\n"
        f"  - {slides_pptx}\n"
        f"  - {slides_pdf}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as exc:
        print(f"RELEASE GATE REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(1)
