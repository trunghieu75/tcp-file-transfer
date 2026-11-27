"""TV5 - Chuong trinh do hieu nang truyen file (PROTOCOL.md v1.0).

Benchmark dong vai mot Client tu dong: dung lai cac ham trong ``src/protocol.py``
cua TV3, gui dung thu tu thong diep UPLOAD/DOWNLOAD va ten truong JSON nhu
PROTOCOL.md. Benchmark chi thay doi tham so thuc nghiem (kich thuoc file,
chunk size phia Client, so Client dong thoi), khong sua giao thuc.

Thi nghiem:
    TN1 - kich thuoc file   : 1 / 10 / 100 MiB, chunk 4 KiB, 1 Client
    TN2 - chunk size Client : 1 / 4 / 64 KiB, file 10 MiB, 1 Client
    TN3 - so Client dong thoi: 1 / 2 / 4, file 10 MiB, chunk 4 KiB

Chay tu thu muc goc repository (mac dinh tu khoi dong server tren loopback):
    python -m benchmarks.bench --experiments tn1 tn2 tn3 --repeat 5

Do toi mot server dang chay san (vi du may khac trong LAN):
    python -m benchmarks.bench --host 192.168.1.10 --port 5000
"""

import argparse
import csv
import datetime as dt
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from src import protocol


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "benchmarks" / "data"
RESULTS_DIR = ROOT / "results"

KiB = 1024
MiB = 1024 * 1024

# Gia tri de xuat o PROTOCOL.md muc 8 (TN1-TN3).
TN1_SIZES = [1 * MiB, 10 * MiB, 100 * MiB]
TN2_CHUNKS = [1 * KiB, 4 * KiB, 64 * KiB]
TN3_CLIENTS = [1, 2, 4]
DEFAULT_SIZE = 10 * MiB
DEFAULT_CHUNK = protocol.DEFAULT_CHUNK_SIZE

# Server hien tai (TV1) doc/gui co dinh 4096 byte moi lan va gioi han 100 MiB.
SERVER_CHUNK_SIZE = 4096
SERVER_MAX_FILE_SIZE = 100 * MiB

RAW_FIELDS = [
    "run_id", "timestamp", "experiment", "operation", "file_size",
    "chunk_size", "server_chunk_size", "clients", "client_id", "repeat",
    "bytes", "elapsed_s", "throughput_mib_s", "success", "checksum_ok", "error",
]
SUMMARY_FIELDS = [
    "experiment", "operation", "file_size", "chunk_size", "clients", "metric",
    "n", "success_rate", "mean", "std", "min", "median", "max",
]


class TransferError(RuntimeError):
    """Server tra ERROR hoac phan hoi khong dung giao thuc."""


# ---------------------------------------------------------------------------
# Du lieu thu nghiem

def ensure_test_file(size, data_dir=DATA_DIR):
    """Tao (mot lan) file du lieu ngau nhien ``size`` byte, tra ve (path, sha256)."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"random_{size}.bin"
    if not path.is_file() or path.stat().st_size != size:
        with open(path, "wb") as file:
            remaining = size
            while remaining > 0:
                block = os.urandom(min(4 * MiB, remaining))
                file.write(block)
                remaining -= len(block)
    return path, protocol.sha256_file(path, chunk_size=MiB)


def unique_name(size):
    """Server khong cho ghi de, nen moi lan upload dung mot ten moi."""
    return f"bench_{size}_{uuid.uuid4().hex[:12]}.bin"


# ---------------------------------------------------------------------------
# Hai thao tac duoc do

def _expect(response, status):
    if not isinstance(response, dict) or response.get("status") != status:
        raise TransferError(f"Mong doi {status}, nhan {response!r}")


def upload(sock, path, remote_name, size, sha256, chunk_size):
    """UPLOAD theo PROTOCOL.md muc 3. Tra ve so byte da gui.

    SHA-256 cua file nguon duoc tinh truoc (ngoai thoi gian do).
    """
    protocol.send_json(sock, {
        "action": "UPLOAD",
        "filename": remote_name,
        "size": size,
        "sha256": sha256,
    })
    _expect(protocol.recv_json(sock), protocol.STATUS_READY)
    sent_hash = protocol.send_file(sock, path, size, chunk_size)
    if sent_hash != sha256:
        raise TransferError("File nguon thay doi trong luc gui")
    _expect(protocol.recv_json(sock), protocol.STATUS_SUCCESS)
    return size


def download(sock, remote_name, dest_dir, chunk_size):
    """DOWNLOAD theo PROTOCOL.md muc 4. Tra ve (so byte, sha256).

    File nhan duoc ghi vao file tam va chi cong bo khi SHA-256 khop
    (``recv_verified_file``); thoi gian do gom ca buoc kiem tra nay.
    """
    protocol.send_json(sock, {"action": "DOWNLOAD", "filename": remote_name})
    meta = protocol.recv_json(sock)
    _expect(meta, protocol.STATUS_READY)
    size, sha256 = meta.get("size"), meta.get("sha256")
    if not protocol.is_valid_size(size) or not protocol.is_valid_sha256(sha256):
        raise TransferError(f"Metadata DOWNLOAD khong hop le: {meta!r}")
    dest = Path(dest_dir) / f"{uuid.uuid4().hex}.bin"
    protocol.send_json(sock, {"status": protocol.STATUS_READY})
    try:
        actual = protocol.recv_verified_file(sock, dest, size, sha256, chunk_size)
    except protocol.ChecksumMismatch:
        protocol.send_json(sock, protocol.error_message("Checksum mismatch"))
        raise
    protocol.send_json(sock, {
        "status": protocol.STATUS_SUCCESS,
        "message": "Download completed",
    })
    dest.unlink(missing_ok=True)
    return size, actual


# ---------------------------------------------------------------------------
# Mot luot do

def _record(experiment, operation, size, chunk_size, clients, client_id,
            repeat, elapsed, success, checksum_ok, error=""):
    throughput = size / MiB / elapsed if success and elapsed > 0 else 0.0
    return {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "experiment": experiment,
        "operation": operation,
        "file_size": size,
        "chunk_size": chunk_size,
        "server_chunk_size": SERVER_CHUNK_SIZE,
        "clients": clients,
        "client_id": client_id,
        "repeat": repeat,
        "bytes": size if success else 0,
        "elapsed_s": round(elapsed, 6),
        "throughput_mib_s": round(throughput, 3),
        "success": int(success),
        "checksum_ok": int(checksum_ok),
        "error": error,
    }


def transfer_once(address, path, size, sha256, chunk_size, work_dir,
                  timeout=60.0, barrier=None):
    """Upload roi download cung mot file tren mot ket noi TCP.

    Thoi gian moi thao tac tinh tu luc gui JSON yeu cau den luc nhan/gui xong
    JSON ket qua (khong gom thoi gian ket noi TCP). Tra ve dict theo thao tac.
    """
    remote_name = unique_name(size)
    results = {}
    with socket.create_connection(address, timeout=timeout) as sock:
        if barrier is not None:
            barrier.wait()

        start = time.perf_counter()
        try:
            upload(sock, path, remote_name, size, sha256, chunk_size)
            results["upload"] = (time.perf_counter() - start, True, True, "")
        except (OSError, ValueError, TransferError) as error:
            results["upload"] = (time.perf_counter() - start, False, False,
                                 f"{type(error).__name__}: {error}")
            results["download"] = (0.0, False, False, "upload failed")
            return results

        start = time.perf_counter()
        try:
            _, actual = download(sock, remote_name, work_dir, chunk_size)
            ok = actual == sha256
            results["download"] = (time.perf_counter() - start, ok, ok,
                                   "" if ok else "sha256 khac file goc")
        except protocol.ChecksumMismatch as error:
            results["download"] = (time.perf_counter() - start, False, False, str(error))
        except (OSError, ValueError, TransferError) as error:
            results["download"] = (time.perf_counter() - start, False, False,
                                   f"{type(error).__name__}: {error}")
    return results


def run_trial(address, experiment, size, chunk_size, clients, repeat,
              work_dir, data_dir=DATA_DIR, timeout=60.0):
    """Mot luot do voi ``clients`` Client chay dong thoi (thread + barrier).

    Ngoai dong cho tung Client, them dong ``aggregate`` (ca khi 1 Client, de so
    sanh TN3): tong byte upload + download cua moi Client / (thoi diem Client
    cuoi xong - thoi diem bat dau chung).
    """
    path, sha256 = ensure_test_file(size, data_dir)
    barrier = threading.Barrier(clients + 1)
    outputs = [None] * clients
    errors = [None] * clients

    def worker(index):
        try:
            outputs[index] = transfer_once(address, path, size, sha256,
                                           chunk_size, work_dir, timeout, barrier)
        except BaseException as error:  # ket noi that bai, barrier vo...
            errors[index] = f"{type(error).__name__}: {error}"
            barrier.abort()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(clients)]
    for thread in threads:
        thread.start()
    wall_start = time.perf_counter()
    try:
        barrier.wait()
        wall_start = time.perf_counter()
    except threading.BrokenBarrierError:
        pass
    for thread in threads:
        thread.join()
    wall = time.perf_counter() - wall_start

    rows = []
    for index in range(clients):
        result = outputs[index] or {
            op: (0.0, False, False, errors[index] or "unknown")
            for op in ("upload", "download")
        }
        for op in ("upload", "download"):
            elapsed, success, checksum_ok, error = result[op]
            rows.append(_record(experiment, op, size, chunk_size, clients,
                                index, repeat, elapsed, success, checksum_ok, error))

    # Tung Client chay upload roi download noi tiep, nen tong hop ca hai.
    done = [row for row in rows if row["success"]]
    total_bytes = sum(row["bytes"] for row in done)
    all_ok = len(done) == len(rows)
    agg = _record(experiment, "aggregate", size, chunk_size, clients, -1,
                  repeat, wall, all_ok, all_ok)
    agg["bytes"] = total_bytes
    agg["throughput_mib_s"] = round(total_bytes / MiB / wall, 3) if wall > 0 else 0.0
    rows.append(agg)
    return rows


# ---------------------------------------------------------------------------
# Ke hoach thi nghiem

def build_plan(experiments, sizes=None, chunks=None, clients=None,
               default_size=DEFAULT_SIZE):
    """Tra ve danh sach (experiment, size, chunk, clients)."""
    plan = []
    if "tn1" in experiments:
        plan += [("TN1", s, DEFAULT_CHUNK, 1) for s in (sizes or TN1_SIZES)]
    if "tn2" in experiments:
        plan += [("TN2", default_size, c, 1) for c in (chunks or TN2_CHUNKS)]
    if "tn3" in experiments:
        plan += [("TN3", default_size, DEFAULT_CHUNK, n) for n in (clients or TN3_CLIENTS)]
    for _, size, chunk, n in plan:
        if size > SERVER_MAX_FILE_SIZE:
            raise ValueError(f"File {size} byte vuot gioi han server {SERVER_MAX_FILE_SIZE}")
        protocol.validate_chunk_size(chunk)
        if n < 1:
            raise ValueError("So Client phai >= 1")
    return plan


def run_plan(address, plan, repeat, warmup, work_dir, data_dir=DATA_DIR,
             timeout=60.0, run_id="", log=print):
    rows = []
    for experiment, size, chunk, clients in plan:
        for i in range(warmup + repeat):
            trial = run_trial(address, experiment, size, chunk, clients,
                              i - warmup, work_dir, data_dir, timeout)
            if i < warmup:
                continue  # luot khoi dong: bo qua (cache dia, cap phat socket...)
            for row in trial:
                row["run_id"] = run_id
            rows += trial
            ok = sum(r["success"] for r in trial if r["operation"] != "aggregate")
            n = sum(1 for r in trial if r["operation"] != "aggregate")
            log(f"{experiment} size={size // KiB}KiB chunk={chunk}B clients={clients} "
                f"lan={i - warmup + 1}/{repeat} thanh_cong={ok}/{n} "
                + " ".join(f"{r['operation']}={r['throughput_mib_s']}MiB/s"
                           for r in trial if r["client_id"] in (0, -1)))
    return rows


# ---------------------------------------------------------------------------
# Thong ke

def summarize(rows):
    """Thong ke theo nhom (experiment, operation, size, chunk, clients).

    Thoi gian va throughput chi tinh tren cac luot thanh cong.
    """
    groups = {}
    for row in rows:
        key = (row["experiment"], row["operation"], int(row["file_size"]),
               int(row["chunk_size"]), int(row["clients"]))
        groups.setdefault(key, []).append(row)

    summary = []
    for key in sorted(groups):
        items = groups[key]
        ok = [r for r in items if int(r["success"])]
        rate = len(ok) / len(items)
        for metric, field in (("elapsed_s", "elapsed_s"),
                              ("throughput_mib_s", "throughput_mib_s")):
            values = [float(r[field]) for r in ok]
            entry = dict(zip(("experiment", "operation", "file_size",
                              "chunk_size", "clients"), key))
            entry.update(metric=metric, n=len(items), success_rate=round(rate, 4))
            if values:
                entry.update(
                    mean=round(statistics.fmean(values), 4),
                    std=round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
                    min=round(min(values), 4),
                    median=round(statistics.median(values), 4),
                    max=round(max(values), 4),
                )
            else:
                entry.update(mean="", std="", min="", median="", max="")
            summary.append(entry)
    return summary


def write_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


# ---------------------------------------------------------------------------
# Moi truong do va server cuc bo

def _git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def environment(address, local_server, args):
    cpu = platform.processor() or platform.machine()
    return {
        "date": dt.datetime.now().isoformat(timespec="seconds"),
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu": cpu,
        "cpu_count": os.cpu_count(),
        "network": "loopback (server cuc bo)" if local_server else "server ngoai",
        "server_address": f"{address[0]}:{address[1]}",
        "server_chunk_size": SERVER_CHUNK_SIZE,
        "repeat": args.repeat,
        "warmup": args.warmup,
        "git_commit": _git_commit(),
        "note": "Client benchmark chay bang thread trong mot tien trinh; "
                "server la tien trinh rieng (python -m src.server).",
    }


def _free_port(host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


class LocalServer:
    """Khoi dong ``python -m src.server`` cua TV1/TV4 trong tien trinh rieng."""

    def __init__(self, upload_dir, log_path, host="127.0.0.1", max_clients=32,
                 timeout=60.0):
        self.host = host
        self.port = _free_port(host)
        self.upload_dir = Path(upload_dir)
        self.log_path = Path(log_path)
        self.max_clients = max_clients
        self.timeout = timeout
        self.process = None
        self._log = None

    @property
    def address(self):
        return (self.host, self.port)

    def __enter__(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "w", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "src.server", "--host", self.host,
             "--port", str(self.port), "--upload-dir", str(self.upload_dir),
             "--max-clients", str(self.max_clients), "--timeout", str(self.timeout)],
            cwd=ROOT, stdout=self._log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Server dung som, xem log {self.log_path}")
            try:
                socket.create_connection(self.address, timeout=0.5).close()
                return self
            except OSError:
                time.sleep(0.1)
        self.__exit__(None, None, None)
        raise RuntimeError("Server khong san sang sau 10 giay")

    def __exit__(self, *exc):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self._log is not None:
            self._log.close()


# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="TV5 benchmark TCP file transfer")
    parser.add_argument("--experiments", nargs="+", default=["tn1", "tn2", "tn3"],
                        choices=["tn1", "tn2", "tn3"])
    parser.add_argument("--repeat", type=int, default=5, help="so lan do moi cau hinh")
    parser.add_argument("--warmup", type=int, default=1, help="so luot khoi dong bo qua")
    parser.add_argument("--sizes", type=int, nargs="+", help="TN1: kich thuoc file (byte)")
    parser.add_argument("--chunks", type=int, nargs="+", help="TN2: chunk size Client (byte)")
    parser.add_argument("--clients", type=int, nargs="+", help="TN3: so Client dong thoi")
    parser.add_argument("--default-size", type=int, default=DEFAULT_SIZE,
                        help="kich thuoc file cho TN2/TN3 (byte)")
    parser.add_argument("--host", help="server co san; bo trong de tu chay server loopback")
    parser.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--tag", default="", help="hau to ten file ket qua, vd 'lan'")
    args = parser.parse_args(argv)
    if args.repeat < 1 or args.warmup < 0:
        parser.error("repeat >= 1, warmup >= 0")
    return args


def main(argv=None):
    args = parse_args(argv)
    plan = build_plan(args.experiments, args.sizes, args.chunks, args.clients,
                      args.default_size)
    suffix = f"_{args.tag}" if args.tag else ""
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    max_clients = max(n for *_, n in plan) + 4

    with tempfile.TemporaryDirectory(prefix="tv5_bench_") as tmp:
        tmp = Path(tmp)
        work_dir = tmp / "downloads"
        work_dir.mkdir()
        if args.host:
            address = (args.host, args.port)
            env = environment(address, False, args)
            rows = run_plan(address, plan, args.repeat, args.warmup, work_dir,
                            args.data_dir, args.timeout, run_id)
        else:
            log_path = args.out_dir / f"bench_server{suffix}.log"
            with LocalServer(tmp / "uploads", log_path, max_clients=max_clients,
                             timeout=args.timeout) as server:
                env = environment(server.address, True, args)
                rows = run_plan(server.address, plan, args.repeat, args.warmup,
                                work_dir, args.data_dir, args.timeout, run_id)

    raw_path = args.out_dir / f"bench_raw{suffix}.csv"
    summary_path = args.out_dir / f"bench_summary{suffix}.csv"
    env_path = args.out_dir / f"bench_env{suffix}.json"
    write_csv(raw_path, rows, RAW_FIELDS)
    write_csv(summary_path, summarize(rows), SUMMARY_FIELDS)
    env_path.write_text(json.dumps(env, indent=2, ensure_ascii=False), encoding="utf-8")

    failures = sum(1 for r in rows if not int(r["success"]))
    print(f"Da ghi {raw_path}, {summary_path}, {env_path}; that bai: {failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
