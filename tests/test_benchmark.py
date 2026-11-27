"""TV5 - Kiem thu chuong trinh benchmark (benchmarks/bench.py, benchmarks/plot.py).

Chay tu thu muc goc repository:
    python -m unittest discover -s tests -p test_benchmark.py -v
"""

import hashlib
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from benchmarks import bench, plot
from src import protocol
from src import server
from src.concurrency import ConnectionManager


class InProcessServerMixin:
    """Server that cua TV1/TV4 chay trong thread, upload dir tam."""

    def start_server(self, max_clients=8):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.upload_dir = self.tmp / "uploads"
        self.work_dir = self.tmp / "downloads"
        self.data_dir = self.tmp / "data"
        self.work_dir.mkdir()
        previous = server.UPLOAD_DIR
        self.addCleanup(setattr, server, "UPLOAD_DIR", previous)
        server.UPLOAD_DIR = self.upload_dir

        manager = ConnectionManager(server.handle_client, client_timeout=5.0,
                                    max_clients=max_clients)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        self.address = listener.getsockname()
        thread = threading.Thread(target=manager.serve_forever, args=(listener,))
        thread.start()

        def stop():
            manager.shutdown()
            thread.join(5)
            listener.close()

        self.addCleanup(stop)


class TransferTests(InProcessServerMixin, unittest.TestCase):
    def setUp(self):
        self.start_server()

    def test_test_file_is_cached_and_hash_matches(self):
        path, digest = bench.ensure_test_file(10_000, self.data_dir)
        self.assertEqual(path.stat().st_size, 10_000)
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        mtime = path.stat().st_mtime_ns
        bench.ensure_test_file(10_000, self.data_dir)
        self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_upload_download_roundtrip_matches_source(self):
        for size in (0, 1, 4095, 4097, 70_000):
            for chunk in (1024, 4096, 65536):
                with self.subTest(size=size, chunk=chunk):
                    path, digest = bench.ensure_test_file(size, self.data_dir)
                    result = bench.transfer_once(self.address, path, size, digest,
                                                 chunk, self.work_dir, timeout=5)
                    for op in ("upload", "download"):
                        _, success, checksum_ok, error = result[op]
                        self.assertTrue(success, error)
                        self.assertTrue(checksum_ok)
        # File tai ve duoc xoa sau moi luot, khong de lai file tam.
        self.assertEqual(list(self.work_dir.iterdir()), [])

    def test_uploaded_file_on_server_is_identical(self):
        path, digest = bench.ensure_test_file(50_000, self.data_dir)
        name = bench.unique_name(50_000)
        with socket.create_connection(self.address, timeout=5) as sock:
            bench.upload(sock, path, name, 50_000, digest, 4096)
        stored = self.upload_dir / name
        self.assertEqual(hashlib.sha256(stored.read_bytes()).hexdigest(), digest)

    def test_wrong_checksum_is_reported_as_failure(self):
        path, digest = bench.ensure_test_file(5_000, self.data_dir)
        wrong = "0" * 64
        result = bench.transfer_once(self.address, path, 5_000, wrong, 4096,
                                     self.work_dir, timeout=5)
        _, success, _, error = result["upload"]
        self.assertFalse(success)
        self.assertIn("TransferError", error)

    def test_concurrent_trial_rows_and_aggregate(self):
        rows = bench.run_trial(self.address, "TN3", 30_000, 4096, 3, 0,
                               self.work_dir, self.data_dir, timeout=5)
        per_client = [r for r in rows if r["operation"] != "aggregate"]
        aggregate = [r for r in rows if r["operation"] == "aggregate"]
        self.assertEqual(len(per_client), 6)  # 3 Client x (upload, download)
        self.assertTrue(all(r["success"] == 1 for r in per_client))
        self.assertEqual(len(aggregate), 1)
        self.assertEqual(aggregate[0]["bytes"], 6 * 30_000)
        self.assertGreater(aggregate[0]["throughput_mib_s"], 0)

    def test_unreachable_server_gives_failed_rows_not_crash(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            closed_port = sock.getsockname()[1]
        rows = bench.run_trial(("127.0.0.1", closed_port), "TN3", 1000, 4096, 2, 0,
                               self.work_dir, self.data_dir, timeout=1)
        self.assertTrue(all(r["success"] == 0 for r in rows))


class PlanAndSummaryTests(unittest.TestCase):
    def test_default_plan_matches_protocol_md(self):
        plan = bench.build_plan(["tn1", "tn2", "tn3"])
        self.assertEqual([p[1] for p in plan if p[0] == "TN1"],
                         [1 << 20, 10 << 20, 100 << 20])
        self.assertEqual([p[2] for p in plan if p[0] == "TN2"], [1024, 4096, 65536])
        self.assertEqual([p[3] for p in plan if p[0] == "TN3"], [1, 2, 4])

    def test_plan_rejects_invalid_parameters(self):
        with self.assertRaises(ValueError):
            bench.build_plan(["tn1"], sizes=[bench.SERVER_MAX_FILE_SIZE + 1])
        with self.assertRaises(ValueError):
            bench.build_plan(["tn2"], chunks=[0])
        with self.assertRaises(ValueError):
            bench.build_plan(["tn3"], clients=[0])

    def test_summary_statistics_ignore_failed_runs(self):
        def row(elapsed, success):
            return {"experiment": "TN1", "operation": "upload", "file_size": 100,
                    "chunk_size": 4096, "clients": 1, "elapsed_s": elapsed,
                    "throughput_mib_s": 1.0 / elapsed if success else 0.0,
                    "success": success}

        summary = bench.summarize([row(1.0, 1), row(3.0, 1), row(9.0, 0), row(2.0, 1)])
        elapsed = next(s for s in summary if s["metric"] == "elapsed_s")
        self.assertEqual(elapsed["n"], 4)
        self.assertEqual(elapsed["success_rate"], 0.75)
        self.assertEqual(elapsed["mean"], 2.0)
        self.assertEqual(elapsed["median"], 2.0)
        self.assertEqual(elapsed["std"], 1.0)

    def test_csv_roundtrip_and_plots(self):
        rows = []
        for exp, op, size, chunk, clients in [
            ("TN1", "upload", 1 << 20, 4096, 1), ("TN1", "download", 1 << 20, 4096, 1),
            ("TN2", "upload", 1 << 20, 1024, 1), ("TN3", "aggregate", 1 << 20, 4096, 2),
        ]:
            for elapsed in (0.5, 0.7):
                rows.append({"experiment": exp, "operation": op, "file_size": size,
                             "chunk_size": chunk, "clients": clients,
                             "elapsed_s": elapsed, "throughput_mib_s": 1 / elapsed,
                             "success": 1})
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bench.write_csv(tmp / "s.csv", bench.summarize(rows), bench.SUMMARY_FIELDS)
            summary = bench.read_csv(tmp / "s.csv")
            made = plot.make_plots(summary, tmp / "plots")
            names = {p.name for p in made}
            self.assertIn("tn1_throughput.png", names)
            self.assertIn("tn2_throughput.png", names)
            self.assertIn("tn3_throughput.png", names)
            self.assertTrue(all(p.stat().st_size > 0 for p in made))
            table = plot.markdown_table(summary)
            self.assertIn("| TN1 | upload | 1 MiB | 4 KiB | 1 | 2 | 100% |", table)


if __name__ == "__main__":
    unittest.main()
