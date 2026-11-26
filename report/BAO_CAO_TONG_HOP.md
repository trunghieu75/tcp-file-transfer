# Khung báo cáo tổng hợp – Đề tài 13 (ET4230)

TV3 tổng hợp và biên tập bản PDF cuối cùng từ nội dung các thành viên. Mỗi
thành viên điền phần của mình (file riêng trong `report/`), TV3 ghép, thống nhất
thuật ngữ và kiểm tra số liệu khớp với kiểm thử thực tế.

| Chương | Nội dung | Người viết | Nguồn | Trạng thái |
|---|---|---|---|---|
| 1. Giới thiệu | Bài toán, mục tiêu, phạm vi bản 1.0 (không resume, không TLS, không xác thực) | TV3 | `README.md` | Chưa viết |
| 2. Cơ sở lý thuyết | TCP luồng byte, socket, three-way handshake, checksum TCP và hàm băm SHA-256 | TV3 | — | Chưa viết |
| 3. Thiết kế giao thức | Framing, UPLOAD/DOWNLOAD, xử lý lỗi, SHA-256, kiểm tra đầu vào | TV3 | `report/TV3_PROTOCOL.md`, `PROTOCOL.md` | Đã có bản nháp |
| 4. Server | Kiến trúc, luồng xử lý UPLOAD/DOWNLOAD, file tạm, log | TV1 | (chờ TV1) | Chờ |
| 5. Client | Giao diện CLI, tiến độ, xử lý lỗi | TV2 | (chờ TV2) | Chờ |
| 6. Đồng thời và độ tin cậy | ConnectionManager, timeout, ngắt kết nối, giới hạn worker | TV4 | `report/TV4_CONCURRENCY.md` | Đã có |
| 7. Kiểm thử | Bảng ca kiểm thử mục 8 `PROTOCOL.md` + kết quả của TV1–TV4 | TV3 tổng hợp | các file test | Một phần |
| 8. Thực nghiệm hiệu năng | TN1–TN3, môi trường đo, CSV, biểu đồ, nhận xét | TV5 | `results/`, `plots/` | Chờ |
| 9. Demo hai máy | Ảnh chụp/log upload-download qua LAN | TV1 + TV2 | — | Chờ |
| 10. Kết luận và hướng phát triển | Kết quả đạt được, giới hạn, resume/TLS/LIST | TV3 | — | Chưa viết |
| Phụ lục | Bảng phân công, minh chứng đóng góp (commit/PR) | Cả nhóm | GitHub | Chờ |

## Quy ước biên tập

- Thuật ngữ thống nhất: *thông điệp điều khiển* (JSON), *dữ liệu file* (byte
  thô), *chunk*, *framing*, *file tạm*, *checksum SHA-256*; tên trường và giá trị
  giữ nguyên dạng code: `action`, `status`, `READY`, `SUCCESS`, `ERROR`.
- Đơn vị: byte, KiB/MiB (cơ số 1024) cho kích thước; MiB/s cho throughput; ghi
  rõ nếu dùng MB (cơ số 1000).
- Mọi số liệu phải có môi trường đo (OS, Python, loopback hay LAN, cấu hình
  máy), số lần lặp và ngày đo. Không đưa số liệu chưa đo thực tế.
- Tính năng chưa triển khai (ví dụ `LIST`) phải ghi rõ "chưa triển khai".
- Hình/bảng đánh số theo chương, có chú thích nguồn.
