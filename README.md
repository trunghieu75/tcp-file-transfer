# TCP File Transfer – ET4230

**Đề tài 13: Chia sẻ tệp Client–Server – Tin cậy và tính toàn vẹn**

## 1. Giới thiệu

Xây dựng chương trình truyền file qua mạng bằng Python và TCP Socket, hỗ trợ upload/download, nhiều Client đồng thời và kiểm tra tính toàn vẹn dữ liệu bằng SHA-256.

## 2. Phân công nhiệm vụ

| Thành viên | Nhiệm vụ |
|---|---|
| 1 – Server | Xây dựng TCP Server, quản lý kết nối và xử lý nhiều Client |
| 2 – Client | Lập trình upload/download, giao diện CLI, **làm slide** |
| 3 – Protocol & Integrity | Truyền file theo chunk, kiểm tra SHA-256, xử lý lỗi |
| 4 – Testing & Benchmark | Kiểm thử, đo hiệu năng, vẽ biểu đồ, **viết báo cáo** |

## 3. Công nghệ sử dụng

- Ngôn ngữ: Python
- Thư viện: socket, threading, hashlib, matplotlib
- Công cụ: VS Code, Git, GitHub, Wireshark (nếu cần)

## 4. Quy định làm việc trên GitHub

Mỗi thành viên làm việc trên branch riêng:

- `feature/server` – Thành viên 1
- `feature/client` – Thành viên 2
- `feature/protocol` – Thành viên 3
- `feature/benchmark` – Thành viên 4

**Quy trình:** Code → Commit → Push → Pull Request → Review → Merge vào `main`.

Không tự ý push trực tiếp lên `main` khi bắt đầu phát triển.

## 5. Sản phẩm cuối cùng

- Chương trình truyền file hoạt động giữa các máy tính.
- Mã nguồn Python và hướng dẫn chạy.
- Kết quả kiểm thử, biểu đồ đánh giá hiệu năng.
- Báo cáo PDF, slide và demo chương trình.

**Học phần:** Mạng máy tính – ET4230.
