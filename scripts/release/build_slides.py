from __future__ import annotations

import json
import re
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DELIV = ROOT / "deliverables"
ASSETS = DELIV / "assets"
EVIDENCE_DIR = DELIV / "evidence"
EVIDENCE_JSON = EVIDENCE_DIR / "evidence_bundle.json"
TEST_SUMMARY_JSON = EVIDENCE_DIR / "test_summary.json"

INK = RGBColor(0x14, 0x28, 0x3B)
MUTED = RGBColor(0x40, 0x56, 0x6B)
ACCENT = RGBColor(0x00, 0x65, 0xC9)
SURFACE = RGBColor(0xED, 0xF8, 0xFC)
BORDER = RGBColor(0xC6, 0xD6, 0xE4)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
PASS_BG = RGBColor(0xDC, 0xFC, 0xE7)
PASS_INK = RGBColor(0x14, 0x53, 0x2D)
WARN_BG = RGBColor(0xFE, 0xF3, 0xC7)
WARN_INK = RGBColor(0x92, 0x40, 0x0E)


try:
    from scripts.release.evidence_validator import prepare_verified_image_stream, validate_and_load_deliverables
except ImportError:
    from evidence_validator import prepare_verified_image_stream, validate_and_load_deliverables


def _load_evidence() -> tuple[dict, dict, str, dict[str, bytes]]:
    ev, ts, fp_hex, verified_assets = validate_and_load_deliverables(
        ROOT,
        evidence_path=EVIDENCE_JSON,
        summary_path=TEST_SUMMARY_JSON,
        check_assets=True,
        check_fingerprint=True,
    )
    return ev, ts, fp_hex, verified_assets


def _add_header(slide, title: str, subtitle: str, page_num: int) -> None:
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(0.08))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()

    tb = slide.shapes.add_textbox(Inches(0.65), Inches(0.32), Inches(11.6), Inches(0.95))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    p1 = tf.paragraphs[0]
    r1 = p1.add_run()
    r1.text = title
    r1.font.name = "Segoe UI"
    r1.font.size = Pt(22 if len(title) > 54 else 24)
    r1.font.bold = True
    r1.font.color.rgb = INK

    p2 = tf.add_paragraph()
    p2.space_before = Pt(3)
    r2 = p2.add_run()
    r2.text = subtitle
    r2.font.name = "Segoe UI"
    r2.font.size = Pt(12.2 if len(subtitle) > 95 else 13)
    r2.font.color.rgb = MUTED

    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.65), Inches(1.32), Inches(12.03), Inches(0.015))
    line.fill.solid()
    line.fill.fore_color.rgb = BORDER
    line.line.fill.background()

    ft = slide.shapes.add_textbox(Inches(0.65), Inches(7.08), Inches(12.03), Inches(0.3))
    ft_tf = ft.text_frame
    ft_tf.margin_left = ft_tf.margin_right = ft_tf.margin_top = ft_tf.margin_bottom = 0
    pf = ft_tf.paragraphs[0]
    pf.alignment = PP_ALIGN.RIGHT
    rf = pf.add_run()
    rf.text = f"Sandbox Lab · VKU Linux OSS 2026 (Topic 17)   |   {page_num} / 11"
    rf.font.name = "Segoe UI"
    rf.font.size = Pt(10)
    rf.font.color.rgb = MUTED


def _add_fitted_picture(slide, stream, x: float, y: float, max_w: float, max_h: float, *, center_x: bool = False) -> None:
    stream.seek(0)
    with Image.open(stream) as im:
        w_px, h_px = im.size
    stream.seek(0)
    aspect = w_px / max(1, h_px)
    fit_w = max_w
    fit_h = fit_w / aspect
    if fit_h > max_h:
        fit_h = max_h
        fit_w = fit_h * aspect
    pos_x = x + (max_w - fit_w) / 2.0 if center_x else x
    slide.shapes.add_picture(stream, Inches(pos_x), Inches(y), width=Inches(fit_w), height=Inches(fit_h))


def _add_card(slide, x: float, y: float, w: float, h: float, title: str, lines: list[str], fill=SURFACE, border=BORDER, title_color=ACCENT) -> None:
    box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    box.adjustments[0] = 0.04
    box.fill.solid()
    box.fill.fore_color.rgb = fill
    box.line.color.rgb = border
    box.line.width = Pt(1.0)

    tb = slide.shapes.add_textbox(Inches(x + 0.22), Inches(y + 0.16), Inches(w - 0.44), Inches(h - 0.32))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0

    p0 = tf.paragraphs[0]
    r0 = p0.add_run()
    r0.text = title
    r0.font.name = "Segoe UI"
    r0.font.size = Pt(15)
    r0.font.bold = True
    r0.font.color.rgb = title_color

    for item in lines:
        p = tf.add_paragraph()
        p.space_before = Pt(7)
        r = p.add_run()
        r.text = item
        r.font.name = "Segoe UI"
        r.font.size = Pt(12.2)
        r.font.color.rgb = INK


def _add_table(slide, x: float, y: float, w: float, h: float, headers: list[str], rows: list[list[str]], col_widths_in: list[float]) -> None:
    shape = slide.shapes.add_table(1 + len(rows), len(headers), Inches(x), Inches(y), Inches(w), Inches(h))
    tbl = shape.table
    for idx, cw in enumerate(col_widths_in):
        tbl.columns[idx].width = Inches(cw)

    for idx, h_txt in enumerate(headers):
        cell = tbl.cell(0, idx)
        cell.fill.solid()
        cell.fill.fore_color.rgb = SURFACE
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = cell.text_frame.paragraphs[0]
        r = p.add_run()
        r.text = h_txt
        r.font.name = "Segoe UI"
        r.font.size = Pt(12)
        r.font.bold = True
        r.font.color.rgb = INK

    for r_idx, row in enumerate(rows):
        for c_idx, val in enumerate(row):
            cell = tbl.cell(r_idx + 1, c_idx)
            cell.fill.solid()
            low = val.lower()
            is_warn = "pending" in low or "skipped" in low or "error" in low or "fail" in low
            is_ok = (not is_warn) and ("passed" in low or val == "Pass")
            if is_warn:
                cell.fill.fore_color.rgb = WARN_BG
            elif is_ok:
                cell.fill.fore_color.rgb = PASS_BG
            else:
                cell.fill.fore_color.rgb = WHITE
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            r = p.add_run()
            r.text = val
            r.font.name = "Segoe UI"
            r.font.size = Pt(11.2)
            r.font.bold = (c_idx == 0 or is_ok or is_warn)
            r.font.color.rgb = WARN_INK if is_warn else (PASS_INK if is_ok else INK)


def build_slides() -> Path:
    export_data, test_summary, source_fp, verified_assets = _load_evidence()
    run_obj = export_data["run"]
    run_label = run_obj["label"]
    snap_rev = run_obj["revision"]
    req_count = run_obj["request_count"]
    ok_count = run_obj["ok_count"]
    nf_count = run_obj["not_found_count"]
    checks = export_data["checks"]
    pass_count = sum(1 for c in checks if c.get("verdict") == "pass")

    unit_s = test_summary["suites"]["unit"]
    int_s = test_summary["suites"]["integration"]
    e2e_s = test_summary["suites"]["e2e"]
    unit_n = unit_s["tests"]
    int_n = int_s["tests"]
    e2e_n = e2e_s["tests"]
    total_passed = test_summary["totals"]["passed"]
    total_tests = test_summary["totals"]["tests"]
    total_skipped = test_summary["totals"]["skipped"]
    skip_suffix = f" ({total_skipped} Skipped)" if total_skipped else ""

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # Slide 1: Title
    s1 = prs.slides.add_slide(blank)
    top_band = s1.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(0.14))
    top_band.fill.solid()
    top_band.fill.fore_color.rgb = ACCENT
    top_band.line.fill.background()

    badge = s1.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.8), Inches(0.85), Inches(4.5), Inches(0.45))
    badge.adjustments[0] = 0.25
    badge.fill.solid()
    badge.fill.fore_color.rgb = SURFACE
    badge.line.color.rgb = BORDER
    bp = badge.text_frame.paragraphs[0]
    bp.alignment = PP_ALIGN.CENTER
    br = bp.add_run()
    br.text = "VKU · LINUX OSS 2026 · TOPIC 17"
    br.font.name = "Segoe UI"
    br.font.size = Pt(12)
    br.font.bold = True
    br.font.color.rgb = ACCENT

    tb1 = s1.shapes.add_textbox(Inches(0.8), Inches(1.50), Inches(11.7), Inches(2.2))
    tf1 = tb1.text_frame
    tf1.word_wrap = True
    p_t = tf1.paragraphs[0]
    rt = p_t.add_run()
    rt.text = "Sandbox Lab"
    rt.font.name = "Segoe UI"
    rt.font.size = Pt(44)
    rt.font.bold = True
    rt.font.color.rgb = INK

    p_sub = tf1.add_paragraph()
    p_sub.space_before = Pt(6)
    rs = p_sub.add_run()
    rs.text = "Secure Agentic AI Tool Execution using Linux Sandbox"
    rs.font.name = "Segoe UI"
    rs.font.size = Pt(24)
    rs.font.bold = True
    rs.font.color.rgb = ACCENT

    p_desc = tf1.add_paragraph()
    p_desc.space_before = Pt(12)
    rd = p_desc.add_run()
    rd.text = "Isolating MCP log-analysis tools inside ephemeral, non-root, read-only, network-disabled Linux containers with paired behavioral controls."
    rd.font.name = "Segoe UI"
    rd.font.size = Pt(15)
    rd.font.color.rgb = MUTED

    # Three architecture summary pills filling the mid canvas evenly
    _add_card(
        s1, 0.8, 3.82, 3.7, 1.05,
        "Linux Container Policy",
        ["UID 10001 · --read-only · --network none · 128m RAM"],
        fill=WHITE, border=BORDER, title_color=ACCENT,
    )
    _add_card(
        s1, 4.8, 3.82, 3.7, 1.05,
        "MCP Stdio & Snapshots",
        ["3 read-only tools pinned by immutable SHA-256"],
        fill=WHITE, border=BORDER, title_color=ACCENT,
    )
    _add_card(
        s1, 8.8, 3.82, 3.7, 1.05,
        "Verified Release Gate",
        [f"{total_passed}/{total_tests} tests passed{skip_suffix} + WSL2 Linux receipt"],
        fill=PASS_BG, border=RGBColor(0x86, 0xEF, 0xAC), title_color=PASS_INK,
    )

    _add_card(
        s1, 0.8, 5.15, 11.7, 1.55,
        "Project Team & Verified Runtime Stack",
        [
            "Group of 3 Students (Members A, B, C — fill names/class before defense) · VKU · Da Nang, November 2026",
            "Stack: Docker Desktop Linux Engine (29.7.2) · Ubuntu 24.04.4 WSL2 · Nginx 1.27 · FastAPI + MCP 1.28.1 · React 19",
        ],
        fill=SURFACE, border=BORDER, title_color=INK,
    )

    # Slide 2: Problem Statement
    s2 = prs.slides.add_slide(blank)
    _add_header(s2, "Why MCP Alone Is Not Enough", "Protocols structure tool calls, while Linux isolation must enforce execution boundaries.", 2)
    _add_card(
        s2, 0.65, 1.60, 5.8, 2.75,
        "1. Risks of Direct Tool Execution",
        [
            "• Untrusted log lines (e.g., crafted HTTP paths) can carry shell metacharacters.",
            "• Unrestricted runners can tamper with snapshots or read host secrets.",
            "• Outbound network access allows silent data exfiltration or lateral scanning.",
        ],
    )
    _add_card(
        s2, 6.85, 1.60, 5.8, 2.75,
        "2. MCP vs. Sandbox Boundary",
        [
            "• Model Context Protocol (MCP) standardizes tool discovery and JSON schemas.",
            "• MCP does NOT isolate processes, filesystems, capabilities, or memory.",
            "• Sandbox Lab separates tool proposals (LLM) from kernel-enforced execution.",
        ],
    )
    _add_card(
        s2, 0.65, 4.60, 5.8, 2.10,
        "3. Agent & Snapshot Guarantees",
        [
            "• Mandatory MCP tool call matching (run_id, snapshot_id, revision, sha256) under 30s.",
            "• Quantitative number guard rejects inverted LLM prose (e.g. '10 out of 3').",
        ],
        fill=WHITE, border=ACCENT, title_color=ACCENT,
    )
    _add_card(
        s2, 6.85, 4.60, 5.8, 2.10,
        "4. Paired Control Guarantees",
        [
            "• Pass requires sandbox blocked AND relaxed control succeeded cleanly.",
            "• Missing control, crash, or abnormal exit (137) -> Error (never Pass).",
        ],
        fill=WHITE, border=ACCENT, title_color=ACCENT,
    )

    # Slide 3: System Architecture
    s3 = prs.slides.add_slide(blank)
    _add_header(s3, "System Architecture & Trust Boundaries", "Host Python spawns MCP stdio (no LLM keys) -> Policy Runner spawns ephemeral Linux worker.", 3)
    _add_fitted_picture(
        s3,
        prepare_verified_image_stream(verified_assets, export_data, "architecture.png"),
        0.65,
        1.42,
        12.03,
        5.05,
        center_x=True,
    )
    cap3 = s3.shapes.add_textbox(Inches(0.65), Inches(6.55), Inches(12.03), Inches(0.42))
    cap3_tf = cap3.text_frame
    cap3_tf.word_wrap = True
    cap3_p = cap3_tf.paragraphs[0]
    cap3_p.alignment = PP_ALIGN.CENTER
    cap3_r = cap3_p.add_run()
    cap3_r.text = "LLM sees only prompts + bounded JSON tool outputs; Docker control stays in Policy Runner; workers share the desktop-linux VM kernel."
    cap3_r.font.name = "Segoe UI"
    cap3_r.font.size = Pt(11)
    cap3_r.font.color.rgb = MUTED

    # Slide 4: Linux Sandbox Policy
    s4 = prs.slides.add_slide(blank)
    _add_header(s4, "Linux Worker Policy (sandbox-policy-v1)", "Defense-in-depth flags applied uniformly by build_docker_run_argv() on every tool call.", 4)
    _add_table(
        s4, 0.65, 1.55, 12.03, 5.2,
        ["Layer", "Docker / Kernel Flag", "Security Purpose", "How Verified"],
        [
            ["Non-root Identity", "--user 10001:10001", "Zero root privileges inside worker", "Container config inspect + runtime os.getuid()"],
            ["Root Filesystem", "--read-only + tmpfs 16m (noexec)", "Blocks binary/system tampering", "Config-inspected (ReadonlyRootfs=true)"],
            ["Input Data Mount", "--mount ...,dst=/input/access.jsonl,readonly", "Protects snapshot integrity + SHA-256", "Behavioral ro_copy/rw_copy + digest check"],
            ["Network Stack", "--network none", "Blocks TCP/UDP/DNS & lab services", "Behavioral + HTTP 200 /ping control"],
            ["Privileges", "--cap-drop ALL --security-opt no-new-privileges", "Strips Linux caps & setuid escalation", "Config-inspected (CapDrop/SecOpt)"],
            ["Resource Ceilings", "--memory 128m --pids-limit 64 --cpus 0.5", "Prevents OOM/fork/CPU exhaustion", "Observed OOM (OOMKilled=True, 137); CPU/PIDs inspected"],
            ["Timeout & Cleanup", "min(10s, deadline), 64 KiB cap, rm -f", "Reclaims hung containers & job cleanup", "Observed 15s sleep abort + ps -a empty"],
        ],
        [2.1, 4.0, 3.03, 2.9],
    )

    # Slide 5: MCP & Snapshot Pinning
    s5 = prs.slides.add_slide(blank)
    _add_header(s5, "Typed MCP Tools & Snapshot Pinning", "Official MCP Python SDK (stdio) exposes strictly 3 read-only log tools pinned to immutable revisions.", 5)
    _add_fitted_picture(
        s5,
        prepare_verified_image_stream(verified_assets, export_data, "mcp-sequence.png"),
        0.65,
        1.48,
        8.1,
        5.2,
    )
    _add_card(
        s5, 8.95, 1.55, 3.75, 3.05,
        "3 Permitted MCP Tools",
        [
            "1. count_status_codes(run_id) — total, 200, 404, sha256.",
            "2. top_error_paths(run_id, limit=1..20) — HTTP >= 400 paths.",
            "3. recent_http_errors(run_id, limit=1..50) — latest errors.",
        ],
        fill=SURFACE, border=BORDER, title_color=INK,
    )
    _add_card(
        s5, 8.95, 4.78, 3.75, 1.92,
        "Agent & MCP Guardrails",
        [
            "• Server-pinned snapshot_id & revision",
            "• Mandatory count/MCP gate + 4 calls / 30s cap",
            "• Strips all LLM API keys before stdio spawn",
        ],
        fill=WHITE, border=ACCENT, title_color=ACCENT,
    )

    # Slide 6: Real Nginx Traffic Demo
    s6 = prs.slides.add_slide(blank)
    _add_header(s6, f"[Live Capture] Nginx Traffic ({run_label}: 10 -> {req_count})", "Real HTTP requests generate JSONL access logs; per-request state recovery prevents partial-batch stalls.", 6)
    _add_fitted_picture(
        s6,
        prepare_verified_image_stream(verified_assets, export_data, "live-traffic-verified.png", crop_bottom_ratio=0.73),
        0.65,
        1.52,
        8.05,
        5.15,
    )
    _add_card(
        s6, 8.95, 1.55, 3.75, 3.20,
        "Verified Run Lifecycle",
        [
            "• Create run dispatches 7x /demo (200) & 3x /missing (404) with unique request IDs.",
            f"• Add 404 advances {run_label} to Revision {snap_rev} ({req_count}/{ok_count}/{nf_count}).",
            "• Pre-send 503 preserves rev1 snapshot; Retry reuses Idempotency-Key.",
        ],
        fill=SURFACE, border=BORDER, title_color=INK,
    )
    _add_card(
        s6, 8.95, 4.92, 3.75, 1.78,
        "Historical & Crash Safety",
        [
            "• Historical (!is_current) runs emit 0 HTTP requests.",
            "• Legacy DBs use legacy_unknown (0 dupes) + atomic snapshot recovery.",
        ],
        fill=WHITE, border=ACCENT, title_color=ACCENT,
    )

    # Slide 7: Control-Paired Verification Philosophy
    s7 = prs.slides.add_slide(blank)
    _add_header(s7, "Why Paired Controls Prevent False-Positive Proofs", "A blocked operation only proves policy enforcement when an identical relaxed control succeeds.", 7)
    _add_card(
        s7, 0.65, 1.65, 3.8, 4.9,
        "Isolated Write Control",
        [
            "Sandbox Worker:",
            "• Mounts isolated 0666 ro_copy readonly.",
            "• Write rejected (exit 1, host SHA-256 unchanged).",
            "",
            "Writable Copy Control:",
            "• Identical image, UID 10001, and check_write_log.",
            "• Mounts rw_copy -> SANDBOX_WRITE_MARKER verified.",
        ],
    )
    _add_card(
        s7, 4.75, 1.65, 3.8, 4.9,
        "Network Isolation Control",
        [
            "Sandbox Worker:",
            "• Runs with --network none.",
            "• Request to sandbox-lab-test-service:8081 fails.",
            "",
            "Bridged Control:",
            "• Identical image, UID 10001, and check_network.",
            "• Connected to sandbox_lab_net -> HTTP 200.",
        ],
    )
    _add_card(
        s7, 8.85, 1.65, 3.8, 4.9,
        "Pass vs. Fail vs. Error",
        [
            "PASS:",
            "• Sandbox blocks action AND control succeeds.",
            "",
            "FAIL:",
            "• Sandbox permits forbidden write or network access.",
            "",
            "ERROR (Never Pass):",
            "• Timeout, Docker down, missing image, or dead control.",
        ],
        fill=WHITE, border=ACCENT, title_color=ACCENT,
    )

    # Slide 8: Live Checks Results
    s8 = prs.slides.add_slide(blank)
    checks_sub = (
        f"All {len(checks)} paired checks Pass on {run_label} Rev {snap_rev}; pinned per snapshot_id (HTTP 503/404 = Fail; control fault = Error)."
        if pass_count == len(checks)
        else f"{pass_count}/{len(checks)} paired checks Pass on {run_label} Rev {snap_rev}; pinned per snapshot_id."
    )
    _add_header(s8, f"[Live Capture] Checks & Evidence ({run_label} Rev {snap_rev})", checks_sub, 8)
    _add_fitted_picture(
        s8,
        prepare_verified_image_stream(verified_assets, export_data, "live-checks-verified.png", crop_bottom_ratio=0.60),
        0.65,
        1.52,
        8.35,
        5.15,
    )
    _add_card(
        s8, 9.20, 1.55, 3.5, 3.25,
        "Worker-Only Probes (N/A in App)",
        [
            "• Outside Mount: --path host marker -> found=false.",
            "• Timeout Cleanup: 15s sleep aborted; ps -a empty.",
            "• OOM Kill: 200 MiB in 128m -> OOMKilled=True (137).",
        ],
        fill=SURFACE, border=BORDER, title_color=INK,
    )
    _add_card(
        s8, 9.20, 4.95, 3.5, 1.75,
        "Honest Fault Verdicts",
        [
            "• Exit 137 on network probe -> Error (WORKER_EXIT_ABNORMAL).",
            "• Control HTTP 204 / crash -> Error; 404/503 -> Fail.",
        ],
        fill=WHITE, border=ACCENT, title_color=ACCENT,
    )

    # Slide 9: Live DeepSeek Agent Q&A & Scale
    s9 = prs.slides.add_slide(blank)
    _add_header(s9, f"[Live Capture] DeepSeek MCP Agent ({run_label}: 3/10 -> 4/11)", "Live DeepSeek (deepseek-flash) invoked count_status_codes via MCP stdio (historical Run 012 capture + live integration).", 9)
    _add_fitted_picture(
        s9,
        prepare_verified_image_stream(verified_assets, export_data, "live-agent-verified.png"),
        0.65,
        1.52,
        8.15,
        5.15,
    )
    _add_card(
        s9, 9.00, 1.55, 3.7, 3.15,
        "Live Agent & UI Craft",
        [
            "• Rev 1 answered 3 of 10; Rev 2 answered 4 of 11 via MCP stdio.",
            "• 100 runs & 10,000-row logs; mocked run detail < 2,048 B.",
            "• Fixture mode makes 0 backend writes; GET-only read retries.",
        ],
        fill=SURFACE, border=BORDER, title_color=INK,
    )
    _add_card(
        s9, 9.00, 4.88, 3.7, 1.82,
        "Quantitative Number Guard",
        [
            "• Rejects inverted counts ('10 out of 3' / '10 of the 3').",
            "• Enforces count_status_codes + Tool counts UI badge.",
        ],
        fill=WHITE, border=ACCENT, title_color=ACCENT,
    )

    # Slide 10: Verification & Honest Limits
    def _case_status(cases: list[dict], needle: str) -> str:
        found = next((c for c in cases if needle in c.get("classname", "") or needle in c.get("name", "")), None)
        return str(found.get("status", "unknown")) if found else "unknown"

    int_cases = int_s.get("cases", [])
    nginx_st = _case_status(int_cases, "test_nginx_runs")
    sandbox_st = _case_status(int_cases, "test_sandbox")
    mcp_st = _case_status(int_cases, "test_mcp")
    live_st = _case_status(int_cases, "test_agent_live")
    mcp_live_label = "2 passed" if (mcp_st == "passed" and live_st == "passed") else f"MCP {mcp_st}, Live {live_st}"
    unit_label = f"{unit_s['passed']} passed" + (f" ({unit_s['skipped']} skipped)" if unit_s["skipped"] else "")
    e2e_label = f"{e2e_s['passed']} passed" + (f" ({e2e_s['skipped']} skipped)" if e2e_s["skipped"] else "")

    s10 = prs.slides.add_slide(blank)
    _add_header(
        s10,
        f"Verification Summary ({total_passed}/{total_tests} Tests Passed{skip_suffix})",
        f"Provenance: deliverables/evidence/test_summary.json & linux_wsl_receipt.json · src {source_fp[:12]}...",
        10,
    )
    _add_table(
        s10, 0.65, 1.55, 12.03, 3.65,
        ["Test Suite", "Coverage", "Observed Status"],
        [
            ["Unit & Regression Tests", "Agent MCP gate/deadline, late-log timeout, isolated checks, pipe cap, lifecycle scripts, deliverable validator, 10k rows", unit_label],
            ["Nginx Integration", "Real Docker Nginx /demo & /missing -> JSONL -> multi-run isolation", f"1 {nginx_st}"],
            ["Docker Sandbox Integration", "ro/rw isolation controls, outside marker --path, cleanup, OOMKilled=True, faults", f"1 {sandbox_st}"],
            ["MCP & Live DeepSeek Integration", "Real FastMCP stdio discovery (3 tools) + Live DeepSeek 3/10 -> 4/11", mcp_live_label],
            ["Playwright E2E & Web Regressions", "1440/1280/390/360 visuals + 100-run + 10k payload + R2/R4 races + focus trap + live flows", e2e_label],
        ],
        [3.3, 6.43, 2.3],
    )
    _add_card(
        s10, 0.65, 5.42, 12.03, 1.35,
        "Key Architectural & Environment Notes",
        [
            "• Containers share the Linux kernel inside Docker Desktop's desktop-linux VM (OS-level isolation, not hardware VM boundary).",
            "• Offline fallback (Model unavailable) remains active whenever no LLM API key is provided in runtime env.",
        ],
        fill=WARN_BG, border=RGBColor(0xF3, 0xC9, 0x7B), title_color=WARN_INK,
    )

    # Slide 11: Contributions & Q&A
    s11 = prs.slides.add_slide(blank)
    _add_header(s11, "Team Contributions & Reproducible Demo", "Clear ownership across Linux infrastructure, MCP/agent backend, and Operator UI/QA.", 11)
    _add_table(
        s11, 0.65, 1.65, 12.03, 3.2,
        ["Team Member", "Core Ownership", "Key Artifacts"],
        [
            ["Member A", "Linux, Nginx, Worker Image & Policy", "infra/*, worker/*, backend/app/sandbox.py, checks.py, scripts/*.sh"],
            ["Member B", "MCP Server/Client, Agent Loop & Storage", "backend/app/mcp_server.py, mcp_client.py, agent.py, store.py, snapshots.py, traffic.py, api.py"],
            ["Member C", "Web Operator UI, Playwright E2E & Release", "web/src/*, tests/e2e/*, README.md, docs/*, scripts/build_release.py, scripts/release/*"],
        ],
        [2.3, 4.2, 5.53],
    )
    _add_card(
        s11, 0.65, 5.05, 12.03, 1.65,
        "3-Student Live Defense Sequence & Offline Release Build",
        [
            "1. Member A: Create run (Nginx 7/3) -> Checks Pass/Fail/Error controls -> raw access.jsonl & worker inspect",
            "2. Member B: Live Agent (count_status_codes -> Ask again Rev 2) OR Offline deterministic MCP + WSL2 receipt",
            "3. Member C: Mobile UI (390px) -> 10k paginated logs -> python scripts/build_release.py (strict offline gate) -> Thank you!",
        ],
        fill=SURFACE, border=ACCENT, title_color=ACCENT,
    )

    DELIV.mkdir(parents=True, exist_ok=True)
    out_pptx = DELIV / "slides.pptx"
    prs.save(str(out_pptx))
    print("Saved PPTX:", out_pptx)
    return out_pptx


if __name__ == "__main__":
    build_slides()
