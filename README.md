# TCP File Transfer – ET4230

**Đề tài 13:** Chia sẻ tệp Client–Server – Tin cậy và tính toàn vẹn

## 1. Giới thiệu

Xây dựng chương trình truyền file qua mạng bằng Python và TCP Socket, hỗ trợ upload/download, nhiều Client đồng thời và kiểm tra tính toàn vẹn dữ liệu bằng SHA-256.

Chương trình sử dụng giao thức tầng ứng dụng do nhóm thiết kế, trao đổi thông điệp điều khiển JSON và truyền nội dung file dưới dạng bytes theo chunk.

## 2. Phân công nhiệm vụ

| Thành viên | Nhiệm vụ chính | Nhiệm vụ bổ sung |
|---|---|---|
| 1 – Server | Xây dựng TCP Server, xử lý UPLOAD/DOWNLOAD, quản lý file | Viết nội dung phần Server |
| 2 – Client | Xây dựng TCP Client, upload/download, giao diện CLI | Tổng hợp slide PowerPoint |
| 3 – Protocol & Integrity | Thiết kế JSON framing, hàm dùng chung, kiểm tra SHA-256 | Tổng hợp và biên tập báo cáo PDF |
| 4 – Concurrency & Reliability | Quản lý nhiều Client đồng thời, threading, timeout, xử lý lỗi | Kiểm thử độ tin cậy |
| 5 – Benchmark & Performance | Đo throughput, thời gian truyền, chạy thí nghiệm, vẽ biểu đồ | Phân tích kết quả thực nghiệm |

Mỗi thành viên tự kiểm thử và viết nội dung liên quan đến phần mình phụ trách. Thành viên 2 và 3 chịu trách nhiệm tổng hợp sản phẩm chung.

## 3. Công nghệ sử dụng

- Ngôn ngữ: Python 3
- Thư viện: socket, threading, hashlib, matplotlib và các thư viện cần thiết
- Công cụ: VS Code, Git, GitHub, Wireshark (nếu cần)
- Giao thức vận chuyển: TCP
- Giao thức ứng dụng: JSON với framing 4 byte, truyền file theo chunk và kiểm tra SHA-256

Phần quản lý kết nối của thành viên 4 đã tích hợp vào Server. Chạy từ thư mục gốc của repository.

```powershell
python -m src.server --host 127.0.0.1 --port 5000 --timeout 30 --max-clients 32 --log-file results/server.log
```

Chạy bộ kiểm thử tự động phần đồng thời và độ tin cậy.

```powershell
python -m unittest discover -s tests -p test_concurrency_reliability.py -v
```

Hướng dẫn cấu hình, ranh giới tích hợp và kết quả kiểm thử ở [tài liệu thành viên 4](report/TV4_CONCURRENCY.md).

Các hàm giao thức dùng chung (JSON framing, `recv_exact`, truyền file theo chunk, SHA-256) nằm trong `src/protocol.py`. Chạy kiểm thử của thành viên 3:

```powershell
python -m unittest discover -s tests -p test_protocol.py -v
```

Giao diện hàm, thiết kế framing/checksum và ghi chú tích hợp ở [tài liệu thành viên 3](report/TV3_PROTOCOL.md).

Benchmark của thành viên 5 tự khởi động Server trên loopback, chạy TN1–TN3 và ghi CSV vào `results/`. Sau đó vẽ biểu đồ vào `plots/`.

```powershell
python -m benchmarks.bench --repeat 10
```

```powershell
python -m benchmarks.plot
```

Phương pháp đo, kết quả, nhận xét và giới hạn ở [tài liệu thành viên 5](report/TV5_BENCHMARK.md).

## 4. Quy định làm việc trên GitHub

Mỗi thành viên làm việc trên branch riêng:

- `feature/server` – Thành viên 1
- `feature/client` – Thành viên 2
- `feature/protocol` – Thành viên 3
- `feature/concurrency` – Thành viên 4
- `feature/benchmark` – Thành viên 5

Quy trình phát triển: Code → Commit → Push → Pull Request → Review → Merge vào `main`.

Các thay đổi mã nguồn được phát triển trên branch riêng. Thay đổi giao thức phải được thống nhất giữa các thành viên trước khi tích hợp.

## 5. Tài liệu nhóm

- [Phân công công việc chi tiết](README_DeTai13.md)
- [Quy định giao thức truyền file](PROTOCOL.md)

## 6. Sản phẩm cuối cùng

- Chương trình TCP Client–Server upload/download hoạt động giữa các máy tính.
- Hỗ trợ nhiều Client, truyền file theo chunk và kiểm tra SHA-256.
- Mã nguồn Python, README và hướng dẫn chạy.
- Kết quả kiểm thử, dữ liệu thí nghiệm, biểu đồ đánh giá hiệu năng.
- Báo cáo PDF, slide, video demo hoặc minh chứng tương đương.
- Bảng phân công và minh chứng đóng góp của 5 thành viên.

**Học phần:** Mạng máy tính – ET4230.
