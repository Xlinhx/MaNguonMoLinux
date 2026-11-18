# Căn cứ và thứ tự ưu tiên

Kiểm tra ngày 18/11/2026 qua Search Service và tải trực tiếp từ tên miền VKU. Các file bên dưới là bản gốc tham chiếu, chưa phải xác nhận mẫu riêng của lớp Linux OSS 2026.

## 1. Đề môn do người dùng cung cấp

**FINAL PROJECT INSTRUCTION AND REQUIREMENTS — LINUX OSS 2026** là căn cứ chính trong cuộc trao đổi này. Link gốc: https://docs.google.com/document/d/1xLSlbGTYf5e3uBosX2qbTxJ6oEnPjHBo/edit . Nội dung người dùng dán là bản được dùng để đọc yêu cầu; không khẳng định đã truy cập được file Google Docs.

Những yêu cầu liên quan trực tiếp:

- Nhóm 2–3 sinh viên; nhóm hiện tại có 3 người.
- Báo cáo và slides bằng tiếng Anh, hình thức tương tự Đồ án cơ sở 3, 4.
- Đề tài có độ khó và triển khai thực tế; demo có tùy biến, vượt việc làm lại hướng dẫn cài đặt.
- Hướng Agentic AI: nghiên cứu kiến trúc/tool use, MCP hoặc cơ chế tương đương; triển khai tool server trên Linux, nhiều công cụ thực dụng, tích hợp agent, tương tác thật với dịch vụ Linux.
- Đề tài 17 do thầy đề xuất: **Secure Agentic AI Tool Execution using Linux Sandbox**.

Shell script thay đổi cấu hình nằm trong hướng server services; sửa source và biên dịch lại nằm trong hướng open-source system. Không tự áp tất cả các nhánh này thành nghĩa vụ đồng thời của đề tài 17. Nhóm vẫn dự kiến có script vận hành và chính sách sandbox tự xây dựng.

Đề chưa cho rubric, số trang, số slide, thời lượng bảo vệ, ngôn ngữ nói hay quy cách nộp mã nguồn. Không gán điểm số hoặc tỷ lệ chấm từ học phần khác sang môn này.

## 2. Biểu mẫu công khai của VKU

Trang nguồn: [Biểu mẫu — Phòng Đào tạo VKU](https://daotao.vku.udn.vn/bieu-mau), mục biểu mẫu đồ án KHMT và hướng dẫn trình bày đồ án cơ sở/thực tập.

| File lưu tại đây | Nguồn gốc | SHA-256 |
|---|---|---|
| `vku/KHMT-do-an-original.zip` | https://daotao.vku.udn.vn/bieumau/KHMTBieu%20mau%20do%20an.zip | `9d12d2b9d5402855a274ce8425559b17aa4969470385ff0e27e4953ecb37c604` |
| `vku/KHMT-report-original.docx` | Trích nguyên byte file `KHMT-Bao cao do an ..... [ten sinh vien].docx` từ ZIP trên | Giữ nguyên nội dung file trong ZIP |
| `vku/Huong-dan-trinh-bay-original.doc` | https://daotao.vku.udn.vn/bieumau/Huong%20dan%20trinh%20bay%20khoa%20luan%20do%20an.doc | `450b3c61130b0a52a8f474fa8ee0088088e3a6e6bb027e310f754cbe21fe9192` |

Đã kiểm tra cấu trúc DOCX: A4 khoảng 21 × 29,7 cm; lề trên/dưới/phải 2 cm, trái 3 cm. Có bìa, trang nhận xét, lời cảm ơn, mục lục, danh mục viết tắt/hình, mở đầu, các chương, kết luận, tài liệu tham khảo, phụ lục. Mẫu mang mốc tháng 05/2021 và có lỗi số chương mẫu; không sao chép nguyên các placeholder đó.

**Điểm cần xác nhận:** Normal style trong DOCX là `VNtimes new roman`, 14 pt; văn bản hướng dẫn DOC ghi Times New Roman, 13 pt. Tạm dùng TNR 13 khi soạn nháp theo hướng dẫn chữ, giữ cấu trúc A4/lề của mẫu; chưa gọi đây là quy định đã chốt của lớp. Trước xuất bản cuối phải ưu tiên file/thông báo hiện hành mà giảng viên áp dụng. Việc kiểm tra hiện tại là đọc metadata và văn bản, chưa phải duyệt bố cục báo cáo đã render.

## 3. Nguồn kỹ thuật cho thiết kế dự kiến

- [Docker Desktop WSL2](https://docs.docker.com/desktop/features/wsl/): phân biệt Ubuntu người dùng với môi trường Engine của Docker Desktop.
- [Docker Engine security](https://docs.docker.com/engine/security/): cơ chế Linux và quyền kiểm soát daemon.
- [Docker bind mounts](https://docs.docker.com/engine/storage/bind-mounts/): mount chỉ đọc; với `--mount` dùng tùy chọn `readonly`, không viết cú pháp `--mount … :ro`.
- [Running containers](https://docs.docker.com/engine/containers/run/): giới hạn tài nguyên, network, user và ý nghĩa lỗi thực thi.
- [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture): host/client/server và tool integration; MCP không tự cung cấp sandbox cho lệnh.
- [Nginx access log module](https://nginx.org/en/docs/http/ngx_http_log_module.html): log truy cập của dịch vụ.

Đây là cơ sở chọn phương án, chưa chứng minh cấu hình hoặc kiểm thử đã chạy trên máy. Khi implement phải kiểm tra lại phiên bản thực tế và tài liệu của SDK đã khóa phiên bản.
