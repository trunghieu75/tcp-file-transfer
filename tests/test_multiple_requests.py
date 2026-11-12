
import socket
import json
import struct

HOST = "127.0.0.1"
PORT = 5000


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
    conn.sendall(struct.pack("!I", len(data)) + data)


def recv_json(conn):
    size = struct.unpack("!I", recv_exact(conn, 4))[0]
    return json.loads(recv_exact(conn, size).decode("utf-8"))


with socket.create_connection((HOST, PORT)) as client:
    for filename in ["khong_co_a.bin", "khong_co_b.bin"]:
        send_json(client, {
            "action": "DOWNLOAD",
            "filename": filename
        })

        response = recv_json(client)
        print(filename, "->", response)

print("Da hoan thanh 2 yeu cau tren cung 1 ket noi")
