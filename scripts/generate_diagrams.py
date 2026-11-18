from __future__ import annotations

from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT_DIR = Path(__file__).resolve().parents[1] / "deliverables" / "assets"


def _box(
    ax,
    x,
    y,
    w,
    h,
    title,
    subtitle="",
    fc="#FFFFFF",
    ec="#14283B",
    lw=1.6,
    title_color="#14283B",
    sub_color="#40566B",
    fontsize=10.2,
):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.0,rounding_size=0.018",
        facecolor=fc,
        edgecolor=ec,
        linewidth=lw,
    )
    ax.add_patch(patch)
    if subtitle:
        ax.text(x + w / 2, y + h * 0.67, title, ha="center", va="center", fontsize=fontsize, fontweight="bold", color=title_color, family="DejaVu Sans")
        ax.text(x + w / 2, y + h * 0.30, subtitle, ha="center", va="center", fontsize=fontsize - 2.1, color=sub_color, family="DejaVu Sans")
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", fontsize=fontsize, fontweight="bold", color=title_color, family="DejaVu Sans")


def _arrow(ax, x1, y1, x2, y2, label="", color="#0065C9", rad=0.0, lx_off=0.0, ly_off=0.024):
    arr = FancyArrowPatch(
        (x1, y1),
        (x2, y2),
        arrowstyle="-|>,head_length=6.5,head_width=4.2",
        color=color,
        linewidth=1.8,
        connectionstyle=f"arc3,rad={rad}",
    )
    ax.add_patch(arr)
    if label:
        mx = (x1 + x2) / 2 + lx_off
        my = (y1 + y2) / 2 + ly_off
        ax.text(
            mx,
            my,
            label,
            ha="center",
            va="center",
            fontsize=8.2,
            color="#14283B",
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.18", fc="#FFFFFF", ec="none", alpha=0.92),
        )


def draw_architecture() -> None:
    fig, ax = plt.subplots(figsize=(12.8, 6.6), dpi=220)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("#FFFFFF")

    # Zone backgrounds
    ax.add_patch(
        FancyBboxPatch(
            (0.245, 0.08),
            0.460,
            0.85,
            boxstyle="round,pad=0.0,rounding_size=0.02",
            facecolor="#F4FAFD",
            edgecolor="#8DC3E6",
            linewidth=1.4,
            linestyle="--",
        )
    )
    ax.text(
        0.475,
        0.90,
        "TRUSTED HOST (FastAPI + Agent + MCP + Runner)",
        ha="center",
        va="center",
        fontsize=8.3,
        fontweight="bold",
        color="#0065C9",
    )

    ax.add_patch(
        FancyBboxPatch(
            (0.740, 0.08),
            0.248,
            0.85,
            boxstyle="round,pad=0.0,rounding_size=0.02",
            facecolor="#FFF8EB",
            edgecolor="#E6A23C",
            linewidth=1.6,
            linestyle="-",
        )
    )
    ax.text(0.864, 0.90, "ISOLATED WORKER / LAB", ha="center", va="center", fontsize=9.0, fontweight="bold", color="#92400E")

    # Left column (External / Untrusted Producers)
    _box(ax, 0.015, 0.67, 0.195, 0.17, "Web Operator UI", "React 19 / TypeScript / Vite\n127.0.0.1:5173", fc="#EDF8FC", ec="#0065C9")
    _box(ax, 0.015, 0.39, 0.195, 0.17, "LLM Provider API", "DeepSeek / OpenAI / Gemini\n(Prompt & JSON Tools Only)", fc="#F8FAFC", ec="#64748B")
    _box(ax, 0.015, 0.11, 0.195, 0.17, "Nginx Lab Service", "nginx:1.27-alpine\n:18080 -> access.jsonl", fc="#FFFFFF", ec="#40566B")

    # Middle Trusted Host columns
    _box(ax, 0.275, 0.67, 0.190, 0.17, "FastAPI + Agent Loop", "127.0.0.1:18000 · SQLite WAL\n30s Deadline & MCP Gate", fc="#FFFFFF", ec="#0065C9")
    _box(ax, 0.505, 0.67, 0.190, 0.17, "Snapshot Store", "<run_id>/rev-XXXX/access.jsonl\nSHA-256 Pinned", fc="#FFFFFF", ec="#0065C9")
    _box(ax, 0.275, 0.39, 0.190, 0.17, "MCP Server (stdio)", "3 Typed Tools (mcp==1.28.1)\nUUID & SHA-256 Validation", fc="#FFFFFF", ec="#0065C9")
    _box(ax, 0.505, 0.39, 0.190, 0.17, "Policy Runner", "docker run (argv, shell=False)\n10s Timeout + rm -f", fc="#FFFFFF", ec="#0065C9")
    _box(ax, 0.365, 0.11, 0.295, 0.17, "Control Harness", "Isolated ro/rw Copies & Net Control\nVerdict: Pass / Fail / Error", fc="#E8F5FC", ec="#0065C9")

    # Right Isolated Worker column
    _box(
        ax,
        0.758,
        0.45,
        0.215,
        0.39,
        "Docker Worker\nsandbox-lab-worker:v1",
        "UID/GID 10001:10001\n--read-only rootfs\n--network none | --cap-drop ALL\nno-new-privileges\n128m RAM | 64 PIDs | 0.5 CPU\n(Shared Docker VM Kernel)",
        fc="#FFFFFF",
        ec="#B45309",
        fontsize=9.4,
    )
    _box(
        ax,
        0.758,
        0.11,
        0.215,
        0.17,
        "Internal Test Service",
        "sandbox_lab_net :8081 (Host :18081)\nControl Reachable / Worker Blocked",
        fc="#FEF2F2",
        ec="#991B1B",
        fontsize=9.4,
    )

    # Arrows reflecting clean non-overlapping topology
    _arrow(ax, 0.210, 0.755, 0.275, 0.755, "REST", ly_off=0.028)
    _arrow(ax, 0.210, 0.510, 0.275, 0.690, "Tool Proposals", rad=0.0, lx_off=-0.032, ly_off=0.032)
    # Route Nginx traffic/logs cleanly through corridor x=0.21..0.275 without crossing LLM box
    _arrow(ax, 0.305, 0.670, 0.210, 0.240, "HTTP & JSONL", rad=-0.12, lx_off=-0.028, ly_off=-0.135)
    _arrow(ax, 0.465, 0.755, 0.505, 0.755, "")
    _arrow(ax, 0.370, 0.670, 0.370, 0.560, "MCP stdio\n(no LLM keys)", lx_off=0.064, ly_off=0.0)
    _arrow(ax, 0.465, 0.475, 0.505, 0.475, "")
    _arrow(ax, 0.600, 0.670, 0.600, 0.560, "SHA-256", lx_off=0.050, ly_off=0.0)
    _arrow(ax, 0.695, 0.740, 0.758, 0.700, "readonly", color="#0F766E", ly_off=0.028)
    _arrow(ax, 0.695, 0.480, 0.758, 0.530, "spawn", color="#B45309", ly_off=0.028)
    _arrow(ax, 0.660, 0.195, 0.758, 0.195, "Control Net", color="#0065C9", ly_off=0.028)
    _arrow(ax, 0.865, 0.450, 0.865, 0.280, "BLOCKED", color="#DC2626", lx_off=0.055, ly_off=0.0)

    ax.set_title("Sandbox Lab Architecture & Trust Boundaries (Topic 17)", fontsize=13.5, fontweight="bold", color="#14283B", pad=10)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_DIR / "architecture.png", bbox_inches="tight", dpi=220)
    plt.close(fig)


def draw_mcp_flow() -> None:
    fig, ax = plt.subplots(figsize=(12.2, 6.0), dpi=220)
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("#FFFFFF")

    actors = [
        (0.08, "Operator UI"),
        (0.29, "Agent Loop"),
        (0.50, "MCP Server"),
        (0.71, "Policy Runner"),
        (0.92, "Linux Worker"),
    ]
    for x, label in actors:
        _box(ax, x - 0.085, 0.85, 0.17, 0.095, label, fc="#EDF8FC", ec="#0065C9", fontsize=10.2)
        ax.plot([x, x], [0.05, 0.85], color="#CBD5E1", linestyle="--", linewidth=1.4, zorder=0)

    steps = [
        (0.76, 0.08, 0.29, '1. Ask "How many 404s?" (Pin snapshot_id, Rev 1)', "#14283B"),
        (0.66, 0.29, 0.50, "2. MCP call_tool(count_status_codes, run_id, snapshot_id)", "#0065C9"),
        (0.56, 0.50, 0.71, "3. Validate UUIDs & Resolve rev-0001/access.jsonl SHA-256", "#0065C9"),
        (0.46, 0.71, 0.92, "4. docker run (--read-only --network none)", "#B45309"),
        (0.36, 0.92, 0.71, "5. JSON {total_requests:10, status_200:7, status_404:3} + rm -f", "#0F766E"),
        (0.26, 0.71, 0.50, "6. Verified ToolResult returned via MCP stdio", "#0065C9"),
        (0.16, 0.50, 0.29, "7. Assert completed MCP call (run_id, snapshot_id) + Model Reply", "#0065C9"),
        (0.07, 0.29, 0.08, '8. Answer "3 of 10" + Trace Link (Rev 1)', "#14283B"),
    ]
    for y, x1, x2, text, color in steps:
        arr = FancyArrowPatch((x1, y), (x2, y), arrowstyle="-|>,head_length=6.5,head_width=4", color=color, linewidth=1.8)
        ax.add_patch(arr)
        ax.text(
            (x1 + x2) / 2,
            y + 0.030,
            text,
            ha="center",
            va="center",
            fontsize=8.6,
            fontweight="bold",
            color=color,
            bbox=dict(boxstyle="round,pad=0.16", fc="#FFFFFF", ec="none", alpha=0.95),
        )

    ax.set_title("MCP Tool Execution & Snapshot Pinning Sequence", fontsize=13.5, fontweight="bold", color="#14283B", pad=8)
    fig.savefig(OUT_DIR / "mcp-sequence.png", bbox_inches="tight", dpi=220)
    plt.close(fig)


if __name__ == "__main__":
    draw_architecture()
    draw_mcp_flow()
    print("Diagrams regenerated.")
