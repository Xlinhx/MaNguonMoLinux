# 01 · Tổng quan môn học và Dự án Sandbox Lab

## 1. Môn học này yêu cầu gì?

Dự án thuộc môn **Linux Open Source Software (Linux OSS 2026)** tại VKU (nhóm 3 sinh viên), thực hiện **Đề tài 17: *Secure Agentic AI Tool Execution using Linux Sandbox***.

Yêu cầu cốt lõi của đề tài:
- Làm bằng **tiếng Anh** ở phần báo cáo cuối theo cấu trúc VKU ([deliverables/report.pdf](../deliverables/report.pdf)).
- Phải là **sản phẩm triển khai thực tế trên Linux**, tương tác với dịch vụ Linux thật (Nginx), không phải chỉ cài lại một công cụ có sẵn theo tutorial.
- Thể hiện hướng **Agentic AI**: có AI Agent gọi **nhiều công cụ (tools)** thực dụng thông qua chuẩn **MCP (*Model Context Protocol*)** hoặc tương đương.
- Trọng tâm của Đề tài 17 là chứng minh cho giảng viên thấy **2 điều**:
  1. **Linux giới hạn được công cụ của AI như thế nào** (user thường, read-only, tắt mạng, giới hạn RAM/CPU/PID, dọn sạch sau khi chạy).
  2. **Nhóm biết phân biệt rạch ròi** giữa *"Linux sandbox chặn thành công"* với *"hạ tầng bị lỗi/sập"* (không báo **Pass** giả).

---

## 2. Hiểu dự án bằng ví dụ đời thường (không cần biết code)

Hãy tưởng tượng:
1. Bạn có một máy chủ web **Nginx** đang chạy. Mỗi lần có người truy cập đúng (`/demo` -> `200 OK`) hoặc truy cập nhầm (`/missing` -> `404 Not Found`), Nginx đều ghi một dòng nhật ký vào file `access.jsonl`.
2. Bạn muốn dùng **AI (DeepSeek / OpenAI / Gemini / Claude)** để hỏi nhanh: *"Đợt truy cập vừa rồi có bao nhiêu lỗi 404?"*.
3. **Vấn đề bảo mật:** Nếu bạn đưa thẳng cho AI quyền chạy lệnh Linux (`bash`) trên máy chủ thật để đọc file log thì cực kỳ nguy hiểm:
   - Đường dẫn URL trong log là dữ liệu từ bên ngoài, có thể cài cắm ký tự đặc biệt hoặc *prompt injection*.
   - Công cụ chạy tự do có thể vô tình hoặc cố ý **sửa file log**, **đọc trộm file bí mật / API key trên máy**, **tuồn dữ liệu ra mạng**, hoặc **ngốn sạch RAM làm treo máy**.
4. **Cách Sandbox Lab giải quyết:**
   - **Bước A (Đông cứng log):** Chụp file log của đúng lượt đó thành một bản sao bất biến (**Snapshot**), đóng dấu mã băm **SHA-256**.
   - **Bước B (Khóa chặt quyền AI qua MCP):** AI chỉ được phép đề xuất gọi **1 trong 3 công cụ đọc log cho phép** qua chuẩn **MCP**. AI không thấy đường dẫn máy chủ thật và không có quyền tự gõ lệnh shell.
   - **Bước C (Nhốt công cụ vào "phòng cách ly" Docker Linux):** Mỗi lần chạy công cụ, hệ thống dựng một container Linux trắng (`sandbox-lab-worker:v1`) chỉ sống đúng vài giây với 6 ổ khóa:
     - Chạy bằng user thường `10001:10001` (không phải `root`).
     - Hệ điều hành bên trong chỉ đọc (`--read-only`).
     - Chỉ nhìn thấy đúng 1 file snapshot được gắn ở chế độ chỉ đọc (`readonly`), tuyệt đối không thấy phần còn lại của máy chủ.
     - Cắt toàn bộ mạng (`--network none`).
     - Tước hết đặc quyền Linux (`--cap-drop ALL`, `no-new-privileges:true`).
     - Giới hạn `128 MiB RAM`, `0.5 CPU`, tối đa `64 tiến trình` và timeout `10 giây` rồi xóa sạch (`docker rm -f`).

---

## 3. Bốn mảnh ghép chính của hệ thống

```mermaid
flowchart LR
    UI["1. Web UI (React)\n4 man hinh van hanh"] -->|REST API| BE["2. FastAPI + SQLite\n(Trusted Host Boundary)"]
    BE -->|HTTP /demo, /missing| NGX["Nginx Linux\nGhi JSONL access log"]
    BE -->|Chup SHA-256| SNAP["Snapshot bat bien\n(rev-0001, rev-0002...)"]
    BE -->|LLM API key chi nam o day| LLM["External LLM\n(DeepSeek / OpenAI...)"]
    BE -->|MCP stdio (da xoa API key)| MCP["MCP Server\n(3 tool doc log)"]
    MCP -->|Policy Runner| DK["3. Docker Worker\nUID 10001 / RO / No-Net"]
    DK -->|Readonly mount| SNAP
    BE -->|Doi chung Pass/Fail/Error| CTRL["4. Control Harness\nBan doi chung co kiem soat"]
```

---

## 4. Đánh giá thẳng thắn về mức độ dự án

- **Độ khó & chiều sâu kỹ thuật:** **Khá cao so với đồ án cơ sở**. Nhóm không làm app mô phỏng giả lập mà kết nối **Nginx thật -> Snapshot SHA-256 -> MCP SDK chuẩn (`mcp==1.28.1` stdio) -> Docker container thật -> Web React/TypeScript**. Ngoài luồng chính, hệ thống còn xử lý các ca thực chiến dễ chết khi demo như: rớt mạng giữa chừng, trùng request khi bấm retry, AI không gọi tool hoặc nói ngược số liệu, và chạy được trên cả Windows lẫn Ubuntu WSL2.
- **Độ tin cậy hiện tại:** Toàn bộ **62 test tự động** (`48` unit + `4` integration với Docker/Nginx/DeepSeek thật + `10` E2E trên trình duyệt thật Chromium) đều pass với `0` test bị bỏ qua (`skipped = 0`), đi kèm báo cáo VKU ([deliverables/report.pdf](../deliverables/report.pdf)) và chứng thực chạy trên Ubuntu WSL2 ([deliverables/evidence/linux_wsl_receipt.json](../deliverables/evidence/linux_wsl_receipt.json)).
- **Phần nhóm cần hoàn thiện thủ công trước khi nộp:**
  1. Điền **tên thật 3 sinh viên, mã lớp, tên giảng viên** vào trang bìa [deliverables/report.docx](../deliverables/report.docx) (rồi xuất lại PDF).
  2. Nếu buổi báo cáo yêu cầu slide trình chiếu, nhóm dựng thêm từ nội dung của báo cáo và bộ ảnh trong [deliverables/assets/](../deliverables/assets).
  3. Cả 3 thành viên đọc kỹ [04-HUONG-DAN-CHAY-DEMO-VA-HOI-DAP-BAO-VE.md](04-HUONG-DAN-CHAY-DEMO-VA-HOI-DAP-BAO-VE.md) và chạy thử một vòng trước khi lên lớp.
