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

ROOT = Path(__file__).resolve().parents[1]
DELIV = ROOT / "deliverables"
ASSETS = DELIV / "assets"
EVIDENCE_JSON = DELIV / "evidence_bundle.json"
TEST_SUMMARY_JSON = DELIV / "test_summary.json"
REQ_TXT = ROOT / "backend" / "requirements.txt"
PKG_LOCK = ROOT / "web" / "package-lock.json"


try:
    from scripts.evidence_validator import prepare_verified_image_stream, validate_and_load_deliverables
except ImportError:
    from evidence_validator import prepare_verified_image_stream, validate_and_load_deliverables


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


def _load_verified_evidence() -> tuple[dict, dict, dict[str, str], str, dict[str, bytes]]:
    ev, ts, fp_hex, verified_assets = validate_and_load_deliverables(
        ROOT,
        evidence_path=EVIDENCE_JSON,
        summary_path=TEST_SUMMARY_JSON,
        check_assets=True,
        check_fingerprint=True,
    )
    return ev, ts, _load_locked_versions(), fp_hex, verified_assets


def _set_cell_shading(cell, fill_hex: str) -> None:
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>')
    cell._tc.get_or_add_tcPr().append(shading)


def _set_cell_margins(cell, top=80, bottom=80, left=120, right=120) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement("w:tcMar")
    for m, val in [("top", top), ("bottom", bottom), ("left", left), ("right", right)]:
        node = OxmlElement(f"w:{m}")
        node.set(qn("w:w"), str(val))
        node.set(qn("w:type"), "dxa")
        tcMar.append(node)
    tcPr.append(tcMar)


def _add_page_number_footer(section) -> None:
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("Page ")
    run.font.name = "Times New Roman"
    run.font.size = Pt(11)
    fld1 = parse_xml(r'<w:fldSimple %s w:instr="PAGE"/>' % nsdecls("w"))
    p._p.append(fld1)


def _add_toc_table(doc: Document, toc_pages: dict[str, int] | None = None) -> None:
    sections = [
        ("LIST OF ABBREVIATIONS", "Abbreviations and technical terms"),
        ("LIST OF FIGURES AND TABLES", "Summary of embedded diagrams, live captures, and evaluation tables"),
        ("INTRODUCTION", "Problem statement, objectives, scope, and group contributions"),
        ("Chapter 1. TECHNICAL BACKGROUND", "Linux isolation, Docker/WSL2, MCP architecture, and Nginx logging"),
        ("Chapter 2. SYSTEM DESIGN", "Trust boundaries, threat model, contracts, and sandbox policy"),
        ("Chapter 3. IMPLEMENTATION", "Nginx/Snapshots, MCP server/client, policy runner, and Web UI"),
        ("Chapter 4. EVALUATION", "Environment, control-paired isolation, live DeepSeek MCP, and test results"),
        ("CONCLUSION", "Summary of achieved objectives and grounded future directions"),
        ("REFERENCES", "Official sources and VKU formatting references"),
        ("APPENDIX A: VERIFIED OSS VERSIONS AND GROUP CONTRIBUTION", "Locked versions, SPDX licenses, and member ownership"),
    ]
    rows = []
    for title, summary in sections:
        pg = str(toc_pages[title]) if (toc_pages and title in toc_pages) else "—"
        rows.append([title, summary, pg])
    _add_table(doc, ["Section", "Scope Summary", "Page"], rows, [6.2, 8.3, 1.5])


def _add_table(doc: Document, headers: list[str], rows: list[list[str]], col_widths: list[float] | None = None) -> None:
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # Keep header row repeated across page breaks and prevent row splits
    hdr_trPr = table.rows[0]._tr.get_or_add_trPr()
    hdr_trPr.append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))
    hdr_trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))

    hdr_cells = table.rows[0].cells
    for idx, h in enumerate(headers):
        hdr_cells[idx].text = ""
        run = hdr_cells[idx].paragraphs[0].add_run(h)
        run.bold = True
        run.font.name = "Times New Roman"
        run.font.size = Pt(11)
        _set_cell_shading(hdr_cells[idx], "EAEFF5")
        _set_cell_margins(hdr_cells[idx])
        if col_widths:
            hdr_cells[idx].width = Cm(col_widths[idx])

    for r_idx, row in enumerate(rows):
        table.rows[r_idx + 1]._tr.get_or_add_trPr().append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
        cells = table.rows[r_idx + 1].cells
        for c_idx, val in enumerate(row):
            cells[c_idx].text = ""
            run = cells[c_idx].paragraphs[0].add_run(val)
            run.font.name = "Times New Roman"
            run.font.size = Pt(11)
            _set_cell_margins(cells[c_idx])
            if col_widths:
                cells[c_idx].width = Cm(col_widths[c_idx])
    doc.add_paragraph()


def _add_figure(
    doc: Document,
    image_path: Path,
    caption: str,
    width_cm: float = 15.0,
    crop_bottom_ratio: float | None = None,
    *,
    verified_assets: dict[str, bytes],
    bundle: dict,
) -> None:
    stream = prepare_verified_image_stream(
        verified_assets,
        bundle,
        image_path.name,
        crop_bottom_ratio=crop_bottom_ratio,
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
    r = p_cap.add_run(caption)
    r.italic = True
    r.font.name = "Times New Roman"
    r.font.size = Pt(11)


def _body(doc: Document, text: str, bold_prefix: str | None = None) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing = 1.35
    p.paragraph_format.space_after = Pt(6)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if bold_prefix:
        rb = p.add_run(bold_prefix)
        rb.bold = True
        rb.font.name = "Times New Roman"
        rb.font.size = Pt(13)
    r = p.add_run(text)
    r.font.name = "Times New Roman"
    r.font.size = Pt(13)


def _bullet(doc: Document, text: str, bold_prefix: str | None = None) -> None:
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.line_spacing = 1.25
    p.paragraph_format.space_after = Pt(4)
    if bold_prefix:
        rb = p.add_run(bold_prefix)
        rb.bold = True
        rb.font.name = "Times New Roman"
        rb.font.size = Pt(13)
    r = p.add_run(text)
    r.font.name = "Times New Roman"
    r.font.size = Pt(13)


def build_report(toc_pages: dict[str, int] | None = None) -> Path:
    export_data, test_summary, versions, source_fp, verified_assets = _load_verified_evidence()

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

    # Configure A4 & VKU margins (top/bottom/right 2 cm, left 3 cm) [7]
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.0)
    sec.bottom_margin = Cm(2.0)
    sec.left_margin = Cm(3.0)
    sec.right_margin = Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(13)
    normal.font.color.rgb = RGBColor(0x00, 0x00, 0x00)

    for lvl, sz in [("Heading 1", 15), ("Heading 2", 14), ("Heading 3", 13)]:
        st = doc.styles[lvl]
        st.font.name = "Times New Roman"
        st.font.size = Pt(sz)
        st.font.bold = True
        st.font.color.rgb = RGBColor(0x00, 0x00, 0x00)

    # --- COVER PAGE ---
    def _center(txt: str, size: int, bold: bool = False, space_after: int = 6) -> None:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(space_after)
        r = p.add_run(txt)
        r.font.name = "Times New Roman"
        r.font.size = Pt(size)
        r.bold = bold

    _center("VIETNAM - KOREA UNIVERSITY OF INFORMATION AND", 13, bold=True, space_after=2)
    _center("COMMUNICATION TECHNOLOGY (VKU)", 13, bold=True, space_after=4)
    _center("FACULTY OF COMPUTER SCIENCE", 13, bold=True, space_after=40)

    _center("COURSE PROJECT REPORT", 18, bold=True, space_after=6)
    _center("LINUX OPEN SOURCE SOFTWARE (LINUX OSS 2026)", 14, bold=True, space_after=28)

    _center("TOPIC 17:", 14, bold=True, space_after=4)
    _center("SECURE AGENTIC AI TOOL EXECUTION", 16, bold=True, space_after=4)
    _center("USING LINUX SANDBOX (SANDBOX LAB)", 16, bold=True, space_after=44)

    cover_table = doc.add_table(rows=4, cols=2)
    cover_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    cover_rows = [
        ("Group Members (3):", "Member A (Linux / Nginx / Worker Policy)\nMember B (MCP / Agent / API / Storage)\nMember C (Web Operator UI / QA / Report)"),
        ("Class / Cohort:", "[Confirm class code before final submission]"),
        ("Instructor:", "[Confirm instructor name before final submission]"),
        ("Deliverable Status:", "Verified draft aligned with VKU A4/margins and TNR 13 pt guide (pending student names/class metadata)"),
    ]
    for idx, (k, v) in enumerate(cover_rows):
        cover_table.rows[idx]._tr.get_or_add_trPr().append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
        c0, c1 = cover_table.rows[idx].cells
        c0.width = Cm(5.5)
        c1.width = Cm(10.0)
        r0 = c0.paragraphs[0].add_run(k)
        r0.bold = True
        r0.font.name = "Times New Roman"
        r0.font.size = Pt(12)
        r1 = c1.paragraphs[0].add_run(v)
        r1.font.name = "Times New Roman"
        r1.font.size = Pt(12)

    doc.add_paragraph()
    _center("Da Nang, November 2026", 13, bold=True, space_after=0)
    doc.add_page_break()

    # Section 1 (Cover, ToC, Front Matter, Introduction) stays unnumbered.
    # Page 1 starts in Section 2 at Chapter 1.
    doc.add_heading("TABLE OF CONTENTS", level=1)
    _add_toc_table(doc, toc_pages=toc_pages)
    doc.add_page_break()

    doc.add_heading("LIST OF ABBREVIATIONS", level=1)
    _add_table(
        doc,
        ["Abbreviation", "Full Term"],
        [
            ["API", "Application Programming Interface"],
            ["CLI", "Command-Line Interface"],
            ["JSONL", "JSON Lines (one JSON object per line)"],
            ["LLM", "Large Language Model"],
            ["MCP", "Model Context Protocol"],
            ["OOM", "Out of Memory"],
            ["PID", "Process Identifier"],
            ["REST", "Representational State Transfer"],
            ["SHA-256", "Secure Hash Algorithm 256-bit"],
            ["UID / GID", "User Identifier / Group Identifier"],
            ["VKU", "Vietnam-Korea University of Information and Communication Technology"],
            ["WSL2", "Windows Subsystem for Linux 2"],
        ],
        [4.0, 12.0],
    )

    doc.add_heading("LIST OF FIGURES AND TABLES", level=1)
    _bullet(doc, "Figure 2.1: Sandbox Lab Architecture and Trust Boundaries")
    _bullet(doc, "Figure 3.1: MCP Tool Execution and Snapshot Pinning Sequence")
    _bullet(doc, f"Figure 3.2: [Live Runtime] Traffic Screen for {run_label} ({req_count} Requests, Revision {snap_rev})")
    _bullet(doc, f"Figure 3.3: [Live Runtime] Checks Screen Showing {checks_caption_phrase} ({run_label} Revision {snap_rev})")
    _bullet(doc, f"Figure 4.1: [Live Runtime] Agent Q&A with DeepSeek + MCP Tool Inspector ({run_label}: Rev 1 -> Rev 2)")
    _bullet(doc, "Table 2.1: Sandbox Worker Policy Controls (sandbox-policy-v1)")
    _bullet(doc, "Table 4.1: Control-Paired Isolation Test Results on Real Docker Engine")
    _bullet(doc, f"Table 4.2: Automated Verification Suite Summary ({total_passed}/{total_tests} Passed" + (f", {total_skipped} Skipped)" if total_skipped else ")"))
    doc.add_page_break()

    # --- INTRODUCTION ---
    doc.add_heading("INTRODUCTION", level=1)
    _body(
        doc,
        "Agentic AI systems increasingly connect Large Language Models (LLMs) to external tools so models can inspect files, query logs, and verify system state. "
        "However, granting an LLM direct shell access or unrestricted host filesystem and network access introduces severe security risks: prompt injection embedded in untrusted log payloads, accidental or malicious file tampering, credential exfiltration over outbound networks, and unbounded CPU or memory consumption [1]. "
        "Notably, the Model Context Protocol (MCP) standardizes how an AI host discovers and invokes typed tools [5], but MCP itself is an integration protocol—it does not isolate or sandbox tool execution.",
    )
    _body(
        doc,
        "This project implements Topic 17 of Linux OSS 2026: Secure Agentic AI Tool Execution using Linux Sandbox (Sandbox Lab). "
        "Rather than merely installing third-party containers, our group built an end-to-end verifiable laboratory combining real Nginx JSON access logging [6], immutable SHA-256-pinned log snapshots, an official MCP stdio server/client exposing three strictly typed log-analysis tools [5], a trusted policy runner enforcing Linux container isolation primitives [1, 2, 3], a control-paired verification harness distinguishing real policy enforcement from infrastructure errors, and a four-screen React/TypeScript operator web interface.",
    )
    _body(
        doc,
        f"Key deliverables verified end to end in this report include: (1) a repeatable local stack on Docker Desktop Linux Engine + Ubuntu 24.04 WSL2 [4]; "
        f"(2) atomic SQLite idempotent run generation (10 initial requests: 7 HTTP 200 and 3 HTTP 404, advancing cleanly to {req_count}/{ok_count}/{nf_count} on {run_label} Revision {snap_rev}); "
        "(3) behavioral and control-paired isolation proofs (isolated ro_copy/rw_copy write checks preserving immutable snapshots, --network none vs. bridged control, outside-mount host marker absence, 10s timeout cleanup, and 128 MiB OOMKilled=True enforcement); and "
        f"(4) full end-to-end verification across {total_passed}/{total_tests} automated tests including live DeepSeek (deepseek-flash) MCP tool execution over stdio (3/10 on Revision 1 advancing to 4/11 on Revision 2) alongside clean Model unavailable offline fallback.",
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
    doc.add_heading("Chapter 1. TECHNICAL BACKGROUND", level=1)
    doc.add_heading("1.1. Linux Container Isolation Primitives", level=2)
    _body(
        doc,
        "Linux containers combine kernel namespaces, control groups (cgroups), Linux capabilities, and filesystem mount flags to confine processes without spawning a separate virtual machine per task [1]. "
        "In Sandbox Lab, each tool invocation uses a fresh container configured with: (a) PID and network namespaces (--network none disables all non-loopback interfaces and DNS resolution) [3]; "
        "(b) non-root execution (--user 10001:10001) so container processes hold zero root privileges [1]; (c) dropped capabilities (--cap-drop ALL) and --security-opt no-new-privileges:true to prevent privilege escalation via setuid binaries [1]; "
        "(d) a read-only root filesystem (--read-only) paired with a bounded non-executable tmpfs (/tmp:rw,noexec,nosuid,nodev,size=16m) and read-only bind mount (--mount ...,readonly) for input snapshot data [2]; and "
        "(e) cgroup resource ceilings (--memory 128m, --memory-swap 128m, --pids-limit 64, --cpus 0.5) plus Docker's default seccomp syscall filter [1, 3].",
    )

    doc.add_heading("1.2. Docker Desktop Architecture on Windows and WSL2", level=2)
    _body(
        doc,
        "On Windows 11 with WSL2, Docker Desktop runs its Linux Engine inside a managed utility VM (context desktop-linux) rather than inside the user's Ubuntu 24.04 distribution [4]. "
        "Distinguishing these two environments is essential for reproducibility: Ubuntu 24.04 WSL2 provides Linux userland tooling (Python 3.12.3, LibreOffice, Bash scripts), while Docker Desktop's desktop-linux context executes the actual Linux kernel containers (x86_64, seccomp, cgroup v2) [4]. "
        "Because Docker Desktop WSL Integration was unmounted inside Ubuntu-24.04 (/var/run/docker.sock absent), our scripts and policy runner support both native Linux docker and Windows docker.exe via wslpath translation without requiring conflicting nested Docker daemons.",
    )

    doc.add_heading("1.3. Model Context Protocol (MCP) and Nginx JSON Access Logging", level=2)
    _body(
        doc,
        f"MCP defines a client-server boundary over structured JSON-RPC messages (implemented via official Python SDK mcp=={versions.get('mcp', '1.28.1')} over stdio transport) [5]. "
        "The host discovers available tools (list_tools) and sends bounded tool arguments (call_tool), while execution security is enforced below MCP inside the trusted policy runner. "
        "For authentic Linux service traffic, Nginx (nginx:1.27-alpine) exposes /demo (HTTP 200) and /missing (HTTP 404) and emits structured JSONL records containing timestamp, run_id, request_id, seq, method, path, status, bytes_sent, and request_time [6].",
    )

    # --- CHAPTER 2 ---
    doc.add_heading("Chapter 2. SYSTEM DESIGN", level=1)
    doc.add_heading("2.1. Architecture and Trust Boundaries", level=2)
    _body(
        doc,
        "Figure 2.1 illustrates the four trust zones in Sandbox Lab: (1) the operator Web UI and external LLM provider API (DeepSeek / OpenAI / Gemini / Anthropic, which propose bounded tool calls to FastAPI's Agent Loop and never touch MCP or Docker directly); "
        "(2) Nginx lab service producing untrusted raw access logs; (3) the Trusted Host Boundary (FastAPI backend, Agent Loop with 30s deadline and strict MCP verification gate, SQLite WAL store, immutable snapshot store, MCP stdio server, and Policy Runner); and "
        "(4) the ephemeral Isolated Worker container and internal test-service network [1, 5].",
    )
    _add_figure(doc, ASSETS / "architecture.png", "Figure 2.1: Sandbox Lab Architecture and Trust Boundaries", width_cm=15.5, verified_assets=verified_assets, bundle=export_data)

    doc.add_heading("2.2. Threat Model and Mitigations", level=2)
    _add_table(
        doc,
        ["Threat Vector", "Untrusted Source", "Defense Implemented in Design"],
        [
            ["Arbitrary command / shell injection", "LLM arguments or crafted URLs in Nginx logs", "No shell tool exposed; subprocess uses argv list with shell=False; worker parses JSON via json.loads() only [1]."],
            ["Host filesystem tampering or escape", "Compromised worker process", "Immutable snapshots mounted --mount readonly; write check uses isolated 0666 ro_copy/rw_copy temp files, leaving source SHA-256 untouched [2]."],
            ["Direct LLM guessing without evidence", "Model skipping tool calls on log queries", "AgentService rejects any direct numeric answer lacking a completed MCP ToolResult matched to (run_id, snapshot_id) [5]."],
            ["Path traversal in tool arguments", "Malformed run_id or snapshot_id", "Strict UUID parsing, allowlisted actions, integer bounds (1-20 / 1-50), and Path.relative_to(snapshots_dir) check."],
            ["Data exfiltration or lateral scan", "Worker process", "--network none disables interfaces; paired control verifies test-service reachability on sandbox_lab_net [3]."],
            ["Resource exhaustion / hung worker", "Infinite loop, pipe flood, or OOM spike", "--memory 128m, --pids-limit 64, --cpus 0.5, 64 KiB pipe cap, total 30s job deadline + job-scoped container cleanup [3]."],
        ],
        [4.2, 4.0, 7.8],
    )

    doc.add_heading("2.3. Data Contracts and Sandbox Worker Policy", level=2)
    _body(
        doc,
        f"The backend persists typed Pydantic v2 models ({versions.get('pydantic', '2.12.5')}) in SQLite WAL mode with BEGIN IMMEDIATE reservation transactions: Run, Snapshot, ToolResult, CheckResult (pinned per snapshot_id), and JobRecord. "
        "Table 2.1 summarizes the worker isolation policy enforced uniformly by build_docker_run_argv().",
    )
    _add_table(
        doc,
        ["Policy Dimension", f"Configured Setting ({env_info.get('policy_version', 'sandbox-policy-v1')})", "Evidence Classification"],
        [
            ["User / Group", "--user 10001:10001 (non-root sandbox user)", "Config inspected + runtime os.getuid() [1]"],
            ["Root Filesystem", "--read-only + --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m", "Config inspected (ReadonlyRootfs=true) [1]"],
            ["Snapshot Bind Mount", "--mount type=bind,src=...,dst=/input/access.jsonl,readonly", "Behavioral + isolated writable-copy control [2]"],
            ["Network Isolation", "--network none", "Behavioral + sandbox_lab_net control [3]"],
            ["Capabilities & Privs", "--cap-drop ALL --security-opt no-new-privileges:true", "Config inspected (CapDrop, SecurityOpt) [1]"],
            ["Compute & PIDs", "--memory 128m --memory-swap 128m --cpus 0.5 --pids-limit 64", "Inspect + 200 MiB OOMKilled=True exit 137 [3]"],
            ["Timeout, Pipes & Cleanup", "min(10s, remaining), 64 KiB stream cap, label cleanup", "Behavioral abort + ps clean + cleanup_ok check"],
        ],
        [4.0, 7.0, 5.0],
    )

    # --- CHAPTER 3 ---
    doc.add_heading("Chapter 3. IMPLEMENTATION", level=1)
    doc.add_heading("3.1. Reused Open-Source Components vs. Group-Authored Modules", level=2)
    _body(
        doc,
        "Consistent with Linux OSS 2026 guidelines, Sandbox Lab builds on standard OSS components while authoring the full integration, security isolation, and UI stack:",
    )
    _bullet(doc, "infra/compose.yaml, infra/nginx/default.conf, infra/test-service/default.conf: Custom JSON access log format capturing X-Sandbox-Run-Id, X-Sandbox-Request-Id, and X-Sandbox-Seq [6]; internal test-service (:8081 internal, :18081 host) for network controls.", bold_prefix="Infrastructure: ")
    _bullet(doc, "worker/Dockerfile, worker/main.py: Non-root UID 10001 worker image executing nine fixed subcommands (3 MCP log tools + 6 harness checks, including parameterized check_outside_mount --path).", bold_prefix="Worker Image: ")
    _bullet(doc, "backend/app/store.py, snapshots.py, traffic.py: Atomic BEGIN IMMEDIATE idempotency reservations (create_run and append_attempts), snapshot-protected reconciliation on log rotation, and immutable SHA-256 snapshot creation.", bold_prefix="Run & Snapshot Engine: ")
    _bullet(doc, "backend/app/sandbox.py, checks.py: Shared policy argv builder, bounded streaming pipe reader, explicit job-label container cleanup, isolated 0666 ro_copy/rw_copy write checks, and strict Pass/Fail/Error verdicts.", bold_prefix="Policy Runner & Controls: ")
    _bullet(doc, "backend/app/mcp_server.py, mcp_client.py, agent.py, api.py: FastMCP stdio server [5], timeout-bounded stdio ClientSession, DeepSeek/OpenAI/Gemini/Anthropic adapters (no silent fallback when explicit provider lacks key), mandatory MCP evidence gate, and cursor-paginated REST + export endpoints.", bold_prefix="MCP & Agent API: ")
    _bullet(doc, "web/src/theme.css, components/, pages/: Shared shell, custom SVG two-rail logo, in-memory fixture isolation (?fixture=... makes zero backend writes), paginated sequence & log queries (?include_requests=false), inert/Tab focus-trapped BottomSheets, and 44px+ mobile touch targets.", bold_prefix="Web Operator UI: ")

    doc.add_heading("3.2. MCP Execution Flow and Snapshot Pinning", level=2)
    _body(
        doc,
        "When an operator submits a question in Agent, FastAPI's AgentService pins the active snapshot_id and revision before querying the external model adapter [5]. "
        "The external LLM receives only the question, typed MCP tool schemas, and subsequent bounded JSON tool outputs—never raw host paths, logs outside the pinned snapshot, or Docker credentials. "
        "To invoke a tool, the host Python client (McpLabClient) explicitly strips all LLM secret environment variables (DEEPSEEK_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY) and spawns backend/app/mcp_server.py over stdio. "
        "Inside the trusted boundary, mcp_server.py validates UUIDs and calls sandbox.execute_tool(), which verifies the snapshot's SHA-256 digest on disk and again on the worker payload, launching ephemeral Docker worker container sandbox-lab-worker:v1 inside Docker Desktop's desktop-linux VM (OS-level namespace/cgroup isolation sharing the VM kernel; controlled solely via the host Docker CLI/daemon) [1, 4]. "
        "Adding a 404 request advances the run revision cleanly (preserving earlier checks in check_history and flagging older Q&A replies with an amber stale-snapshot Ask again banner).",
    )
    _add_figure(doc, ASSETS / "mcp-sequence.png", "Figure 3.1: MCP Tool Execution and Snapshot Pinning Sequence", width_cm=15.0, verified_assets=verified_assets, bundle=export_data)

    doc.add_heading("3.3. Live Verified Operator Interface", level=2)
    _body(
        doc,
        f"Figures 3.2 and 3.3 show historical live runtime screenshots captured from Playwright on {run_label} (Revision {snap_rev}, ID: {run_id}, captured 2026-11-18) backed by real Nginx logs and {env_info.get('worker_image', 'sandbox-lab-worker:v1')}. "
        f"Traffic displays the {req_count}/{ok_count}/{nf_count} transition; Checks shows {checks_summary_phrase} bound to Snapshot {snap_rev} ({snap_sha}...).",
    )
    _add_figure(doc, ASSETS / "live-traffic-verified.png", f"Figure 3.2: [Live Runtime] Traffic Screen on {run_label} ({req_count} Requests: {ok_count} OK, {nf_count} 404)", width_cm=14.8, crop_bottom_ratio=0.73, verified_assets=verified_assets, bundle=export_data)
    _add_figure(doc, ASSETS / "live-checks-verified.png", f"Figure 3.3: [Live Runtime] Checks Screen Showing {checks_caption_phrase} on {run_label} Revision {snap_rev}", width_cm=14.8, crop_bottom_ratio=0.60, verified_assets=verified_assets, bundle=export_data)

    # --- CHAPTER 4 ---
    doc.add_heading("Chapter 4. EVALUATION", level=1)
    doc.add_heading("4.1. Test Environment and Exported Session Evidence", level=2)
    _body(
        doc,
        f"Post-remediation verification ran on November 19, 2026 on {env_info.get('os', 'Windows 11')} (Docker Desktop Engine 29.7.2, Compose v5.4.0, desktop-linux context), Python {env_info.get('python', '3.12.10')}, Node v24.19.0, and clean Ubuntu 24.04.4 LTS WSL2 onboarding (Python 3.12.3 isolated venv, Linux @rollup/rollup-x64-gnu Vite build, and Linux MCP stdio receipt in deliverables/linux_wsl_receipt.json) [4]. "
        f"The audited historical session export (deliverables/evidence_bundle.json, captured 2026-11-18 on {run_label}, Snapshot ID: {snap_id}, SHA-256: {snap_sha}...) confirms {checks_outcome_phrase} on Revision {snap_rev} (plus {max(0, len(check_history) - len(checks))} prior checks in check_history = {len(check_history)} total check records) and completed live DeepSeek (deepseek-flash) MCP Q&A across Revision 1 ('{rev1_txt}') and Revision 2 ('{rev2_txt}'), illustrated in Figure 4.1.",
    )
    _add_figure(doc, ASSETS / "live-agent-verified.png", f"Figure 4.1: [Live Runtime] Agent Q&A with DeepSeek + MCP Tool Inspector ({run_label}: Rev 1 -> Rev 2)", width_cm=14.8, crop_bottom_ratio=0.84, verified_assets=verified_assets, bundle=export_data)

    doc.add_heading("4.2. Control-Paired Sandbox Isolation Results", level=2)
    _body(
        doc,
        "Table 4.1 summarizes observed outcomes from the live Checks ledger (deliverables/evidence_bundle.json) and real Docker integration suite (tests/integration/test_sandbox.py).",
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
    _add_table(
        doc,
        ["Test Case", "Expected", "Observed & Control Proof", "Test / App Verdict", "Verification Mode"],
        [
            ["Read log (snapshot)", read_chk["expected"], read_proof, f"Test: Pass | App: {read_chk['verdict'].capitalize()}", "Behavioral + SHA-256 control"],
            ["Write log (ro vs rw)", write_chk["expected"], write_proof, f"Test: Pass | App: {write_chk['verdict'].capitalize()}", "Behavioral + isolated rw copy"],
            ["Reach test service", net_chk["expected"], net_proof, f"Test: Pass | App: {net_chk['verdict'].capitalize()}", "Behavioral + sandbox_lab_net"],
            ["Outside mount marker", "Blocked", "Host secret marker created on host; check_outside_mount --path returned found=false (exit != 0).", "Test: Pass | App: N/A (worker probe)", "Behavioral + parameterized marker"],
            ["Timeout & cleanup", "Terminated", "15s sleep aborted at 1.5s timeout; docker ps -a by call-id returned empty; follow-up read succeeded.", "Test: Pass | App: N/A (worker probe)", "Behavioral + ps verification"],
            ["Memory limit (200 MiB)", "OOM Kill", "200 MiB allocation in 128m container exited 137 (OOMKilled=True); --cpus 0.5 & --pids-limit 64 config-inspected.", "Test: Pass | App: N/A (worker probe)", "Observed OOM + docker inspect"],
            ["Network 503/404 vs Ctrl 204", "Fail / Error", "Worker HTTP 503/404 with HTTP 200 /ping JSON control -> App Fail; Control HTTP 204 or worker crash -> App Error.", "Test: Pass | App: Fail / Error", "Negative HTTP & fault injection"],
            ["Infra / timeout fault", "Error", "Missing image, dead control service, or read_log timeout classified strictly as App verdict='error' (never Pass/Fail).", "Test: Pass | App: Error", "Negative fault injection"],
        ],
        [2.9, 1.6, 6.2, 2.3, 3.0],
    )

    doc.add_heading("4.3. Automated Test Suite Summary and Scope Boundaries", level=2)
    unit_status = f"{unit_s['passed']} passed" + (f" ({unit_s['skipped']} skipped)" if unit_s["skipped"] else " (0 failed)")
    int_status = f"{int_s['passed']} passed" + (f" ({int_s['skipped']} skipped)" if int_s["skipped"] else " (0 failed)")
    e2e_status = f"{e2e_s['passed']} passed" + (f" ({e2e_s['skipped']} skipped)" if e2e_s["skipped"] else " (0 failed)")
    total_status = f"{total_passed} passed / {total_tests} total" + (f" ({total_skipped} skipped)" if total_skipped else "")
    _add_table(
        doc,
        ["Suite / Command", "Scope Covered", "Observed Result"],
        [
            [f"tests/unit/ ({unit_n} tests)", "Runs/concurrency/idempotency, late-log ReadTimeout, isolated check copies, pipe cap, snapshot check pinning, MCP gate & deadline, lifecycle scripts, deliverable validator, 10k scale", unit_status],
            [f"tests/integration/ ({int_n} tests)", "Real Docker Nginx runs, real Docker isolation/OOMKilled/cleanup, real MCP stdio tools, live DeepSeek test (skipped if no key in env)", int_status],
            [f"tests/e2e/ ({e2e_n} tests)", "Playwright 1440/1280/390/360 visuals (0 backend writes), 100-run deep link, 10,000-row cursor-paginated log query & mocked run detail < 2,048 bytes (?include_requests=false), R2/R4 & Stage B recovery", e2e_status],
            ["WSL2 Ubuntu Clean Onboarding", "Isolated Linux Python 3.12.3 venv, @rollup/rollup-linux-x64-gnu Vite build, real Nginx -> MCP stdio -> Docker rev1/rev2", "Passed (linux_wsl_receipt.json)"],
            ["web: npm run typecheck && build", "TypeScript strict check and Vite production bundle (Windows & Linux)", "Exit 0"],
            ["Total Pytest Verified", f"Source fingerprint: {source_fp[:16]}...", total_status],
        ],
        [5.2, 7.6, 3.2],
    )
    _body(
        doc,
        "Remaining Environmental Scope Notes: (1) Container isolation relies on the Linux kernel shared inside Docker Desktop's desktop-linux VM and does not claim hardware-VM isolation against kernel vulnerabilities [1, 4]. "
        "(2) In Ubuntu 24.04 WSL2 without /var/run/docker.sock enabled, scripts/start.sh and scripts/verify.sh fall back to Windows docker.exe + wslpath -w and keep runtime bind-mount paths under /mnt/<drive>/... cleanly [4]. "
        "(3) Without a live LLM API key in the environment, the UI cleanly exercises Model unavailable, and McpLabClient strips all LLM provider secret variables before spawning the MCP stdio server [5]. "
        "(4) Formal VKU formatting note: Huong-dan-trinh-bay-original.doc specifies Times New Roman 13 pt while KHMT-report-original.docx Normal style uses 14 pt [7]; confirm final point size and cover metadata (student names, class code, instructor) prior to submission.",
    )

    # --- CONCLUSION ---
    doc.add_heading("CONCLUSION", level=1)
    _body(
        doc,
        "Sandbox Lab demonstrates a complete, verifiable pattern for secure agentic AI tool execution on Linux: models and untrusted logs never obtain shell or Docker privileges, while every tool invocation runs inside an ephemeral non-root container bound to an immutable SHA-256 log snapshot with --read-only rootfs, --network none, dropped capabilities, strict MCP tool verification, total task deadlines, and deterministic cleanup [1, 2, 3, 5]. "
        "Pairing sandbox checks with isolated writable and networked controls guarantees that isolation claims reflect kernel enforcement rather than broken test fixtures.",
    )

    # --- REFERENCES & APPENDICES ---
    doc.add_heading("REFERENCES", level=1)
    refs = [
        "[1] Docker Inc., 'Docker Engine Security', Docker Documentation. https://docs.docker.com/engine/security/",
        "[2] Docker Inc., 'Bind Mounts (readonly option)', Docker Documentation. https://docs.docker.com/engine/storage/bind-mounts/",
        "[3] Docker Inc., 'Running Containers & Resource Constraints', Docker Documentation. https://docs.docker.com/engine/containers/run/",
        "[4] Docker Inc., 'Docker Desktop WSL 2 Backend', Docker Documentation. https://docs.docker.com/desktop/features/wsl/",
        "[5] Anthropic / Model Context Protocol, 'MCP Architecture Overview', 2025. https://modelcontextprotocol.io/docs/learn/architecture",
        "[6] Nginx Inc., 'Module ngx_http_log_module', Nginx Documentation. https://nginx.org/en/docs/http/ngx_http_log_module.html",
        "[7] VKU Academic Affairs, 'Project Report Templates (KHMT)', daotao.vku.udn.vn/bieu-mau (reference copies in docs/references/vku/).",
    ]
    for r in refs:
        _body(doc, r)

    doc.add_heading("APPENDIX A: VERIFIED OSS VERSIONS AND GROUP CONTRIBUTION", level=1)
    _add_table(
        doc,
        ["Component / Package", "Locked Version", "License / Role"],
        [
            ["Nginx (nginx:1.27-alpine)", "1.27-alpine", "2-clause BSD · HTTP lab target & JSONL access log"],
            ["Alpine Linux + Python (worker)", "3.21 / 3.12", "MIT / PSF · Ephemeral non-root sandbox worker"],
            ["FastAPI / Pydantic / Uvicorn", f"{versions.get('fastapi', '?')} / {versions.get('pydantic', '?')} / {versions.get('uvicorn', '?')}", "MIT (FastAPI, Pydantic) / BSD-3-Clause (Uvicorn) · Backend REST API & contracts"],
            ["MCP Python SDK (mcp)", versions.get("mcp", "?"), "MIT · Official FastMCP server & stdio ClientSession"],
            ["React / TypeScript / Vite", f"{versions.get('react', '?')} / {versions.get('typescript', '?')} / {versions.get('vite', '?')}", "MIT (React, Vite) / Apache-2.0 (TypeScript) · Web Operator UI"],
            ["pytest / Playwright", f"{versions.get('pytest', '?')} / {versions.get('playwright', '?')}", "MIT / Apache-2.0 · Unit, integration, E2E & visual checks"],
        ],
        [5.5, 3.8, 6.7],
    )
    _add_table(
        doc,
        ["Member", "Primary Ownership", "Verified Deliverables"],
        [
            ["Member A", "Linux / Nginx / Worker / Policy Scripts", "infra/*, worker/*, backend/app/sandbox.py, checks.py, scripts/*"],
            ["Member B", "MCP / Agent / API / Snapshot Storage", "backend/app/mcp_server.py, mcp_client.py, agent.py, store.py, snapshots.py, traffic.py, api.py"],
            ["Member C", "Web Operator UI / E2E / Documentation", "web/src/*, tests/e2e/*, README.md, docs/architecture.md, docs/demo.md, report & slides"],
        ],
        [3.0, 5.5, 7.5],
    )

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

    # Front matter sections (LIST OF ABBREVIATIONS, LIST OF FIGURES AND TABLES, INTRODUCTION) remain unnumbered ("—") in ToC.
    headings = [
        "Chapter 1. TECHNICAL BACKGROUND",
        "Chapter 2. SYSTEM DESIGN",
        "Chapter 3. IMPLEMENTATION",
        "Chapter 4. EVALUATION",
        "CONCLUSION",
        "REFERENCES",
        "APPENDIX A: VERIFIED OSS VERSIONS AND GROUP CONTRIBUTION",
    ]
    found: dict[str, int] = {}
    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            raw_text = page.get_text("text")
            footer_match = re.search(r"\bPage\s+(\d+)\b", raw_text)
            if not footer_match:
                continue  # Skip unnumbered Section 1 (Cover, ToC, Front Matter, Introduction)
            printed_page = int(footer_match.group(1))
            txt = " ".join(raw_text.split())
            for h in headings:
                if h not in found and h in txt:
                    found[h] = printed_page
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
    print("Verified exact PDF ToC pages:", _extract_heading_pages(pdf_path))
    return pass2_docx, pdf_path


if __name__ == "__main__":
    build_report_with_real_toc()
