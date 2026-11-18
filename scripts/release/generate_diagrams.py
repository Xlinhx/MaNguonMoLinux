from __future__ import annotations

from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Polygon, Rectangle

OUT_DIR = Path(__file__).resolve().parents[2] / "deliverables" / "assets"

INK = "#111827"
SUB_INK = "#374151"
LINE_DARK = "#111827"
LINE_MID = "#4B5563"
BG_WHITE = "#FFFFFF"
BG_NODE = "#F9FAFB"
BG_BEVEL = "#E5E7EB"
BG_BAR = "#FFFFFF"


def _draw_uml_node(
    ax,
    x: float,
    y: float,
    w: float,
    h: float,
    stereotype: str,
    title: str,
    *,
    dx: float = 0.014,
    dy: float = 0.018,
    fc: str = BG_NODE,
) -> None:
    """Draw a standard UML 2.5.1 3D Deployment Node / ExecutionEnvironment cube."""
    # Front face
    ax.add_patch(Rectangle((x, y), w, h, facecolor=fc, edgecolor=INK, linewidth=1.35, zorder=1))
    # Top 3D bevel
    top_poly = Polygon(
        [(x, y + h), (x + dx, y + h + dy), (x + w + dx, y + h + dy), (x + w, y + h)],
        closed=True,
        facecolor=BG_BEVEL,
        edgecolor=INK,
        linewidth=1.15,
        zorder=1,
    )
    ax.add_patch(top_poly)
    # Right 3D bevel
    right_poly = Polygon(
        [(x + w, y), (x + w + dx, y + dy), (x + w + dx, y + h + dy), (x + w, y + h)],
        closed=True,
        facecolor="#D1D5DB",
        edgecolor=INK,
        linewidth=1.15,
        zorder=1,
    )
    ax.add_patch(right_poly)

    ax.text(
        x + w / 2,
        y + h - 0.022,
        stereotype,
        ha="center",
        va="center",
        fontsize=7.6,
        color=SUB_INK,
        family="DejaVu Sans",
        zorder=2,
    )
    ax.text(
        x + w / 2,
        y + h - 0.048,
        title,
        ha="center",
        va="center",
        fontsize=8.6,
        fontweight="bold",
        color=INK,
        family="DejaVu Sans",
        zorder=2,
    )


def _draw_uml_component(
    ax,
    x: float,
    y: float,
    w: float,
    h: float,
    name: str,
    detail: str = "",
    *,
    stereotype: str = "<<component>>",
    fc: str = BG_WHITE,
    sub_fs: float = 7.2,
) -> None:
    """Draw a sharp-cornered UML 2.5.1 Component with top-right component icon."""
    ax.add_patch(Rectangle((x, y), w, h, facecolor=fc, edgecolor=INK, linewidth=1.25, zorder=3))
    # Standard UML 2 component glyph at top-right inside box
    iw, ih = 0.014, 0.020
    ix, iy = x + w - iw - 0.008, y + h - ih - 0.008
    ax.add_patch(Rectangle((ix, iy), iw, ih, facecolor=BG_WHITE, edgecolor=INK, linewidth=0.9, zorder=4))
    tw, th = 0.006, 0.0045
    ax.add_patch(Rectangle((ix - tw * 0.5, iy + ih * 0.62), tw, th, facecolor=BG_WHITE, edgecolor=INK, linewidth=0.85, zorder=5))
    ax.add_patch(Rectangle((ix - tw * 0.5, iy + ih * 0.22), tw, th, facecolor=BG_WHITE, edgecolor=INK, linewidth=0.85, zorder=5))

    ax.text(
        x + (w - 0.012) / 2,
        y + h * 0.78,
        stereotype,
        ha="center",
        va="center",
        fontsize=6.9,
        color=SUB_INK,
        family="DejaVu Sans",
        zorder=4,
    )
    ax.text(
        x + w / 2,
        y + h * (0.55 if detail else 0.42),
        name,
        ha="center",
        va="center",
        fontsize=8.6,
        fontweight="bold",
        color=INK,
        family="DejaVu Sans",
        zorder=4,
    )
    if detail:
        ax.text(
            x + w / 2,
            y + h * 0.23,
            detail,
            ha="center",
            va="center",
            fontsize=sub_fs,
            color=SUB_INK,
            family="DejaVu Sans",
            linespacing=1.22,
            zorder=4,
        )


def _uml_link(
    ax,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    label: str = "",
    *,
    dashed: bool = True,
    arrowstyle: str = "->",
    rad: float = 0.0,
    lx: float | None = None,
    ly: float | None = None,
    fs: float = 7.2,
    lw: float = 1.25,
) -> None:
    arr = FancyArrowPatch(
        (x1, y1),
        (x2, y2),
        arrowstyle=arrowstyle,
        mutation_scale=11,
        color=INK,
        linewidth=lw,
        linestyle="--" if dashed else "-",
        connectionstyle=f"arc3,rad={rad}",
        zorder=6,
    )
    ax.add_patch(arr)
    if label:
        mx = lx if lx is not None else (x1 + x2) / 2
        my = ly if ly is not None else (y1 + y2) / 2 + 0.018
        ax.text(
            mx,
            my,
            label,
            ha="center",
            va="center",
            fontsize=fs,
            color=INK,
            family="DejaVu Sans",
            bbox=dict(boxstyle="square,pad=0.12", fc=BG_WHITE, ec="none", alpha=0.95),
            zorder=7,
        )


def draw_architecture() -> None:
    """UML 2.5.1 Deployment & Component Diagram in grayscale academic print style."""
    fig, ax = plt.subplots(figsize=(13.6, 7.2), dpi=240)
    ax.set_xlim(-0.01, 1.01)
    ax.set_ylim(-0.01, 1.01)
    ax.axis("off")
    fig.patch.set_facecolor(BG_WHITE)

    # UML Nodes (3D deployment/execution environments)
    _draw_uml_node(
        ax, 0.272, 0.03, 0.410, 0.91,
        "<<device / boundary>>",
        "Trusted Host (FastAPI · SQLite WAL · Docker CLI)",
        fc="#F9FAFB",
    )
    _draw_uml_node(
        ax, 0.742, 0.49, 0.244, 0.45,
        "<<executionEnvironment>>",
        "Worker Container (--network none)",
        fc="#FFFFFF",
    )
    _draw_uml_node(
        ax, 0.742, 0.03, 0.244, 0.36,
        "<<executionEnvironment>>",
        "Lab Bridge (sandbox_lab_net)",
        fc="#F9FAFB",
    )

    # Left External / Boundary Nodes & Components
    _draw_uml_component(
        ax, 0.008, 0.72, 0.186, 0.17,
        "WebOperatorUI",
        "React Browser Console\n(Traffic · Agent · Checks · Logs)",
    )
    _draw_uml_component(
        ax, 0.008, 0.40, 0.186, 0.20,
        "ExternalLLMProvider",
        "Receives Prompt, 3 Schemas\n& Bounded Tool JSON Only",
        stereotype="<<externalService>>",
    )
    _draw_uml_component(
        ax, 0.008, 0.06, 0.186, 0.20,
        "NginxLabService",
        "Serves /demo & /missing\nAppends JSONL Access Log",
        stereotype="<<container>>",
    )

    # Trusted Host Components
    _draw_uml_component(
        ax, 0.288, 0.71, 0.172, 0.16,
        "FastAPI & AgentLoop",
        "REST API, 30s Budget,\nRevision & Count Gate",
    )
    _draw_uml_component(
        ax, 0.494, 0.71, 0.172, 0.16,
        "SnapshotStore",
        "Immutable JSONL + SHA-256\nPinned per Run / Revision",
    )
    _draw_uml_component(
        ax, 0.288, 0.40, 0.172, 0.19,
        "MCPStdioServer",
        "3 Read-Only Log Tools\n(API Keys Stripped)",
    )
    _draw_uml_component(
        ax, 0.494, 0.40, 0.172, 0.19,
        "TrustedPolicyRunner",
        "Spawns & Cleans Workers\n(shell=False · SHA-256)",
    )
    _draw_uml_component(
        ax, 0.288, 0.06, 0.172, 0.19,
        "TrafficService",
        "Dispatches HTTP Batches\n& Reconciles JSONL Logs",
    )
    _draw_uml_component(
        ax, 0.494, 0.06, 0.172, 0.19,
        "ControlHarness",
        "Isolated ro/rw Copies &\nPaired Verdict Logic",
    )

    # Right Container Execution Components
    _draw_uml_component(
        ax, 0.764, 0.52, 0.202, 0.31,
        "DockerWorker",
        "Non-Root UID 10001\nRead-Only Rootfs + /tmp\nSingle Readonly Mount\n(Shares Host/VM Kernel)",
        stereotype="<<artifact / runtime>>",
    )
    _draw_uml_component(
        ax, 0.764, 0.21, 0.155, 0.105,
        "ControlWorker",
        "sandbox_lab_net",
        stereotype="<<runtime>>",
    )
    _draw_uml_component(
        ax, 0.764, 0.05, 0.202, 0.105,
        "InternalTestService",
        "GET /ping -> HTTP 200 JSON",
        stereotype="<<container>>",
    )

    # UML Dependencies & Associations
    _uml_link(ax, 0.194, 0.805, 0.288, 0.805, "<<REST>>", dashed=False, arrowstyle="-|>", lx=0.236, ly=0.835, fs=6.9)
    _uml_link(ax, 0.288, 0.742, 0.194, 0.562, "<<prompt + json>>", dashed=True, rad=0.14, lx=0.222, ly=0.692, fs=6.9)
    _uml_link(ax, 0.194, 0.482, 0.288, 0.708, "<<toolProposal>>", dashed=True, rad=0.14, lx=0.246, ly=0.535, fs=6.9)

    _uml_link(ax, 0.288, 0.205, 0.194, 0.205, "<<HTTP>>", dashed=True, lx=0.236, ly=0.235, fs=6.9)
    _uml_link(ax, 0.194, 0.115, 0.288, 0.115, "<<JSONL>>", dashed=True, lx=0.236, ly=0.085, fs=6.9)

    # TrafficService -> SnapshotStore cleanly along central corridor x=0.477
    ax.plot([0.374, 0.477, 0.477], [0.250, 0.315, 0.755], color=INK, linestyle="--", linewidth=1.2, zorder=5)
    _uml_link(ax, 0.477, 0.755, 0.494, 0.755, "<<freeze>>", dashed=True, lx=0.477, ly=0.315, fs=6.9)

    # Host internal links
    _uml_link(ax, 0.460, 0.790, 0.494, 0.790, "", dashed=True)
    _uml_link(ax, 0.374, 0.710, 0.374, 0.590, "<<MCP stdio>>", dashed=False, arrowstyle="-|>", lx=0.418, ly=0.650, fs=6.9)
    _uml_link(ax, 0.460, 0.495, 0.494, 0.495, "", dashed=True)
    _uml_link(ax, 0.580, 0.710, 0.580, 0.590, "<<SHA-256>>", dashed=True, lx=0.625, ly=0.650, fs=6.9)
    _uml_link(ax, 0.580, 0.250, 0.580, 0.400, "<<checks>>", dashed=True, lx=0.618, ly=0.325, fs=6.9)

    # Host -> Docker Worker
    _uml_link(ax, 0.666, 0.775, 0.764, 0.755, "<<ro bind>>", dashed=True, lx=0.715, ly=0.805, fs=6.9)
    _uml_link(ax, 0.666, 0.515, 0.764, 0.580, "<<docker CLI>>", dashed=False, arrowstyle="-|>", lx=0.715, ly=0.492, fs=6.9)

    # Control Harness -> Control Worker -> Test Service
    _uml_link(ax, 0.666, 0.200, 0.764, 0.255, "<<control>>", dashed=True, lx=0.715, ly=0.255, fs=6.9)
    _uml_link(ax, 0.835, 0.210, 0.835, 0.155, "<<HTTP 200>>", dashed=True, lx=0.835, ly=0.183, fs=6.8)

    # Blocked network egress outside ControlWorker
    _uml_link(
        ax, 0.946, 0.520, 0.946, 0.155,
        "X  blocked\n(--network none)",
        dashed=True, arrowstyle="-|>", lx=0.922, ly=0.435, fs=6.9,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_DIR / "architecture.png", bbox_inches="tight", dpi=240)
    plt.close(fig)


def draw_mcp_flow() -> None:
    """Strict OMG UML 2.5.1 Sequence Diagram (sd frame, lifelines, activation bars, self-calls, create/destroy)."""
    fig, ax = plt.subplots(figsize=(14.0, 8.8), dpi=240)
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.axis("off")
    fig.patch.set_facecolor(BG_WHITE)

    # Interaction Frame (UML 2.5.1 Section 17.2)
    fx, fy, fw, fh = 0.00, 0.00, 1.00, 1.00
    ax.add_patch(Rectangle((fx, fy), fw, fh, facecolor=BG_WHITE, edgecolor=INK, linewidth=1.4, zorder=0))
    # Pentagon sd tag at top-left
    tag_w, tag_h = 0.43, 0.042
    pent = Polygon(
        [(fx, fy + fh), (fx + tag_w, fy + fh), (fx + tag_w, fy + fh - tag_h * 0.62), (fx + tag_w - 0.020, fy + fh - tag_h), (fx, fy + fh - tag_h)],
        closed=True,
        facecolor=BG_NODE,
        edgecolor=INK,
        linewidth=1.2,
        zorder=1,
    )
    ax.add_patch(pent)
    ax.text(
        fx + 0.012,
        fy + fh - tag_h * 0.50,
        "sd  MCP Tool Execution (count_status_codes, Rev 1)",
        ha="left",
        va="center",
        fontsize=8.6,
        fontweight="bold",
        color=INK,
        family="DejaVu Sans",
        zorder=2,
    )

    # Top lifelines (worker created dynamically at Step 6, so worker head is drawn lower at Step 6)
    xs = {
        "ui": 0.082,
        "loop": 0.238,
        "llm": 0.394,
        "mcp": 0.552,
        "runner": 0.714,
        "worker": 0.896,
    }
    heads_top = [
        ("ui", "ui : OperatorUI"),
        ("loop", "loop : AgentLoop"),
        ("llm", "llm : LLMProvider"),
        ("mcp", "mcp : MCPServer"),
        ("runner", "runner : PolicyRunner"),
    ]
    head_y, head_w, head_h = 0.895, 0.142, 0.048
    lifeline_end_y = 0.030

    for key, label in heads_top:
        x = xs[key]
        ax.add_patch(Rectangle((x - head_w / 2, head_y), head_w, head_h, facecolor=BG_NODE, edgecolor=INK, linewidth=1.25, zorder=2))
        ax.text(x, head_y + head_h / 2, label, ha="center", va="center", fontsize=7.8, fontweight="bold", color=INK, family="DejaVu Sans", zorder=3)
        ax.plot([x, x], [lifeline_end_y, head_y], color=LINE_MID, linestyle="--", linewidth=1.1, zorder=1)

    # Activation Bars (ExecutionSpecifications) strictly synced with message y coordinates
    bar_w = 0.014

    def _bar(key: str, y_top: float, y_bot: float, offset_x: float = 0.0, fc: str = BG_BAR) -> None:
        ax.add_patch(Rectangle((xs[key] - bar_w / 2 + offset_x, y_bot), bar_w, y_top - y_bot, facecolor=fc, edgecolor=INK, linewidth=1.1, zorder=4))

    # Y schedule (top-to-bottom)
    y1 = 0.840    # 1: POST /questions
    y1b = 0.798   # 1b: 202 / job_id (return)
    y2 = 0.752    # 2: pin snapshot & requestLLM(...)
    y3 = 0.706    # 3: toolProposal: count_status_codes
    y4 = 0.654    # 4: call_tool(...) [keys stripped]
    y5 = 0.602    # 5: execute_tool(snapshot_id)
    y6 = 0.545    # 6: <<create>> docker run ... -> creates worker head box!
    worker_head_y = 0.521
    y7 = 0.456    # 7: stdout JSON {...} -> returns to runner
    y7b_top = 0.422
    y7b_bot = 0.390  # 7b: self verify sha256
    y8 = 0.352    # 8: <<destroy>> docker rm -f -> Destruction X on worker
    y9 = 0.305    # 9: WorkerExecutionResult
    y10 = 0.258   # 10: ToolResult (MCP stdio)
    y11 = 0.212   # 11: sendToolResult(bounded JSON)
    y11b = 0.166  # 11b: draftReply
    y12_top = 0.134
    y12_bot = 0.102  # 12: self verify rev & 404/total counts
    y13 = 0.062   # 13: GET /jobs/{id} & /messages -> "3 404s / 10"

    # Execution bars
    _bar("ui", y1 + 0.012, y1b - 0.008)
    _bar("loop", y1, y1b - 0.005)
    _bar("loop", y2 + 0.015, y13 - 0.010)
    _bar("llm", y2, y3)
    _bar("mcp", y4, y10)
    _bar("runner", y5, y9)
    _bar("runner", y7b_top, y7b_bot, offset_x=bar_w * 0.45, fc="#F3F4F6")  # nested self-execution bar
    _bar("worker", worker_head_y, y8)
    _bar("llm", y11, y11b)
    _bar("loop", y12_top, y12_bot, offset_x=bar_w * 0.45, fc="#F3F4F6")  # nested self-execution bar
    _bar("ui", y13 + 0.008, y13 - 0.012)

    # Dynamic Worker lifeline head created at Step 6 and destroyed at Step 8 (UML Create + Destroy)
    wx = xs["worker"]
    w_head_w = 0.150
    ax.add_patch(Rectangle((wx - w_head_w / 2, worker_head_y), w_head_w, head_h, facecolor=BG_NODE, edgecolor=INK, linewidth=1.25, zorder=2))
    ax.text(wx, worker_head_y + head_h / 2, "worker : DockerWorker", ha="center", va="center", fontsize=7.6, fontweight="bold", color=INK, family="DejaVu Sans", zorder=3)
    ax.plot([wx, wx], [y8, worker_head_y], color=LINE_MID, linestyle="--", linewidth=1.1, zorder=1)
    # UML DestructionOccurrence 'X' at y8
    xr = 0.011
    ax.plot([wx - xr, wx + xr], [y8 - xr, y8 + xr], color=INK, linewidth=1.8, zorder=7)
    ax.plot([wx - xr, wx + xr], [y8 + xr, y8 - xr], color=INK, linewidth=1.8, zorder=7)

    def _seq_msg(y: float, k1: str, k2: str, text: str, *, is_reply: bool = False, target_x: float | None = None, lx: float | None = None, fs: float = 7.7) -> None:
        x_start = xs[k1] + (bar_w / 2 if xs[k2] > xs[k1] else -bar_w / 2)
        if target_x is not None:
            x_end = target_x
        else:
            x_end = xs[k2] + (-bar_w / 2 if xs[k2] > xs[k1] else bar_w / 2)
        arr = FancyArrowPatch(
            (x_start, y),
            (x_end, y),
            arrowstyle="->" if is_reply else "-|>",
            mutation_scale=12,
            color=INK,
            linewidth=1.3,
            linestyle="--" if is_reply else "-",
            zorder=6,
        )
        ax.add_patch(arr)
        ax.text(
            lx if lx is not None else (x_start + x_end) / 2,
            y + 0.016,
            text,
            ha="center",
            va="center",
            fontsize=fs,
            color=INK,
            family="DejaVu Sans",
            bbox=dict(boxstyle="square,pad=0.10", fc=BG_WHITE, ec="none", alpha=0.96),
            zorder=7,
        )

    def _self_call(key: str, y_top: float, y_bot: float, label: str, fs: float = 7.5) -> None:
        x_base = xs[key] + bar_w / 2
        x_nested = xs[key] + bar_w * 0.95
        loop_w = 0.036
        ax.plot([x_base, x_base + loop_w, x_base + loop_w], [y_top, y_top, y_bot], color=INK, linewidth=1.25, zorder=6)
        arr = FancyArrowPatch(
            (x_base + loop_w, y_bot),
            (x_nested, y_bot),
            arrowstyle="-|>",
            mutation_scale=10,
            color=INK,
            linewidth=1.25,
            zorder=6,
        )
        ax.add_patch(arr)
        ax.text(
            x_base + loop_w + 0.008,
            (y_top + y_bot) / 2,
            label,
            ha="left",
            va="center",
            fontsize=fs,
            color=INK,
            family="DejaVu Sans",
            bbox=dict(boxstyle="square,pad=0.08", fc=BG_WHITE, ec="none", alpha=0.95),
            zorder=7,
        )

    _seq_msg(y1, "ui", "loop", "1: POST /questions", is_reply=False)
    _seq_msg(y1b, "loop", "ui", "1b: 200 job_id <<async>>", is_reply=True)
    _seq_msg(y2, "loop", "llm", "2: pin(Rev 1) & prompt + 3 schemas", is_reply=False)
    _seq_msg(y3, "llm", "loop", "3: toolProposal(count_status_codes)", is_reply=True)
    _seq_msg(y4, "loop", "mcp", "4: call_tool(run_id, snapshot_id) [keys stripped]", is_reply=False, lx=0.395)
    _seq_msg(y5, "mcp", "runner", "5: validateUUIDs & execute_tool(snapshot_id)", is_reply=False)
    _seq_msg(y6, "runner", "worker", "6: <<create>> docker run\n(--read-only --network none)", is_reply=True, target_x=wx - w_head_w / 2, lx=0.735, fs=7.1)
    _seq_msg(y7, "worker", "runner", "7: stdout JSON {total_requests,\nstatus_200, status_404, snapshot_sha256}", is_reply=True, lx=0.805, fs=7.0)
    _self_call("runner", y7b_top, y7b_bot, "7b: verify sha256(snapshot)")
    _seq_msg(y8, "runner", "worker", "8: <<destroy>> docker rm -f", is_reply=False, target_x=wx - xr - 0.002, lx=0.805, fs=7.2)
    _seq_msg(y9, "runner", "mcp", "9: WorkerExecutionResult", is_reply=True)
    _seq_msg(y10, "mcp", "loop", "10: ToolResult (MCP stdio)", is_reply=True, lx=0.395)
    _seq_msg(y11, "loop", "llm", "11: sendToolResult(bounded JSON)", is_reply=False)
    _seq_msg(y11b, "llm", "loop", "11b: draftReply", is_reply=True)
    _self_call("loop", y12_top, y12_bot, "12: verify rev & 404/total counts")
    _seq_msg(y13, "loop", "ui", '13: GET /jobs/{id} & /messages ("3 404s / 10")', is_reply=True, lx=0.205, fs=7.3)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_DIR / "mcp-sequence.png", bbox_inches="tight", dpi=240)
    plt.close(fig)


if __name__ == "__main__":
    draw_architecture()
    draw_mcp_flow()
    print("Diagrams regenerated cleanly (strict grayscale UML 2.5.1).")
