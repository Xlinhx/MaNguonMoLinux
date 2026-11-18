# 04 · Hướng dẫn chạy demo & Hỏi đáp bảo vệ (3 thành viên)

Tài liệu này giúp nhóm **tự chạy lại hệ thống từ đầu**, **chia vai thuyết trình mượt**, và **trả lời chắc chắn các câu hỏi phản biện thường gặp**.

---

## 1. Cách khởi động hệ thống để demo

### Điều kiện tiên quyết
- **Docker Desktop** đang mở (`Linux containers`, context `desktop-linux`).
- **Python 3.12+** (`python -m pip install -r backend/requirements.txt`) và **Node.js 18+/24+** (`cd web && npm install`).
- Image worker đã build sẵn:
  ```powershell
  docker build -t sandbox-lab-worker:v1 .\worker
  ```

### Khởi động trên Windows PowerShell (mở 2 cửa sổ terminal)
1. **Terminal 1 (Hạ tầng Docker + FastAPI backend):**
   ```powershell
   docker compose -f .\infra\compose.yaml up -d
   # (Tùy chọn nếu muốn AI Agent trả lời thật bằng DeepSeek; bỏ qua 3 dòng export nếu demo offline)
   $env:DEEPSEEK_API_KEY="sk-<your-key>"
   $env:SANDBOX_LAB_LLM_PROVIDER="deepseek"
   $env:SANDBOX_LAB_MODEL="deepseek-chat"
   python -m uvicorn backend.app.api:app --host 127.0.0.1 --port 18000
   ```
2. **Terminal 2 (Web UI):**
   ```powershell
   cd .\web
   npm run dev
   ```
3. Mở trình duyệt vào `http://127.0.0.1:5173`.
   *(Muốn chạy trọn bộ bằng Bash trong Ubuntu WSL2: xem lệnh `bash scripts/preflight.sh` -> `bash scripts/start.sh` -> `bash scripts/stop.sh` trong [README.md](../README.md)).*

### Xử lý tình huống không có mạng / không có API key khi bảo vệ
- **Traffic**, **Checks**, **Access log** chạy **100% cục bộ** qua Docker Desktop — **không cần Internet hay API key**.
- **Agent** trên web khi thiếu key sẽ báo lịch sự `Model unavailable`, không sập app. Lúc đó chuyển sang chạy test MCP stdio thật với Docker worker:
  ```powershell
  python -m pytest tests/integration/test_mcp.py -v
  ```
  Và mở file bằng chứng chạy trên Ubuntu WSL2: [deliverables/evidence/linux_wsl_receipt.json](../deliverables/evidence/linux_wsl_receipt.json).

---

## 2. Kịch bản chia vai thuyết trình (5–7 phút cho 3 người)

### Thành viên A · Hạ tầng Linux, Nginx & Kiểm chứng cô lập (`Traffic` + `Checks`)
1. Mở tab **Traffic** -> bấm **Create run**. Giải thích backend vừa gửi 10 HTTP request thật tới **Nginx trong Docker** (`7` lần `/demo` = 200, `3` lần `/missing` = 404) và đóng băng thành **Snapshot Revision 1** có mã SHA-256.
2. Chọn ô request `04` (`404 Not Found`) -> bấm **Open in access log →** để chứng minh `run_id` và `request_id` khớp 1-1 với log JSON thật.
3. Mở tab **Checks** -> bấm **Run checks** -> bấm vào từng dòng để mở hộp **Control evidence**:
   - **Read log:** Worker UID `10001` đọc được snapshot và khớp SHA-256.
   - **Write log:** Bị chặn khi gắn `readonly`, nhưng **bản đối chứng `rw_copy` ghi thành công** -> chứng minh chính cờ mount của Docker chặn, file snapshot gốc vẫn an toàn.
   - **Reach test service:** Bị chặn dưới `--network none`, trong khi **bản đối chứng trên `sandbox_lab_net` nhận `HTTP 200 /ping`**.

### Thành viên B · Giao thức MCP, Khóa Snapshot & AI Agent (`Agent`)
1. Giải thích ranh giới bảo mật: API key chỉ nằm ở FastAPI host; `McpLabClient` **xóa hết API key** trước khi mở `mcp_server.py` qua `stdio`; AI chỉ thấy câu hỏi + 3 schema tool đọc log (`count_status_codes`, `top_error_paths`, `recent_http_errors`).
2. *(Nếu có key live)* Mở tab **Agent** trên **Revision 1** -> hỏi `"How many 404s are in this run?"` -> Agent trả `3 of 10...` kèm nhãn `Tool counts: 3 404 / 10 total` -> bấm **View tool call →** chỉ ra `run_id`, `Revision 1`, SHA-256 khớp tuyệt đối.
3. Qua **Traffic** bấm **Add 404** (lên `11` request, **Revision 2**). Quay lại **Agent** chỉ vào banner hổ phách **`Answer uses revision 1 · Ask again`** -> bấm **Ask again** -> kết quả mới trả `4 of 11` trên Revision 2.
4. *(Nếu không có key)* Chạy lệnh `pytest tests/integration/test_mcp.py -v` cho giảng viên thấy thực thi thật 3 tool qua MCP stdio + Docker worker trên Revision 1 và 2.

### Thành viên C · Trải nghiệm UI, Phân loại lỗi & Kết quả kiểm thử
1. Thu nhỏ trình duyệt về màn hình điện thoại (`390px`) -> cho thấy giao diện tự chuyển `BottomSheet` vuốt/đóng bằng bàn phím, danh sách log mượt (đã kiểm thử tới `10.000` dòng phân trang cursor).
2. Tóm tắt bảng phân biệt **Pass / Fail / Error** (tại sao mất service đối chứng hoặc worker bị kill `exit 137` phải báo **Error**, không bao giờ báo **Pass**).
3. Nêu kết quả kiểm chứng: **62/62 test pass** (`0` skipped), có xác nhận chạy trên **Ubuntu 24.04.4 WSL2** và xuất báo cáo chuẩn VKU bằng `python scripts/build_release.py`.

---

## 3. Bộ 12 câu hỏi giảng viên hay vặn & Gợi ý trả lời chuẩn

1. **Hỏi: "MCP đã có rồi sao không để MCP tự bảo vệ luôn, phải dựng Docker sandbox làm gì?"**
   - *Trả lời:* MCP chỉ là giao thức chuẩn hóa việc AI khám phá và gọi tool (JSON-RPC), bản thân MCP **không** cô lập tiến trình, file hay mạng. Vì vậy nhóm đặt lớp **Policy Runner + Docker** nằm bên dưới MCP server để Linux kernel thực thi rào chắn thật.

2. **Hỏi: "Tại sao phải dùng Docker container mỗi lần gọi một cái rồi xóa? Như vậy có chậm không?"**
   - *Trả lời:* Với phân tích log định kỳ, việc dựng container Alpine nhẹ mất dưới vài trăm mili-giây nhưng đổi lại mỗi lần chạy đều sạch trạng thái, không tích tụ rác hay mã độc tồn dư giữa các lượt gọi. Hết timeout `10s` hoặc xong là `docker rm -f` ngay.

3. **Hỏi: "Nếu web test (`test-service`) bị tắt hoặc sai đường dẫn thì worker cũng không kết nối được, lúc đó app có báo Pass giả không?"**
   - *Trả lời:* Không ạ. Đó chính là lý do nhóm làm **kiểm tra đối chứng (control)**: mỗi lần kiểm tra mạng hay ghi file, nhóm chạy song song một worker giống hệt (cùng image, cùng UID `10001`) được phép vào mạng `sandbox_lab_net` hoặc ghi vào bản sao riêng `rw_copy`. Nếu đối chứng không trả về đúng `HTTP 200 /ping` JSON (ví dụ trả `204` hoặc sập), kết quả lập tức là **Error**, không bao giờ thành **Pass**.

4. **Hỏi: "Làm sao chắc chắn worker không bị root từ bên trong để sửa file snapshot?"**
   - *Trả lời:* Có 4 lớp khóa chồng nhau: `--user 10001:10001`, `--cap-drop ALL`, `--security-opt no-new-privileges:true`, cộng với `--read-only` rootfs và `--mount ...,readonly` cho riêng file snapshot. Ngay cả bản kiểm tra `Write log` cũng chỉ chạy trên bản sao tạm `0666` tách biệt, file snapshot gốc của người dùng không bao giờ gắn quyền ghi.

5. **Hỏi: "Cụ thể trong code đã kiểm chứng giới hạn tài nguyên (RAM, CPU, PID) thật hay mới chỉ ghi cấu hình?"**
   - *Trả lời:* Nhóm phân tách minh bạch trong Báo cáo (Bảng 4.1): cờ `--cpus 0.5` và `--pids-limit 64` được kiểm qua `docker inspect` cấu hình container; còn giới hạn bộ nhớ `--memory 128m` được chứng minh **tại runtime thật** bằng cách cho worker cấp phát `200 MiB` và quan sát kernel kill với `OOMKilled=True, exit 137`. Timeout `15s` cũng được thử thật và bị ngắt ở `1.5s`, sau đó `docker ps -a` xác nhận rỗng.

6. **Hỏi: "Docker container có cách ly hoàn toàn như máy ảo VM nếu bị lỗi nhân (kernel exploit) không?"**
   - *Trả lời:* Không ạ — nhóm đã nêu rõ giới hạn này ở mục *Environmental Scope Notes* trong báo cáo: container vẫn dùng chung nhân Linux trong VM `desktop-linux` của Docker Desktop, nên đây là cô lập mức OS (namespace, cgroup, capability, seccomp), không thay thế được phần cứng ảo hóa microVM trước các lỗ hổng zero-day của kernel.

7. **Hỏi: "Nếu AI bịa câu trả lời mà không thèm gọi MCP tool, hoặc tool trả về `3/10` mà AI viết ngược thành `10 out of 3` thì sao?"**
   - *Trả lời:* Trong `AgentService` ([backend/app/agent.py](../backend/app/agent.py)), mọi câu trả lời bắt buộc phải đi kèm ít nhất 1 `ToolResult` hoàn tất khớp đúng `(run_id, snapshot_id, revision, snapshot_sha256)`, không có sẽ trả lỗi `MCP_TOOL_REQUIRED`. Ngoài ra câu hỏi đếm tổng số phải gọi `count_status_codes` và đi qua hàm `has_consistent_404_counts`: nếu AI nói ngược/mâu thuẫn số, backend tự động tổng hợp lại câu chuẩn trực tiếp từ kết quả tool và UI luôn hiển thị badge số liệu xác thực `Tool counts: 3 404 / 10 total`.

8. **Hỏi: "Kẻ xấu chèn lệnh shell như `; rm -rf /` vào URL gửi lên Nginx thì khi AI đọc log có bị dính shell injection không?"**
   - *Trả lời:* Không, vì từ Nginx (ghi JSON log), Policy Runner (`subprocess` truyền mảng `argv` với `shell=False`), đến `worker/main.py` (parse từng dòng bằng `json.loads()`), **không có chỗ nào dùng shell** để nội suy chuỗi URL. Dự án có test hồi quy riêng cho các ký tự shell metacharacters.

9. **Hỏi: "Đang tạo 10 request mà Nginx sập ở request thứ 4 thì sao? Bấm lại có bị thành 11 dòng hay kẹt luôn không?"**
   - *Trả lời:* Bảng `run_planned_requests` lưu trạng thái từng request (`not_sent`, `dispatched_uncertain`, `sent`). Request `#04` nghi ngờ đã đi qua mạng sẽ được đánh dấu không gửi lại bừa; bấm **Refresh** chỉ bù `#05..#10` còn chưa gửi. Còn các lượt **lịch sử cũ** (`is_current = False`) bị khóa ở chế độ chỉ đọc (`0` HTTP request), không bao giờ phát thêm traffic ngoài ý muốn.

10. **Hỏi: "Tại sao hệ thống tách `API key` ra khỏi MCP server? Đưa chung biến môi trường thì sao?"**
    - *Trả lời:* Theo nguyên tắc đặc quyền tối thiểu (*least privilege*): tiến trình MCP server hay các công cụ đọc log không cần biết API key để gọi AI. `McpLabClient` lọc bỏ hết 5 biến API key phổ biến trước khi spawn tiến trình `stdio` để dù phần xử lý log có sự cố cũng không thể đọc trộm key.

11. **Hỏi: "Báo cáo đề cập WSL2 và `docker.exe`, nhóm chạy thật trên Linux hay chỉ trên Windows?"**
    - *Trả lời:* Khi Docker chạy trong `desktop-linux` engine (là Linux container thật), nhóm chạy toàn bộ quy trình kiểm chứng **cả trên Windows lẫn bên trong Ubuntu 24.04.4 WSL2**: biên dịch lại frontend với `@rollup/rollup-linux-x64-gnu`, chạy Nginx -> MCP stdio -> Docker worker qua `wslpath -w` cho cả Revision 1 (`3/10`) & Revision 2 (`4/11`), lưu biên nhận tại `deliverables/evidence/linux_wsl_receipt.json`.

12. **Hỏi: "Nếu không sửa code nữa, làm sao thầy kiểm tra bộ test & báo cáo khớp đúng với source hiện tại?"**
    - *Trả lời:* Nhóm có script cổng phát hành `python scripts/build_release.py` và `evidence_validator.py`: nó tự tính mã băm SHA-256 trên 70 file nguồn, đối chiếu khớp 100% với 3 file JUnit XML (`62/62` passed, `0` skipped), biên nhận Linux WSL2 và hash của từng ảnh chụp trước khi cho phép xuất `report.docx` / `report.pdf`. Nếu thiếu test live, suite rỗng, hay tráo ảnh, cổng phát hành sẽ từ chối ngay.
