# 00 · Hướng dẫn đọc nhanh cho người mới (Từ con số 0 đến thuyết trình được)

> Nếu bạn mới mở project này lần đầu và **chưa hiểu gì về kiến trúc, Docker, MCP hay code bên trong**, chỉ cần đọc lần lượt đúng **4 tài liệu đánh số** dưới đây (~15–20 phút) là đủ hiểu toàn bộ hệ thống và tự tin demo / trả lời phản biện trước giảng viên.

## Lộ trình đọc theo thứ tự

| STT | Tài liệu | Đọc để nắm cái gì? | Thời gian |
|---|---|---|---|
| **01** | [01-TONG-QUAN-MON-HOC-VA-DU-AN.md](01-TONG-QUAN-MON-HOC-VA-DU-AN.md) | Bối cảnh môn **Linux OSS 2026**, **Đề tài 17** là gì, tại sao cần dự án này (giải thích bằng ví dụ đời thường), bức tranh 4 mảnh ghép & đánh giá mức độ dự án. | ~4 phút |
| **02** | [02-KIEN-TRUC-VA-LUONG-CHAY-TU-A-DEN-Z.md](02-KIEN-TRUC-VA-LUONG-CHAY-TU-A-DEN-Z.md) | Đường đi thực tế của dữ liệu khi bấm từng nút trên web: **Traffic -> Snapshot -> Checks đối chứng -> AI Agent qua MCP stdio -> Docker Worker**. | ~6 phút |
| **03** | [03-CHI-TIET-CODE-VA-THU-MUC.md](03-CHI-TIET-CODE-VA-THU-MUC.md) | Mở thư mục nào ra xem? File nào làm chức năng gì trong `backend/`, `worker/`, `infra/`, `web/`, `scripts/`, `tests/`, `deliverables/`? | ~5 phút |
| **04** | [04-HUONG-DAN-CHAY-DEMO-VA-HOI-DAP-BAO-VE.md](04-HUONG-DAN-CHAY-DEMO-VA-HOI-DAP-BAO-VE.md) | Cách khởi động demo (cả khi **có API key** và **không có API key**), kịch bản thuyết trình chia 3 người và **12 câu hỏi thầy hay hỏi + cách trả lời chuẩn**. | ~6 phút |

## Tài liệu tham chiếu tiếng Anh có sẵn

- [architecture.md](architecture.md): Bảng tóm tắt kiến trúc, 11 chính sách bảo mật `sandbox-policy-v1` và giới hạn kỹ thuật bằng tiếng Anh.
- [demo.md](demo.md): Script vận hành buổi demo bảo vệ bằng tiếng Anh khớp với báo cáo VKU.
- [deliverables/report.pdf](../deliverables/report.pdf): Báo cáo chính thức định dạng VKU (tiếng Anh).
- [README.md](../README.md): Hướng dẫn cài đặt, chạy dịch vụ và chạy kiểm thử (`pytest`, `verify.sh`, `build_release.py`).

## Tóm tắt một dòng trước khi đọc tiếp

> **Sandbox Lab** cho phép một **AI Agent** trả lời câu hỏi về log truy cập web của **Nginx** thông qua giao thức **MCP**, trong khi mọi thao tác đọc/phân tích log đều bị nhốt vào một **container Linux dùng một lần** (không phải root, ổ đĩa chỉ đọc, cắt mạng) và được kiểm chứng bằng **phép thử đối chứng** để chứng minh Linux thực sự chặn chứ không phải lỗi hạ tầng.
