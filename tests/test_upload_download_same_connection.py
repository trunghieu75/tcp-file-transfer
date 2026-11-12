
import socket
import json
import struct
import hashlib
import uuid

HOST = "127.0.0.1"
PORT = 5000

# Tao ten file rieng de tranh trung ten
FILENAME = f"roundtrip_{uuid.uuid4().hex[:8]}.bin"

# Tao 10 KiB du lieu mau
FILE_DATA = bytes(range(256)) * 40
FILE_SIZE = len(FILE_DATA)
FILE_HASH = hashlib.sha256(FILE_DATA).hexdigest()


def send_json(conn, message):
    data = json.dumps(message).encode("utf-8")
    conn.sendall(struct.pack("!I", len(data)) + data)


def recv_exact(conn, n):
    data = b""
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Mat ket noi")
        data += chunk
    return data


def recv_json(conn):
    header = recv_exact(conn, 4)
    size = struct.unpack("!I", header)[0]
    return json.loads(recv_exact(conn, size).decode("utf-8"))


# Chi tao MOT ket noi TCP
with socket.create_connection((HOST, PORT), timeout=15) as client:

    # ===== YEU CAU 1: UPLOAD =====
    print("=== UPLOAD ===")

    send_json(client, {
        "action": "UPLOAD",
        "filename": FILENAME,
        "size": FILE_SIZE,
        "sha256": FILE_HASH
    })

    response = recv_json(client)
    print("Server:", response)

    assert response["status"] == "READY"

    for i in range(0, FILE_SIZE, 4096):
        client.sendall(FILE_DATA[i:i + 4096])

    result = recv_json(client)
    print("Ket qua:", result)

    assert result["status"] == "SUCCESS"
    assert result["sha256"] == FILE_HASH

    # ===== YEU CAU 2: DOWNLOAD =====
    # Van su dung bien client, KHONG tao socket moi
    print("\n=== DOWNLOAD ===")

    send_json(client, {
        "action": "DOWNLOAD",
        "filename": FILENAME
    })

    response = recv_json(client)
    print("Server:", response)

    assert response["status"] == "READY"
    assert response["size"] == FILE_SIZE

    send_json(client, {"status": "READY"})

    downloaded_data = recv_exact(client, response["size"])
    downloaded_hash = hashlib.sha256(downloaded_data).hexdigest()

    if downloaded_hash == response["sha256"]:
        send_json(client, {"status": "SUCCESS"})
    else:
        send_json(client, {"status": "ERROR"})

    assert downloaded_hash == FILE_HASH
    assert downloaded_data == FILE_DATA

    print("DOWNLOAD thanh cong")
    print("SHA-256 chinh xac: True")

print("\nHOAN THANH 2 YEU CAU TREN CUNG 1 KET NOI TCP")
