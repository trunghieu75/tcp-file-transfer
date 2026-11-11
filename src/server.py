import socket
import threading
import json
import struct
from pathlib import Path
import hashlib

HOST = "127.0.0.1"
PORT = 5000
MAX_FILE_SIZE = 10 * 1024 * 1024
UPLOAD_DIR = Path("results/uploads")


# Nhan chinh xac n byte
def recv_exact(conn, n):
    data = b""

    while len(data) < n:
        chunk = conn.recv(n - len(data))

        if not chunk:
            raise ConnectionError("Mat ket noi")

        data += chunk

    return data


# Nhan mot thong diep JSON
def recv_json(conn):
    header = recv_exact(conn, 4)

    size = struct.unpack("!I", header)[0]

    if size < 1 or size > 4096:
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

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = UPLOAD_DIR / filename

    # Tao bo tinh SHA-256
    hasher = hashlib.sha256()
    remaining = file_size

    try:
        # xb: tao file moi, khong ghi de file cu
        with open(file_path, "xb") as f:

            send_json(conn, {"status": "READY"})

            while remaining > 0:
                data = conn.recv(min(4096, remaining))

                if not data:
                    raise ConnectionError("Mat ket noi")

                # Ghi du lieu vao file
                f.write(data)

                # Cap nhat SHA-256
                hasher.update(data)

                remaining -= len(data)

    except FileExistsError:
        send_json(conn, {
            "status": "ERROR",
            "message": "File da ton tai"
        })
        return

    except Exception:
        file_path.unlink(missing_ok=True)
        raise

    # Lay gia tri bam cua file da nhan
    actual_hash = hasher.hexdigest()

    # So sanh SHA-256
    if actual_hash != expected_hash.lower():

        file_path.unlink(missing_ok=True)

        send_json(conn, {
            "status": "ERROR",
            "message": "SHA-256 khong khop"
        })
        return

    print("Nhan file thanh cong:", filename)

    send_json(conn, {
        "status": "SUCCESS",
        "size": file_size,
        "sha256": actual_hash
    })


# Xu ly mot Client
def handle_client(conn, addr):
    print("Client ket noi:", addr)

    try:
        with conn:
            request = recv_json(conn)

            action = request.get("action")

            if action == "UPLOAD":
                print("Client yeu cau UPLOAD")
                handle_upload(conn, request)

            elif action == "DOWNLOAD":
                print("Client yeu cau DOWNLOAD")

                send_json(conn, {
                    "status": "READY"
                })

            else:
                send_json(conn, {
                    "status": "ERROR"
                })

    except (OSError, ValueError, ConnectionError, json.JSONDecodeError) as e:
        print("Loi xu ly Client:", e)

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
