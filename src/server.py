import socket
import threading
import json
import struct
from pathlib import Path
import hashlib
import os
import tempfile

HOST = "127.0.0.1"
PORT = 5000
MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_JSON_SIZE = 64 * 1024
UPLOAD_DIR = Path("results/uploads")


# Nhan chinh xac n byte

def recv_exact(conn, n, allow_eof=False):
    data = b""

    while len(data) < n:
        chunk = conn.recv(n - len(data))

        if not chunk:
            if allow_eof and not data:
                raise EOFError("Client da dong ket noi")

            raise ConnectionError("Mat ket noi khi dang nhan du lieu")

        data += chunk

    return data



# Nhan mot thong diep JSON

def recv_json(conn, allow_eof=False):
    header = recv_exact(conn, 4, allow_eof=allow_eof)

    size = struct.unpack("!I", header)[0]

    if size < 1 or size > MAX_JSON_SIZE:
        raise ValueError("Kich thuoc JSON khong hop le")

    data = recv_exact(conn, size)

    return json.loads(data.decode("utf-8"))



# Gui thong diep JSON
def send_json(conn, message):
    data = json.dumps(message).encode("utf-8")

    header = struct.pack("!I", len(data))

    conn.sendall(header + data)

# Xu ly yeu cau upload file

 
def handle_upload(conn, request):

    filename = request.get("filename")
    file_size = request.get("size")
    expected_hash = request.get("sha256")

    # Kiem tra ten file va kich thuoc
    if (
        not isinstance(filename, str)
        or not filename
        or filename in (".", "..")
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
        or not isinstance(file_size, int)
        or isinstance(file_size, bool)
        or file_size < 0
        or file_size > MAX_FILE_SIZE
    ):
        send_json(conn, {"status": "ERROR"})
        return

    # Kiem tra SHA-256 hop le
    if (
        not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(c not in "0123456789abcdefABCDEF"
               for c in expected_hash)
    ):
        send_json(conn, {
            "status": "ERROR",
            "message": "SHA-256 khong hop le"
        })
        return
    
    # Tao thu muc luu file
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # Thu muc rieng cho file tam
    temp_dir = UPLOAD_DIR / ".tmp"
    temp_dir.mkdir(exist_ok=True)

    file_path = UPLOAD_DIR / filename

    # Khong cho ghi de file da ton tai
    if file_path.exists():
        send_json(conn, {
            "status": "ERROR",
            "message": "File da ton tai"
        })
        return

    temp_path = None

    try:
        # Tao file tam co ten duy nhat
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=temp_dir,
            prefix="upload_",
            suffix=".part",
            delete=False
        ) as f:

            temp_path = Path(f.name)

            # Bao Client bat dau gui du lieu
            send_json(conn, {"status": "READY"})

            hasher = hashlib.sha256()
            remaining = file_size

            # Nhan file theo chunk
            while remaining > 0:
                data = conn.recv(min(4096, remaining))

                if not data:
                    raise ConnectionError("Mat ket noi")

                f.write(data)
                hasher.update(data)
                remaining -= len(data)

        # So sanh SHA-256
        actual_hash = hasher.hexdigest()

        if actual_hash != expected_hash.lower():
            send_json(conn, {
                "status": "ERROR",
                "message": "SHA-256 khong khop"
            })
            return

        # Cong bo file ma khong ghi de file cu
        try:
            os.link(temp_path, file_path)
        except FileExistsError:
            send_json(conn, {
                "status": "ERROR",
                "message": "File da ton tai"
            })
            return

        print("Nhan file thanh cong:", filename)

        send_json(conn, {
            "status": "SUCCESS",
            "size": file_size,
            "sha256": actual_hash
        })

    finally:
        # Luon don dep file tam
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def handle_download(conn, request):
    # Lay ten file Client muon tai
    filename = request.get("filename")

    # Kiem tra ten file hop le
    if (
        not isinstance(filename, str)
        or not filename
        or filename in (".", "..")
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
    ):
        send_json(conn, {
            "status": "ERROR",
            "message": "Ten file khong hop le"
        })
        return

    # Xac dinh duong dan file
    file_path = UPLOAD_DIR / filename

    # Kiem tra file co ton tai khong
    if not file_path.is_file():
        send_json(conn, {
            "status": "ERROR",
            "message": "File khong ton tai"
        })
        return

    # Lay kich thuoc file
    file_size = file_path.stat().st_size

    # Tinh SHA-256 cua file
    hasher = hashlib.sha256()

    with open(file_path, "rb") as f:
        while True:
            data = f.read(4096)

            if not data:
                break

            hasher.update(data)

    file_hash = hasher.hexdigest()

    # Gui metadata ve Client
    send_json(conn, {
        "status": "READY",
        "filename": filename,
        "size": file_size,
        "sha256": file_hash
    })

    # Cho Client xac nhan san sang nhan file
    response = recv_json(conn)

    if (
        not isinstance(response, dict)
        or response.get("status") != "READY"
    ):
        print("Client khong san sang nhan file")
        return

    # Gui noi dung file theo chunk
    remaining = file_size

    with open(file_path, "rb") as f:
        while remaining > 0:
            data = f.read(min(4096, remaining))

            if not data:
                raise OSError("File thay doi trong qua trinh gui")

            conn.sendall(data)
            remaining -= len(data)

    print("Da gui file:", filename)

    # Nhan ket qua kiem tra tu Client
    result = recv_json(conn)

    if (
        isinstance(result, dict)
        and result.get("status") == "SUCCESS"
    ):
        print("Client tai file thanh cong:", filename)
    else:
        print("Client bao loi DOWNLOAD:", result)


# Xu ly mot Client


def handle_client(conn, addr):
    print("Client ket noi:", addr)

    try:
        with conn:
            while True:
                try:
                    # Cho phep Client dong ket noi sau khi xong
                    request = recv_json(conn, allow_eof=True)

                except EOFError:
                    print("Client da dong ket noi:", addr)
                    break

                # Kiem tra request
                if not isinstance(request, dict):
                    send_json(conn, {
                        "status": "ERROR",
                        "message": "Request phai la JSON object"
                    })
                    continue

                action = request.get("action")

                if action == "UPLOAD":
                    print("Client yeu cau UPLOAD")
                    handle_upload(conn, request)

                elif action == "DOWNLOAD":
                    print("Client yeu cau DOWNLOAD")
                    handle_download(conn, request)

                else:
                    send_json(conn, {
                        "status": "ERROR",
                        "message": "Action khong hop le"
                    })

    except (OSError, ValueError, UnicodeError) as e:
        print("Loi xu ly Client:", e)

    finally:
        print("Da xu ly xong Client:", addr)


# Tao TCP Server
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:

    server.bind((HOST, PORT))
    server.listen()

    print("Server dang chay tai", HOST, PORT)

    try:
        while True:
            conn, addr = server.accept()

            t = threading.Thread(
                target=handle_client,
                args=(conn, addr),
                daemon=True
            )

            t.start()

    except KeyboardInterrupt:
        print("Dung Server")
