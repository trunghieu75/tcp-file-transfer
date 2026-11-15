# Phần 4 Quản lý đồng thời và độ tin cậy

Thành viên 4 cung cấp bộ quản lý kết nối, tích hợp với nghiệp vụ upload/download
đã có của thành viên 1, và kiểm thử các tình huống nhiều client, timeout,
ngắt kết nối và lỗi truyền dở. Các tên trường JSON, framing và thứ tự bắt tay
được thực hiện theo tài liệu giao thức nhóm đã cung cấp.

**Cách phân chia mã nguồn.** `src/concurrency.py` quản lý listener, worker,
socket và dừng server. `src/server.py` tiếp tục quản lý yêu cầu và tệp.
`tests/test_concurrency_reliability.py` kiểm tra cả hai phần bằng socket TCP
thật, cổng được hệ điều hành chọn và kho tệp riêng trong thư mục tạm.

Mỗi kết nối được một thread riêng xử lý. Cùng kết nối đó có thể thực hiện nhiều
yêu cầu tuần tự. Upload hoặc download đang chờ ở một client vẫn cho phép các
client khác được phục vụ, trong giới hạn số worker đã cấu hình.

**Chạy server từ thư mục gốc repository.** Python 3.10 trở lên và thư viện chuẩn
đủ để chạy phần này. Môi trường đã kiểm thử dùng Windows và Python 3.11.8.

```powershell
python -m src.server --host 127.0.0.1 --port 5000 --timeout 30 --max-clients 32 --log-file results/server.log
```

Cách chạy trực tiếp mà nhóm đã dùng cũng được hỗ trợ.

```powershell
python src/server.py --timeout 30 --max-clients 32
```

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| --host | 127.0.0.1 | Địa chỉ lắng nghe |
| --port | 5000 | Cổng TCP |
| --timeout | 30 | Thời gian chờ của thao tác socket tính bằng giây |
| --max-clients | 32 | Số kết nối đang xử lý tối đa |
| --upload-dir | results/uploads | Kho tệp của server |
| --log-file | Ghi ra terminal | Nếu truyền đường dẫn, ghi thêm log UTF-8 vào tệp đó |

Khi demo hai máy, dùng `--host 0.0.0.0` trên server, còn client kết nối đến
IP LAN thực của máy server và cùng cổng. Cấu hình mạng cần cho phép kết nối đó.

**Timeout và vòng đời kết nối.** Timeout áp dụng khi chờ request, nhận nội dung
upload, chờ READY của download, gửi nội dung download và chờ kết quả cuối.
Đây là thời gian chờ của từng thao tác socket. Một kết nối vẫn có thể tồn tại
lâu hơn nếu các thao tác tiếp tục có tiến triển.

Khi số client đạt giới hạn, kết nối đến thêm được đóng trước khi xử lý request.
Client đó cần kết nối lại sau khi có chỗ trống. Cơ chế này giới hạn số thread
và socket hoạt động đồng thời. Giá trị 32 là lựa chọn triển khai có thể cấu hình.

Mỗi worker chạy handler trong khối try/finally và đóng socket khi kết thúc.
Worker gặp lỗi được ghi log riêng. Khi dừng bằng Ctrl+C, bộ quản lý dừng nhận
client mới, đóng listener, gọi shutdown trên các socket đang xử lý để đánh thức
thao tác mạng, rồi chờ worker thoát và hoàn tất các khối finally.

Trong trường hợp upload dở, khối finally sẵn có của TV1 đóng và xóa tệp tạm.
Bộ quản lý chờ việc dọn dẹp đó trước khi hoàn tất shutdown. Worker chịu trách
nhiệm đóng socket của mình sau cùng.

**Hành vi khi gặp lỗi.** Request JSON sai định dạng, sai giới hạn, sai kiểu,
lệnh không được hỗ trợ hoặc metadata không hợp lệ được báo ERROR khi còn ở
ranh giới thông điệp phù hợp rồi đóng kết nối. Yêu cầu hợp lệ tải tệp không tồn
tại trả ERROR và vẫn cho phép yêu cầu tiếp theo. Download bị client hủy bằng
ERROR ở bước chờ READY cũng cho phép kết nối tiếp tục được sử dụng.

Khi upload bị ngắt hoặc timeout giữa nội dung, server đóng kết nối và dọn tệp
tạm. Khi lỗi xảy ra giữa nội dung download, server kết thúc kết nối. Bên nhận
cần phát hiện thiếu byte, dọn tệp nhận dở và thiết lập kết nối mới. Giao dịch
đã nhận đủ nhưng checksum sai trả ERROR và xóa tệp tạm.

Hai client cùng upload một tên có thể cùng nhận READY nếu tên đích chưa tồn
tại khi kiểm tra. Bước công bố tệp của TV1 dùng `os.link`, vì vậy chỉ một lượt
được tạo tên chính thức. Lượt còn lại nhận ERROR và dọn tệp tạm. Nội dung của
lượt thành công được giữ nguyên. Cơ chế này cần filesystem hỗ trợ hard link.

**Sử dụng bộ quản lý khi ghép mã.** TV1 cung cấp hàm `handle_client(conn, addr)`
và để các lỗi truyền dở kết thúc handler. TV4 tạo `ConnectionManager` với hàm
đó, sau đó gọi `serve_forever(listener)` với socket đã bind và listen.
`active_count` cho biết số worker đang được quản lý. `shutdown()` dùng để dừng
tiếp nhận và chờ giải phóng tài nguyên. Một đối tượng quản lý phục vụ một lần
khởi động server.

TV1 cần giữ cơ chế dọn tệp tạm trong finally khi sửa nghiệp vụ. TV2 cần xử lý
EOF, reset và timeout khi nhận thiếu nội dung. TV3 có thể thay các hàm framing
bằng module dùng chung khi tích hợp, đồng thời giữ nguyên giao diện và cách
nhận đủ byte. TV5 có thể dùng các tham số dòng lệnh để cấu hình môi trường đo.

**Log phục vụ demo.** Mỗi dòng có thời gian, mức log, tên thread và sự kiện.

| Sự kiện | Ý nghĩa |
|---|---|
| server_listening | Listener đã được khởi động |
| client_connected | Worker bắt đầu xử lý kết nối |
| request | Đã đọc được action của request |
| upload_completed | Đã xác minh và công bố tệp upload |
| download_sent | Đã gửi đủ nội dung download |
| download_confirmed | Client báo download thành công |
| client_timeout | Thao tác mạng hết thời gian chờ |
| client_error | Kết nối hoặc thông điệp gặp lỗi |
| client_handler_failed | Handler phát sinh lỗi ngoài dự kiến |
| client_rejected | Server từ chối nhận thêm kết nối |
| client_interrupted | Worker được ngắt khi server đang dừng |
| client_closed | Worker đã giải phóng socket và kết thúc |

**Chạy kiểm thử tự động.** Lệnh này chạy riêng các kiểm thử tự động TV4.
Các script kiểm thử cũ của nhóm cần server đang chạy và dữ liệu đầu vào,
nên có cách chạy riêng ở phần tiếp theo.

```powershell
python -m unittest discover -s tests -p test_concurrency_reliability.py -v
```

| Nhóm kiểm thử | Hành vi được kiểm tra |
|---|---|
| Đồng thời | 6 client đều đạt READY trước khi gửi nội dung, rồi upload/download và xác minh dữ liệu |
| Xung đột tên | Hai upload cùng tên, một SUCCESS và một ERROR, tệp đích đúng với lượt thắng |
| Kích thước biên | Tệp 0, 100 và 10000 byte, nhiều giao dịch trên cùng kết nối |
| Upload dở | Client ngắt giữa chừng, dọn tệp tạm và upload lại cùng tên thành công |
| Upload timeout | Nhận thiếu byte, kết nối đóng và tệp nhận dở được xóa |
| Client treo | Timeout khi chờ request, header JSON, READY và xác nhận download |
| Download dở | Client ngắt hoặc ngừng đọc, worker được giải phóng và kho tệp vẫn đúng |
| Checksum | Sai checksum bị từ chối, tệp tạm được dọn, kết nối tiếp tục dùng được |
| Thông điệp lỗi | JSON sai, sai kiểu, sai độ dài, action và metadata không hợp lệ |
| Luồng TCP | Header bị chia nhỏ và nhiều request được gửi liền nhau |
| Giới hạn worker | Kết nối vượt giới hạn bị đóng, chỗ trống được dùng lại sau khi client rời |
| Shutdown | Ngắt upload đang chờ với timeout 10 giây, dọn tài nguyên trong dưới 3 giây |
| Lỗi handler | Một handler phát sinh RuntimeError, listener tiếp tục phục vụ |

**Kết quả đã quan sát ngày 15 tháng 11 năm 2026.** 22 ca kiểm thử TV4 đều đạt
trên Windows, Python 3.11.8, dùng TCP loopback. Lượt chạy xác nhận sau cùng của
bộ kiểm thử có thời gian 6.662 giây. Đây là thời gian thực thi kiểm thử và bao
gồm các ca chủ động chờ timeout.

Bốn script cũ của nhóm cũng đã chạy đạt với một tiến trình server thật.
Khi chạy xác nhận, cổng 5000 trong nội dung script được thay bằng cổng tạm
trong bộ nhớ, và thư mục làm việc được cô lập để bảo vệ dữ liệu hiện có.

| Script hiện có | Kết quả |
|---|---|
| tests/test_json_upload.py | Đạt |
| tests/test_json_download.py | Đạt |
| tests/test_multiple_requests.py | Đạt |
| tests/test_upload_download_same_connection.py | Đạt |

Ngoài mã thoát, đã đối chiếu nội dung tệp upload/download với dữ liệu nguồn,
kiểm tra thư mục tệp tạm rỗng và xác nhận các sự kiện log đã được ghi.
Đã kiểm tra cách chạy module, chạy trực tiếp và từ chối tham số CLI không hợp lệ.

**Chạy lại các script cũ để demo.** Mở terminal thứ nhất chạy server mặc định
ở cổng 5000. Trong terminal thứ hai ở gốc repository, tạo tệp đầu vào mẫu rồi
chạy lần lượt. Tên `upload_temp_test.bin` cần chưa tồn tại trước lượt upload.

```powershell
python -c "from pathlib import Path; p = Path('results/original.bin'); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(bytes(range(256)) * 256)"
python tests/test_json_upload.py
python tests/test_json_download.py
python tests/test_multiple_requests.py
python tests/test_upload_download_same_connection.py
```

Phần xác nhận hiện tại được thực hiện trên một máy qua loopback. Nhóm cần
bổ sung demo giữa hai máy và phép đo hiệu năng trong môi trường đã mô tả
khi hoàn thiện báo cáo chung. Các ca kiểm thử TV4 tạo dữ liệu kiểm tra có
kiểm soát và tự dọn thư mục tạm sau khi kết thúc.
