"""TV3 - Kiem thu cac ham giao thuc dung chung (src/protocol.py).

Chay tu thu muc goc repository:
    python -m unittest tests.test_protocol -v
"""

import hashlib
import io
import json
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from src import protocol
from src import server


SIZES = [0, 1, 4095, 4096, 4097, 3 * 4096 + 5, 1024 * 1024 + 7]
CHUNK_SIZES = [1024, 4096, 65536]


class FakeSocket:
    """Socket gia: tra ve du lieu theo tung manh co dinh de mo phong TCP."""

    def __init__(self, data, piece=1):
        self.data = bytes(data)
        self.piece = piece
        self.position = 0
        self.sent = bytearray()

    def recv(self, n):
        end = min(self.position + min(n, self.piece), len(self.data))
        chunk = self.data[self.position:end]
        self.position = end
        return chunk

    def sendall(self, data):
        self.sent.extend(data)


def pattern(size):
    return bytes((i * 31 + 7) % 256 for i in range(size))


class SocketPairTestCase(unittest.TestCase):
    def setUp(self):
        self.left, self.right = socket.socketpair()
        for sock in (self.left, self.right):
            sock.settimeout(5)
            self.addCleanup(sock.close)

    def run_in_thread(self, target, *args):
        result = {}

        def runner():
            try:
                result["value"] = target(*args)
            except BaseException as error:  # pragma: no cover - re-raised below
                result["error"] = error

        thread = threading.Thread(target=runner)
        thread.start()
        return thread, result

    def join(self, thread, result):
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "Thread gui bi treo")
        if "error" in result:
            raise result["error"]
        return result.get("value")


class RecvExactTests(unittest.TestCase):
    def test_reassembles_fragmented_stream(self):
        sock = FakeSocket(b"abcdefghij", piece=3)
        self.assertEqual(protocol.recv_exact(sock, 10), b"abcdefghij")

    def test_does_not_read_past_n(self):
        sock = FakeSocket(b"headerREST", piece=100)
        self.assertEqual(protocol.recv_exact(sock, 6), b"header")
        self.assertEqual(protocol.recv_exact(sock, 4), b"REST")

    def test_zero_bytes(self):
        self.assertEqual(protocol.recv_exact(FakeSocket(b""), 0), b"")

    def test_eof_midway_raises_connection_closed(self):
        with self.assertRaises(protocol.ConnectionClosed):
            protocol.recv_exact(FakeSocket(b"abc"), 5)

    def test_eof_midway_even_with_allow_eof(self):
        with self.assertRaises(protocol.ConnectionClosed):
            protocol.recv_exact(FakeSocket(b"ab"), 4, allow_eof=True)

    def test_clean_eof_with_allow_eof(self):
        with self.assertRaises(EOFError):
            protocol.recv_exact(FakeSocket(b""), 4, allow_eof=True)

    def test_clean_eof_without_allow_eof(self):
        with self.assertRaises(protocol.ConnectionClosed):
            protocol.recv_exact(FakeSocket(b""), 4)

    def test_connection_closed_is_connection_error(self):
        self.assertTrue(issubclass(protocol.ConnectionClosed, ConnectionError))

    def test_rejects_invalid_n(self):
        for n in (-1, 1.5, True, "4"):
            with self.subTest(n=n), self.assertRaises(ValueError):
                protocol.recv_exact(FakeSocket(b"abcd"), n)


class JsonFramingTests(unittest.TestCase):
    def test_header_is_4_byte_big_endian_length(self):
        frame = protocol.encode_json({"action": "LIST"})
        payload = frame[4:]
        self.assertEqual(frame[:4], struct.pack(">I", len(payload)))
        self.assertEqual(json.loads(payload.decode("utf-8")), {"action": "LIST"})

    def test_roundtrip_with_unicode(self):
        message = {
            "action": "UPLOAD",
            "filename": "bao_cao_mạng_máy_tính.pdf",
            "size": 100,
            "sha256": "a" * 64,
        }
        sock = FakeSocket(b"")
        protocol.send_json(sock, message)
        self.assertEqual(protocol.recv_json(FakeSocket(sock.sent, piece=1)), message)

    def test_two_frames_in_one_segment(self):
        data = protocol.encode_json({"status": "READY"}) + protocol.encode_json(
            {"status": "SUCCESS", "message": "Upload completed"}
        )
        sock = FakeSocket(data, piece=len(data))
        self.assertEqual(protocol.recv_json(sock), {"status": "READY"})
        self.assertEqual(protocol.recv_json(sock)["status"], "SUCCESS")

    def test_frame_followed_by_raw_file_bytes(self):
        data = protocol.encode_json({"status": "READY"}) + b"\x00\x01\x02"
        sock = FakeSocket(data, piece=len(data))
        self.assertEqual(protocol.recv_json(sock), {"status": "READY"})
        self.assertEqual(protocol.recv_exact(sock, 3), b"\x00\x01\x02")

    def test_max_size_payload_accepted(self):
        overhead = len(json.dumps({"x": ""}, separators=(",", ":")))
        message = {"x": "y" * (protocol.MAX_JSON_SIZE - overhead)}
        frame = protocol.encode_json(message)
        self.assertEqual(len(frame) - 4, protocol.MAX_JSON_SIZE)
        self.assertEqual(protocol.recv_json(FakeSocket(frame, piece=8192)), message)

    def test_encode_rejects_oversize(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode_json({"x": "y" * protocol.MAX_JSON_SIZE})

    def test_encode_rejects_non_object(self):
        for value in ([1, 2], "READY", None, 5):
            with self.subTest(value=value), self.assertRaises(protocol.ProtocolError):
                protocol.encode_json(value)

    def test_encode_rejects_unserializable_and_nan(self):
        for value in ({"x": b"bytes"}, {"x": float("nan")}):
            with self.subTest(value=value), self.assertRaises(protocol.ProtocolError):
                protocol.encode_json(value)

    def test_recv_rejects_bad_length_without_reading_body(self):
        for length in (0, protocol.MAX_JSON_SIZE + 1, 0xFFFFFFFF):
            with self.subTest(length=length):
                sock = FakeSocket(struct.pack("!I", length) + b"{}")
                with self.assertRaises(protocol.ProtocolError):
                    protocol.recv_json(sock)
                self.assertEqual(sock.position, 4)

    def test_recv_rejects_invalid_payloads(self):
        payloads = [
            b"{not json",
            b"\xff\xfe\xfd",
            b"[1,2,3]",
            b'"READY"',
            b'{"size": NaN}',
            b" ",
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                frame = struct.pack("!I", len(payload)) + payload
                with self.assertRaises(protocol.ProtocolError):
                    protocol.recv_json(FakeSocket(frame))

    def test_protocol_error_is_value_error(self):
        # ConnectionManager (TV4) bat ValueError de ghi log va dong ket noi.
        self.assertTrue(issubclass(protocol.ProtocolError, ValueError))

    def test_recv_json_truncated_body(self):
        frame = protocol.encode_json({"status": "READY"})[:-2]
        with self.assertRaises(protocol.ConnectionClosed):
            protocol.recv_json(FakeSocket(frame))

    def test_recv_json_clean_eof(self):
        with self.assertRaises(EOFError):
            protocol.recv_json(FakeSocket(b""), allow_eof=True)

    def test_error_message(self):
        self.assertEqual(
            protocol.error_message("File not found"),
            {"status": "ERROR", "message": "File not found"},
        )


class CompatibilityWithServerTests(unittest.TestCase):
    """Dinh dang cua module dung chung tuong thich voi ham trong server.py."""

    def test_protocol_to_server(self):
        message = {"action": "DOWNLOAD", "filename": "tệp.txt"}
        sock = FakeSocket(b"")
        protocol.send_json(sock, message)
        self.assertEqual(server.recv_json(FakeSocket(sock.sent, piece=2)), message)

    def test_server_to_protocol(self):
        message = {"status": "READY", "filename": "tệp.txt", "size": 5,
                   "sha256": "b" * 64}
        sock = FakeSocket(b"")
        server.send_json(sock, message)
        self.assertEqual(protocol.recv_json(FakeSocket(sock.sent, piece=2)), message)


class ValidationTests(unittest.TestCase):
    def test_valid_filenames(self):
        for name in ("example.txt", "data.bin", "báo cáo.pdf", ".hidden",
                     "a" * 255, "file.tar.gz", "CONFIG.txt"):
            with self.subTest(name=name):
                self.assertTrue(protocol.is_valid_filename(name))

    def test_invalid_filenames(self):
        names = [
            "", ".", "..", "../secret.txt", "..\\secret.txt", "dir/file.txt",
            "dir\\file.txt", "/etc/passwd", "C:\\Windows\\win.ini", "C:file.txt",
            "file.txt:stream", "bad\x00name", "tab\tname", "new\nline",
            "trailing.", "trailing ", " leading", "CON", "nul.txt", "com1",
            "LPT9.log", "a" * 256, "ạ" * 100, None, 123, b"file.txt",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertFalse(protocol.is_valid_filename(name))

    def test_sha256_validation(self):
        self.assertTrue(protocol.is_valid_sha256("0" * 64))
        self.assertTrue(protocol.is_valid_sha256("ABCDEF" + "0" * 58))
        for value in ("0" * 63, "0" * 65, "g" * 64, None, 0, "0" * 63 + " "):
            with self.subTest(value=value):
                self.assertFalse(protocol.is_valid_sha256(value))

    def test_size_validation(self):
        self.assertTrue(protocol.is_valid_size(0))
        self.assertTrue(protocol.is_valid_size(100, max_size=100))
        for value in (-1, 1.0, True, "10", None):
            with self.subTest(value=value):
                self.assertFalse(protocol.is_valid_size(value))
        self.assertFalse(protocol.is_valid_size(101, max_size=100))

    def test_chunk_size_validation(self):
        self.assertEqual(protocol.validate_chunk_size(1), 1)
        for value in (0, -4096, 1.5, True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                protocol.validate_chunk_size(value)


class Sha256Tests(unittest.TestCase):
    def test_known_vectors(self):
        self.assertEqual(
            protocol.sha256_bytes(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertEqual(
            protocol.sha256_bytes(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_file_hash_independent_of_chunk_size(self):
        with tempfile.TemporaryDirectory() as directory:
            for size in SIZES:
                data = pattern(size)
                path = Path(directory) / f"f{size}.bin"
                path.write_bytes(data)
                expected = hashlib.sha256(data).hexdigest()
                for chunk_size in [1, 7] + CHUNK_SIZES:
                    if size > 100_000 and chunk_size < 1024:
                        continue
                    with self.subTest(size=size, chunk_size=chunk_size):
                        actual = protocol.sha256_file(path, chunk_size)
                        self.assertEqual(actual, expected)
                        self.assertEqual(actual, actual.lower())
                        self.assertEqual(len(actual), 64)

    def test_detects_single_bit_change(self):
        data = bytearray(pattern(5000))
        original = protocol.sha256_bytes(bytes(data))
        data[4096] ^= 0x01
        self.assertNotEqual(protocol.sha256_bytes(bytes(data)), original)


class ChunkTransferTests(SocketPairTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def make_file(self, size, name="source.bin"):
        path = self.directory / name
        path.write_bytes(pattern(size))
        return path

    def test_roundtrip_sizes_and_chunk_sizes(self):
        for size in SIZES:
            source = self.make_file(size)
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            for chunk_size in CHUNK_SIZES:
                with self.subTest(size=size, chunk_size=chunk_size):
                    # Gui them mot JSON sau du lieu: ben nhan khong duoc doc lan.
                    def sender():
                        digest = protocol.send_file(
                            self.left, source, size, chunk_size
                        )
                        protocol.send_json(self.left, {"status": "SUCCESS"})
                        return digest

                    thread, result = self.run_in_thread(sender)
                    output = io.BytesIO()
                    received = protocol.recv_file(
                        self.right, output, size, chunk_size
                    )
                    sent = self.join(thread, result)
                    self.assertEqual(sent, expected)
                    self.assertEqual(received, expected)
                    self.assertEqual(output.getvalue(), source.read_bytes())
                    self.assertEqual(
                        protocol.recv_json(self.right), {"status": "SUCCESS"}
                    )

    def test_recv_never_requests_more_than_chunk_size(self):
        requests = []

        class Recorder(FakeSocket):
            def recv(self, n):
                requests.append(n)
                return super().recv(n)

        sock = Recorder(pattern(10000), piece=10**6)
        protocol.recv_file(sock, io.BytesIO(), 10000, chunk_size=4096)
        self.assertEqual(requests, [4096, 4096, 1808])

    def test_send_chunks_respect_chunk_size(self):
        source = self.make_file(10000)
        sizes = []

        class Recorder(FakeSocket):
            def sendall(self, data):
                sizes.append(len(data))

        protocol.send_file(Recorder(b""), source, chunk_size=4096)
        self.assertEqual(sizes, [4096, 4096, 1808])

    def test_send_only_declared_size(self):
        source = self.make_file(5000)
        sock = FakeSocket(b"")
        digest = protocol.send_file(sock, source, size=1000)
        self.assertEqual(bytes(sock.sent), pattern(5000)[:1000])
        self.assertEqual(digest, protocol.sha256_bytes(pattern(5000)[:1000]))

    def test_send_file_shorter_than_declared(self):
        source = self.make_file(100)
        with self.assertRaises(OSError):
            protocol.send_file(FakeSocket(b""), source, size=200)

    def test_recv_file_connection_lost(self):
        with self.assertRaises(protocol.ConnectionClosed):
            protocol.recv_file(FakeSocket(pattern(100)), io.BytesIO(), 200)

    def test_progress_callback(self):
        source = self.make_file(9000)
        calls = []
        protocol.send_file(FakeSocket(b""), source, chunk_size=4096,
                           progress=lambda done, total: calls.append((done, total)))
        self.assertEqual(calls, [(4096, 9000), (8192, 9000), (9000, 9000)])

        calls.clear()
        protocol.recv_file(FakeSocket(b""), io.BytesIO(), 0,
                           progress=lambda done, total: calls.append((done, total)))
        self.assertEqual(calls, [(0, 0)])

    def test_timeout_propagates(self):
        self.right.settimeout(0.2)
        with self.assertRaises(TimeoutError):
            protocol.recv_file(self.right, io.BytesIO(), 10)


class VerifiedReceiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.dest = self.directory / "received.bin"

    def leftovers(self):
        return sorted(p.name for p in self.directory.iterdir())

    def test_success_publishes_file(self):
        for size in (0, 4095, 4096, 4097):
            data = pattern(size)
            dest = self.directory / f"ok_{size}.bin"
            with self.subTest(size=size):
                sock = FakeSocket(data, piece=1000)
                digest = protocol.recv_verified_file(
                    sock, dest, size, hashlib.sha256(data).hexdigest().upper()
                )
                self.assertEqual(digest, hashlib.sha256(data).hexdigest())
                self.assertEqual(dest.read_bytes(), data)
        self.assertFalse(any(name.endswith(".part") for name in self.leftovers()))

    def test_checksum_mismatch_leaves_nothing(self):
        data = pattern(5000)
        with self.assertRaises(protocol.ChecksumMismatch) as context:
            protocol.recv_verified_file(FakeSocket(data), self.dest, 5000, "0" * 64)
        self.assertEqual(context.exception.actual, hashlib.sha256(data).hexdigest())
        self.assertEqual(self.leftovers(), [])

    def test_corrupted_byte_detected(self):
        data = bytearray(pattern(5000))
        expected = hashlib.sha256(bytes(data)).hexdigest()
        data[10] ^= 0xFF
        with self.assertRaises(protocol.ChecksumMismatch):
            protocol.recv_verified_file(FakeSocket(data), self.dest, 5000, expected)
        self.assertEqual(self.leftovers(), [])

    def test_disconnect_leaves_nothing(self):
        data = pattern(5000)
        with self.assertRaises(protocol.ConnectionClosed):
            protocol.recv_verified_file(
                FakeSocket(data[:3000]), self.dest, 5000,
                hashlib.sha256(data).hexdigest(),
            )
        self.assertEqual(self.leftovers(), [])

    def test_does_not_overwrite_existing(self):
        self.dest.write_bytes(b"original")
        data = pattern(100)
        with self.assertRaises(FileExistsError):
            protocol.recv_verified_file(
                FakeSocket(data), self.dest, 100, hashlib.sha256(data).hexdigest()
            )
        self.assertEqual(self.dest.read_bytes(), b"original")
        self.assertEqual(self.leftovers(), ["received.bin"])

    def test_rejects_invalid_expected_hash(self):
        with self.assertRaises(ValueError):
            protocol.recv_verified_file(FakeSocket(b""), self.dest, 0, "xyz")
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
