
import socket
import json
import struct
import hashlib
from pathlib import Path

HOST = "127.0.0.1"
PORT = 5000

FILENAME = "upload_temp_test.bin"
DOWNLOAD_PATH = Path("results/downloaded_test.bin")


def recv_exact(conn, n):
    data = b""

    while len(data) < n:
        chunk = conn.recv(n - len(data))

        if not chunk:
            raise ConnectionError("Mat ket noi")

        data += chunk

    return data


def send_json(conn, message):
    data = json.dumps(message).encode("utf-8")
    header = struct.pack("!I", len(data))
    conn.sendall(header + data)


def recv_json(conn):
    header = recv_exact(conn, 4)
    size = struct.unpack("!I", header)[0]

    if size < 1 or size > 4096:
        raise ValueError("Kich thuoc JSON khong hop le")

    data = recv_exact(conn, size)
    return json.loads(data.decode("utf-8"))


with socket.create_connection((HOST, PORT), timeout=30) as client:

    # Gui yeu cau DOWNLOAD
    send_json(client, {
        "action": "DOWNLOAD",
        "filename": FILENAME
    })

    # Nhan metadata tu Server
    response = recv_json(client)
    print("Server tra loi:", response)

    if response.get("status") == "READY":

        file_size = response["size"]
        expected_hash = response["sha256"]

        # Kiem tra metadata
        if (
            not isinstance(file_size, int)
            or isinstance(file_size, bool)
            or file_size < 0
            or file_size > 10 * 1024 * 1024
            or not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(c not in "0123456789abcdef"
                   for c in expected_hash)
        ):
            send_json(client, {
                "status": "ERROR",
                "message": "Metadata khong hop le"
            })
            raise ValueError("Metadata khong hop le")

        DOWNLOAD_PATH.parent.mkdir(
            parents=True, exist_ok=True
        )

        # Mo file de luu du lieu tai ve
        with open(DOWNLOAD_PATH, "wb") as f:

            # Bao Server da san sang
            send_json(client, {"status": "READY"})

            hasher = hashlib.sha256()
            remaining = file_size

            # Nhan file theo chunk
            while remaining > 0:
                data = client.recv(min(4096, remaining))

                if not data:
                    raise ConnectionError("Nhan thieu du lieu")

                f.write(data)
                hasher.update(data)
                remaining -= len(data)

        # Kiem tra SHA-256
        actual_hash = hasher.hexdigest()

        if actual_hash == expected_hash:
            send_json(client, {"status": "SUCCESS"})
            print("DOWNLOAD thanh cong")
            print("So byte:", file_size)
            print("SHA-256 chinh xac: True")
        else:
            DOWNLOAD_PATH.unlink(missing_ok=True)
            send_json(client, {"status": "ERROR"})
            print("SHA-256 chinh xac: False")

    else:
        print("DOWNLOAD that bai:", response)
