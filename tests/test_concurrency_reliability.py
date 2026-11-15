"""TV4 regression tests using real TCP sockets and an isolated temporary store.

Run from the repository root with
python -m unittest discover -s tests -p test_concurrency_reliability.py -v
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from src import server
from src.concurrency import ConnectionManager


class ConcurrencyReliabilityTests(unittest.TestCase):
    def setUp(self):
        temporary_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_dir.cleanup)
        previous_upload_dir = server.UPLOAD_DIR
        self.addCleanup(setattr, server, "UPLOAD_DIR", previous_upload_dir)
        self.upload_dir = Path(temporary_dir.name) / "uploads"
        self.upload_dir.mkdir()
        server.UPLOAD_DIR = self.upload_dir

        self.manager = ConnectionManager(
            server.handle_client, client_timeout=1.0, max_clients=8
        )
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        self.address = listener.getsockname()
        self.accept_thread = threading.Thread(
            target=self.manager.serve_forever,
            args=(listener,),
            name="test-listener",
        )
        self.addCleanup(self.stop_server)
        self.accept_thread.start()

    def stop_server(self):
        self.manager.shutdown()
        self.accept_thread.join(timeout=3)
        self.assertFalse(self.accept_thread.is_alive(), "Listener did not stop")
        self.assertEqual(self.manager.active_count, 0)

    def connect(self):
        connection = socket.create_connection(self.address, timeout=3)
        self.addCleanup(connection.close)
        return connection

    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for worker cleanup")

    def assert_no_partial_files(self):
        self.assertEqual(list(self.upload_dir.glob(".tmp/*.part")), [])

    def begin_upload(self, connection, filename, data, checksum=None):
        server.send_json(connection, {
            "action": "UPLOAD",
            "filename": filename,
            "size": len(data),
            "sha256": checksum or hashlib.sha256(data).hexdigest(),
        })
        self.assertEqual(server.recv_json(connection)["status"], "READY")

    def finish_upload(self, connection, data):
        connection.sendall(data)
        return server.recv_json(connection)

    def download(self, connection, filename, expected_data):
        server.send_json(connection, {"action": "DOWNLOAD", "filename": filename})
        metadata = server.recv_json(connection)
        self.assertEqual(metadata["status"], "READY")
        self.assertEqual(metadata["size"], len(expected_data))
        server.send_json(connection, {"status": "READY"})
        data = server.recv_exact(connection, metadata["size"])
        self.assertEqual(data, expected_data)
        self.assertEqual(hashlib.sha256(data).hexdigest(), metadata["sha256"])
        server.send_json(connection, {"status": "SUCCESS"})

    def assert_server_responds(self):
        with socket.create_connection(self.address, timeout=3) as connection:
            server.send_json(connection, {"action": "DOWNLOAD", "filename": "missing"})
            self.assertEqual(server.recv_json(connection)["status"], "ERROR")

    def test_six_simultaneous_uploads_and_downloads(self):
        barrier = threading.Barrier(6, timeout=3)

        def roundtrip(index):
            data = bytes([index]) * (10000 + index)
            filename = f"client_{index}.bin"
            with socket.create_connection(self.address, timeout=3) as connection:
                self.begin_upload(connection, filename, data)
                # All six connections must reach READY before any sends its body.
                barrier.wait()
                result = self.finish_upload(connection, data)
                self.assertEqual(result["status"], "SUCCESS")
                self.download(connection, filename, data)

        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(roundtrip, range(6)))
        self.wait_until(lambda: self.manager.active_count == 0)
        self.assert_no_partial_files()

    def test_simultaneous_same_name_never_overwrites_winner(self):
        barrier = threading.Barrier(2, timeout=3)
        payloads = [b"A" * 10000, b"B" * 10000]

        def upload(data):
            with socket.create_connection(self.address, timeout=3) as connection:
                self.begin_upload(connection, "shared.bin", data)
                barrier.wait()
                return self.finish_upload(connection, data)["status"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(upload, payloads))
        self.assertEqual(sorted(statuses), ["ERROR", "SUCCESS"])
        self.assertEqual(
            (self.upload_dir / "shared.bin").read_bytes(),
            payloads[statuses.index("SUCCESS")],
        )
        self.wait_until(lambda: self.manager.active_count == 0)
        self.assert_no_partial_files()

    def test_empty_and_partial_chunk_files_on_persistent_connection(self):
        connection = self.connect()
        for size in (0, 100, 10000):
            with self.subTest(size=size):
                data = b"X" * size
                filename = f"size_{size}.bin"
                self.begin_upload(connection, filename, data)
                self.assertEqual(self.finish_upload(connection, data)["status"], "SUCCESS")
                self.download(connection, filename, data)

    def test_missing_files_keep_persistent_connection_usable(self):
        connection = self.connect()
        for filename in ("missing_a", "missing_b"):
            server.send_json(connection, {"action": "DOWNLOAD", "filename": filename})
            self.assertEqual(server.recv_json(connection)["status"], "ERROR")

    def test_upload_disconnect_cleans_partial_file_and_allows_retry(self):
        connection = self.connect()
        data = b"payload" * 2000
        self.begin_upload(connection, "retry.bin", data)
        connection.sendall(data[:100])
        connection.close()
        self.wait_until(lambda: self.manager.active_count == 0)
        self.assertFalse((self.upload_dir / "retry.bin").exists())
        self.assert_no_partial_files()

        retry = self.connect()
        self.begin_upload(retry, "retry.bin", data)
        self.assertEqual(self.finish_upload(retry, data)["status"], "SUCCESS")

    def test_upload_timeout_closes_stream_without_inserting_json(self):
        connection = self.connect()
        self.begin_upload(connection, "timeout.bin", b"X" * 10000)
        connection.sendall(b"partial")
        self.assert_server_responds()
        self.assertEqual(connection.recv(1), b"")
        self.wait_until(lambda: self.manager.active_count == 0)
        self.assertFalse((self.upload_dir / "timeout.bin").exists())
        self.assert_no_partial_files()

    def test_idle_connection_times_out(self):
        connection = self.connect()
        self.assertEqual(connection.recv(1), b"")
        self.wait_until(lambda: self.manager.active_count == 0)
        self.assert_server_responds()

    def test_partial_json_header_times_out_without_blocking_other_clients(self):
        connection = self.connect()
        connection.sendall(b"\x00")
        self.assert_server_responds()
        self.assertEqual(connection.recv(1), b"")
        self.wait_until(lambda: self.manager.active_count == 0)

    def test_disconnect_during_json_body_releases_worker(self):
        connection = self.connect()
        connection.sendall(struct.pack("!I", 100) + b"{")
        self.wait_until(lambda: self.manager.active_count == 1)
        connection.close()
        self.wait_until(lambda: self.manager.active_count == 0)
        self.assert_server_responds()

    def test_checksum_failure_removes_temp_and_allows_next_request(self):
        connection = self.connect()
        self.begin_upload(connection, "bad.bin", b"wrong", checksum="0" * 64)
        self.assertEqual(self.finish_upload(connection, b"wrong")["status"], "ERROR")
        self.assertFalse((self.upload_dir / "bad.bin").exists())
        self.assert_no_partial_files()
        self.begin_upload(connection, "good.bin", b"correct")
        self.assertEqual(self.finish_upload(connection, b"correct")["status"], "SUCCESS")

    def test_download_waiting_for_ready_times_out(self):
        (self.upload_dir / "data.bin").write_bytes(b"data")
        connection = self.connect()
        server.send_json(connection, {"action": "DOWNLOAD", "filename": "data.bin"})
        self.assertEqual(server.recv_json(connection)["status"], "READY")
        self.assertEqual(connection.recv(1), b"")
        self.wait_until(lambda: self.manager.active_count == 0)
        self.assert_server_responds()

    def test_download_waiting_for_final_confirmation_times_out(self):
        data = b"data"
        (self.upload_dir / "data.bin").write_bytes(data)
        connection = self.connect()
        server.send_json(connection, {"action": "DOWNLOAD", "filename": "data.bin"})
        server.recv_json(connection)
        server.send_json(connection, {"status": "READY"})
        self.assertEqual(server.recv_exact(connection, len(data)), data)
        self.assertEqual(connection.recv(1), b"")
        self.wait_until(lambda: self.manager.active_count == 0)

    def test_download_disconnect_preserves_source_and_other_clients(self):
        data = b"X" * (1024 * 1024)
        (self.upload_dir / "data.bin").write_bytes(data)
        connection = self.connect()
        server.send_json(connection, {"action": "DOWNLOAD", "filename": "data.bin"})
        server.recv_json(connection)
        server.send_json(connection, {"status": "READY"})
        connection.close()
        self.wait_until(lambda: self.manager.active_count == 0)
        self.download(self.connect(), "data.bin", data)

    def test_stalled_download_reader_releases_worker(self):
        def small_send_buffer(connection, address):
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            server.handle_client(connection, address)

        self.manager.handler = small_send_buffer
        (self.upload_dir / "large.bin").write_bytes(b"X" * (8 * 1024 * 1024))
        connection = self.connect()
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        server.send_json(connection, {"action": "DOWNLOAD", "filename": "large.bin"})
        self.assertEqual(server.recv_json(connection)["status"], "READY")
        server.send_json(connection, {"status": "READY"})
        self.wait_until(lambda: self.manager.active_count == 0)
        self.assert_server_responds()

    def test_download_cancel_keeps_connection_synchronized(self):
        (self.upload_dir / "data.bin").write_bytes(b"data")
        connection = self.connect()
        server.send_json(connection, {"action": "DOWNLOAD", "filename": "data.bin"})
        self.assertEqual(server.recv_json(connection)["status"], "READY")
        server.send_json(connection, {"status": "ERROR", "message": "Cannot save file"})
        server.send_json(connection, {"action": "DOWNLOAD", "filename": "missing"})
        self.assertEqual(server.recv_json(connection)["status"], "ERROR")

    def test_invalid_requests_return_error_and_close(self):
        bodies = [b"{", b"[]", b'"text"', b'{"action":"UNKNOWN"}', b"{}"]
        for body in bodies:
            with self.subTest(body=body):
                connection = self.connect()
                connection.sendall(struct.pack("!I", len(body)) + body)
                self.assertEqual(server.recv_json(connection)["status"], "ERROR")
                self.assertEqual(connection.recv(1), b"")
        self.assert_server_responds()

    def test_invalid_frame_length_returns_error_and_closes(self):
        for size in (0, server.MAX_JSON_SIZE + 1):
            with self.subTest(size=size):
                connection = self.connect()
                connection.sendall(struct.pack("!I", size))
                self.assertEqual(server.recv_json(connection)["status"], "ERROR")
                self.assertEqual(connection.recv(1), b"")

    def test_invalid_upload_metadata_returns_error_and_closes(self):
        connection = self.connect()
        server.send_json(connection, {"action": "UPLOAD", "filename": "missing_size"})
        self.assertEqual(server.recv_json(connection)["status"], "ERROR")
        self.assertEqual(connection.recv(1), b"")
        self.assert_server_responds()

    def test_fragmented_json_and_pipelined_requests(self):
        connection = self.connect()
        body = json.dumps({"action": "DOWNLOAD", "filename": "missing"}).encode()
        frame = struct.pack("!I", len(body)) + body
        for part in (frame[:1], frame[1:3], frame[3:8], frame[8:]):
            connection.sendall(part)
        self.assertEqual(server.recv_json(connection)["status"], "ERROR")
        connection.sendall(frame + frame)
        self.assertEqual(server.recv_json(connection)["status"], "ERROR")
        self.assertEqual(server.recv_json(connection)["status"], "ERROR")

    def test_capacity_limit_rejects_excess_and_reuses_finished_slots(self):
        self.manager.max_clients = 2
        first = self.connect()
        second = self.connect()
        self.wait_until(lambda: self.manager.active_count == 2)
        rejected = self.connect()
        self.assertEqual(rejected.recv(1), b"")
        first.close()
        self.wait_until(lambda: self.manager.active_count == 1)
        self.assert_server_responds()
        second.close()

    def test_shutdown_interrupts_upload_and_cleans_partial_file(self):
        self.manager.client_timeout = 10.0
        connection = self.connect()
        self.begin_upload(connection, "shutdown.bin", b"X" * 10000)
        connection.sendall(b"partial")
        started = time.monotonic()
        self.manager.shutdown()
        self.accept_thread.join(timeout=3)
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(self.accept_thread.is_alive())
        self.assertEqual(self.manager.active_count, 0)
        self.assertFalse((self.upload_dir / "shutdown.bin").exists())
        self.assert_no_partial_files()
        try:
            self.assertEqual(connection.recv(1), b"")
        except ConnectionResetError:
            # Shutdown may discard unread body bytes and reset the peer on Windows.
            pass

    def test_unexpected_handler_exception_does_not_stop_listener(self):
        def broken_handler(connection, address):
            raise RuntimeError("Simulated handler failure")

        self.manager.handler = broken_handler
        with self.assertLogs("src.concurrency", level="ERROR"):
            connection = self.connect()
            self.assertEqual(connection.recv(1), b"")
            self.wait_until(lambda: self.manager.active_count == 0)
        self.manager.handler = server.handle_client
        self.assert_server_responds()


if __name__ == "__main__":
    unittest.main()
