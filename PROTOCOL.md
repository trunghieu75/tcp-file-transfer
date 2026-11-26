# PROTOCOL – TCP File Transfer (ET4230)

**Đề tài 13:** Chia sẻ tệp Client–Server – Tin cậy và tính toàn vẹn  
**Phiên bản giao thức:** 1.0 (cập nhật phân công nhóm 5 thành viên; không thay đổi giao thức truyền dữ liệu)  
**Triển khai tham chiếu:** `src/protocol.py`, kiểm thử tại `tests/test_protocol.py` (TV3).  
**Ngôn ngữ triển khai:** Python 3; **giao thức vận chuyển:** TCP.

> Đây là **giao thức tầng ứng dụng do nhóm tự quy ước**, không phải FTP chuẩn. Cả 5 thành viên cần thống nhất và tuân theo tài liệu này để các module hoạt động tương thích. `README_DeTai13.md` là tài liệu phân công công việc chi tiết; `PROTOCOL.md` là quy ước kỹ thuật khi giao tiếp và tích hợp code.

## 1. Quy ước chung

| Thành phần | Quy định |
|---|---|
| Kết nối | Client kết nối đến địa chỉ IP của Server, cổng mặc định **5000**. |
| Mã hóa thông điệp điều khiển | **JSON UTF-8**. |
| Định khung JSON | **4 byte đầu** là độ dài phần JSON (số nguyên không dấu, **big-endian**), tiếp theo là đúng số byte JSON. |
| Giới hạn JSON | Tối đa **64 KiB** cho mỗi thông điệp điều khiển. |
| Dữ liệu file | Gửi dưới dạng **byte nhị phân thô**, không đưa nội dung file vào JSON. |
| Chunk mặc định | **4096 byte/lần đọc hoặc gửi**; các chunk là cách đọc/ghi, **không phải ranh giới thông điệp TCP**. |
| Xác định hết file | Đọc đủ **`size` byte** đã công bố, không đợi TCP đóng kết nối. |
| Kiểm tra file | **SHA-256** của toàn bộ nội dung file; chuỗi hex viết thường gồm 64 ký tự. |
| Một kết nối | Xử lý **một yêu cầu tại một thời điểm**, có thể tiếp tục gửi yêu cầu khác sau khi hoàn tất. |
| Nhiều Client | Server có thể phục vụ nhiều kết nối TCP đồng thời. |

**Đặc biệt:** TCP là luồng byte. Một lần `sendall()` **không tương ứng** với một lần `recv()`. Vì vậy, phải có hàm `recv_exact(sock, n)` để lặp nhận đến khi đủ `n` byte hoặc báo mất kết nối. Cả hai phía dùng chung quy tắc này.

## 2. Cấu trúc thông điệp

Mọi thông điệp điều khiển có dạng:

```text
[4 byte độ dài JSON, big-endian][N byte JSON UTF-8]
```

Ví dụ một JSON (phần JSON minh họa, chưa bao gồm 4 byte độ dài):

```json
{"action":"UPLOAD","filename":"example.txt","size":100,"sha256":"<64 ký tự hex SHA-256>"}
```

**Quy tắc trường:** `action`, `status` viết hoa; `filename` là tên file, không chứa đường dẫn; `size` là số nguyên không âm tính theo **byte**; `sha256` là chuỗi hex SHA-256. Trường nào không có trong mẫu của một thông điệp thì không bắt buộc.

## 3. Upload: Client → Server

1. Client tính `size` và SHA-256 của file, gửi **một JSON**:
   ```json
   {"action":"UPLOAD","filename":"example.txt","size":100,"sha256":"<64 ký tự hex SHA-256>"}
   ```
2. Server kiểm tra yêu cầu, tên file và dung lượng; trả lời:
   ```json
   {"status":"READY"}
   ```
   Nếu không chấp nhận, trả `{"status":"ERROR","message":"Lý do"}`; **không gửi file**.
3. Sau `READY`, Client gửi **chính xác `size` byte dữ liệu file**, lần lượt đọc theo chunk (mặc định 4096 byte). **Không** có JSON hay dấu phân cách chen giữa các chunk.
4. Server nhận đủ `size` byte, tính SHA-256 của file đã nhận và so sánh với giá trị ở bước 1.
5. Nếu trùng, Server lưu file và gửi:
   ```json
   {"status":"SUCCESS","message":"Upload completed"}
   ```
   Nếu khác, xóa file tạm và gửi `{"status":"ERROR","message":"Checksum mismatch"}`.

**Lưu ý:** Server nên lưu vào file tạm trong khi nhận và chỉ đổi sang tên chính thức sau khi checksum đúng. Nếu ngắt kết nối khi chưa nhận đủ dữ liệu, xóa file tạm, ghi log và kết thúc kết nối; **không chèn JSON lỗi vào giữa luồng file**.

## 4. Download: Server → Client

1. Client gửi:
   ```json
   {"action":"DOWNLOAD","filename":"example.txt"}
   ```
2. Server kiểm tra file; nếu tồn tại, tính kích thước và SHA-256 rồi gửi:
   ```json
   {"status":"READY","filename":"example.txt","size":100,"sha256":"<64 ký tự hex SHA-256>"}
   ```
   Nếu không tồn tại, gửi `{"status":"ERROR","message":"File not found"}` và **không gửi dữ liệu**.
3. Client chuẩn bị file tạm và trả lời:
   ```json
   {"status":"READY"}
   ```
4. Server gửi **chính xác `size` byte** dữ liệu nhị phân (đọc theo chunk 4096 byte).
5. Client nhận đủ byte, kiểm tra SHA-256 và chỉ đổi tên file tạm khi checksum đúng. Client phản hồi:
   ```json
   {"status":"SUCCESS","message":"Download completed"}
   ```
   Nếu checksum sai, xóa file tạm và phản hồi `{"status":"ERROR","message":"Checksum mismatch"}`.

Nếu Client không thể chuẩn bị nơi lưu, ở bước 3 gửi `{"status":"ERROR","message":"Cannot save file"}` để Server không bắt đầu gửi file.

## 5. Danh sách file (tùy chọn)

Client gửi:

```json
{"action":"LIST"}
```

Server trả về (chỉ các file được phép tải):

```json
{"status":"SUCCESS","files":["example.txt","data.bin"]}
```

Danh sách cần nằm trong giới hạn JSON 64 KiB; bản đầu tiên có thể bỏ chức năng `LIST` nếu nhóm muốn ưu tiên upload/download.

## 6. Xử lý lỗi và giới hạn phạm vi

- Khi nhận thông điệp JSON sai định dạng, quá dài, thiếu trường bắt buộc hoặc lệnh không hỗ trợ: trả `ERROR` nếu còn an toàn để trả, rồi đóng kết nối.
- Hai phía cần đặt **timeout hợp lý**; khi `recv()` trả về `b''` trước khi đủ dữ liệu, coi là mất kết nối.
- Chỉ chấp nhận tên file đơn giản, **không cho phép** `../`, `/`, `\\` hoặc đường dẫn tuyệt đối; chỉ truy cập trong thư mục lưu trữ đã cấu hình.
  *Làm rõ (không đổi giao thức):* tên khác rỗng, không phải `.`/`..`; không chứa `/`, `\`, `:` (trên Windows `C:x` là đường dẫn theo ổ đĩa, thoát khỏi thư mục lưu trữ), `\x00` hay ký tự điều khiển; không có khoảng trắng đầu/cuối hoặc dấu `.` ở cuối; không phải tên thiết bị Windows (`CON`, `NUL`, `COM1`…); tối đa 255 byte UTF-8. Hàm kiểm tra dùng chung: `protocol.is_valid_filename()`.
- Giới hạn kích thước file theo dung lượng máy; không ghi đè file đang tồn tại nếu chưa có chính sách rõ ràng (bản đầu tiên: báo `ERROR`).
- Bản 1.0 **không hỗ trợ resume**, mã hóa TLS hay xác thực người dùng. TCP bảo đảm truyền byte tin cậy trong một kết nối; SHA-256 ở đây dùng để **kiểm tra tính toàn vẹn**, không phải bằng chứng bảo mật hoặc xác thực nguồn gửi.
- Không bắt đầu yêu cầu mới trên cùng kết nối khi giao dịch hiện tại chưa kết thúc; nếu mất đồng bộ luồng, **đóng và kết nối lại**.

## 7. Quy định tích hợp code cho nhóm 5 thành viên

| Thành viên | Trách nhiệm liên quan đến giao thức và tích hợp |
|---|---|
| **1 – TCP Server** | Xử lý nghiệp vụ `UPLOAD`/`DOWNLOAD`, kiểm tra tên file và điều kiện lưu/đọc file, đọc/gửi đúng số byte, phản hồi `READY`/`SUCCESS`/`ERROR`, ghi log. Tích hợp bộ quản lý kết nối do thành viên 4 phát triển. |
| **2 – TCP Client** | Gửi request đúng trường `action`, upload/download theo đúng thứ tự bắt tay, gửi/nhận dữ liệu theo chunk, hiển thị tiến độ và thông báo lỗi. Phụ trách tổng hợp slide theo `README_DeTai13.md`. |
| **3 – Protocol & Integrity** | Duy trì `PROTOCOL.md`; xây dựng hàm JSON framing, nhận đủ byte, truyền theo chunk và SHA-256 dùng chung; kiểm thử các hàm đó. Phụ trách tổng hợp báo cáo theo `README_DeTai13.md`. |
| **4 – Concurrency & Reliability** | Thiết kế và kiểm thử cơ chế xử lý nhiều kết nối đồng thời, quản lý thread/socket, timeout, ngắt kết nối, và lỗi khi truyền dở; phối hợp thành viên 1 tích hợp vào server. |
| **5 – Benchmark & Performance** | Viết chương trình đo thời gian hoàn tất, throughput và tỷ lệ thành công; thử nhiều kích thước file, chunk size, số client; lưu dữ liệu và biểu đồ, không thay đổi định dạng giao thức khi benchmark. |

**Ranh giới trách nhiệm quan trọng:**

- **TV1 quản lý logic file và phản hồi từng yêu cầu**; **TV4 quản lý vòng đời kết nối, khả năng phục vụ đồng thời và độ tin cậy**. Hai thành viên thống nhất cách gọi hàm xử lý client trước khi ghép code; tránh hai người cùng viết hai phiên bản server.
- **TV3 cung cấp các hàm giao thức dùng chung**; TV1 và TV2 sử dụng lại, không tự tạo các định dạng JSON khác nhau.
- **TV5 chỉ thay đổi tham số thực nghiệm trong giới hạn cho phép**, không tự sửa thứ tự thông điệp `UPLOAD`/`DOWNLOAD` hoặc tên các trường JSON.

**Các hàm dùng chung đã triển khai trong `src/protocol.py`** (mô tả đầy đủ ở mục 10):

```python
send_json(sock, data)       # Gửi 4 byte độ dài + JSON UTF-8
recv_json(sock)             # Đọc 4 byte độ dài + đúng số byte JSON
recv_exact(sock, n)         # Đọc đúng n byte hoặc báo lỗi
sha256_file(path)           # Tính SHA-256 của file
send_file(sock, path, size, chunk_size)                   # Gửi đúng size byte theo chunk
recv_file(sock, output, size, chunk_size)                 # Nhận đúng size byte theo chunk
recv_verified_file(sock, dest, size, sha256, chunk_size)  # Nhận vào file tạm, kiểm tra, công bố
```

Kích thước chunk là tham số `chunk_size` (mặc định `DEFAULT_CHUNK_SIZE = 4096`) để TV5 benchmark. Độ dài JSON tối đa vẫn là **64 KiB**, chunk mặc định vẫn là **4096 byte**.

**Quy tắc đồng bộ và GitHub:**

1. Mọi request sử dụng trường **`action`**, không dùng `command`. Giá trị thao tác được hỗ trợ trong bản 1.0 là `UPLOAD`, `DOWNLOAD` (và `LIST` nếu nhóm triển khai).
2. Nếu đổi tên trường, định dạng thông điệp, thứ tự gửi/nhận, kết quả trả về hay chính sách đóng kết nối, **TV3 cập nhật `PROTOCOL.md`** và thông báo cho **TV1, TV2, TV4, TV5** trước khi merge Pull Request.
3. TV1 và TV2 kiểm thử lại upload/download sau mỗi thay đổi protocol. TV4 kiểm tra các tình huống lỗi và đồng thời; TV5 kiểm tra lại script benchmark nếu giao diện gọi thay đổi.
4. Mỗi thành viên làm việc trên branch riêng theo `README_DeTai13.md`; chỉ tích hợp code đã được nhóm kiểm tra.

## 8. Các trường hợp kiểm thử và phân công trách nhiệm

Các trường hợp tối thiểu cần được kiểm tra trước khi hoàn thiện đề tài:

| Nhóm kiểm thử | Nội dung | Phụ trách chính (phối hợp) |
|---|---|---|
| **Chức năng cơ bản** | Upload rồi download một file; SHA-256 của file gốc và file tải về phải trùng. | **TV1 + TV2** (TV3) |
| **Ranh giới kích thước** | File **0 byte**, file nhỏ hơn **4096 byte**, file lớn hơn **4096 byte**. | **TV3** (TV1, TV2) |
| **Thông điệp và dữ liệu lỗi** | JSON sai định dạng/quá dài/thiếu trường; file không tồn tại, tên file không hợp lệ, file đích đã tồn tại; checksum không khớp. | **TV3 + TV4** (TV1, TV2) |
| **Mất kết nối** | Client/Server ngắt giữa chừng, timeout, nhận thiếu byte; không để lại file hoàn chỉnh bị hỏng. | **TV4** (TV1, TV2) |
| **Nhiều Client** | Nhiều client upload/download đồng thời; thử xung đột tên file; không làm hỏng dữ liệu. | **TV4** (TV1, TV2) |
| **Hiệu năng** | Đo thời gian, throughput, tỷ lệ thành công với nhiều kích thước file, chunk size, số client. | **TV5** (TV4, TV1, TV2) |
| **Nhiều yêu cầu/kết nối** | Thử nhiều file lần lượt trên một kết nối **nếu nhóm triển khai giữ kết nối lâu dài**; nếu chưa hỗ trợ, mỗi giao dịch dùng kết nối mới và ghi rõ giới hạn. | **TV1 + TV2** (TV3, TV4) |

### Kịch bản thực nghiệm dự kiến (TV5 phụ trách)

| Kịch bản | Tham số thay đổi đề xuất | Chỉ tiêu đo |
|---|---|---|
| TN1 – Kích thước file | 1 MB / 10 MB / 100 MB | Thời gian hoàn tất, throughput, checksum |
| TN2 – Kích thước chunk | 1 KB / 4 KB / 64 KB | Thời gian hoàn tất, throughput |
| TN3 – Số Client đồng thời | 1 / 2 / 4 client | Throughput tổng, thời gian, tỷ lệ thành công |

Các giá trị này là **đề xuất của nhóm**, không phải giá trị bắt buộc từ giảng viên. Phải ghi rõ môi trường đo, cấu hình và đơn vị; thực nghiệm với ít nhất hai cấu hình chunk/concurrency theo yêu cầu đề 13. Với phép đo có dao động, nên lặp nhiều lần và báo cáo thống kê thực đo, không tạo số liệu giả.

## 9. Phối hợp báo cáo, slide và nghiệm thu

- **TV1:** Cung cấp mô tả Server, luồng xử lý `UPLOAD`/`DOWNLOAD`, ảnh/log chạy thành công và lỗi.
- **TV2:** Cung cấp mô tả Client, ảnh chạy và **tổng hợp slide PowerPoint** từ nội dung cả nhóm gửi.
- **TV3:** Cung cấp phần thiết kế protocol, framing, checksum và **tổng hợp/biên tập báo cáo PDF**; kiểm tra thuật ngữ thống nhất.
- **TV4:** Cung cấp mô tả concurrency, cách xử lý lỗi, ca kiểm thử và minh chứng nhiều client.
- **TV5:** Cung cấp kịch bản, CSV, biểu đồ, số liệu, nhận xét và giới hạn thực nghiệm.

Mỗi thành viên tự chuẩn bị nội dung chuyên môn của phần mình và cùng rà soát tài liệu cuối cùng. Việc phân công chi tiết, đăng ký vai trò và tiến độ được theo dõi trong **`README_DeTai13.md`**; không dùng tài liệu này thay thế README.

## 10. Giao diện `src/protocol.py` (TV3)

Import: `from src import protocol` (chạy từ thư mục gốc, kiểu `python -m src.server`) hoặc `import protocol` khi chạy trực tiếp file trong `src/`. Module chỉ dùng thư viện chuẩn của Python.

### 10.1 Hằng số

| Tên | Giá trị | Ý nghĩa |
|---|---|---|
| `PROTOCOL_VERSION` | `"1.0"` | Phiên bản tài liệu này |
| `DEFAULT_PORT` | `5000` | Cổng mặc định |
| `MAX_JSON_SIZE` | `65536` | Độ dài tối đa phần JSON (byte) |
| `DEFAULT_CHUNK_SIZE` | `4096` | Chunk mặc định khi đọc/gửi file |
| `HEADER` | `struct.Struct("!I")` | Header 4 byte, big-endian |
| `ACTIONS` | `UPLOAD`, `DOWNLOAD`, `LIST` | Các giá trị `action` hợp lệ |
| `STATUS_READY`, `STATUS_SUCCESS`, `STATUS_ERROR` | `"READY"`, `"SUCCESS"`, `"ERROR"` | Các giá trị `status` |

### 10.2 Ngoại lệ

| Ngoại lệ | Kế thừa | Khi nào |
|---|---|---|
| `ProtocolError` | `ValueError` | Độ dài JSON ngoài `1..65536`, không phải UTF-8, JSON sai, không phải object, chứa `NaN`/`Infinity` |
| `ChecksumMismatch` | `ProtocolError` | SHA-256 dữ liệu nhận khác giá trị đã công bố (thuộc tính `expected`, `actual`) |
| `ConnectionClosed` | `ConnectionError` | Bên kia đóng kết nối khi chưa nhận đủ byte |
| `EOFError` | (có sẵn) | Chỉ khi `allow_eof=True` và kết nối đóng **trước byte đầu tiên**, tức kết thúc bình thường giữa hai request |
| `TimeoutError` | `OSError` | Timeout của socket, được truyền nguyên cho người gọi |

`ProtocolError` là `ValueError` và `ConnectionClosed` là `OSError`, nên `ConnectionManager` của TV4 vẫn bắt và ghi log đúng như hiện tại mà không cần sửa.

### 10.3 Hàm

| Hàm | Mô tả |
|---|---|
| `recv_exact(sock, n, allow_eof=False)` | Lặp `recv()` đến khi đủ `n` byte; không đọc quá `n`. |
| `encode_json(msg)` / `decode_json(payload)` | Tạo khung đầy đủ / giải mã phần thân. JSON gọn (`separators=(",", ":")`), UTF-8 (`ensure_ascii=False`). |
| `send_json(sock, msg)` | Gửi header + JSON trong một `sendall`. Từ chối thông điệp > 64 KiB **trước khi gửi**. |
| `recv_json(sock, allow_eof=False)` | Đọc header, kiểm tra độ dài **trước khi đọc phần thân**, trả về `dict`. |
| `error_message(text)` | Tạo `{"status":"ERROR","message":text}`. |
| `is_valid_filename(name)` | Quy tắc tên file ở mục 6. |
| `is_valid_size(size, max_size=None)` | Số nguyên không âm, không phải `bool`, không vượt `max_size`. |
| `is_valid_sha256(value)` | Chuỗi hex 64 ký tự (chấp nhận chữ hoa khi nhận; luôn **gửi** chữ thường). |
| `validate_chunk_size(n)` | Trả về `n` nếu là số nguyên dương, ngược lại `ValueError`. |
| `sha256_bytes(data)`, `sha256_file(path, chunk_size=4096)` | Hex chữ thường; kết quả không phụ thuộc `chunk_size`. |
| `send_file(sock, path, size=None, chunk_size=4096, progress=None)` | Gửi đúng `size` byte đầu của file (mặc định cả file), mỗi lần tối đa `chunk_size`; file ngắn hơn `size` thì `OSError`. Trả về SHA-256 của phần đã gửi. |
| `recv_file(sock, output, size, chunk_size=4096, progress=None)` | Nhận đúng `size` byte, ghi vào file object; mỗi `recv` tối đa `chunk_size`. Trả về SHA-256. |
| `recv_verified_file(sock, dest, size, sha256, chunk_size=4096, progress=None)` | Nhận vào file tạm cùng thư mục `dest`, so SHA-256, rồi công bố bằng hard link (không ghi đè; Windows không có hard link thì dùng `os.rename`, cũng không ghi đè). Mọi lỗi đều xóa file tạm. Hàm **không tự gửi JSON**. |

`progress(done, total)` được gọi sau mỗi chunk (file 0 byte thì gọi một lần `(0, 0)`); TV2 dùng để vẽ thanh tiến độ.

### 10.4 Ví dụ sử dụng

Server xử lý UPLOAD (TV1), sau khi đã kiểm tra `filename`, `size`, `sha256`:

```python
send_json(conn, {"status": STATUS_READY})
try:
    recv_verified_file(conn, UPLOAD_DIR / filename, size, sha256, chunk_size)
except ChecksumMismatch:
    send_json(conn, error_message("Checksum mismatch"))
except FileExistsError:
    send_json(conn, error_message("File already exists"))
else:
    send_json(conn, {"status": STATUS_SUCCESS, "message": "Upload completed"})
# ConnectionClosed / TimeoutError: không gửi JSON, để ConnectionManager đóng kết nối.
```

Client DOWNLOAD (TV2):

```python
send_json(sock, {"action": "DOWNLOAD", "filename": name})
meta = recv_json(sock)
if meta.get("status") != STATUS_READY:
    raise RuntimeError(meta.get("message"))
send_json(sock, {"status": STATUS_READY})
try:
    recv_verified_file(sock, DOWNLOAD_DIR / name, meta["size"], meta["sha256"],
                       progress=show_progress)
except ChecksumMismatch:
    send_json(sock, error_message("Checksum mismatch"))
else:
    send_json(sock, {"status": STATUS_SUCCESS, "message": "Download completed"})
```

Lưu ý thứ tự: với DOWNLOAD, Client nên kiểm tra `DOWNLOAD_DIR / name` chưa tồn tại **trước** khi gửi `READY`; nếu không lưu được thì gửi `{"status":"ERROR","message":"Cannot save file"}` theo mục 4.

### 10.5 Kiểm thử

```powershell
python -m unittest discover -s tests -p test_protocol.py -v
```

Các trường hợp được kiểm thử: phân mảnh và gộp segment TCP; framing big-endian; giới hạn 64 KiB (biên đúng 65536 byte được chấp nhận, 65537 bị từ chối mà không đọc phần thân); JSON sai cú pháp, không phải object, không phải UTF-8, có `NaN`; EOF sạch và EOF giữa chừng; vector SHA-256 chuẩn; file 0 / 1 / 4095 / 4096 / 4097 / 12293 / 1 MiB + 7 byte với chunk 1 KiB / 4 KiB / 64 KiB; không đọc lố sang thông điệp JSON kế tiếp; checksum sai, hỏng 1 byte, hỏng 1 bit; mất kết nối; không ghi đè; tên file nguy hiểm; timeout; tương thích hai chiều với `send_json`/`recv_json` đang có trong `server.py`.

## 11. Lịch sử thay đổi

| Ngày | Phiên bản | Người sửa | Nội dung |
|---|---|---|---|
| — | 1.0 | Nhóm | Bản giao thức ban đầu (mục 1–6) |
| — | 1.0 | Nhóm | Cập nhật mục 7–9 cho cơ cấu 5 thành viên |
| 26/11/2026 | 1.0 | TV3 | Thêm `src/protocol.py`, `tests/test_protocol.py` và mục 10; làm rõ quy tắc tên file ở mục 6. **Không** đổi định dạng thông điệp, thứ tự gửi/nhận hay tên trường. |

---

*Tài liệu làm việc nội bộ của nhóm ET4230. Quy định kỹ thuật ở mục 1–6 giữ nguyên so với bản giao thức 1.0 ban đầu; mục 7–9 được cập nhật để phù hợp cơ cấu 5 thành viên. Những tính năng chưa triển khai phải được ghi rõ trong báo cáo và chỉ xác nhận hoàn thành khi có kiểm thử thực tế.*
