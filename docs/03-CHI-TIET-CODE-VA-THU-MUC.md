# 03 · Bản đồ code và thư mục: File nào làm gì?

Khi giảng viên hỏi *"Phần này nằm ở file nào?"* hoặc bạn cần đọc lại code để sửa/giải thích, tra thẳng bảng dưới đây.

---

## 1. Cấu trúc tổng thể của repository

```text
MaNguonMoLinux/
├── README.md                  # Hướng dẫn cài đặt, chạy dịch vụ và các lệnh kiểm thử
├── .env.example               # Mẫu biến môi trường (KHÔNG lưu API key thật vào Git)
├── backend/                   # FastAPI REST API, logic tạo run, checks, MCP và Agent
├── worker/                    # Image Linux cô lập (Alpine + UID 10001 + Python worker)
├── infra/                     # Docker Compose cho Nginx (:18080) & Test Service (:18081)
├── web/                       # Giao diện React 19 + TypeScript + Vite (:5173)
├── scripts/                   # Script khởi động/dừng/kiểm tra & build_release.py
├── tests/                     # 62 test tự động (unit, integration thật, Playwright E2E)
├── docs/                      # Tài liệu hướng dẫn (00..04 tiếng Việt + architecture/demo tiếng Anh)
└── deliverables/              # Báo cáo VKU (report.docx/pdf), ảnh & bằng chứng đã kiểm định
```

---

## 2. Chi tiết từng file quan trọng nhất

### A. Hạ tầng Linux & Worker cô lập (`infra/`, `worker/`)
| File | Nhiệm vụ chính | Ghi chú khi giải thích |
|---|---|---|
| [infra/compose.yaml](../infra/compose.yaml) | Khai báo 2 service `nginx` (`127.0.0.1:18080`) và `test-service` (`127.0.0.1:18081`) cùng mạng nội bộ `sandbox_lab_net`. | Lưu log Nginx ra `runtime/nginx-logs/`. |
| [infra/nginx/default.conf](../infra/nginx/default.conf) | Định nghĩa endpoint `/demo` (`200`), `/missing` (`404`) và format log JSON chứa `run_id`, `request_id`, `seq`. | Nguồn dữ liệu thật cho **Traffic** và **Access log**. |
| [infra/test-service/default.conf](../infra/test-service/default.conf) | Endpoint `/ping` trả `HTTP 200` JSON `{"service":"sandbox-test-service","status":"ok"}`. | Dùng làm đích cho kiểm tra mạng `reach_test_service`. |
| [worker/Dockerfile](../worker/Dockerfile) | Đóng gói `alpine:3.21` + `python3`, tạo user không quyền root `10001:10001`, quyền file `0555`. | Chạy `ENTRYPOINT ["python3", "/app/main.py"]`. |
| [worker/main.py](../worker/main.py) | Thực thi 9 hành động cho phép (`count_status_codes`, `top_error_paths`, `recent_http_errors` và 6 lệnh `check_*`). Đọc/hash file log một lượt (`_read_sha256_and_records`). | Không gọi shell, mọi log được parse an toàn bằng `json.loads()`. |

### B. Backend, MCP & Sandbox Policy (`backend/app/`)
| File | Nhiệm vụ chính | Hàm / lớp cần nhớ |
|---|---|---|
| [backend/app/config.py](../backend/app/config.py) | Đọc cấu hình từ biến môi trường tiến trình (`Settings.from_env()`), tạo các đường dẫn `runtime/`. | Lưu ý: đọc trực tiếp `os.getenv`, không tự đọc `.env`. |
| [backend/app/models.py](../backend/app/models.py) | Định nghĩa dữ liệu bằng Pydantic: `Run`, `Snapshot`, `RequestRecord`, `ToolResult`, `CheckResult`, `AgentMessage`, `JobRecord`. | Ràng buộc kiểu dữ liệu toàn hệ thống. |
| [backend/app/store.py](../backend/app/store.py) | Lưu trữ SQLite WAL mode; giữ khóa `Idempotency-Key` bằng `BEGIN IMMEDIATE`, quản lý cột `dispatch_state` (`legacy_unknown` cho DB nâng cấp). | Ngăn tạo trùng run và phát trùng request khi retry. |
| [backend/app/snapshots.py](../backend/app/snapshots.py) | Parse log Nginx, ghi snapshot nguyên tử (`.tmp` -> `os.replace`) và tính mã băm SHA-256 cho từng revision. | Không bao giờ ghi đè snapshot đã đóng. |
| [backend/app/traffic.py](../backend/app/traffic.py) | Điều phối gửi batch 10 request ban đầu (`create_run`), `append_404`, đối soát log (`reconcile_run_logs`) và bảo vệ run lịch sử chỉ đọc (`0` request). | Xử lý tốt cả rớt mạng giữa batch và khôi phục sau crash. |
| [backend/app/sandbox.py](../backend/app/sandbox.py) | **Trái tim bảo mật:** dựng danh sách cờ `docker run` (`build_docker_run_argv`), chặn hành động ngoài allowlist, cắt pipe `64 KiB`, dọn container theo `call_id`/`job_id`, dịch path WSL2 (`wslpath -w` khi dùng `docker.exe`). | Mọi lời gọi worker đều bắt buộc đi qua đây. |
| [backend/app/checks.py](../backend/app/checks.py) | Chạy 3 phép kiểm đối chứng (`run_read_log_check`, `run_write_log_check` dùng 2 bản sao 0666 độc lập, `run_network_check` kiểm chặt `exit_code` và `/ping`). | Đảm bảo lỗi hạ tầng hoặc `exit 137` luôn ra `Error` (`WORKER_EXIT_ABNORMAL`). |
| [backend/app/mcp_server.py](../backend/app/mcp_server.py) | FastMCP server qua `stdio`, chỉ mở 3 tool đọc log, kiểm UUID & giới hạn `limit`, kiểm lại SHA-256 kết quả tool. | Không mở quyền chạy shell hay tool `check_*`. |
| [backend/app/mcp_client.py](../backend/app/mcp_client.py) | Khởi chạy `mcp_server.py` qua `stdio_client`, **xóa sạch mọi API key LLM** khỏi biến môi trường con trước khi gọi. | Ngăn lộ secret xuống tiến trình tool/worker. |
| [backend/app/agent.py](../backend/app/agent.py) | Vòng lặp Agent (`AgentService`), adapter cho DeepSeek/OpenAI/Gemini/Anthropic, kiểm tra khóa `snapshot_id`/`revision`/`sha256` và kiểm định số học (`has_consistent_404_counts`). | Chặn model đoán mò hoặc đảo ngược số (`10 out of 3`). |
| [backend/app/api.py](../backend/app/api.py) | FastAPI endpoints (`/api/health`, `/api/runs`, `/refresh`, `/requests/404`, `/logs`, `/checks`, `/questions`, `/jobs/{id}/cancel`, `/export`). | Phục vụ cho Web UI và export dữ liệu kiểm chứng. |

### C. Giao diện Web (`web/src/`)
| File / Thư mục | Nhiệm vụ chính |
|---|---|
| [web/src/App.tsx](../web/src/App.tsx) | Điều hướng 4 tab, quản lý lượt chạy đang chọn (`selectionGenRef` chống ghi đè kết quả đến muộn khi đổi run nhanh), theo dõi job Agent nền. |
| [web/src/pages/TrafficPage.tsx](../web/src/pages/TrafficPage.tsx) | Dải request `01..10`, nút `Create run` / `Add 404`, ẩn nút phát traffic trên run lịch sử chỉ đọc. |
| [web/src/pages/ChecksPage.tsx](../web/src/pages/ChecksPage.tsx) | Bảng 3 phép kiểm (`Pass`/`Fail`/`Error`) + hộp **Control evidence**. |
| [web/src/pages/AgentPage.tsx](../web/src/pages/AgentPage.tsx) | Chat hỏi đáp, tiến trình MCP, nút `Cancel`, cảnh báo `Revision` cũ + `Ask again`, nhãn `Tool counts: X 404 / Y total` và `View tool call →`. |
| [web/src/pages/AccessLogPage.tsx](../web/src/pages/AccessLogPage.tsx) | Lọc theo mã/path, phân trang cursor 10.000 dòng, định vị tới request đang chọn. |
| [web/src/components/](../web/src/components) | `HeaderNav`, `LogoMark`, `RunSelector`, `PaginationBar`, `BottomSheet` (hỗ trợ `inert` + Tab focus trap trên mobile), `EmptyRunState`. |

### D. Scripts, Kiểm thử & Đầu ra (`scripts/`, `tests/`, `deliverables/`)
- **Scripts vận hành:** [preflight.sh](../scripts/preflight.sh) (kiểm tra môi trường), [start.sh](../scripts/start.sh) / [stop.sh](../scripts/stop.sh) (bật/tắt kèm bảo vệ PGID chống sót tiến trình), [verify.sh](../scripts/verify.sh) (chạy đủ test + chặn `skipped > 0` ở chế độ phát hành), [build_release.py](../scripts/build_release.py) (cổng kiểm định + xuất báo cáo VKU).
- **Module đóng gói ([scripts/release/](../scripts/release)):** `evidence_validator.py` (đối chiếu SHA-256 ảnh, fingerprint code, không lộ key, khớp JUnit XML), `build_report.py` (tạo DOCX 2 section đánh số `Page 1` từ Chương 1 + tự khớp Mục lục PDF), `generate_diagrams.py` (vẽ `architecture.png`, `mcp-sequence.png`).
- **Kiểm thử ([tests/](../tests)):**
  - `tests/unit/` (`9` file, `48` test): kiểm tra policy args, concurrency, legacy DB, crash recovery, MCP digest, chống đảo số Agent, lifecycle script và validator.
  - `tests/integration/` (`4` file, `4` test): chạy với **Docker + Nginx + MCP stdio + DeepSeek thật**, kiểm tra `OOMKilled=True`, outside mount, cleanup.
  - `tests/e2e/` (`3` file, `10` test): Playwright chạy thật trên desktop (`1440px`/`1280px`) & mobile (`390px`/`360px`), race condition đổi tab/đổi run, `10.000` dòng log.
- **Đầu ra ([deliverables/](../deliverables)):** [report.docx](../deliverables/report.docx), [report.pdf](../deliverables/report.pdf), ảnh minh họa trong `assets/`, và toàn bộ bằng chứng kiểm chứng trong `evidence/` (`evidence_bundle.json`, `linux_wsl_receipt.json`, `test_summary.json`, `junit/`).
