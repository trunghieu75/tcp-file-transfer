
import socket
import json
import struct
from pathlib import Path
import hashlib

HOST = "127.0.0.1"
PORT = 5000

file_path = Path("results/original.bin")

# Gui thong diep JSON
def send_json(conn, message):
    data = json.dumps(message).encode("utf-8")
    conn.sendall(struct.pack("!I", len(data)) + data)


# Nhan du so byte
def recv_exact(conn, n):
    data = b""

    while len(data) < n:
        chunk = conn.recv(n - len(data))

        if not chunk:
            raise ConnectionError("Mat ket noi")

        data += chunk

    return data


# Nhan JSON tu Server
def recv_json(conn):
    header = recv_exact(conn, 4)
    size = struct.unpack("!I", header)[0]

    if size < 1 or size > 4096:
        raise ValueError("JSON khong hop le")

    data = recv_exact(conn, size)
    return json.loads(data.decode("utf-8"))


# Lay kich thuoc file
file_size = file_path.stat().st_size
hasher = hashlib.sha256()

with open(file_path, "rb") as f:
    while True:
        data = f.read(4096)

        if not data:
            break

        hasher.update(data)

file_hash = hasher.hexdigest()

print("SHA-256 goc:", file_hash)

with socket.create_connection((HOST, PORT)) as client:

    # Gui yeu cau upload
    send_json(client, {
        "action": "UPLOAD",
        "filename": "upload_temp_test.bin",
        "size": file_size,
         "sha256": file_hash
    })

    # Cho Server tra loi
    response = recv_json(client)
    print("Server tra loi:", response)

    if response.get("status") == "READY":

        # Doc va gui file
        with open(file_path, "rb") as f:
            while True:
                data = f.read(4096)

                if not data:
                    break

                client.sendall(data)

        # Nhan thong bao hoan thanh
        result = recv_json(client)
        print("Ket qua upload:", result)

        if result.get("status") == "SUCCESS":
            print(
                "SHA-256 chinh xac:",
                result.get("sha256") == file_hash
            )
