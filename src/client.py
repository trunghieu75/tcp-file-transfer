"""
TCP File Transfer Client
"""

import hashlib
import json
import socket
import struct
import time
from pathlib import Path


# 1. CẤU HÌNH CLIENT

SERVER_HOST = "127.0.0.1"   # IP máy chạy Server
SERVER_PORT = 5000          # Phải trùng PORT trong server.py

CHUNK_SIZE = 4096           # Server hiện tại nhận/gửi tối đa 4096 byte mỗi lần
MAX_FILE_SIZE = 100 * 1024 * 1024  # Giới hạn upload của Server: 100 MiB
DOWNLOAD_DIR = Path("downloads")
SOCKET_TIMEOUT = 60


# 2. CÁC HÀM GIAO TIẾP TCP

def send_json(sock, message):
    """
    Gửi một JSON message theo đúng định dạng Server đang dùng:
    [4 byte độ dài JSON] + [nội dung JSON UTF-8].
    """
    data = json.dumps(message).encode("utf-8")
    header = struct.pack("!I", len(data))
    sock.sendall(header + data)


def recv_exact(sock, number_of_bytes):
    """
    Nhận chính xác number_of_bytes byte.
    TCP không đảm bảo một lần recv() sẽ nhận đủ số byte yêu cầu.
    """
    data = bytearray()

    while len(data) < number_of_bytes:
        chunk = sock.recv(number_of_bytes - len(data))

        if not chunk:
            raise ConnectionError("Server đã đóng kết nối giữa chừng.")

        data.extend(chunk)

    return bytes(data)


def recv_json(sock):
    """
    Nhận một JSON message theo đúng định dạng Server đang dùng.
    Đầu tiên đọc 4 byte độ dài, sau đó đọc đủ phần JSON.
    """
    header = recv_exact(sock, 4)
    message_size = struct.unpack("!I", header)[0]

    if message_size < 1 or message_size > 64 * 1024:
        raise ValueError("Kích thước JSON Server gửi không hợp lệ.")

    data = recv_exact(sock, message_size)
    return json.loads(data.decode("utf-8"))


def sha256_file(file_path):
    """Tính mã SHA-256 của file theo từng khối nhỏ."""
    hasher = hashlib.sha256()

    with open(file_path, "rb") as file:
        while True:
            chunk = file.read(CHUNK_SIZE)

            if not chunk:
                break

            hasher.update(chunk)

    return hasher.hexdigest()


def is_valid_filename(filename):
    """Chỉ chấp nhận tên file, không chấp nhận đường dẫn thư mục."""
    return (
        bool(filename)
        and filename not in (".", "..")
        and "/" not in filename
        and "\\" not in filename
        and "\x00" not in filename
    )


def show_progress(label, transferred, total, start_time):
    """Hiển thị tiến độ và tốc độ truyền file."""
    if total == 0:
        percent = 100
    else:
        percent = transferred * 100 / total

    elapsed = max(time.monotonic() - start_time, 0.001)
    speed = transferred / elapsed / (1024 * 1024)

    bar_length = 25
    filled = bar_length if total == 0 else int(bar_length * transferred / total)
    bar = "#" * filled + "-" * (bar_length - filled)

    print(
        f"\r{label}: [{bar}] {percent:6.2f}% "
        f"| {transferred}/{total} bytes | {speed:.2f} MiB/s",
        end="",
        flush=True,
    )

    if transferred >= total:
        print()


# 3. KẾT NỐI ĐẾN SERVER

def connect_to_server():
    """Tạo một kết nối TCP đến Server."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(SOCKET_TIMEOUT)

    try:
        sock.connect((SERVER_HOST, SERVER_PORT))
    except OSError:
        sock.close()
        raise

    print(f"[OK] Đã kết nối Server {SERVER_HOST}:{SERVER_PORT}")
    return sock


# 4. CHỨC NĂNG UPLOAD

def upload_file(sock):
    """Gửi một file từ máy Client lên Server."""
    source_text = input("Nhập đường dẫn file cần upload: ").strip().strip('"')
    source_path = Path(source_text).expanduser()

    # Kiểm tra file ở máy Client
    if not source_path.is_file():
        print("[ERROR] Không tìm thấy file này.")
        return

    filename = source_path.name

    if not is_valid_filename(filename):
        print("[ERROR] Tên file không hợp lệ.")
        return

    file_size = source_path.stat().st_size

    # Server từ chối file lớn hơn 100 MiB, nên kiểm tra ngay tại Client
    if file_size > MAX_FILE_SIZE:
        print("[ERROR] File vượt quá giới hạn 100 MiB của Server.")
        return

    # Tính mã SHA-256 trước khi gửi
    file_hash = sha256_file(source_path)

    print(f"Tên file: {filename}")
    print(f"Kích thước: {file_size} bytes")
    print(f"SHA-256: {file_hash}")

    # Gửi yêu cầu UPLOAD và thông tin file
    request = {
        "action": "UPLOAD",
        "filename": filename,
        "size": file_size,
        "sha256": file_hash,
    }
    send_json(sock, request)

    # Chờ Server trả lời READY
    response = recv_json(sock)

    if response.get("status") != "READY":
        print("[SERVER ERROR]", response.get("message", "Server từ chối upload."))
        return

    # Khi Server đã READY, bắt đầu gửi nội dung file
    print("Server đã sẵn sàng nhận file.")
    start_time = time.monotonic()
    sent = 0

    with open(source_path, "rb") as file:
        while True:
            chunk = file.read(CHUNK_SIZE)

            if not chunk:
                break

            sock.sendall(chunk)
            sent += len(chunk)
            show_progress("Uploading", sent, file_size, start_time)

    if file_size == 0:
        show_progress("Uploading", 0, 0, start_time)

    # Chờ Server kiểm tra SHA-256 và trả kết quả
    result = recv_json(sock)

    if result.get("status") == "SUCCESS":
        print("[SUCCESS] Server đã nhận và xác minh file thành công.")
    else:
        print("[ERROR] Server không chấp nhận file:",
              result.get("message", result.get("status")))


# 5. CHỨC NĂNG DOWNLOAD

def download_file(sock):
    """Tải một file từ Server về máy Client."""
    filename = input("Nhập tên file cần download: ").strip()

    if not is_valid_filename(filename):
        print("[ERROR] Chỉ nhập tên file, không nhập đường dẫn.")
        return

    # Yêu cầu Server gửi file
    send_json(sock, {
        "action": "DOWNLOAD",
        "filename": filename,
    })

    # Nhận metadata hoặc thông báo lỗi từ Server
    metadata = recv_json(sock)

    if metadata.get("status") != "READY":
        print("[SERVER ERROR]", metadata.get("message", "Không thể download file."))
        return

    server_filename = metadata.get("filename")
    file_size = metadata.get("size")
    expected_hash = metadata.get("sha256")

    # Kiểm tra metadata Server gửi về
    if not isinstance(server_filename, str) or not is_valid_filename(server_filename):
        raise ValueError("Server trả về tên file không hợp lệ.")

    if server_filename != filename:
        raise ValueError("Tên file Server trả về không khớp yêu cầu.")

    if not isinstance(file_size, int) or isinstance(file_size, bool) or file_size < 0:
        raise ValueError("Server trả về kích thước file không hợp lệ.")

    if (
        not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(char not in "0123456789abcdefABCDEF" for char in expected_hash)
    ):
        raise ValueError("Server trả về mã SHA-256 không hợp lệ.")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination = DOWNLOAD_DIR / server_filename
    temporary = DOWNLOAD_DIR / (server_filename + ".part")

    # Không ghi đè file đã có ở Client
    if destination.exists() or temporary.exists():
        # Server đang chờ phản hồi READY; gửi ERROR để báo chưa sẵn sàng.
        send_json(sock, {
            "status": "ERROR",
            "message": "File đích đã tồn tại ở Client.",
        })
        print("[ERROR] File đã tồn tại trong thư mục downloads.")
        return

    # Báo Server rằng Client đã sẵn sàng nhận dữ liệu
    send_json(sock, {"status": "READY"})

    # Nhận đúng số byte mà Server thông báo
    print(f"Đang tải {server_filename} ({file_size} bytes)...")
    start_time = time.monotonic()
    received = 0

    try:
        with open(temporary, "wb") as file:
            while received < file_size:
                amount = min(CHUNK_SIZE, file_size - received)
                chunk = recv_exact(sock, amount)

                file.write(chunk)
                received += len(chunk)
                show_progress("Downloading", received, file_size, start_time)

        if file_size == 0:
            show_progress("Downloading", 0, 0, start_time)

        # Bước 5: Tính SHA-256 file vừa tải và so sánh với Server
        actual_hash = sha256_file(temporary)

        if actual_hash.lower() != expected_hash.lower():
            send_json(sock, {
                "status": "ERROR",
                "message": "SHA-256 không khớp.",
            })
            raise ValueError("File tải về không toàn vẹn (SHA-256 không khớp).")

        # Đổi tên file tạm thành file hoàn chỉnh. dùng rename để không ghi đè file đích đã tồn tại.
        temporary.rename(destination)

        # Báo Server biết Client đã nhận và kiểm tra thành công
        send_json(sock, {"status": "SUCCESS"})

        print(f"[SUCCESS] Đã lưu file tại: {destination}")
        print(f"[OK] SHA-256: {actual_hash}")

    except Exception:
        # Xóa file tạm nếu quá trình nhận hoặc xác minh bị lỗi
        if temporary.exists():
            temporary.unlink()
        raise


# 6. VÒNG LẶP MENU CHÍNH

def print_menu():
    print("\n TCP FILE TRANSFER CLIENT ")
    print("1. Upload file")
    print("2. Download file")
    print("0. Exit")
    print("                            ")


def run_client():
    """
    Chỉ chạy một Client.
    Client giữ kết nối để thực hiện nhiều thao tác lần lượt.
    Nếu kết nối bị lỗi, chương trình sẽ thử kết nối lại ở thao tác tiếp theo.
    """
    sock = None

    try:
        sock = connect_to_server()

        while True:
            print_menu()
            choice = input("Chọn chức năng: ").strip()

            if choice == "0":
                print("Đang đóng Client...")
                break

            if choice not in ("1", "2"):
                print("[ERROR] Vui lòng chọn 1, 2 hoặc 0.")
                continue

            try:
                # Nếu chưa có kết nối (do lỗi trước đó), kết nối lại
                if sock is None:
                    sock = connect_to_server()

                if choice == "1":
                    upload_file(sock)
                elif choice == "2":
                    download_file(sock)

            except (OSError, ConnectionError, TimeoutError, ValueError) as error:
                print(f"\n[ERROR] {error}")
                print("Kết nối hiện tại sẽ được đóng để tránh lệch dữ liệu giao thức.")

                if sock is not None:
                    sock.close()
                    sock = None

                print("Client sẽ kết nối lại khi bạn thực hiện thao tác tiếp theo.")

    except OSError as error:
        print(f"[FATAL] Không thể kết nối đến Server: {error}")

    finally:
        if sock is not None:
            sock.close()

        print("Client đã dừng.")


if __name__ == "__main__":
    run_client()
