# TV5 – Benchmark & Performance

**Phụ trách:** Thành viên 5. **Nhánh:** `feature/benchmark`.
**Mã nguồn:** `benchmarks/bench.py` (đo), `benchmarks/plot.py` (biểu đồ, bảng), `tests/test_benchmark.py` (kiểm thử).
**Dữ liệu:** `results/bench_raw*.csv`, `results/bench_summary*.csv`, `results/bench_summary*.md`, `results/bench_env*.json`. **Biểu đồ:** `plots/*.png`.

Nội dung này dùng cho **chương 8 – Thực nghiệm hiệu năng** của báo cáo tổng hợp.

## 1. Mục tiêu và phạm vi

Đo **thời gian hoàn tất**, **throughput** và **tỷ lệ thành công / checksum đúng** của chương trình truyền file. Thay đổi ba tham số theo `PROTOCOL.md` mục 8:

| Kịch bản | Tham số thay đổi | Giữ cố định |
|---|---|---|
| TN1 – Kích thước file | 1 / 10 / 100 MiB | chunk Client 4 KiB, 1 Client |
| TN2 – Chunk size phía Client | 1 / 4 / 64 KiB | file 10 MiB, 1 Client |
| TN3 – Số Client đồng thời | 1 / 2 / 4 | file 10 MiB, chunk 4 KiB |

Benchmark **không thay đổi giao thức**. Nó gửi đúng thứ tự `UPLOAD` → `READY` → dữ liệu → `SUCCESS` và `DOWNLOAD` → `READY`(metadata) → `READY` → dữ liệu → `SUCCESS`, với đúng tên trường JSON. Mọi thao tác đều gọi hàm chung trong `src/protocol.py` (TV3): `send_json`, `recv_json`, `send_file`, `recv_verified_file`. Server được đo là server thật của TV1/TV4 (`python -m src.server`).

## 2. Phương pháp đo

- **Một lượt đo (trial):** mỗi Client mở một kết nối TCP, upload một file dữ liệu ngẫu nhiên (`os.urandom`) dưới một tên duy nhất, sau đó download lại chính file đó trên **cùng kết nối**. Tên phải duy nhất vì Server không cho ghi đè.
- **Thời gian một thao tác:** đo bằng `time.perf_counter()`, tính từ lúc gửi JSON yêu cầu đến lúc nhận (upload) hoặc gửi (download) xong JSON `SUCCESS`. Không tính thời gian bắt tay TCP.
  - *Upload:* SHA-256 của file nguồn được tính trước, **ngoài** thời gian đo. Thời gian đo có gồm việc Server ghi file tạm, băm SHA-256 và công bố file.
  - *Download:* thời gian đo có gồm việc Server tính SHA-256 trước khi trả `READY`, và việc Client băm, ghi file tạm, kiểm tra checksum (`recv_verified_file`).
- **Throughput:** `kích thước file / thời gian`, đơn vị **MiB/s** (1 MiB = 1 048 576 byte).
- **TN3 – throughput tổng:** các Client chạy bằng thread, xuất phát cùng lúc qua `threading.Barrier` sau khi đã kết nối xong. Dòng `aggregate` = tổng byte mọi Client đã truyền (upload + download) / thời gian từ lúc xuất phát đến khi Client cuối cùng xong.
- **Thành công:** Server trả `SUCCESS` và SHA-256 của file tải về trùng SHA-256 file gốc. Thời gian và throughput chỉ thống kê trên các lượt thành công. Tỷ lệ thành công được báo riêng.
- **Lặp:** mặc định 1 lượt khởi động (bỏ qua) + 5 lượt đo cho mỗi cấu hình (kết quả mục 4 chạy với `--repeat 10`). `bench_summary.csv` ghi trung bình, độ lệch chuẩn mẫu, min, median, max.
- **Môi trường:** ghi tự động vào `results/bench_env.json` (OS, Python, CPU, loopback/LAN, commit Git, ngày đo).

## 3. Cách chạy

Chạy từ thư mục gốc repository. Cần `matplotlib` cho bước vẽ.

Đo trên loopback (script tự khởi động server ở một cổng trống, upload dir tạm):

```powershell
python -m benchmarks.bench --repeat 10
```

```powershell
python -m benchmarks.plot
```

Chỉ chạy một phần, hoặc đổi tham số:

```powershell
python -m benchmarks.bench --experiments tn2 --chunks 1024 4096 16384 65536 --repeat 10
```

Đo qua LAN, với server chạy trên máy khác (`python -m src.server --host 0.0.0.0 --port 5000`):

```powershell
python -m benchmarks.bench --host 192.168.1.10 --port 5000 --tag lan
```

```powershell
python -m benchmarks.plot --tag lan
```

Kiểm thử chương trình benchmark:

```powershell
python -m unittest discover -s tests -p test_benchmark.py -v
```

## 4. Kết quả đo (loopback)

> Xem `results/bench_summary.md` (bảng đầy đủ, sinh tự động) và các hình trong `plots/`.

**Môi trường lượt đo này** (lấy từ `results/bench_env.json`):

- Windows 11 (10.0.26200), Python 3.13.12, CPU Intel 8 luồng.
- Loopback `127.0.0.1`.
- Server chunk 4096 byte.
- 1 lượt khởi động + **10 lượt đo** mỗi cấu hình, ngày 27/11/2026, commit `3ec75b5`.
- **Tỷ lệ thành công 100%** ở mọi cấu hình. SHA-256 file tải về luôn trùng file gốc.

**Về cách đọc số liệu:** thời gian đo có **đuôi dài**. Phần lớn lượt có thời gian gần nhau, nhưng thỉnh thoảng một lượt chậm hơn 5–20 lần. Ví dụ upload 10 MiB thường mất khoảng 0,055 s, có lượt lên tới khoảng 0,4 s. Nguyên nhân có thể là hệ điều hành hoặc phần mềm diệt virus quét file mới ghi, hoặc tác vụ nền. Điều này **chưa được kiểm chứng**. Vì vậy biểu đồ dùng **median** (cột) và **min–max** (thanh), còn bảng ghi cả median lẫn trung bình ± độ lệch chuẩn.

### TN1 – Kích thước file (chunk 4 KiB, 1 Client)

| File | Upload median (MiB/s) | Download median (MiB/s) | Upload median (s) | Download median (s) |
|---|---|---|---|---|
| 1 MiB | 125,5 | 69,5 | 0,008 | 0,014 |
| 10 MiB | 181,4 | 149,2 | 0,055 | 0,067 |
| 100 MiB | 120,2 | 23,9 | 0,832 | 4,190 |

Nhận xét:

- **File nhỏ có throughput thấp hơn.** Mỗi giao dịch có chi phí cố định: 2–4 thông điệp JSON khứ hồi, tạo file tạm, hard link, và với download là băm lại file ở Server. Với 1 MiB, chi phí này chiếm phần đáng kể so với thời gian truyền dữ liệu.
- **Download 100 MiB chậm rõ rệt** (khoảng 24 MiB/s, dao động mạnh). Lý do: Server phải đọc và băm toàn bộ 100 MiB trước khi trả `READY`, sau đó đọc file lần nữa để gửi, còn Client vừa ghi file tạm vừa băm. Mỗi byte bị đọc/ghi đĩa ít nhất 3 lần. Với file lớn, dữ liệu dễ bị đẩy khỏi bộ nhớ đệm của hệ điều hành. Upload không có bước băm trước ở Server nên nhanh hơn nhiều.

### TN2 – Chunk size phía Client (file 10 MiB, 1 Client, Server cố định 4 KiB)

| Chunk Client | Upload median (MiB/s) | Download median (MiB/s) |
|---|---|---|
| 1 KiB | 57,9 | 123,4 |
| 4 KiB | 178,4 | 122,9 |
| 64 KiB | 319,5 | 78,9 |

Nhận xét:

- **Upload phụ thuộc mạnh vào chunk** (tăng khoảng 5,5 lần từ 1 KiB lên 64 KiB). Ở phía gửi, mỗi chunk tốn một lần `read()` file, một lần `sendall()` và một lần `update()` SHA-256 trong Python. Chunk lớn thì số lần gọi ít đi, nên chi phí cho mỗi lần gọi giảm. Server vẫn nhận 4096 byte/lần nhưng không phải nút cổ chai ở tốc độ này.
- **Download gần như không cải thiện theo chunk Client.** Bên gửi là Server, vẫn gửi 4096 byte/lần, nên chunk `recv` phía Client không quyết định tốc độ. Giá trị 64 KiB thấp hơn nằm trong vùng dao động lớn (min–max chồng nhau). Chưa đủ cơ sở để kết luận chunk lớn làm download chậm đi.
- Muốn đánh giá đầy đủ ảnh hưởng của chunk thì cần cho Server nhận tham số chunk (xem mục 6).

### TN3 – Số Client đồng thời (file 10 MiB, chunk 4 KiB)

| Client | Upload median mỗi Client (MiB/s) | Download median mỗi Client (MiB/s) | Tổng median (MiB/s) | Thời gian cả lượt median (s) |
|---|---|---|---|---|
| 1 | 183,2 | 144,8 | 158,6 | 0,126 |
| 2 | 109,7 | 110,8 | 86,6 | 0,462 |
| 4 | 19,9 | 16,1 | 56,6 | 1,416 |

Nhận xét:

- **Tính đúng đắn khi đồng thời:** 4 Client upload/download song song đều thành công, checksum đúng 100%. Phần đồng thời của TV4 không làm hỏng dữ liệu.
- **Hiệu năng không tăng theo số Client trên một máy.** Throughput mỗi Client giảm mạnh, và throughput tổng cũng giảm chứ không tăng. Có thể vì:
  - Server Python dùng thread nên các thread tranh nhau GIL cho vòng lặp `recv`/`write` 4096 byte;
  - Client và Server chạy chung CPU và đĩa;
  - các lượt chậm đột biến cộng dồn, mà thời gian cả lượt lại do Client chậm nhất quyết định.
- **TN3 kém ổn định giữa các lần chạy.** Lượt chạy thử trước đó (cùng máy) cho throughput tổng median với 4 Client khoảng 97 MiB/s, lượt này khoảng 57 MiB/s. Cần đo lại qua LAN hai máy, khi Client và Server không tranh tài nguyên, trước khi đưa ra kết luận định lượng về khả năng mở rộng.

## 5. Giới hạn của thực nghiệm

1. **Chunk size chỉ thay đổi phía Client.** Server hiện đọc/gửi cố định 4096 byte mỗi lần (`src/server.py`, hằng số `4096` viết thẳng trong `handle_upload`/`handle_download`). Vì vậy TN2 chỉ phản ánh chunk phía Client. Cột `server_chunk_size` trong CSV ghi rõ giá trị này.
2. **Loopback không phải mạng thật.** Đo trên `127.0.0.1` thì không có giới hạn băng thông hay độ trễ của mạng. Kết quả chủ yếu phản ánh chi phí CPU của Python (băm SHA-256, gọi `send`/`recv`, ghi đĩa). Cần đo bổ sung qua LAN (mục 6).
3. **Client và Server cùng máy.** Hai bên tranh CPU và đĩa của nhau. Các Client của TN3 là thread trong cùng một tiến trình Python (chịu GIL; `hashlib` và I/O socket nhả GIL nên ảnh hưởng nhỏ). Server là tiến trình riêng.
4. **File lớn nhất là 100 MiB,** đúng bằng giới hạn `MAX_FILE_SIZE` của Server.
5. **Không đo Client CLI của TV2** (`src/client.py`), vì client này dùng `input()` nên không tự động hóa được. Benchmark dùng client riêng nhưng chung hàm giao thức của TV3.
6. Số liệu chỉ có giá trị với môi trường ghi trong `bench_env.json`. Không ngoại suy sang máy khác.

## 6. Việc còn chờ thành viên khác

| Việc | Cần ai | Lý do |
|---|---|---|
| TN2 với chunk phía **Server** thay đổi | **TV1** thêm tham số `--chunk-size` cho `src/server.py` (hoặc dùng `protocol.send_file/recv_file` với `chunk_size`) | TV5 không được tự sửa server. Khi có tham số, chỉ cần truyền thêm cờ vào `LocalServer` trong `bench.py` và cập nhật `SERVER_CHUNK_SIZE`. |
| Đo qua **LAN hai máy** | **TV1 + TV2** (buổi demo hai máy) | Cần hai máy cùng mạng. Script đã hỗ trợ `--host`/`--port`/`--tag lan`. |
| Đo lại sau khi Server/Client chuyển sang dùng `src/protocol.py` | **TV1, TV2, TV3** | Giao diện hàm hoặc hành vi Server thay đổi thì phải chạy lại để số liệu khớp bản cuối cùng. |
| (Tùy chọn) Đo bằng Client CLI của TV2 | **TV2** | Cần client có chế độ không tương tác (tham số dòng lệnh). |
| Ghép chương 8 vào báo cáo PDF | **TV3** | TV5 cung cấp file này, bảng, CSV và hình. |
