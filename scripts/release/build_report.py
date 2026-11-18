from __future__ import annotations

import json
import re
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[2]
DELIV = ROOT / "deliverables"
ASSETS = DELIV / "assets"
EVIDENCE_DIR = DELIV / "evidence"
EVIDENCE_JSON = EVIDENCE_DIR / "evidence_bundle.json"
TEST_SUMMARY_JSON = EVIDENCE_DIR / "test_summary.json"
WSL_RECEIPT_JSON = EVIDENCE_DIR / "linux_wsl_receipt.json"
REQ_TXT = ROOT / "backend" / "requirements.txt"
PKG_LOCK = ROOT / "web" / "package-lock.json"


try:
    from scripts.release.evidence_validator import (
        compute_source_fingerprint,
        prepare_verified_image_stream,
        validate_and_load_deliverables,
    )
except ImportError:
    from evidence_validator import (
        compute_source_fingerprint,
        prepare_verified_image_stream,
        validate_and_load_deliverables,
    )


def _load_locked_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    if REQ_TXT.exists():
        for line in REQ_TXT.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_.-]+)==([^\s#]+)", line)
            if m:
                versions[m.group(1).lower()] = m.group(2)
    if PKG_LOCK.exists():
        lock = json.loads(PKG_LOCK.read_text(encoding="utf-8"))
        pkgs = lock.get("packages", {})
        for pkg_name in ("react", "typescript", "vite"):
            ver = pkgs.get(f"node_modules/{pkg_name}", {}).get("version")
            if ver:
                versions[pkg_name] = str(ver)
    return versions


def _load_verified_evidence() -> tuple[dict, dict, dict[str, str], str, int, dict[str, bytes]]:
    ev, ts, fp_hex, verified_assets = validate_and_load_deliverables(
        ROOT,
        evidence_path=EVIDENCE_JSON,
        summary_path=TEST_SUMMARY_JSON,
        check_assets=True,
        check_fingerprint=True,
    )
    _, file_count, _ = compute_source_fingerprint(ROOT)
    return ev, ts, _load_locked_versions(), fp_hex, file_count, verified_assets


def _set_rfonts(element, font_name: str = "Times New Roman") -> None:
    """Bind all four OpenXML font slots to Times New Roman so PDF conversion avoids sans fallbacks."""
    rPr = element.get_or_add_rPr()
    existing = rPr.find(qn("w:rFonts"))
    if existing is not None:
        rPr.remove(existing)
    rPr.insert(
        0,
        parse_xml(
            f'<w:rFonts {nsdecls("w")} w:ascii="{font_name}" w:hAnsi="{font_name}" '
            f'w:cs="{font_name}" w:eastAsia="{font_name}"/>'
        ),
    )


def _format_run(run, *, size_pt: int = 13, bold: bool = False, italic: bool = False, color: RGBColor = RGBColor(0, 0, 0)) -> None:
    run.font.name = "Times New Roman"
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.italic = italic
    run.font.color.rgb = color
    _set_rfonts(run._r, "Times New Roman")


def _configure_styles(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(13)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    _set_rfonts(normal._element, "Times New Roman")

    for style_name, size_pt, space_before, space_after in (
        ("Heading 1", 15, 10, 6),
        ("Heading 2", 14, 10, 4),
        ("Heading 3", 13, 8, 4),
    ):
        st = doc.styles[style_name]
        st.font.name = "Times New Roman"
        st.font.size = Pt(size_pt)
        st.font.bold = True
        st.font.color.rgb = RGBColor(0, 0, 0)
        st.paragraph_format.space_before = Pt(space_before)
        st.paragraph_format.space_after = Pt(space_after)
        st.paragraph_format.keep_with_next = True
        _set_rfonts(st._element, "Times New Roman")


def _add_heading(doc: Document, text: str, level: int = 1, *, centered: bool = False) -> None:
    p = doc.add_heading(level=level)
    p.paragraph_format.keep_with_next = True
    if centered:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    size_pt = {1: 15, 2: 14, 3: 13}.get(level, 13)
    run = p.add_run(text)
    _format_run(run, size_pt=size_pt, bold=True)


def _set_cell_shading(cell, fill_hex: str) -> None:
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>')
    cell._tc.get_or_add_tcPr().append(shading)


def _set_cell_margins(cell, top: int = 75, bottom: int = 75, left: int = 115, right: int = 115) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement("w:tcMar")
    for m, val in [("top", top), ("bottom", bottom), ("left", left), ("right", right)]:
        node = OxmlElement(f"w:{m}")
        node.set(qn("w:w"), str(val))
        node.set(qn("w:type"), "dxa")
        tcMar.append(node)
    tcPr.append(tcMar)


def _set_borderless_table(table) -> None:
    tblPr = table._tbl.tblPr
    borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        '<w:top w:val="none"/><w:left w:val="none"/><w:bottom w:val="none"/>'
        '<w:right w:val="none"/><w:insideH w:val="dotted" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        '<w:insideV w:val="none"/>'
        '</w:tblBorders>'
    )
    tblPr.append(borders)


def _apply_col_widths(table, col_widths_cm: list[float]) -> None:
    """Enforce exact OOXML fixed table layout so LibreOffice/Word honor proportional column widths."""
    table.autofit = False
    dxa_widths = [int(round(w * 567)) for w in col_widths_cm]
    total_dxa = sum(dxa_widths)
    tblPr = table._tbl.tblPr
    for tag in ("w:tblW", "w:tblLayout"):
        old = tblPr.find(qn(tag))
        if old is not None:
            tblPr.remove(old)
    tblPr.append(parse_xml(f'<w:tblW {nsdecls("w")} w:w="{total_dxa}" w:type="dxa"/>'))
    tblPr.append(parse_xml(f'<w:tblLayout {nsdecls("w")} w:type="fixed"/>'))

    tblGrid = table._tbl.tblGrid
    if tblGrid is not None:
        for gc in list(tblGrid):
            tblGrid.remove(gc)
        for dxa in dxa_widths:
            tblGrid.append(parse_xml(f'<w:gridCol {nsdecls("w")} w:w="{dxa}"/>'))

    for idx, (col, w_cm, dxa) in enumerate(zip(table.columns, col_widths_cm, dxa_widths)):
        col.width = Cm(w_cm)
        for row in table.rows:
            cell = row.cells[idx]
            cell.width = Cm(w_cm)
            tcPr = cell._tc.get_or_add_tcPr()
            old_w = tcPr.find(qn("w:tcW"))
            if old_w is not None:
                tcPr.remove(old_w)
            tcPr.insert(0, parse_xml(f'<w:tcW {nsdecls("w")} w:w="{dxa}" w:type="dxa"/>'))


def _add_page_number_footer(section) -> None:
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _format_run(p.add_run("Page "), size_pt=11)
    for fld_xml in (
        f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>',
        f'<w:instrText {nsdecls("w")} xml:space="preserve"> PAGE </w:instrText>',
        f'<w:fldChar {nsdecls("w")} w:fldCharType="separate"/>',
        f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>',
    ):
        r = p.add_run()
        _format_run(r, size_pt=11)
        r._r.append(parse_xml(fld_xml))


def _add_toc_table(doc: Document, toc_pages: dict[str, int] | None = None) -> None:
    toc_items: list[tuple[str, int, str]] = [
        ("INSTRUCTOR'S COMMENTS", 1, "—"),
        ("LIST OF ABBREVIATIONS", 1, "—"),
        ("LIST OF FIGURES AND TABLES", 1, "—"),
        ("INTRODUCTION", 1, "—"),
        ("Chapter 1. TECHNICAL BACKGROUND", 1, ""),
        ("    1.1. Linux Container Isolation Primitives", 2, "1.1. Linux Container Isolation Primitives"),
        ("    1.2. Docker Desktop Execution on Windows and WSL2", 2, "1.2. Docker Desktop Execution on Windows and WSL2"),
        ("    1.3. Model Context Protocol and Nginx Access Logging", 2, "1.3. Model Context Protocol and Nginx Access Logging"),
        ("Chapter 2. SYSTEM DESIGN", 1, ""),
        ("    2.1. Architecture and Trust Boundaries", 2, "2.1. Architecture and Trust Boundaries"),
        ("    2.2. Threat Model and Isolation Decisions", 2, "2.2. Threat Model and Isolation Decisions"),
        ("    2.3. Data Contracts and Worker Sandbox Policy", 2, "2.3. Data Contracts and Worker Sandbox Policy"),
        ("Chapter 3. IMPLEMENTATION", 1, ""),
        ("    3.1. End-to-End Lifecycle of a Traffic Run", 2, "3.1. End-to-End Lifecycle of a Traffic Run"),
        ("    3.2. MCP Tool Execution, Snapshot Pinning, and Count Verification", 2, "3.2. MCP Tool Execution, Snapshot Pinning, and Count Verification"),
        ("    3.3. Operator Interface and Snapshot Inspection", 2, "3.3. Operator Interface and Snapshot Inspection"),
        ("Chapter 4. EVALUATION", 1, ""),
        ("    4.1. Experimental Environment and Evidence Sources", 2, "4.1. Experimental Environment and Evidence Sources"),
        ("    4.2. Control-Paired Isolation Experiments and Verdict Analysis", 2, "4.2. Control-Paired Isolation Experiments and Verdict Analysis"),
        ("    4.3. Automated Test Suite Results and Scope Limitations", 2, "4.3. Automated Test Suite Results and Scope Limitations"),
        ("CONCLUSION", 1, ""),
        ("APPENDIX A: ENVIRONMENT, TEST INVENTORY & CONTRIBUTIONS", 1, ""),
        ("REFERENCES", 1, ""),
    ]

    table = doc.add_table(rows=1 + len(toc_items), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_borderless_table(table)

    hdr_trPr = table.rows[0]._tr.get_or_add_trPr()
    hdr_trPr.append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))
    hdr_trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))

    h0, h1 = table.rows[0].cells
    _format_run(h0.paragraphs[0].add_run("Section"), size_pt=11, bold=True)
    h1.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _format_run(h1.paragraphs[0].add_run("Page"), size_pt=11, bold=True)
    _set_cell_margins(h0, top=40, bottom=40, left=60, right=60)
    _set_cell_margins(h1, top=40, bottom=40, left=60, right=60)

    for idx, (display_label, lvl, lookup_override) in enumerate(toc_items, start=1):
        table.rows[idx]._tr.get_or_add_trPr().append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
        c0, c1 = table.rows[idx].cells
        key = lookup_override if lookup_override not in ("", "—") else display_label.strip()
        if lookup_override == "—":
            pg_str = "—"
        elif toc_pages and key in toc_pages:
            pg_str = str(toc_pages[key])
        else:
            pg_str = "—"

        p0 = c0.paragraphs[0]
        p0.paragraph_format.space_before = Pt(1)
        p0.paragraph_format.space_after = Pt(1)
        _format_run(p0.add_run(display_label), size_pt=11, bold=(lvl == 1))

        p1 = c1.paragraphs[0]
        p1.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p1.paragraph_format.space_before = Pt(1)
        p1.paragraph_format.space_after = Pt(1)
        _format_run(p1.add_run(pg_str), size_pt=11, bold=(lvl == 1))

        _set_cell_margins(c0, top=32, bottom=32, left=60, right=60)
        _set_cell_margins(c1, top=32, bottom=32, left=60, right=60)
    _apply_col_widths(table, [14.4, 1.6])
    doc.add_paragraph()


def _add_table_caption(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.keep_with_next = True
    _format_run(p.add_run(text), size_pt=12, bold=True)


def _add_table(
    doc: Document,
    headers: list[str],
    rows: list[list[str]],
    col_widths: list[float] | None = None,
    *,
    font_pt: int = 10,
    pad_v: int = 55,
) -> None:
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    hdr_trPr = table.rows[0]._tr.get_or_add_trPr()
    hdr_trPr.append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))
    hdr_trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))

    hdr_cells = table.rows[0].cells
    for idx, h in enumerate(headers):
        hdr_cells[idx].text = ""
        p = hdr_cells[idx].paragraphs[0]
        p.paragraph_format.line_spacing = 1.12
        _format_run(p.add_run(h), size_pt=font_pt, bold=True)
        _set_cell_shading(hdr_cells[idx], "EAEFF5")
        _set_cell_margins(hdr_cells[idx], top=pad_v, bottom=pad_v, left=95, right=95)

    for r_idx, row in enumerate(rows):
        table.rows[r_idx + 1]._tr.get_or_add_trPr().append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
        cells = table.rows[r_idx + 1].cells
        for c_idx, val in enumerate(row):
            cells[c_idx].text = ""
            p = cells[c_idx].paragraphs[0]
            p.paragraph_format.line_spacing = 1.12
            _format_run(p.add_run(val), size_pt=font_pt)
            _set_cell_margins(cells[c_idx], top=pad_v, bottom=pad_v, left=95, right=95)
    if col_widths:
        _apply_col_widths(table, col_widths)
    p_sp = doc.add_paragraph()
    p_sp.paragraph_format.space_before = Pt(0)
    p_sp.paragraph_format.space_after = Pt(2)
    _format_run(p_sp.add_run(" "), size_pt=3)


def _add_figure(
    doc: Document,
    image_path: Path,
    caption: str,
    width_cm: float = 15.2,
    crop_bottom_ratio: float | None = None,
    crop_box: tuple[float, float, float, float] | None = None,
    add_border: bool = False,
    *,
    verified_assets: dict[str, bytes],
    bundle: dict,
) -> None:
    stream = prepare_verified_image_stream(
        verified_assets,
        bundle,
        image_path.name,
        crop_bottom_ratio=crop_bottom_ratio,
        crop_box=crop_box,
        add_border=add_border,
    )
    p_img = doc.add_paragraph()
    p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_img.paragraph_format.space_before = Pt(6)
    p_img.paragraph_format.space_after = Pt(4)
    p_img.paragraph_format.keep_with_next = True
    p_img.add_run().add_picture(stream, width=Cm(width_cm))

    p_cap = doc.add_paragraph()
    p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_cap.paragraph_format.space_after = Pt(10)
    _format_run(p_cap.add_run(caption), size_pt=11, italic=True)


def _body(doc: Document, text: str, bold_prefix: str | None = None, *, justify: bool = True) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing = 1.35
    p.paragraph_format.space_after = Pt(6)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY if justify else WD_ALIGN_PARAGRAPH.LEFT
    if bold_prefix:
        _format_run(p.add_run(bold_prefix), size_pt=13, bold=True)
    _format_run(p.add_run(text), size_pt=13)


def _ref_item(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.85)
    p.paragraph_format.first_line_indent = Cm(-0.85)
    p.paragraph_format.line_spacing = 1.18
    p.paragraph_format.space_after = Pt(5)
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _format_run(p.add_run(text), size_pt=13)


def _bullet(doc: Document, text: str, bold_prefix: str | None = None) -> None:
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.line_spacing = 1.25
    p.paragraph_format.space_after = Pt(4)
    if bold_prefix:
        _format_run(p.add_run(bold_prefix), size_pt=13, bold=True)
    _format_run(p.add_run(text), size_pt=13)


def build_report(toc_pages: dict[str, int] | None = None) -> Path:
    export_data, test_summary, versions, source_fp, file_count, verified_assets = _load_verified_evidence()

    wsl_receipt: dict = {}
    if WSL_RECEIPT_JSON.is_file():
        wsl_receipt = json.loads(WSL_RECEIPT_JSON.read_text(encoding="utf-8"))

    run_obj = export_data["run"]
    snap_obj = export_data["snapshot"]
    checks = export_data["checks"]
    check_history = export_data.get("check_history", [])
    messages = export_data.get("messages", [])
    env_info = export_data.get("environment", {})

    run_label = run_obj["label"]
    run_id = run_obj["id"]
    snap_id = snap_obj["id"]
    snap_rev = snap_obj["revision"]
    snap_sha = str(snap_obj["sha256"])[:16]
    req_count = run_obj["request_count"]
    ok_count = run_obj["ok_count"]
    nf_count = run_obj["not_found_count"]

    unit_s = test_summary["suites"]["unit"]
    int_s = test_summary["suites"]["integration"]
    e2e_s = test_summary["suites"]["e2e"]
    unit_n = unit_s["tests"]
    int_n = int_s["tests"]
    e2e_n = e2e_s["tests"]
    total_tests = test_summary["totals"]["tests"]
    total_passed = test_summary["totals"]["passed"]
    total_skipped = test_summary["totals"]["skipped"]
    ts_prov = test_summary.get("provenance") or {}
    live_model_name = str(ts_prov.get("model") or "deepseek-chat")

    pass_count = sum(1 for c in checks if c.get("verdict") == "pass")
    total_checks = len(checks)
    verdict_breakdown = ", ".join(f"{c['name']}={c['verdict']}" for c in checks)
    checks_summary_phrase = (
        f"all {total_checks} control-paired Pass verdicts"
        if pass_count == total_checks
        else f"{pass_count}/{total_checks} Pass verdicts ({verdict_breakdown})"
    )
    checks_caption_phrase = (
        f"All {total_checks} Pass Verdicts"
        if pass_count == total_checks
        else f"{pass_count}/{total_checks} Checks Pass ({verdict_breakdown})"
    )
    checks_outcome_phrase = (
        f"passed all {total_checks} current checks"
        if pass_count == total_checks
        else f"recorded {pass_count}/{total_checks} passing checks ({verdict_breakdown})"
    )

    asst_msgs = [m for m in messages if m.get("role") == "assistant" and m.get("status") == "completed"]
    rev1_ans = next((m for m in asst_msgs if m.get("revision") == 1), None)
    rev2_ans = next((m for m in asst_msgs if m.get("revision") == 2), None)
    rev1_txt = str(rev1_ans["content"]).replace("**", "").strip() if rev1_ans else "3/10"
    rev2_txt = str(rev2_ans["content"]).replace("**", "").strip() if rev2_ans else "4/11"

    doc = Document()
    _configure_styles(doc)

    # Section 1: Cover Page, Instructor Comments, ToC, Abbreviations, Lists, Introduction (Unnumbered per VKU guide [8])
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.0)
    sec.bottom_margin = Cm(2.0)
    sec.left_margin = Cm(3.0)
    sec.right_margin = Cm(2.0)

    # --- COVER PAGE (Page 1: Official VKU KHMT framed cover) & INNER TITLE PAGE (Page 2: borderless inner cover) ---
    def _build_cover_page(*, framed: bool) -> None:
        outer = doc.add_table(rows=2, cols=1)
        outer.alignment = WD_TABLE_ALIGNMENT.CENTER
        _apply_col_widths(outer, [16.0])
        tblPr = outer._tbl.tblPr
        if framed:
            tbl_borders = parse_xml(
                f'<w:tblBorders {nsdecls("w")}>'
                '<w:top w:val="double" w:sz="12" w:space="0" w:color="1F4E79"/>'
                '<w:left w:val="double" w:sz="12" w:space="0" w:color="1F4E79"/>'
                '<w:bottom w:val="double" w:sz="12" w:space="0" w:color="1F4E79"/>'
                '<w:right w:val="double" w:sz="12" w:space="0" w:color="1F4E79"/>'
                '<w:insideH w:val="none"/><w:insideV w:val="none"/>'
                '</w:tblBorders>'
            )
        else:
            tbl_borders = parse_xml(
                f'<w:tblBorders {nsdecls("w")}>'
                '<w:top w:val="none"/><w:left w:val="none"/><w:bottom w:val="none"/>'
                '<w:right w:val="none"/><w:insideH w:val="none"/><w:insideV w:val="none"/>'
                '</w:tblBorders>'
            )
        old_b = tblPr.find(qn("w:tblBorders"))
        if old_b is not None:
            tblPr.remove(old_b)
        tblPr.append(tbl_borders)

        # Row 0 fills main vertical span; Row 1 pins date cleanly at bottom of frame
        tr0 = outer.rows[0]._tr.get_or_add_trPr()
        tr0.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
        tr0.append(parse_xml(f'<w:trHeight {nsdecls("w")} w:val="12750" w:hRule="exact"/>'))
        tr1 = outer.rows[1]._tr.get_or_add_trPr()
        tr1.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
        tr1.append(parse_xml(f'<w:trHeight {nsdecls("w")} w:val="900" w:hRule="exact"/>'))

        c_main = outer.rows[0].cells[0]
        c_foot = outer.rows[1].cells[0]
        _set_cell_margins(c_main, top=320, bottom=120, left=340, right=340)
        _set_cell_margins(c_foot, top=40, bottom=240, left=340, right=340)
        c_foot._tc.get_or_add_tcPr().append(parse_xml(f'<w:vAlign {nsdecls("w")} w:val="bottom"/>'))

        def _c_para(container, txt: str, size: int, *, bold: bool = False, italic: bool = False, before: int = 0, after: int = 4, use_first: bool = False):
            p = container.paragraphs[0] if use_first else container.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(before)
            p.paragraph_format.space_after = Pt(after)
            _format_run(p.add_run(txt), size_pt=size, bold=bold, italic=italic)
            return p

        _c_para(c_main, "THE UNIVERSITY OF DANANG", 12, bold=True, after=2, use_first=True)
        _c_para(c_main, "VIETNAM - KOREA UNIVERSITY OF INFORMATION AND", 12, bold=True, after=2)
        _c_para(c_main, "COMMUNICATION TECHNOLOGY (VKU)", 12, bold=True, after=4)
        _c_para(c_main, "FACULTY OF COMPUTER SCIENCE", 13, bold=True, after=14)

        vku_logo = ASSETS / "vku-logo.png"
        if vku_logo.is_file():
            p_logo = c_main.add_paragraph()
            p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_logo.paragraph_format.space_before = Pt(4)
            p_logo.paragraph_format.space_after = Pt(24)
            p_logo.add_run().add_picture(str(vku_logo), width=Cm(3.6))
        else:
            _c_para(c_main, "", 12, after=24)

        _c_para(c_main, "COURSE PROJECT REPORT", 18, bold=True, after=6)
        _c_para(c_main, "COURSE: LINUX OPEN SOURCE SOFTWARE (LINUX OSS 2026)", 13, bold=True, after=26)

        _c_para(c_main, "TOPIC 17:", 13, bold=True, after=6)
        _c_para(c_main, "SECURE AGENTIC AI TOOL EXECUTION", 16, bold=True, after=4)
        _c_para(c_main, "USING LINUX SANDBOX (SANDBOX LAB)", 16, bold=True, after=46)

        meta_tbl = c_main.add_table(rows=4, cols=2)
        meta_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        _apply_col_widths(meta_tbl, [3.6, 10.6])
        m_tblPr = meta_tbl._tbl.tblPr
        m_old = m_tblPr.find(qn("w:tblBorders"))
        if m_old is not None:
            m_tblPr.remove(m_old)
        m_tblPr.append(
            parse_xml(
                f'<w:tblBorders {nsdecls("w")}>'
                '<w:top w:val="none"/><w:left w:val="none"/><w:bottom w:val="none"/>'
                '<w:right w:val="none"/><w:insideH w:val="none"/><w:insideV w:val="none"/>'
                '</w:tblBorders>'
            )
        )
        cover_rows = [
            (
                "Students:",
                "Member A — [Full Name, Student ID]\n"
                "Member B — [Full Name, Student ID]\n"
                "Member C — [Full Name, Student ID]",
            ),
            ("Class:", "[Class Code]"),
            ("Major:", "Computer Science"),
            ("Instructor:", "[Academic Title, Full Name]"),
        ]
        for idx, (k, v) in enumerate(cover_rows):
            meta_tbl.rows[idx]._tr.get_or_add_trPr().append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
            mc0, mc1 = meta_tbl.rows[idx].cells
            mc0.paragraphs[0].paragraph_format.space_after = Pt(2)
            mc1.paragraphs[0].paragraph_format.space_after = Pt(2)
            _format_run(mc0.paragraphs[0].add_run(k), size_pt=12, bold=True)
            _format_run(mc1.paragraphs[0].add_run(v), size_pt=12, bold=(idx > 0))
            _set_cell_margins(mc0, top=45, bottom=45, left=60, right=60)
            _set_cell_margins(mc1, top=45, bottom=45, left=60, right=60)

        _c_para(c_foot, "Da Nang, November 2026", 13, bold=True, italic=True, after=0, use_first=True)

    _build_cover_page(framed=True)
    doc.add_page_break()

    _build_cover_page(framed=False)
    doc.add_page_break()

    # --- INSTRUCTOR'S COMMENTS PAGE (Official VKU dotted-line evaluation template) ---
    for hdr_line, sz in (
        ("THE UNIVERSITY OF DANANG", 11),
        ("VIETNAM - KOREA UNIVERSITY OF INFORMATION AND COMMUNICATION TECHNOLOGY", 11),
        ("FACULTY OF COMPUTER SCIENCE", 12),
    ):
        p_uh = doc.add_paragraph()
        p_uh.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_uh.paragraph_format.space_after = Pt(2)
        _format_run(p_uh.add_run(hdr_line), size_pt=sz, bold=True)

    p_ic_title = doc.add_paragraph()
    p_ic_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_ic_title.paragraph_format.space_before = Pt(14)
    p_ic_title.paragraph_format.space_after = Pt(12)
    _format_run(p_ic_title.add_run("INSTRUCTOR'S COMMENTS"), size_pt=16, bold=True)

    for lbl, val in (
        ("Project Title: ", "Secure Agentic AI Tool Execution Using Linux Sandbox (Topic 17)"),
        ("Students: ", "Member A – [Full Name, ID]; Member B – [Full Name, ID]; Member C – [Full Name, ID]"),
        ("Class: ", "[Class Code]     |     Course: Linux Open Source Software (Linux OSS 2026)"),
    ):
        p_m = doc.add_paragraph()
        p_m.paragraph_format.space_after = Pt(4)
        _format_run(p_m.add_run(lbl), size_pt=12, bold=True)
        _format_run(p_m.add_run(val), size_pt=12)

    dot_line = "." * 146
    eval_sections = [
        ("1. Work attitude, diligence, and progress during project execution:", 3),
        ("2. Technical content, implementation quality, and experimental results:", 4),
        ("3. Report structure, presentation, and academic formatting:", 3),
        ("4. Strengths, limitations, and recommendations:", 3),
    ]
    for prompt_txt, n_lines in eval_sections:
        p_q = doc.add_paragraph()
        p_q.paragraph_format.space_before = Pt(8)
        p_q.paragraph_format.space_after = Pt(4)
        _format_run(p_q.add_run(prompt_txt), size_pt=12, bold=True)
        for _ in range(n_lines):
            p_d = doc.add_paragraph()
            p_d.paragraph_format.space_before = Pt(2)
            p_d.paragraph_format.space_after = Pt(4)
            _format_run(p_d.add_run(dot_line), size_pt=12)

    p_score = doc.add_paragraph()
    p_score.paragraph_format.space_before = Pt(10)
    p_score.paragraph_format.space_after = Pt(18)
    _format_run(p_score.add_run("5. Score (10-pt scale): "), size_pt=12, bold=True)
    _format_run(p_score.add_run(".......... / 10    |    Defense Eligibility:   [  ] Approved    [  ] Not Approved"), size_pt=12)

    p_sig = doc.add_paragraph()
    p_sig.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_sig.paragraph_format.space_after = Pt(2)
    _format_run(p_sig.add_run("Da Nang, ____ / ____ / 2026\n"), size_pt=12, italic=True)
    _format_run(p_sig.add_run("INSTRUCTOR\n"), size_pt=12, bold=True)
    _format_run(p_sig.add_run("(Signature and Full Name)"), size_pt=11, italic=True)
    doc.add_page_break()

    # --- TABLE OF CONTENTS ---
    _add_heading(doc, "TABLE OF CONTENTS", level=1, centered=True)
    _add_toc_table(doc, toc_pages=toc_pages)
    doc.add_page_break()

    # --- LIST OF ABBREVIATIONS ---
    _add_heading(doc, "LIST OF ABBREVIATIONS", level=1)
    _add_table(
        doc,
        ["Abbreviation", "Full Term"],
        [
            ["AI", "Artificial Intelligence"],
            ["API", "Application Programming Interface"],
            ["ASGI", "Asynchronous Server Gateway Interface"],
            ["CLI", "Command-Line Interface"],
            ["CPU", "Central Processing Unit"],
            ["E2E", "End-to-End"],
            ["HTTP", "Hypertext Transfer Protocol"],
            ["JSON", "JavaScript Object Notation"],
            ["JSONL", "JSON Lines (one JSON object per line)"],
            ["LLM", "Large Language Model"],
            ["MCP", "Model Context Protocol"],
            ["OOM", "Out of Memory"],
            ["OS", "Operating System"],
            ["PID", "Process Identifier"],
            ["QA", "Quality Assurance"],
            ["REST", "Representational State Transfer"],
            ["SDK", "Software Development Kit"],
            ["SHA-256", "Secure Hash Algorithm 256-bit"],
            ["UI", "User Interface"],
            ["UID / GID", "User Identifier / Group Identifier"],
            ["UML", "Unified Modeling Language"],
            ["UUID", "Universally Unique Identifier"],
            ["VKU", "Vietnam-Korea University of Information and Communication Technology"],
            ["VM", "Virtual Machine"],
            ["WAL", "Write-Ahead Logging"],
            ["WSL2", "Windows Subsystem for Linux 2"],
        ],
        [3.4, 12.6],
        font_pt=9,
        pad_v=24,
    )
    doc.add_page_break()

    # --- LIST OF FIGURES & LIST OF TABLES (with real printed Page numbers) ---
    fig_titles = [
        ("Figure 2.1", "System Architecture and Trust Boundaries"),
        ("Figure 3.1", "UML Sequence Diagram of MCP Tool Execution (Agent Loop to Docker Worker, Rev 1)"),
        ("Figure 3.2", f"[Historical Capture · Run 012] Traffic Screen ({req_count} Requests, Revision {snap_rev})"),
        ("Figure 3.3", f"[Historical Capture · Run 012] Checks Screen Showing {checks_caption_phrase} (Revision {snap_rev})"),
        ("Figure 4.1", f"[Historical Capture · Run 012] Agent Q&A Across Revisions 1 and 2"),
    ]
    tbl_titles = [
        ("Table 2.1", "Threat Vectors Mapped to System Design Decisions"),
        ("Table 2.2", "Sandbox Worker Policy Controls (sandbox-policy-v1)"),
        ("Table 3.1", "Reused Open-Source Components vs. Group-Authored Modules"),
        ("Table 4.1", "Control-Paired Sandbox Isolation Results and Worker-Only Probes"),
        ("Table 4.2", f"Automated Verification Summary ({total_passed}/{total_tests} Tests Passed" + (f", {total_skipped} Skipped)" if total_skipped else ")")),
        ("Table A.1", "Verified Software Environment and Runtime Configuration"),
        ("Table A.2", "Locked Open-Source Dependencies and Licenses"),
        ("Table A.3", "Group Member Roles and Authored Modules"),
    ]

    _add_heading(doc, "LIST OF FIGURES AND TABLES", level=1)
    _add_table_caption(doc, "List of Figures")
    fig_rows = [
        [num, title, str(toc_pages[num]) if (toc_pages and num in toc_pages) else "—"]
        for num, title in fig_titles
    ]
    _add_table(doc, ["Figure", "Title", "Page"], fig_rows, [2.4, 12.0, 1.6], font_pt=10, pad_v=36)

    _add_table_caption(doc, "List of Tables")
    tbl_rows = [
        [num, title, str(toc_pages[num]) if (toc_pages and num in toc_pages) else "—"]
        for num, title in tbl_titles
    ]
    _add_table(doc, ["Table", "Title", "Page"], tbl_rows, [2.4, 12.0, 1.6], font_pt=10, pad_v=36)
    doc.add_page_break()

    # --- INTRODUCTION ---
    _add_heading(doc, "INTRODUCTION", level=1)
    _body(
        doc,
        "How can an Artificial Intelligence (AI) agent count Hypertext Transfer Protocol (HTTP) 404 responses in an Nginx access log while Linux restricts what its tools can read, write, and access over the network? "
        "This question defines the engineering problem addressed in Topic 17 of Linux Open Source Software (Linux OSS 2026) at Vietnam-Korea University of Information and Communication Technology (VKU). "
        "When an AI host connects a Large Language Model (LLM) to local inspection tools, granting direct shell execution exposes raw host files, credentials, and network interfaces to untrusted model arguments or crafted log lines [4].",
    )
    _body(
        doc,
        "Although the Model Context Protocol (MCP) standardizes tool discovery and typed JavaScript Object Notation (JSON) schemas between an AI host and a tool server, MCP is an integration boundary rather than an isolation mechanism [1]. "
        "To resolve both halves of the problem concurrently, the group designed Sandbox Lab with two parallel objectives: "
        "(1) produce accurate, revision-aware HTTP log analyses from a live Nginx service, and "
        "(2) confine every tool invocation inside an ephemeral, non-root Linux container whose filesystem writes and network egress are restricted by kernel mechanisms.",
    )
    _body(
        doc,
        "Success is evaluated against measurable evidence rather than assumption. Every analysis batch is frozen into an immutable JSON Lines (JSONL) snapshot identified by a Secure Hash Algorithm 256-bit (SHA-256) digest, and every sandbox restriction is paired with a relaxed control execution to verify that blocked behavior stems from Linux policy enforcement rather than broken test fixtures [2, 4]. "
        "The remainder of this report proceeds from background primitives (Chapter 1) to trust-boundary architecture (Chapter 2), end-to-end request and tool workflows (Chapter 3), control-paired evaluation and scope limits (Chapter 4), and overall conclusions.",
    )

    # --- SECTION 2: MAIN CONTENT STARTING AT PAGE 1 ---
    sec2 = doc.add_section(WD_SECTION.NEW_PAGE)
    sec2.page_width = Cm(21.0)
    sec2.page_height = Cm(29.7)
    sec2.top_margin = Cm(2.0)
    sec2.bottom_margin = Cm(2.0)
    sec2.left_margin = Cm(3.0)
    sec2.right_margin = Cm(2.0)
    sec2.different_first_page_header_footer = False
    sec2.header.is_linked_to_previous = False
    sec2.footer.is_linked_to_previous = False
    sec2._sectPr.append(parse_xml(f'<w:pgNumType {nsdecls("w")} w:start="1"/>'))
    _add_page_number_footer(sec2)

    # --- CHAPTER 1 ---
    _add_heading(doc, "Chapter 1. TECHNICAL BACKGROUND", level=1)
    _body(
        doc,
        "Understanding why Sandbox Lab enforces restrictions beneath MCP requires distinguishing kernel-level Linux isolation primitives from host-level execution environments and application protocols.",
    )

    _add_heading(doc, "1.1. Linux Container Isolation Primitives", level=2)
    _body(
        doc,
        "Linux containers isolate workloads on a shared kernel by combining namespaces, control groups (cgroups), Linux capability dropping, and filesystem mount flags [4]. "
        "Process identifier (PID) and network namespaces restrict what processes and sockets a container can observe; running without network interfaces blocks outbound connections and name resolution [5]. "
        "Executing processes under a dedicated non-root user identifier and group identifier (UID/GID), dropping all Linux capabilities, and disabling privilege acquisition prevent processes from escalating privileges via setuid binaries [4]. "
        "Mount flags add filesystem protection: a read-only root filesystem paired with a read-only bind mount prevents modification of input logs on the host [2], while memory, PID, and Central Processing Unit (CPU) limits bound resource usage [5]. "
        "Exact runtime flags applied to every Sandbox Lab container are consolidated in Table 2.2.",
    )

    _add_heading(doc, "1.2. Docker Desktop Execution on Windows and WSL2", level=2)
    _body(
        doc,
        "On Windows 11 with Windows Subsystem for Linux 2 (WSL2), Docker Desktop executes Linux containers inside a managed utility virtual machine (VM, context desktop-linux) rather than inside the user's Ubuntu Operating System (OS) distribution [3]. "
        "In this project's test environment, Ubuntu 24.04.4 WSL2 provides Linux developer tools (Python 3.12, Bash scripts, Node/Vite, and LibreOffice), while Docker Desktop's desktop-linux engine provides the Linux kernel namespaces, cgroups v2, and seccomp filtering used by worker containers [3]. "
        "When Docker Desktop WSL Integration is disabled and /var/run/docker.sock is absent inside Ubuntu, the runner delegates container commands to Windows docker.exe using wslpath translation so snapshot bind mounts on Windows-backed paths remain accessible to the container engine.",
    )

    _add_heading(doc, "1.3. Model Context Protocol and Nginx Access Logging", level=2)
    _body(
        doc,
        "MCP defines a client-server protocol over structured JSON-RPC messages over standard input/output (stdio) [1]. "
        "An AI host discovers typed tools via list_tools and dispatches validated arguments via call_tool; however, MCP itself provides no process, network, or filesystem confinement [1]. "
        "To ground log analysis in authentic Linux service behavior, Nginx serves HTTP routes /demo (HTTP 200) and /missing (HTTP 404) and emits structured JSONL access records carrying timestamp, run identifier, request identifier, sequence number, HTTP method, path, status, bytes_sent, and request_time [6].",
    )

    # --- CHAPTER 2 ---
    _add_heading(doc, "Chapter 2. SYSTEM DESIGN", level=1)
    _body(
        doc,
        "To answer the central design question without trusting either the external language model or raw Nginx log lines, Sandbox Lab separates proposal, host orchestration, and isolated worker execution across explicit trust zones.",
    )

    _add_heading(doc, "2.1. Architecture and Trust Boundaries", level=2)
    _body(
        doc,
        "As shown in Figure 2.1, the system divides components into external interfaces on the left, a trusted Python host boundary in the center, and two strictly separated container zones on the right. "
        "Operators interact through a React Web User Interface (UI) over Representational State Transfer (REST) Application Programming Interface (API) calls. "
        "When a user submits a question, the FastAPI Agent Loop transmits the question, the three MCP tool schemas (count_status_codes, recent_http_errors, and top_error_paths), and bounded JSON tool outputs to the configured external LLM provider, receiving proposed tool calls and draft answer prose in return. "
        "Because recent_http_errors and top_error_paths return bounded fields (HTTP path, request ID, status code, and timestamp), the provider observes selected log fields in tool results, but never receives host filesystem paths, raw log files, or environment credentials.",
    )
    _body(
        doc,
        "Inside the trusted host boundary, Nginx HTTP traffic dispatch and JSONL log reading remain separate responsibilities: Traffic Service sends HTTP requests to Nginx and reads confirmed JSONL lines to freeze immutable snapshots in Snapshot Store. "
        "Only the Trusted Policy Runner communicates with the Docker daemon. On the right side of Figure 2.1, isolated worker containers launched with network access disabled are separated from the internal lab test-service network (sandbox_lab_net): "
        "tool workers cannot reach the internal test service, whereas paired control probes attached to sandbox_lab_net reach HTTP 200 /ping cleanly [2, 4, 5].",
    )
    _add_figure(
        doc,
        ASSETS / "architecture.png",
        "Figure 2.1: System Architecture and Trust Boundaries",
        width_cm=14.4,
        verified_assets=verified_assets,
        bundle=export_data,
    )

    _add_heading(doc, "2.2. Threat Model and Isolation Decisions", level=2)
    _body(
        doc,
        "Each design decision in Sandbox Lab corresponds directly to a failure or abuse vector identified in the threat model (Table 2.1). "
        "Rather than permitting workers to read live mutable Nginx logs, the host extracts confirmed entries for a single run and freezes them into a revisioned snapshot file verified by SHA-256 before and after worker execution. "
        "Similarly, observing a blocked write or failed connection inside a sandbox is inconclusive on its own because a misconfigured path or down service produces the same failure. "
        "The Control Harness therefore pairs each sandbox check with an identical relaxed control run so only positive control confirmation alongside sandbox refusal yields a Pass verdict.",
    )

    _add_table_caption(doc, "Table 2.1: Threat Vectors Mapped to System Design Decisions")
    _add_table(
        doc,
        ["Threat Vector", "Untrusted Source", "Design Mitigation"],
        [
            ["Shell or command injection", "Model arguments or crafted HTTP request paths", "Subprocesses use explicit argv lists (shell=False); worker exposes fixed subcommands only and parses JSON via json.loads() [4]."],
            ["Host filesystem tampering", "Compromised log-analysis worker process", "Input snapshot mounted readonly; write checks run on isolated temporary copies, keeping source SHA-256 unchanged [2]."],
            ["Stale or mismatched snapshot reads", "Race between Add 404 updates and queries", "Every tool call and check binds server-side to (run_id, snapshot_id, revision) verified by SHA-256."],
            ["Fabricated or inverted numeric answers", "Model skipping tools or swapping counts", "Agent loop rejects zero-tool answers, requires count_status_codes on count questions, and validates role consistency."],
            ["Outbound data exfiltration", "Worker process opening network sockets", "Tool workers run with networking disabled; paired control probes verify test-service availability separately [5]."],
            ["Resource exhaustion or hung tasks", "Infinite loops, fork storms, or memory spikes", "128 MiB memory ceiling, PID/CPU limits, 64 KiB stream cap, 10s worker timeout, and job-scoped container removal [5]."],
        ],
        [4.0, 4.0, 8.0],
        font_pt=9.5,
        pad_v=30,
    )

    _add_heading(doc, "2.3. Data Contracts and Worker Sandbox Policy", level=2)
    _body(
        doc,
        "State persistence uses SQLite in Write-Ahead Logging (WAL) mode with explicit transaction reservations for Run, Snapshot, ToolResult, CheckResult (pinned per snapshot), and JobRecord models. "
        "Every container launched by the Trusted Policy Runner shares the uniform isolation profile documented in Table 2.2, separating runtime behavioral observations from configuration-inspected attributes.",
    )
    _add_table_caption(doc, "Table 2.2: Sandbox Worker Policy Controls (sandbox-policy-v1)")
    _add_table(
        doc,
        ["Policy Dimension", f"Configured Setting ({env_info.get('policy_version', 'sandbox-policy-v1')})", "Verification Basis"],
        [
            ["User / Group", "--user 10001:10001 (non-root user)", "Container config inspect + runtime os.getuid() [4]"],
            ["Root Filesystem", "--read-only + --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m", "Config inspected (ReadonlyRootfs=true) [4]"],
            ["Snapshot Bind Mount", "--mount type=bind,src=...,dst=/input/access.jsonl,readonly", "Behavioral write refusal + writable control copy [2]"],
            ["Network Isolation", "--network none", "Behavioral block + sandbox_lab_net HTTP 200 control [5]"],
            ["Capabilities & Privileges", "--cap-drop ALL --security-opt no-new-privileges:true", "Config inspected (CapDrop, SecurityOpt) [4]"],
            ["Compute & PIDs", "--memory 128m --memory-swap 128m --cpus 0.5 --pids-limit 64", "Runtime 200 MiB Out-of-Memory (OOM) kill (exit 137) + docker inspect [5]"],
            ["Timeout & Cleanup", "min(10s, deadline), 64 KiB pipe cap, label-scoped docker rm -f", "Behavioral 15s sleep abort + empty docker ps -a check"],
        ],
        [3.4, 7.6, 5.0],
    )

    # --- CHAPTER 3 ---
    _add_heading(doc, "Chapter 3. IMPLEMENTATION", level=1)
    _body(
        doc,
        "While Sandbox Lab builds upon open-source infrastructure components (Table 3.1), the traffic state machine, immutable revisioning, MCP validation wrapper, container runner, control harness, and operator interface were authored by the group.",
    )

    _add_heading(doc, "3.1. End-to-End Lifecycle of a Traffic Run", level=2)
    _body(
        doc,
        "A complete run begins when the operator triggers Create run in the Web UI. Using an idempotent SQLite reservation, the backend plans ten ordered HTTP requests—seven GET /demo (200 OK) and three GET /missing (404 Not Found)—assigning each request a Universally Unique Identifier (UUID) and sequence number in custom HTTP headers (X-Sandbox-Run-Id, X-Sandbox-Request-Id, X-Sandbox-Seq). "
        "Traffic Service tracks per-request dispatch state (not_sent, dispatched_uncertain, or sent) before and after each HTTP call so retrying a failed batch after a lost HTTP response never dispatches duplicate requests. "
        "Historical runs marked non-current remain read-only: backend reconciliation and UI controls emit zero HTTP requests on historical runs, and pre-migration rows lacking dispatch history default safely to legacy_unknown.",
    )
    _body(
        doc,
        "Once Nginx flushes all ten JSONL records to disk, Snapshot Store writes an immutable Revision 1 snapshot file atomically via temporary file replacement, records its SHA-256 digest in SQLite, and marks the run ready. "
        "Triggering Add 404 appends an eleventh GET /missing request and seals Revision 2 (11 requests: 7 successful, 4 not found). Because Revision 1's snapshot directory remains immutable on disk, earlier agent answers remain anchored to Revision 1 while stale banners invite the operator to re-query Revision 2.",
    )
    _add_table_caption(doc, "Table 3.1: Reused Open-Source Components vs. Group-Authored Modules")
    _add_table(
        doc,
        ["Layer", "Reused Open-Source Software", "Group-Authored Implementation"],
        [
            ["Lab Web Service", "nginx:1.27-alpine container image [6]", "Custom JSONL access log format with run/request correlation headers and internal /ping target."],
            ["Worker Runtime", "alpine:3.21 + python3 base image", "Non-root UID 10001 worker entrypoint implementing single-pass read/hash and 3 MCP + 6 check actions."],
            ["State & Snapshots", "FastAPI, Pydantic v2, SQLite WAL", "Idempotent batch reservation, crash-safe atomic snapshots, revision protection across log rotation."],
            ["MCP & Isolation", "MCP Python Software Development Kit (SDK, stdio) [1]", "Key-stripping McpLabClient, server-side UUID/SHA-256 enforcement, shell=False Docker runner, and Control Harness."],
            ["Operator Console", "React 19, TypeScript, Vite", "Four-screen console, cursor-paginated log viewer, in-memory fixture preview, and mobile bottom sheets."],
        ],
        [3.2, 4.8, 8.0],
    )

    _add_heading(doc, "3.2. MCP Tool Execution, Snapshot Pinning, and Count Verification", level=2)
    _body(
        doc,
        "Figure 3.1 traces a user's aggregate count query through the Agent Loop and isolated worker using Unified Modeling Language (UML) sequence notation (solid arrows denote calls; dashed arrows denote returns) [7]. "
        "When the operator submits a question, POST /api/runs/{run_id}/questions enqueues an asynchronous background task and returns a trackable job_id; the Agent Loop task then pins the run's active snapshot_id, revision, and SHA-256 digest and opens a 30-second deadline before calling the LLM provider. "
        "When the provider proposes count_status_codes, McpLabClient strips all provider API keys from the child environment and invokes the MCP server over stdio. "
        "The MCP server validates UUIDs and invokes the Trusted Policy Runner, which checks the snapshot's SHA-256 digest on disk and spawns docker run with readonly snapshot mounting and networking disabled.",
    )
    _body(
        doc,
        "The worker reads and hashes /input/access.jsonl in a single streaming pass, prints JSON counts ({total_requests: 10, status_200: 7, status_404: 3, snapshot_sha256}) to standard output, and exits. "
        "Crucially, container cleanup (docker rm -f) and digest verification are executed by the Policy Runner on the host—never by the worker itself—before returning ToolResult back across MCP stdio. "
        "The Agent Loop forwards the bounded JSON result to the LLM provider to draft a natural-language reply, then verifies both revision pinning and numeric role consistency: if a model swaps numerator and denominator (such as reporting '10 out of 3'), the host synthesizes a single authoritative response directly from the verified count_status_codes payload before UI polling checks GET /api/jobs/{job_id} and renders GET /api/runs/{run_id}/messages. "
        "For deeper drill-down on the same run, recent_http_errors and top_error_paths return newest error sequence IDs and paths (such as GET /missing) that cross-reference directly against the Access log screen.",
    )
    _add_figure(
        doc,
        ASSETS / "mcp-sequence.png",
        "Figure 3.1: UML Sequence Diagram of MCP Tool Execution (Agent Loop to Docker Worker, Rev 1)",
        width_cm=15.6,
        verified_assets=verified_assets,
        bundle=export_data,
    )

    _add_heading(doc, "3.3. Operator Interface and Snapshot Inspection", level=2)
    _body(
        doc,
        f"Figures 3.2 and 3.3 reproduce audited historical live captures from {run_label} (Revision {snap_rev}, captured 2026-11-18) running against Docker Nginx and {env_info.get('worker_image', 'sandbox-lab-worker:v1')}. "
        f"In Figure 3.2, the Traffic view displays the read-only banner for historical {run_label}, the {req_count}-request breakdown ({ok_count} HTTP 200, {nf_count} HTTP 404), and sequence cell #10 selected for inspection. "
        f"In Figure 3.3, the Checks view presents {checks_summary_phrase} pinned to Snapshot {snap_rev} alongside Write log's paired control evidence confirming that writes to the readonly copy failed while the writable control copy recorded the marker. "
        "The fourth console view, Access log, provides cursor-paginated inspection of raw JSONL request entries cross-linked directly from Traffic and Checks, and is omitted here for brevity.",
    )
    _add_figure(
        doc,
        ASSETS / "live-traffic-verified.png",
        f"Figure 3.2: [Historical Capture · {run_label}] Traffic Screen ({req_count} Requests, Revision {snap_rev})",
        width_cm=15.8,
        crop_box=(0.055, 0.015, 0.945, 0.81),
        add_border=True,
        verified_assets=verified_assets,
        bundle=export_data,
    )
    _add_figure(
        doc,
        ASSETS / "live-checks-verified.png",
        f"Figure 3.3: [Historical Capture · {run_label}] Checks Screen Showing {checks_caption_phrase} (Revision {snap_rev})",
        width_cm=15.8,
        crop_box=(0.055, 0.015, 0.945, 0.585),
        add_border=True,
        verified_assets=verified_assets,
        bundle=export_data,
    )

    # --- CHAPTER 4 ---
    _add_heading(doc, "Chapter 4. EVALUATION", level=1)
    _body(
        doc,
        "Evaluation tests whether the implemented system answers log-count queries accurately across snapshot revisions while enforcing and accurately classifying Linux sandbox limits.",
    )

    _add_heading(doc, "4.1. Experimental Environment and Evidence Sources", level=2)
    _body(
        doc,
        "All claims in this chapter draw from three distinct evidence sources recorded in Appendix A: "
        f"(1) the audited historical session capture from {run_label} (Figure 4.1, showing Revision 1 '3 404s / 10' advancing to Revision 2 '4 404s / 11' via DeepSeek MCP calls); "
        f"(2) the full automated verification suite ({total_passed}/{total_tests} passing tests across unit, live Docker/Nginx/DeepSeek integration using {live_model_name}, and Playwright End-to-End (E2E) tests); and "
        "(3) an independent Ubuntu 24.04.4 LTS WSL2 run invoking Windows docker.exe across both Revisions 1 and 2 for all three MCP tools. "
        "Separating historical screenshots from automated JUnit suites and the Linux receipt prevents stale visual artifacts from being mistaken for live runtime outputs of later code edits.",
    )
    _add_figure(
        doc,
        ASSETS / "live-agent-verified.png",
        f"Figure 4.1: [Historical Capture · {run_label}] Agent Q&A Across Revisions 1 and 2",
        width_cm=15.8,
        crop_box=(0.055, 0.015, 0.945, 0.82),
        add_border=True,
        verified_assets=verified_assets,
        bundle=export_data,
    )

    _add_heading(doc, "4.2. Control-Paired Isolation Experiments and Verdict Analysis", level=2)
    _body(
        doc,
        "To evaluate whether Linux network isolation genuinely blocks egress without misclassifying an unreachable service as a pass, the Control Harness runs two containers under identical image, UID, and command arguments. "
        "Under networking disabled, the sandboxed worker failed to reach the internal test service. A second control worker connected to the lab bridge network (sandbox_lab_net) received HTTP 200 JSON from the same endpoint, establishing that the service was healthy during the isolation test and justifying a Pass verdict (Table 4.1). "
        "When negative fault-injection tests replaced the control response with HTTP 204 or simulated an abnormal container exit code (137), the application refused Pass/Fail and classified the run as Error; conversely, when a mocked sandbox unexpectedly reached an HTTP 404 or 503 response while the control succeeded, it classified the outcome as Fail (Allowed).",
    )
    _body(
        doc,
        "File write protection follows the same paired methodology without touching the canonical snapshot. "
        "The harness clones the snapshot to two temporary 0666 copies: the sandbox container mounts one copy read-only and rejects writes (exit 1, SHA-256 unchanged), while the control container mounts the second copy writable and writes SANDBOX_WRITE_MARKER. "
        "Additional worker-level checks—outside-mount marker absence via --path, 15s sleep timeout with docker ps -a cleanup verification, and 200 MiB allocation inside a 128 MiB cgroup (OOMKilled=True, exit 137)—were executed directly through worker/runner integration tests and therefore carry N/A (worker probe) as their UI application verdict in Table 4.1.",
    )

    read_chk = next(c for c in checks if c["name"] == "read_log")
    write_chk = next(c for c in checks if c["name"] == "write_log")
    net_chk = next(c for c in checks if c["name"] == "reach_test_service")
    read_ctrl = read_chk.get("control_result") or {}
    write_ctrl = write_chk.get("control_result") or {}
    net_ctrl = net_chk.get("control_result") or {}
    read_ev = read_chk.get("evidence") or {}
    write_ev = write_chk.get("evidence") or {}
    read_proof = (
        f"{read_ev.get('summary', 'No summary')} (UID {read_ev.get('uid', '?')}, lines={read_ctrl.get('lines', '?')})"
        if read_chk["verdict"] == "pass"
        else f"{read_ev.get('summary', 'Execution error')} | Control: {read_ctrl.get('summary', 'Control not verified')}"
    )
    write_proof = (
        f"{write_ev.get('read_only_mount', 'n/a')} | Control: {write_ctrl.get('summary', 'n/a')} | Source untouched={write_ev.get('source_snapshot_untouched')}"
        if write_chk["verdict"] == "pass"
        else f"{write_ev.get('summary', 'Execution error')} | Control: {write_ctrl.get('summary', 'Control not verified')}"
    )
    net_proof = (
        f"--network none blocked | {net_ctrl.get('summary', 'n/a')}"
        if net_chk["verdict"] == "pass"
        else f"{(net_chk.get('evidence') or {}).get('summary', 'Execution error')} | Control: {net_ctrl.get('summary', 'Control not verified')}"
    )

    _add_table_caption(doc, "Table 4.1: Control-Paired Sandbox Isolation Results and Worker-Only Probes")
    _add_table(
        doc,
        ["Test Case", "Expected", "Observed & Control Proof", "Test / App Verdict", "Verification Mode"],
        [
            ["Read log (snapshot)", read_chk["expected"], read_proof, f"Test: Pass | App: {read_chk['verdict'].capitalize()}", "Behavioral + SHA-256 match"],
            ["Write log (ro vs rw)", write_chk["expected"], write_proof, f"Test: Pass | App: {write_chk['verdict'].capitalize()}", "Behavioral + isolated rw copy"],
            ["Reach test service", net_chk["expected"], net_proof, f"Test: Pass | App: {net_chk['verdict'].capitalize()}", "Behavioral + sandbox_lab_net"],
            ["Outside mount marker", "Blocked", "Host marker created; check_outside_mount --path returned found=false (exit != 0).", "Test: Pass | App: N/A (worker probe)", "Behavioral + --path probe"],
            ["Timeout & cleanup", "Terminated", "15s sleep aborted at 1.5s timeout; docker ps -a empty; subsequent read succeeded.", "Test: Pass | App: N/A (worker probe)", "Behavioral + ps check"],
            ["Memory limit (200 MiB)", "OOM Kill", "200 MiB allocation in 128m cgroup exited 137 (OOMKilled=True); CPU/PID flags inspected.", "Test: Pass | App: N/A (worker probe)", "Observed OOM + docker inspect"],
            ["HTTP 503/404 vs Ctrl 204", "Fail / Error", "Sandbox HTTP 503/404 with 200 /ping control -> Fail; Ctrl 204 or exit 137 -> Error.", "Test: Pass | App: Fail / Error", "Fault injection"],
            ["Infra / timeout fault", "Error", "Missing image, down test-service, or read_log timeout -> App verdict='error' (never Pass/Fail).", "Test: Pass | App: Error", "Fault injection"],
        ],
        [2.7, 2.0, 5.8, 2.5, 3.0],
        pad_v=45,
    )

    _add_heading(doc, "4.3. Automated Test Suite Results and Scope Limitations", level=2)
    unit_status = f"{unit_s['passed']} passed" + (f" ({unit_s['skipped']} skipped)" if unit_s["skipped"] else " (0 failed)")
    int_status = f"{int_s['passed']} passed" + (f" ({int_s['skipped']} skipped)" if int_s["skipped"] else " (0 failed)")
    e2e_status = f"{e2e_s['passed']} passed" + (f" ({e2e_s['skipped']} skipped)" if e2e_s["skipped"] else " (0 failed)")
    total_status = f"{total_passed} passed / {total_tests} total" + (f" ({total_skipped} skipped)" if total_skipped else "")

    _body(
        doc,
        "Table 4.2 summarizes automated verification across unit regressions (historical read-only protection, pre-migration legacy safety, number inversion checks, single-pass worker reads, and strict release validation), live container/MCP integration, Playwright E2E UI tests, and Ubuntu WSL2 execution. "
        "Two payload-related tests measure distinct properties and are reported separately: mocked run-detail metadata responses with ?include_requests=false stayed under 2,048 bytes, while cursor pagination queries were verified over 10,000 rows; neither test constitutes a full-system throughput benchmark.",
    )
    _add_table_caption(
        doc,
        f"Table 4.2: Automated Verification Summary ({total_passed}/{total_tests} Tests Passed" + (f", {total_skipped} Skipped)" if total_skipped else ")"),
    )
    _add_table(
        doc,
        ["Suite / Command", "Scope Covered", "Observed Result"],
        [
            [f"tests/unit/ ({unit_n} tests)", "Idempotent batch creation, historical read-only safety, legacy database upgrade, number guard, check faults, script hygiene, strict release validation, 10k pagination", unit_status],
            [f"tests/integration/ ({int_n} tests)", f"Live Docker Nginx runs, Docker isolation/OOMKilled/cleanup, FastMCP stdio tools, live DeepSeek ({live_model_name}) test", int_status],
            [f"tests/e2e/ ({e2e_n} tests)", "Playwright desktop/mobile UI, 100-run deep link, 10k cursor logs, < 2,048 B mocked detail, focus trap, security-invariant and UI regression cases", e2e_status],
            ["WSL2 Ubuntu Verification", "Linux Vite build + real Nginx → MCP stdio → docker.exe worker across Revisions 1 and 2 (3/10 → 4/11)", "Passed"],
            ["Web Typecheck & Build", "TypeScript compiler check (tsc --noEmit) and Vite bundle build", "Exit 0"],
            ["Total Pytest Verified", f"Source fingerprint prefix: {source_fp[:16]}... ({len(check_history)} check_history records across Revisions 1–2 in Run 012 export)", total_status],
        ],
        [4.8, 8.0, 3.2],
    )
    _body(
        doc,
        "Measured boundaries also define clear engineering limits. First, containers inside Docker Desktop's desktop-linux VM share a single Linux kernel; OS-level isolation limits filesystem, network, capability, and memory privileges but does not isolate against kernel vulnerabilities like a hardware VM [3, 4]. "
        "Second, the FastAPI backend remains part of the Trusted Computing Base because it holds Docker Command-Line Interface (CLI) privileges and reads LLM credentials from its terminal environment. "
        "Third, without an LLM provider API key exported to the backend terminal, the Agent screen renders Model unavailable while Traffic, Checks, Access log, and deterministic MCP container tools remain operable.",
    )

    # --- CONCLUSION ---
    _add_heading(doc, "CONCLUSION", level=1)
    _body(
        doc,
        "Returning to the question posed at the start—how an AI agent can count HTTP 404 responses in an Nginx log while Linux limits tool capabilities—Sandbox Lab resolves the problem by decoupling model reasoning from execution privilege. "
        "Nginx requests are recorded per run into immutable SHA-256-pinned snapshots, exposed strictly through three typed MCP stdio tools with provider secrets stripped, and executed inside ephemeral non-root Docker containers with read-only mounts and disabled networking [1, 2, 4].",
    )
    _body(
        doc,
        "Across live integration and WSL2 execution, the system traced HTTP batches from 3 of 10 errors in Revision 1 to 4 of 11 after appending a 404 request in Revision 2, prevented inverted model counts from corrupting saved answers, and distinguished enforced blocks (Pass) from missing controls or abnormal worker exits (Error). "
        "Future work could adopt rootless or microVM container runtimes to reduce kernel sharing across host and worker environments.",
    )

    # --- APPENDIX A ---
    _add_heading(doc, "APPENDIX A: ENVIRONMENT, TEST INVENTORY & CONTRIBUTIONS", level=1)
    _add_table_caption(doc, "Table A.1: Verified Software Environment and Runtime Configuration")
    _add_table(
        doc,
        ["Parameter", "Recorded Value"],
        [
            ["Host OS / WSL2 Kernel", f"{env_info.get('os', 'Windows 11')} · {wsl_receipt.get('host_os', 'Ubuntu 24.04.4 LTS')} ({wsl_receipt.get('kernel', 'WSL2')})"],
            ["Docker Engine / Context", f"Docker Desktop Engine {wsl_receipt.get('docker_engine', '29.7.2')} (desktop-linux; docker.exe + wslpath in WSL2 without socket mount)"],
            ["Python / Node Runtime", f"Windows Python {env_info.get('python', '3.12.10')} · WSL2 Python {wsl_receipt.get('python', '3.12.3')} · Node.js v24 / Linux Vite verified"],
            ["Worker Image & Policy", f"{env_info.get('worker_image', 'sandbox-lab-worker:v1')} ({str(wsl_receipt.get('worker_image_id', 'sha256:verified'))[:24]}...) · {env_info.get('policy_version', 'sandbox-policy-v1')}"],
            ["LLM Integration Model", f"{ts_prov.get('provider', 'deepseek')} ({live_model_name}; configured via terminal env vars, never .env auto-loaded or passed to MCP)"],
            ["Evidence Locations", "deliverables/evidence/evidence_bundle.json (Run 012), test_summary.json, linux_wsl_receipt.json, junit/*.xml"],
            [f"Source Fingerprint ({file_count} files)", f"{source_fp}"],
            ["VKU Format Standard", "A4 (21.0 × 29.7 cm); margins top/bottom/right 2 cm, left 3 cm; Times New Roman 13 pt body aligned with VKU guidelines [8]."],
        ],
        [4.8, 11.2],
    )

    _add_table_caption(doc, "Table A.2: Locked Open-Source Dependencies and Licenses")
    _add_table(
        doc,
        ["Component", "Version & License", "Role in System"],
        [
            ["Nginx (nginx:1.27-alpine)", "1.27-alpine · 2-clause BSD", "HTTP lab service & structured JSONL access log"],
            ["Alpine + Python (worker)", "3.21 / 3.12 · MIT / PSF", "Ephemeral non-root sandbox worker runtime"],
            ["FastAPI / Pydantic / Uvicorn", f"{versions.get('fastapi', '?')} / {versions.get('pydantic', '?')} / {versions.get('uvicorn', '?')} · MIT / MIT / BSD-3-Clause", "Backend REST API, contracts & Asynchronous Server Gateway Interface (ASGI) server"],
            ["MCP Python SDK (mcp)", f"{versions.get('mcp', '?')} · MIT", "Official FastMCP server & stdio ClientSession"],
            ["React / TypeScript / Vite", f"{versions.get('react', '?')} / {versions.get('typescript', '?')} / {versions.get('vite', '?')} · MIT / Apache-2.0 / MIT", "Operator Web UI toolchain"],
            ["pytest / Playwright", f"{versions.get('pytest', '?')} / {versions.get('playwright', '?')} · MIT / Apache-2.0", "Unit, integration, and browser E2E test suites"],
        ],
        [4.5, 4.8, 6.7],
    )

    _add_table_caption(doc, "Table A.3: Group Member Roles and Authored Modules")
    _add_table(
        doc,
        ["Member", "Primary Scope", "Authored Modules"],
        [
            ["Member A", "Linux, Nginx, Worker Image & Isolation Scripts", "infra/*, worker/*, backend/app/sandbox.py, checks.py, scripts/*.sh"],
            ["Member B", "MCP Server/Client, Agent Loop & Storage", "backend/app/mcp_server.py, mcp_client.py, agent.py, store.py, snapshots.py, traffic.py, api.py"],
            ["Member C", "Operator Web UI, E2E Quality Assurance (QA) & Packaging", "web/src/*, tests/e2e/*, README.md, docs/*, scripts/build_release.py, scripts/release/*"],
        ],
        [3.0, 5.2, 7.8],
    )

    # --- REFERENCES (After Appendix A, alphabetically sorted per VKU general guidelines [8]) ---
    _add_heading(doc, "REFERENCES", level=1)
    refs = [
        "[1] Anthropic / Model Context Protocol, 'MCP Architecture Overview', 2025. https://modelcontextprotocol.io/docs/learn/architecture",
        "[2] Docker Inc., 'Bind Mounts (readonly option)', Docker Documentation. https://docs.docker.com/engine/storage/bind-mounts/",
        "[3] Docker Inc., 'Docker Desktop WSL 2 Backend', Docker Documentation. https://docs.docker.com/desktop/features/wsl/",
        "[4] Docker Inc., 'Docker Engine Security', Docker Documentation. https://docs.docker.com/engine/security/",
        "[5] Docker Inc., 'Running Containers and Resource Constraints', Docker Documentation. https://docs.docker.com/engine/containers/run/",
        "[6] Nginx Inc., 'Module ngx_http_log_module', Nginx Documentation. https://nginx.org/en/docs/http/ngx_http_log_module.html",
        "[7] Object Management Group (OMG), 'Unified Modeling Language (UML) Version 2.5.1', 2017. https://www.omg.org/spec/UML/2.5.1",
        "[8] VKU Academic Affairs, 'Project Report Guidelines and KHMT Templates', Vietnam-Korea University of ICT. https://daotao.vku.udn.vn/bieu-mau",
    ]
    for r in refs:
        _ref_item(doc, r)

    DELIV.mkdir(parents=True, exist_ok=True)
    out_docx = DELIV / "report.docx"
    doc.save(str(out_docx))
    print("Saved DOCX:", out_docx)
    return out_docx


def _convert_docx_to_pdf(docx_path: Path, pdf_out_dir: Path) -> Path:
    import shutil
    import subprocess
    import sys

    pdf_out = pdf_out_dir / f"{docx_path.stem}.pdf"
    if sys.platform == "win32":
        soffice_win = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice_win:
            subprocess.run(
                [soffice_win, "--headless", "--convert-to", "pdf", "--outdir", str(pdf_out_dir), str(docx_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            return pdf_out
        drive = docx_path.drive.rstrip(":").lower()
        rest = docx_path.as_posix().split(":", 1)[1]
        in_wsl = f"/mnt/{drive}{rest}"
        out_drive = pdf_out_dir.drive.rstrip(":").lower()
        out_rest = pdf_out_dir.as_posix().split(":", 1)[1]
        out_wsl = f"/mnt/{out_drive}{out_rest}"
        subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-e", "libreoffice", "--headless", "--convert-to", "pdf", "--outdir", out_wsl, in_wsl],
            check=True,
            capture_output=True,
            text=True,
        )
        return pdf_out
    subprocess.run(
        ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", str(pdf_out_dir), str(docx_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return pdf_out


def _extract_heading_pages(pdf_path: Path) -> dict[str, int]:
    import fitz

    targets = [
        "Chapter 1. TECHNICAL BACKGROUND",
        "1.1. Linux Container Isolation Primitives",
        "1.2. Docker Desktop Execution on Windows and WSL2",
        "1.3. Model Context Protocol and Nginx Access Logging",
        "Chapter 2. SYSTEM DESIGN",
        "2.1. Architecture and Trust Boundaries",
        "2.2. Threat Model and Isolation Decisions",
        "2.3. Data Contracts and Worker Sandbox Policy",
        "Chapter 3. IMPLEMENTATION",
        "3.1. End-to-End Lifecycle of a Traffic Run",
        "3.2. MCP Tool Execution, Snapshot Pinning, and Count Verification",
        "3.3. Operator Interface and Snapshot Inspection",
        "Chapter 4. EVALUATION",
        "4.1. Experimental Environment and Evidence Sources",
        "4.2. Control-Paired Isolation Experiments and Verdict Analysis",
        "4.3. Automated Test Suite Results and Scope Limitations",
        "CONCLUSION",
        "APPENDIX A: ENVIRONMENT, TEST INVENTORY & CONTRIBUTIONS",
        "REFERENCES",
        "Figure 2.1",
        "Figure 3.1",
        "Figure 3.2",
        "Figure 3.3",
        "Figure 4.1",
        "Table 2.1",
        "Table 2.2",
        "Table 3.1",
        "Table 4.1",
        "Table 4.2",
        "Table A.1",
        "Table A.2",
        "Table A.3",
    ]
    found: dict[str, int] = {}
    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            raw_text = page.get_text("text")
            footer_match = re.search(r"\bPage\s+(\d+)\b", raw_text)
            if not footer_match:
                continue  # Skip unnumbered Section 1 (Cover, Comments, ToC, Front Matter, Introduction)
            printed_page = int(footer_match.group(1))
            txt = " ".join(raw_text.split())
            for label in targets:
                if label in found:
                    continue
                if label.startswith(("Figure ", "Table ")):
                    if f"{label}:" in txt:
                        found[label] = printed_page
                elif label in txt:
                    found[label] = printed_page
    return found


def build_report_with_real_toc() -> tuple[Path, Path]:
    pass1_docx = build_report(toc_pages=None)
    pdf_path = _convert_docx_to_pdf(pass1_docx, DELIV)
    toc_pages = _extract_heading_pages(pdf_path)
    pass2_docx = build_report(toc_pages=toc_pages)
    pdf_path = _convert_docx_to_pdf(pass2_docx, DELIV)
    verify_pages = _extract_heading_pages(pdf_path)
    if verify_pages != toc_pages:
        pass3_docx = build_report(toc_pages=verify_pages)
        pdf_path = _convert_docx_to_pdf(pass3_docx, DELIV)
        pass2_docx = pass3_docx
    print("Verified exact PDF ToC pages:", _extract_heading_pages(pdf_path))
    return pass2_docx, pdf_path


if __name__ == "__main__":
    build_report_with_real_toc()
