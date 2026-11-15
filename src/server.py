import socket
import json
import struct
from pathlib import Path
import hashlib
import os
import tempfile
import argparse
import logging
import math

if __package__:
    from .concurrency import ConnectionManager
else:
    from concurrency import ConnectionManager

HOST = "127.0.0.1"
PORT = 5000
MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_JSON_SIZE = 64 * 1024
UPLOAD_DIR = Path("results/uploads")
LOGGER = logging.getLogger(__name__)


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
        raise ValueError("Ten file hoac kich thuoc UPLOAD khong hop le")

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
        raise ValueError("SHA-256 khong hop le")
    
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

        LOGGER.info("upload_completed filename=%s bytes=%s", filename, file_size)

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
        raise ValueError("Ten file DOWNLOAD khong hop le")

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
        if isinstance(response, dict) and response.get("status") == "ERROR":
            LOGGER.info("download_cancelled filename=%s", filename)
            return
        raise ValueError("Client phai tra READY hoac ERROR truoc DOWNLOAD")

    # Gui noi dung file theo chunk
    remaining = file_size

    with open(file_path, "rb") as f:
        while remaining > 0:
            data = f.read(min(4096, remaining))

            if not data:
                raise OSError("File thay doi trong qua trinh gui")

            conn.sendall(data)
            remaining -= len(data)

    LOGGER.info("download_sent filename=%s bytes=%s", filename, file_size)

    # Nhan ket qua kiem tra tu Client
    result = recv_json(conn)

    if (
        isinstance(result, dict)
        and result.get("status") == "SUCCESS"
    ):
        LOGGER.info("download_confirmed filename=%s", filename)
    elif isinstance(result, dict) and result.get("status") == "ERROR":
        LOGGER.warning("download_failed filename=%s result=%s", filename, result)
    else:
        raise ValueError("Ket qua DOWNLOAD phai la SUCCESS hoac ERROR")


# Xu ly mot Client


def handle_client(conn, addr):
    # ConnectionManager owns the socket lifecycle.
    while True:
        try:
            request = recv_json(conn, allow_eof=True)
        except EOFError:
            return
        except (ValueError, UnicodeError):
            # Safe to reply before file transfer starts.
            send_json(conn, {"status": "ERROR", "message": "JSON khong hop le"})
            return

        if not isinstance(request, dict):
            send_json(conn, {
                "status": "ERROR",
                "message": "Request phai la JSON object"
            })
            return

        action = request.get("action")
        LOGGER.info("request address=%s action=%s", addr, action)
        if action == "UPLOAD":
            handle_upload(conn, request)
        elif action == "DOWNLOAD":
            handle_download(conn, request)
        else:
            send_json(conn, {
                "status": "ERROR",
                "message": "Action khong hop le"
            })
            return


def main():
    global UPLOAD_DIR

    parser = argparse.ArgumentParser(description="TCP file server ET4230")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-clients", type=int, default=32)
    parser.add_argument("--upload-dir", type=Path, default=UPLOAD_DIR)
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be positive and finite")
    if args.max_clients <= 0:
        parser.error("max-clients must be positive")

    UPLOAD_DIR = args.upload_dir
    try:
        handlers = [logging.StreamHandler()]
        if args.log_file is not None:
            args.log_file.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
            handlers=handlers,
        )
        manager = ConnectionManager(
            handle_client,
            client_timeout=args.timeout,
            max_clients=args.max_clients,
        )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind((args.host, args.port))
            listener.listen()
            LOGGER.info("server_listening host=%s port=%s", args.host, args.port)
            manager.serve_forever(listener)
    except KeyboardInterrupt:
        LOGGER.info("server_stopped")
    except OSError as error:
        LOGGER.error("server_error error=%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
