# Phần 3 Giao thức ứng dụng và tính toàn vẹn dữ liệu

Thành viên 3 duy trì `PROTOCOL.md`, xây dựng module dùng chung `src/protocol.py`
cho framing JSON, nhận đủ byte, truyền file theo chunk và SHA-256, và kiểm thử
các hàm đó trong `tests/test_protocol.py`. Phần này cũng là nội dung chương
"Thiết kế giao thức" của báo cáo tổng hợp.

## 1. Vì sao cần giao thức tầng ứng dụng

TCP cung cấp một **luồng byte** tin cậy và có thứ tự, nhưng không giữ ranh
giới thông điệp. Một lần `sendall(100 byte)` ở bên gửi có thể đến bên nhận
dưới dạng nhiều lần `recv()` nhỏ hơn, hoặc gộp chung với byte của thông điệp
sau (cơ chế phân đoạn theo MSS, thuật toán Nagle, bộ đệm nhận). Vì vậy hai phía
cần thỏa thuận:

1. Thông điệp điều khiển bắt đầu và kết thúc ở đâu (**framing**).
2. Dữ liệu file dài bao nhiêu byte, để biết khi nào hết file mà không cần đóng kết nối.
3. Thứ tự bắt tay trước khi gửi dữ liệu, để bên nhận kịp từ chối.
4. Cách xác nhận file nhận được giống hệt file gốc (**toàn vẹn**).

## 2. Định khung thông điệp

```text
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|            Độ dài N của phần JSON (uint32, big-endian)        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                 N byte JSON UTF-8 (1 ≤ N ≤ 65536)             |
|                              ...                              |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

Ví dụ `{"status":"READY"}` dài 18 byte được gửi thành
`00 00 00 12` theo sau là 18 byte JSON.

Các lựa chọn thiết kế:

| Lựa chọn | Lý do |
|---|---|
| Tiền tố độ dài thay vì ký tự phân cách (`\n`) | Không phải quét tìm ký tự kết thúc, không cần escape; bên nhận biết trước phải đọc bao nhiêu byte. |
| 4 byte, big-endian (network byte order) | Quy ước chuẩn của giao thức mạng; `struct.Struct("!I")`. |
| Giới hạn 64 KiB, kiểm tra **trước** khi đọc phần thân | Một header độc hại `FF FF FF FF` không thể bắt bên nhận cấp phát 4 GiB. |
| JSON cho điều khiển | Dễ đọc khi debug/Wireshark, dễ mở rộng trường mà không vỡ tương thích. |
| Nội dung file là byte thô, **không** nằm trong JSON | Không tốn 33 % như base64, không giới hạn bởi 64 KiB, đọc/ghi theo chunk được. |

Hàm nền tảng là `recv_exact(sock, n)`: lặp `recv(n - đã_nhận)` cho tới khi đủ
`n` byte, và coi `recv()` trả về `b""` là bên kia đã đóng. Hàm không bao giờ
yêu cầu quá số byte còn thiếu, nên byte của thông điệp tiếp theo vẫn còn nguyên
trong bộ đệm socket. `recv_json` = `recv_exact(4)` → kiểm tra độ dài →
`recv_exact(N)` → giải mã UTF-8 → `json.loads` → bắt buộc là object.

Tham số `allow_eof=True` phân biệt hai trường hợp đóng kết nối: đóng **trước
byte đầu tiên** của một request là kết thúc phiên bình thường (`EOFError`),
còn đóng **giữa chừng** luôn là lỗi (`ConnectionClosed`).

## 3. Truyền file theo chunk

Sau `READY`, bên gửi phát đúng `size` byte; bên nhận đọc đúng `size` byte.
Chunk (mặc định 4096 byte) chỉ là kích thước bộ đệm đọc/ghi, **không** phải
ranh giới thông điệp: bên nhận có thể nhận 4096 byte bên gửi phát thành nhiều
mảnh nhỏ hơn. `send_file` / `recv_file` nhận `chunk_size` làm tham số để TV5
đo ảnh hưởng của nó (1 KiB / 4 KiB / 64 KiB) mà không đổi định dạng giao thức.

Vì độ dài đã công bố trước, cùng một kết nối có thể tiếp tục được dùng cho
request kế tiếp. Kiểm thử `test_roundtrip_sizes_and_chunk_sizes` gửi một JSON
ngay sau dữ liệu file và xác nhận bên nhận đọc được JSON đó nguyên vẹn.

## 4. Tính toàn vẹn bằng SHA-256

| Bước | Upload | Download |
|---|---|---|
| Công bố | Client gửi `sha256` trong request | Server gửi `sha256` trong `READY` |
| Nhận | Server ghi vào file tạm `.part`, băm tăng dần từng chunk | Client làm tương tự |
| So sánh | Server so hash thực với hash công bố | Client so sánh |
| Đúng | Công bố tên chính thức (hard link, không ghi đè) → `SUCCESS` | Công bố → Client gửi `SUCCESS` |
| Sai | Xóa file tạm → `ERROR "Checksum mismatch"` | Xóa file tạm → Client gửi `ERROR` |

`recv_verified_file` gói toàn bộ cột "Nhận → So sánh → Công bố" để TV1 và TV2
dùng chung. Mọi đường lỗi (checksum sai, mất kết nối, timeout, file đích đã có)
đều đi qua khối `finally` xóa file tạm, nên **không bao giờ có file hỏng mang
tên chính thức**. Băm tăng dần trong lúc nhận giúp không phải đọc lại file sau
khi ghi.

**Phạm vi của SHA-256 trong đề tài.** TCP đã có checksum 16 bit cho từng
segment, nhưng checksum này yếu và không bảo vệ lỗi ở tầng trên (ghi đĩa sai,
lỗi bộ nhớ, bug ghép chunk, file nguồn bị sửa trong lúc gửi). SHA-256 kiểm tra
đầu-cuối trên toàn bộ file: chỉ cần thay 1 bit, giá trị băm khác hoàn toàn
(kiểm thử `test_detects_single_bit_change`). Tuy nhiên hash được gửi trên
cùng kênh không mã hóa, nên kẻ tấn công có thể sửa cả file lẫn hash. SHA-256 ở
đây là **kiểm tra toàn vẹn**, không phải xác thực nguồn gửi; muốn chống giả mạo
cần TLS hoặc HMAC với khóa chung (ngoài phạm vi bản 1.0).

## 5. Kiểm tra đầu vào

Server không tin dữ liệu Client gửi. `is_valid_filename` chỉ chấp nhận tên file
đơn giản. Ngoài `../`, `/`, `\`, module còn chặn các trường hợp đặc thù Windows:

| Đầu vào | Rủi ro nếu chấp nhận |
|---|---|
| `C:evil.txt` | Trên Windows, `Path("results/uploads") / "C:evil.txt"` trả về `C:evil.txt`, tức ghi ra **thư mục hiện tại của ổ C**, thoát khỏi kho lưu trữ |
| `file.txt:stream` | Ghi vào Alternate Data Stream của NTFS, ẩn khỏi `dir` |
| `CON`, `NUL`, `COM1`, `nul.txt` | Mở thiết bị thay vì file |
| `name.` / `name ` | Windows tự cắt dấu chấm/khoảng trắng cuối → trùng tên file khác |
| `\x00`, `\n`, ký tự điều khiển | Cắt chuỗi ở tầng C, làm giả dòng log |

`is_valid_size` từ chối `bool` (trong Python `True` là `int`), số âm và số thực;
`is_valid_sha256` yêu cầu đúng 64 ký tự hex.

## 6. Kết quả kiểm thử

```powershell
python -m unittest discover -s tests -p test_protocol.py -v
```

**Kết quả quan sát ngày 26/11/2026:** 47 ca kiểm thử đều đạt trong 0,53 giây
trên Windows 11, Python 3.12.10, dùng socket thật (`socket.socketpair`) và
socket giả trả dữ liệu từng byte. Chạy lại bộ kiểm thử TV4
(`test_concurrency_reliability.py`) trên cùng nhánh: 22/22 đạt (một lần chạy
trước đó có 1 ca lỗi do thời gian, lỗi này cũng xuất hiện khi chạy trên `main`
chưa có thay đổi của TV3, nên không liên quan đến module mới).

| Nhóm | Số ca | Nội dung chính |
|---|---|---|
| `RecvExactTests` | 9 | Ghép mảnh, không đọc lố, `n = 0`, EOF sạch/giữa chừng, tham số sai |
| `JsonFramingTests` | 14 | Header big-endian, Unicode, 2 khung trong 1 segment, khung + byte file, biên đúng 64 KiB, quá dài, độ dài 0 / 65537 / 2³²−1 bị từ chối mà không đọc thân, JSON sai/không UTF-8/không phải object/NaN |
| `CompatibilityWithServerTests` | 2 | `protocol` ↔ `server.py` hiện tại trao đổi được hai chiều |
| `ValidationTests` | 5 | Tên file hợp lệ/nguy hiểm, SHA-256, size, chunk size |
| `Sha256Tests` | 3 | Vector chuẩn (`""`, `"abc"`), hash không phụ thuộc chunk size, đổi 1 bit |
| `ChunkTransferTests` | 8 | 7 kích thước × 3 chunk size qua socket thật, số byte mỗi lần gửi/nhận ≤ chunk, chỉ gửi `size` byte, file ngắn hơn công bố, mất kết nối, progress, timeout |
| `VerifiedReceiveTests` | 6 | Công bố đúng (0 / 4095 / 4096 / 4097 byte), checksum sai, hỏng 1 byte, mất kết nối, không ghi đè, hash sai định dạng; sau mỗi ca không còn file `.part` |

Kích thước 0, 4095, 4096 và 4097 byte là các biên quanh chunk mặc định theo
yêu cầu mục 8 của `PROTOCOL.md` ("ranh giới kích thước", TV3 phụ trách chính).

## 7. Ghi chú tích hợp gửi TV1, TV2, TV4, TV5

Module mới **chưa** thay thế code trong `server.py` / `client.py`, vì các file
đó thuộc TV1 và TV2. Khi đối chiếu code hiện có với `PROTOCOL.md`, TV3 ghi nhận:

| # | Vị trí | Khác biệt / vấn đề | Đề xuất |
|---|---|---|---|
| 1 | `server.py` `handle_upload`, `handle_download` | Kiểm tra tên file không chặn `:` → trên Windows `C:evil.txt` ghi ra ngoài `results/uploads` (đã xác nhận bằng `pathlib`) | **Ưu tiên cao:** thay khối kiểm tra bằng `protocol.is_valid_filename(filename)` |
| 2 | `server.py` upload thành công | Trả `{"status":"SUCCESS","size":…,"sha256":…}`, thiếu `"message":"Upload completed"` | Thêm `message` (giữ `size`, `sha256` là trường phụ, không vi phạm) |
| 3 | `server.py` | Thông điệp lỗi khác tài liệu: `"SHA-256 khong khop"` thay vì `"Checksum mismatch"`, `"File khong ton tai"` thay vì `"File not found"` | Thống nhất chuỗi theo mục 3–4, hoặc cập nhật tài liệu nếu nhóm muốn tiếng Việt |
| 4 | `server.py` upload metadata sai | Gửi `{"status":"ERROR"}` không có `message` | Dùng `error_message("Invalid filename or size")` |
| 5 | `server.py` | Chưa có `LIST` (tùy chọn ở mục 5) | Ghi rõ trong báo cáo nếu không làm |
| 6 | `tests/test_json_upload.py` | `recv_json` cục bộ giới hạn JSON 4096 byte thay vì 64 KiB | Import `protocol.recv_json` |
| 7 | `server.py`, `client.py`, các script test | Mỗi file tự viết lại `send_json`/`recv_json`/`recv_exact`/`sha256_file` | Import từ `src/protocol.py`; giao diện giữ nguyên tên, `recv_exact`/`recv_json` có cùng tham số `allow_eof` như `server.py` |
| 8 | `server.py`, `client.py` | Chunk 4096 viết cứng | Truyền `chunk_size` để TV5 benchmark |

Không mục nào ở trên đòi đổi định dạng giao thức. Khi TV1/TV2 tích hợp, chạy
lại `test_protocol.py`, `test_concurrency_reliability.py` và các script upload/download.
