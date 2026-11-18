from __future__ import annotations

from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT_DIR = Path(__file__).resolve().parents[2] / "deliverables" / "assets"


def _box(
    ax,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    subtitle: str = "",
    *,
    fc: str = "#FFFFFF",
    ec: str = "#14283B",
    lw: float = 1.6,
    ls: str = "-",
    title_color: str = "#14283B",
    sub_color: str = "#40566B",
    fontsize: float = 9.4,
    sub_fontsize: float = 7.4,
) -> None:
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.0,rounding_size=0.015",
        facecolor=fc,
        edgecolor=ec,
        linewidth=lw,
        linestyle=ls,
    )
    ax.add_patch(patch)
    if subtitle:
        ax.text(
            x + w / 2,
            y + h * 0.69,
            title,
            ha="center",
            va="center",
            fontsize=fontsize,
            fontweight="bold",
            color=title_color,
            family="DejaVu Sans",
        )
        ax.text(
            x + w / 2,
            y + h * 0.30,
            subtitle,
            ha="center",
            va="center",
            fontsize=sub_fontsize,
            color=sub_color,
            family="DejaVu Sans",
            linespacing=1.28,
        )
    else:
        ax.text(
            x + w / 2,
            y + h / 2,
            title,
            ha="center",
            va="center",
            fontsize=fontsize,
            fontweight="bold",
            color=title_color,
            family="DejaVu Sans",
        )


def _arrow(
    ax,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    label: str = "",
    *,
    color: str = "#0065C9",
    ls: str = "-",
    rad: float = 0.0,
    lx: float | None = None,
    ly: float | None = None,
    lx_off: float = 0.0,
    ly_off: float = 0.020,
    fontsize: float = 7.5,
) -> None:
    arr = FancyArrowPatch(
        (x1, y1),
        (x2, y2),
        arrowstyle="-|>",
        mutation_scale=13,
        color=color,
        linewidth=1.55,
        linestyle=ls,
        connectionstyle=f"arc3,rad={rad}",
    )
    ax.add_patch(arr)
    if label:
        mx = lx if lx is not None else ((x1 + x2) / 2 + lx_off)
        my = ly if ly is not None else ((y1 + y2) / 2 + ly_off)
        ax.text(
            mx,
            my,
            label,
            ha="center",
            va="center",
            fontsize=fontsize,
            color=color if color != "#475569" else "#14283B",
            fontweight="bold",
            family="DejaVu Sans",
            bbox=dict(boxstyle="round,pad=0.14", fc="#FFFFFF", ec="none", alpha=0.94),
        )


def draw_architecture() -> None:
    fig, ax = plt.subplots(figsize=(13.6, 7.0), dpi=240)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("#FFFFFF")

    # Zone 2: Trusted Host Boundary (center)
    ax.add_patch(
        FancyBboxPatch(
            (0.268, 0.04),
            0.418,
            0.92,
            boxstyle="round,pad=0.0,rounding_size=0.018",
            facecolor="#F4FAFD",
            edgecolor="#0065C9",
            linewidth=1.5,
            linestyle="--",
        )
    )
    ax.text(
        0.477,
        0.930,
        "TRUSTED HOST (Python Backend · SQLite WAL · Docker CLI)",
        ha="center",
        va="center",
        fontsize=8.0,
        fontweight="bold",
        color="#0065C9",
        family="DejaVu Sans",
    )

    # Zone 3A: Isolated Worker Container (top-right, strictly no network)
    ax.add_patch(
        FancyBboxPatch(
            (0.752, 0.49),
            0.238,
            0.47,
            boxstyle="round,pad=0.0,rounding_size=0.018",
            facecolor="#FFFBEB",
            edgecolor="#B45309",
            linewidth=1.6,
            linestyle="-",
        )
    )
    ax.text(
        0.871,
        0.930,
        "ISOLATED WORKER (--network none)",
        ha="center",
        va="center",
        fontsize=8.0,
        fontweight="bold",
        color="#92400E",
        family="DejaVu Sans",
    )

    # Zone 3B: Control & Lab Test Network (bottom-right, separate Docker bridge network)
    ax.add_patch(
        FancyBboxPatch(
            (0.752, 0.04),
            0.238,
            0.35,
            boxstyle="round,pad=0.0,rounding_size=0.018",
            facecolor="#F8FAFC",
            edgecolor="#475569",
            linewidth=1.5,
            linestyle="-.",
        )
    )
    ax.text(
        0.871,
        0.368,
        "LAB NETWORK (sandbox_lab_net)",
        ha="center",
        va="center",
        fontsize=8.0,
        fontweight="bold",
        color="#334155",
        family="DejaVu Sans",
    )

    # Left column: External Actors & Lab Web Service
    _box(
        ax, 0.010, 0.72, 0.194, 0.17,
        "Web Operator UI",
        "Browser Console\n(Traffic · Agent · Checks · Logs)",
        fc="#EDF8FC", ec="#0065C9", fontsize=9.4, sub_fontsize=7.6,
    )
    _box(
        ax, 0.010, 0.41, 0.194, 0.20,
        "External LLM API",
        "Prompt, 3 Tool Schemas &\nBounded Tool JSON Only",
        fc="#F8FAFC", ec="#64748B", fontsize=9.4, sub_fontsize=7.6,
    )
    _box(
        ax, 0.010, 0.08, 0.194, 0.20,
        "Nginx Lab Service",
        "Serves /demo & /missing\nAppends JSONL Access Log",
        fc="#FFFFFF", ec="#334155", fontsize=9.4, sub_fontsize=7.6,
    )

    # Center Trusted Host components (clean 3 rows x 2 columns)
    _box(
        ax, 0.286, 0.72, 0.176, 0.17,
        "FastAPI & Agent Loop",
        "REST API, 30s Budget,\nNumber & Revision Gate",
        fc="#FFFFFF", ec="#0065C9", fontsize=9.2, sub_fontsize=7.6,
    )
    _box(
        ax, 0.492, 0.72, 0.176, 0.17,
        "Snapshot Store",
        "Immutable JSONL + SHA-256\nPinned per Run / Revision",
        fc="#FFFFFF", ec="#0065C9", fontsize=9.2, sub_fontsize=7.6,
    )
    _box(
        ax, 0.286, 0.41, 0.176, 0.20,
        "MCP Stdio Server",
        "3 Read-Only Log Tools\n(API Keys Stripped)",
        fc="#FFFFFF", ec="#0065C9", fontsize=9.2, sub_fontsize=7.6,
    )
    _box(
        ax, 0.492, 0.41, 0.176, 0.20,
        "Trusted Policy Runner",
        "Spawns & Cleans Workers\n(shell=False · SHA-256)",
        fc="#FFFFFF", ec="#0065C9", fontsize=9.2, sub_fontsize=7.6,
    )
    _box(
        ax, 0.286, 0.08, 0.176, 0.20,
        "Traffic Service",
        "Dispatches HTTP Batches\n& Reconciles JSONL Logs",
        fc="#E8F5FC", ec="#0065C9", fontsize=9.2, sub_fontsize=7.6,
    )
    _box(
        ax, 0.492, 0.08, 0.176, 0.20,
        "Control Harness",
        "Isolated ro/rw Copies &\nPaired Verdict Logic",
        fc="#E8F5FC", ec="#0065C9", fontsize=9.2, sub_fontsize=7.6,
    )

    # Right Zone boxes
    _box(
        ax, 0.768, 0.54, 0.206, 0.34,
        "Docker Worker Container",
        "Ephemeral Non-Root Process\nRead-Only Rootfs + /tmp\nSingle Readonly Snapshot Mount\n(Shares Host/VM Linux Kernel)",
        fc="#FFFFFF", ec="#B45309", fontsize=9.2, sub_fontsize=7.6,
    )
    _box(
        ax, 0.768, 0.23, 0.155, 0.105,
        "Control Worker",
        "sandbox_lab_net",
        fc="#FFFFFF", ec="#0065C9", fontsize=8.8, sub_fontsize=7.5,
    )
    _box(
        ax, 0.768, 0.06, 0.206, 0.100,
        "Internal Test Service",
        "GET /ping -> HTTP 200 JSON",
        fc="#FEF2F2", ec="#991B1B", fontsize=8.8, sub_fontsize=7.5,
    )

    # Arrows: clean orthogonal / non-crossing paths with white label backplates
    _arrow(ax, 0.204, 0.805, 0.286, 0.805, "REST", lx=0.245, ly=0.835, fontsize=7.8)
    _arrow(ax, 0.286, 0.748, 0.204, 0.575, "Prompt + JSON", color="#475569", rad=0.14, lx=0.236, ly=0.690, fontsize=7.6)
    _arrow(ax, 0.204, 0.495, 0.286, 0.715, "Tool\nProposals", color="#0065C9", rad=0.14, lx=0.238, ly=0.545, fontsize=7.5)

    _arrow(ax, 0.286, 0.225, 0.204, 0.225, "HTTP", color="#0065C9", lx=0.245, ly=0.252, fontsize=7.8)
    _arrow(ax, 0.204, 0.135, 0.286, 0.135, "JSONL", color="#0F766E", ls="--", lx=0.245, ly=0.105, fontsize=7.8)

    # Traffic Service -> Snapshot Store along center gap x=0.477; label placed cleanly in open lower corridor y=0.335
    ax.plot([0.374, 0.477, 0.477], [0.280, 0.335, 0.765], color="#0F766E", linestyle="--", linewidth=1.45)
    _arrow(ax, 0.477, 0.765, 0.492, 0.765, "Freeze", color="#0F766E", ls="--", lx=0.477, ly=0.335, fontsize=7.6)

    # Host internal links
    _arrow(ax, 0.462, 0.805, 0.492, 0.805, "")
    _arrow(ax, 0.374, 0.720, 0.374, 0.610, "MCP stdio", lx=0.418, ly=0.665, fontsize=7.6)
    _arrow(ax, 0.462, 0.510, 0.492, 0.510, "")
    _arrow(ax, 0.580, 0.720, 0.580, 0.610, "SHA-256", lx=0.625, ly=0.665, fontsize=7.6)
    _arrow(ax, 0.580, 0.280, 0.580, 0.410, "Checks", color="#0065C9", lx=0.618, ly=0.345, fontsize=7.6)

    # Host -> Docker Worker (label centered cleanly in white gap x=0.718)
    _arrow(ax, 0.668, 0.790, 0.768, 0.770, "readonly\nmount", color="#0F766E", lx=0.718, ly=0.822, fontsize=7.6)
    _arrow(ax, 0.668, 0.545, 0.768, 0.605, "docker\nrun / rm -f", color="#B45309", lx=0.719, ly=0.510, fontsize=7.2)

    # Control Harness -> Control Worker -> Test Service
    _arrow(ax, 0.668, 0.220, 0.768, 0.275, "Control", color="#0065C9", lx=0.718, ly=0.272, fontsize=7.6)
    _arrow(ax, 0.835, 0.230, 0.835, 0.160, "HTTP 200", color="#0F766E", lx=0.835, ly=0.195, fontsize=7.5)

    # Isolated Worker -> Internal Test Service blocked arrow cleanly outside Control Worker box
    _arrow(ax, 0.948, 0.540, 0.948, 0.160, "BLOCKED\n(--network none)", color="#DC2626", ls="--", lx=0.922, ly=0.438, fontsize=7.4)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_DIR / "architecture.png", bbox_inches="tight", dpi=240)
    plt.close(fig)


def draw_mcp_flow() -> None:
    # Strict UML 2.5.1 sequence notation: solid -|> for synchronous calls, dashed -> for returns.
    fig, ax = plt.subplots(figsize=(14.4, 8.4), dpi=240)
    ax.set_xlim(-0.04, 1.03)
    ax.set_ylim(-0.02, 1.01)
    ax.axis("off")
    fig.patch.set_facecolor("#FFFFFF")

    actors = [
        (0.080, "Operator UI"),
        (0.255, "Agent Loop"),
        (0.430, "LLM Provider"),
        (0.605, "MCP Server\n(stdio)"),
        (0.780, "Policy Runner"),
        (0.945, "Docker Worker"),
    ]
    for x, label in actors:
        _box(ax, x - 0.070, 0.91, 0.140, 0.078, label, fc="#EDF8FC", ec="#0065C9", fontsize=9.2)
        ax.plot([x, x], [0.00, 0.91], color="#94A3B8", linestyle="--", linewidth=1.2, zorder=0)

    def _msg(y: float, x1: float, x2: float, text: str, color: str, is_reply: bool = False, lx: float | None = None, fs: float = 8.0) -> None:
        arr = FancyArrowPatch(
            (x1, y),
            (x2, y),
            arrowstyle="->" if is_reply else "-|>",
            mutation_scale=13,
            color=color,
            linewidth=1.55,
            linestyle="--" if is_reply else "-",
        )
        ax.add_patch(arr)
        ax.text(
            lx if lx is not None else (x1 + x2) / 2,
            y + 0.022,
            text,
            ha="center",
            va="center",
            fontsize=fs,
            fontweight="bold",
            color=color,
            family="DejaVu Sans",
            bbox=dict(boxstyle="round,pad=0.14", fc="#FFFFFF", ec="none", alpha=0.96),
        )

    _msg(0.855, 0.080, 0.255, "1. POST /questions -> job_id (queues async Agent task)", "#14283B", is_reply=False, lx=0.220, fs=7.9)
    _msg(0.788, 0.255, 0.430, "2. Pin Rev 1 sha256; Question + 3 tool schemas ->", "#0065C9", is_reply=False, fs=7.9)
    _msg(0.720, 0.430, 0.255, "3. Tool proposal: count_status_codes(run_id)", "#475569", is_reply=True, fs=7.9)
    _msg(0.652, 0.255, 0.605, "4. call_tool(count_status_codes, run_id, snapshot_id) [keys stripped]", "#0065C9", is_reply=False, fs=7.9)
    _msg(0.584, 0.605, 0.780, "5. Validate UUIDs & execute_tool(snapshot_id)", "#0065C9", is_reply=False, fs=7.9)
    _msg(0.516, 0.780, 0.945, "6. docker run --read-only --network none (readonly snapshot)", "#B45309", is_reply=False, lx=0.800, fs=7.8)
    _msg(0.448, 0.945, 0.780, "7. stdout JSON {total_requests, status_200, status_404, snapshot_sha256}", "#0F766E", is_reply=True, lx=0.770, fs=7.6)

    # Self-action box centered cleanly over Policy Runner lifeline with generous internal margin
    _box(
        ax, 0.665, 0.352, 0.230, 0.056,
        "8. Verify sha256 & docker rm -f",
        fc="#FFFBEB", ec="#B45309", lw=1.2, fontsize=8.1,
    )

    _msg(0.305, 0.780, 0.605, "9. WorkerExecutionResult", "#0F766E", is_reply=True, fs=8.0)
    _msg(0.240, 0.605, 0.255, "10. Verified ToolResult via MCP stdio", "#0065C9", is_reply=True, fs=8.0)
    _msg(0.175, 0.255, 0.430, "11. Bounded JSON result", "#0065C9", is_reply=False, fs=7.5)
    _msg(0.110, 0.430, 0.255, "12. Draft answer text", "#475569", is_reply=True, fs=7.9)

    # Self-check box centered cleanly over Agent Loop lifeline without touching borders
    _box(
        ax, 0.145, 0.045, 0.220, 0.046,
        "13. Verify rev & 404/total counts",
        fc="#E8F5FC", ec="#0065C9", lw=1.1, fontsize=7.9,
    )
    _msg(0.005, 0.255, 0.080, '14. GET /jobs/{id} & /messages -> "3 404s / 10" (Rev 1)', "#14283B", is_reply=True, lx=0.220, fs=7.9)

    fig.savefig(OUT_DIR / "mcp-sequence.png", bbox_inches="tight", dpi=240)
    plt.close(fig)


if __name__ == "__main__":
    draw_architecture()
    draw_mcp_flow()
    print("Diagrams regenerated cleanly.")
