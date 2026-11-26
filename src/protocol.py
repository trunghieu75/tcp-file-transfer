"""TV3 - Ham giao thuc dung chung (PROTOCOL.md v1.0).

Server (TV1), Client (TV2), kiem thu (TV4) va benchmark (TV5) dung lai cac
ham nay thay vi tu viet framing/checksum rieng.

Dinh dang thong diep dieu khien::

    [4 byte do dai JSON, unsigned big-endian][N byte JSON UTF-8], 1 <= N <= 64 KiB

Du lieu file la byte tho, gui dung ``size`` byte ngay sau READY.
"""

import hashlib
import json
import os
import struct
import tempfile
from pathlib import Path


PROTOCOL_VERSION = "1.0"
DEFAULT_PORT = 5000
MAX_JSON_SIZE = 64 * 1024
DEFAULT_CHUNK_SIZE = 4096
HEADER = struct.Struct("!I")
SHA256_HEX_LENGTH = 64
MAX_FILENAME_LENGTH = 255

ACTIONS = ("UPLOAD", "DOWNLOAD", "LIST")
STATUS_READY = "READY"
STATUS_SUCCESS = "SUCCESS"
STATUS_ERROR = "ERROR"

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_FORBIDDEN_FILENAME_CHARS = frozenset('/\\:\x00')
_WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


class ProtocolError(ValueError):
    """Thong diep sai dinh dang, qua dai hoac khong dung giao thuc."""


class ConnectionClosed(ConnectionError):
    """Ben kia dong ket noi truoc khi nhan du du lieu."""


class ChecksumMismatch(ProtocolError):
    """SHA-256 cua du lieu nhan duoc khac gia tri da cong bo."""

    def __init__(self, expected, actual):
        super().__init__(f"Checksum mismatch: expected {expected}, got {actual}")
        self.expected = expected
        self.actual = actual


# ---------------------------------------------------------------------------
# Nhan dung n byte

def recv_exact(sock, n, allow_eof=False):
    """Nhan chinh xac ``n`` byte tu ``sock``.

    TCP la luong byte: mot lan ``recv()`` co the tra ve it hon so byte yeu cau.
    Neu ``allow_eof`` va ket noi dong khi chua nhan byte nao thi nem
    ``EOFError`` (ket thuc binh thuong giua hai request); dong giua chung
    luon nem ``ConnectionClosed``.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError("n phai la so nguyen khong am")

    buffer = bytearray()
    while len(buffer) < n:
        chunk = sock.recv(n - len(buffer))
        if not chunk:
            if allow_eof and not buffer:
                raise EOFError("Ben kia da dong ket noi")
            raise ConnectionClosed(
                f"Mat ket noi: moi nhan {len(buffer)}/{n} byte"
            )
        buffer.extend(chunk)
    return bytes(buffer)


# ---------------------------------------------------------------------------
# JSON framing

def _reject_constant(name):
    raise ProtocolError(f"Gia tri JSON khong hop le: {name}")


def encode_json(message):
    """Ma hoa ``message`` (dict) thanh khung ``[4 byte do dai][JSON]``."""
    if not isinstance(message, dict):
        raise ProtocolError("Thong diep phai la JSON object")
    try:
        payload = json.dumps(
            message, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProtocolError(f"Khong the ma hoa JSON: {error}") from error
    if len(payload) > MAX_JSON_SIZE:
        raise ProtocolError(
            f"JSON dai {len(payload)} byte, vuot gioi han {MAX_JSON_SIZE}"
        )
    return HEADER.pack(len(payload)) + payload


def decode_json(payload):
    """Giai ma phan JSON (khong gom header) thanh dict."""
    try:
        message = json.loads(
            payload.decode("utf-8"), parse_constant=_reject_constant
        )
    except UnicodeDecodeError as error:
        raise ProtocolError("JSON khong phai UTF-8 hop le") from error
    except json.JSONDecodeError as error:
        raise ProtocolError(f"JSON sai dinh dang: {error.msg}") from error
    if not isinstance(message, dict):
        raise ProtocolError("Thong diep phai la JSON object")
    return message


def send_json(sock, message):
    """Gui mot thong diep dieu khien trong mot lan ``sendall``."""
    sock.sendall(encode_json(message))


def recv_json(sock, allow_eof=False):
    """Nhan mot thong diep dieu khien va tra ve dict.

    Do dai ngoai khoang ``1..MAX_JSON_SIZE`` bi tu choi truoc khi doc phan
    than, nen ben gui khong the bat ben nhan cap phat bo nho tuy y.
    """
    header = recv_exact(sock, HEADER.size, allow_eof=allow_eof)
    (length,) = HEADER.unpack(header)
    if length < 1 or length > MAX_JSON_SIZE:
        raise ProtocolError(f"Do dai JSON khong hop le: {length}")
    return decode_json(recv_exact(sock, length))


def error_message(message):
    """Tao phan hoi ``{"status":"ERROR","message":...}``."""
    return {"status": STATUS_ERROR, "message": str(message)}


# ---------------------------------------------------------------------------
# Kiem tra truong du lieu

def validate_chunk_size(chunk_size):
    """Tra ve ``chunk_size`` neu la so nguyen duong, nguoc lai nem ValueError."""
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
        raise ValueError("chunk_size phai la so nguyen duong")
    if chunk_size <= 0:
        raise ValueError("chunk_size phai la so nguyen duong")
    return chunk_size


def is_valid_size(size, max_size=None):
    """``size`` la so nguyen khong am (khong phai bool), khong vuot ``max_size``."""
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return False
    return max_size is None or size <= max_size


def is_valid_sha256(value):
    """Chuoi hex 64 ky tu (chap nhan ca chu hoa khi nhan)."""
    return (
        isinstance(value, str)
        and len(value) == SHA256_HEX_LENGTH
        and all(c in _HEX_DIGITS for c in value)
    )


def is_valid_filename(filename):
    """Chi chap nhan ten file don gian, khong chua duong dan.

    Tu choi ``/``, ``\\``, ``:`` (o dia/ADS tren Windows), ky tu dieu khien,
    ``.``/``..``, ten rong hoac dai qua ``MAX_FILENAME_LENGTH`` byte UTF-8,
    ten ket thuc bang dau cham/khoang trang (Windows tu cat bo) va ten
    thiet bi danh rieng cua Windows (CON, NUL, COM1...).
    """
    if not isinstance(filename, str) or not filename:
        return False
    if filename in (".", "..") or filename != filename.strip():
        return False
    if filename.endswith("."):
        return False
    if any(c in _FORBIDDEN_FILENAME_CHARS or ord(c) < 32 for c in filename):
        return False
    if filename.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        return False
    try:
        encoded = filename.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= MAX_FILENAME_LENGTH


# ---------------------------------------------------------------------------
# SHA-256

def sha256_bytes(data):
    """SHA-256 cua mot khoi byte, hex chu thuong."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, chunk_size=DEFAULT_CHUNK_SIZE):
    """SHA-256 cua toan bo file, doc theo chunk de khong nap het vao RAM."""
    validate_chunk_size(chunk_size)
    hasher = hashlib.sha256()
    with open(path, "rb") as file:
        while True:
            data = file.read(chunk_size)
            if not data:
                break
            hasher.update(data)
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Truyen file theo chunk

def send_file(sock, path, size=None, chunk_size=DEFAULT_CHUNK_SIZE, progress=None):
    """Gui dung ``size`` byte dau cua file ``path`` qua ``sock``.

    ``size`` mac dinh la kich thuoc file hien tai. Neu file ngan hon
    ``size`` (bi sua trong luc gui) thi nem ``OSError``; ben nhan se thay
    thieu byte. ``progress(sent, total)`` duoc goi sau moi chunk.
    Tra ve SHA-256 cua dung phan du lieu da gui.
    """
    validate_chunk_size(chunk_size)
    if size is None:
        size = os.path.getsize(path)
    if not is_valid_size(size):
        raise ValueError("size phai la so nguyen khong am")

    hasher = hashlib.sha256()
    sent = 0
    with open(path, "rb") as file:
        while sent < size:
            data = file.read(min(chunk_size, size - sent))
            if not data:
                raise OSError(f"File ngan hon kich thuoc cong bo: {sent}/{size}")
            sock.sendall(data)
            hasher.update(data)
            sent += len(data)
            if progress is not None:
                progress(sent, size)
    if size == 0 and progress is not None:
        progress(0, 0)
    return hasher.hexdigest()


def recv_file(sock, output, size, chunk_size=DEFAULT_CHUNK_SIZE, progress=None):
    """Nhan dung ``size`` byte tu ``sock`` va ghi vao file object ``output``.

    Khong doc qua ``size`` byte, nen thong diep JSON gui ngay sau du lieu
    van con nguyen trong socket. Tra ve SHA-256 cua du lieu da nhan.
    """
    validate_chunk_size(chunk_size)
    if not is_valid_size(size):
        raise ValueError("size phai la so nguyen khong am")

    hasher = hashlib.sha256()
    received = 0
    while received < size:
        data = sock.recv(min(chunk_size, size - received))
        if not data:
            raise ConnectionClosed(f"Mat ket noi: moi nhan {received}/{size} byte")
        output.write(data)
        hasher.update(data)
        received += len(data)
        if progress is not None:
            progress(received, size)
    if size == 0 and progress is not None:
        progress(0, 0)
    return hasher.hexdigest()


def _publish_no_overwrite(temp_path, dest_path):
    """Doi ten file tam thanh ten chinh thuc, khong ghi de file da co."""
    try:
        os.link(temp_path, dest_path)
    except FileExistsError:
        raise
    except (AttributeError, NotImplementedError, OSError):
        # Filesystem khong ho tro hard link. Tren Windows os.rename
        # cung bao FileExistsError neu dich da ton tai.
        if os.name != "nt":
            raise
        os.rename(temp_path, dest_path)


def recv_verified_file(sock, dest_path, size, expected_sha256,
                       chunk_size=DEFAULT_CHUNK_SIZE, progress=None):
    """Nhan file vao file tam, kiem tra SHA-256, roi moi cong bo ``dest_path``.

    - Checksum sai: xoa file tam, nem ``ChecksumMismatch``.
    - Mat ket noi/timeout: xoa file tam, nem lai loi goc.
    - ``dest_path`` da ton tai: nem ``FileExistsError`` (khong ghi de).

    Khong bao gio de lai file hong o ``dest_path``. Ham khong tu gui JSON;
    nguoi goi quyet dinh phan hoi SUCCESS/ERROR theo PROTOCOL.md.
    Tra ve SHA-256 (hex chu thuong) cua file da luu.
    """
    if not is_valid_sha256(expected_sha256):
        raise ValueError("expected_sha256 phai la chuoi hex 64 ky tu")
    dest_path = Path(dest_path)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=dest_path.parent,
            prefix=".recv_",
            suffix=".part",
            delete=False,
        ) as output:
            temp_path = Path(output.name)
            actual = recv_file(sock, output, size, chunk_size, progress)

        if actual != expected_sha256.lower():
            raise ChecksumMismatch(expected_sha256.lower(), actual)
        _publish_no_overwrite(temp_path, dest_path)
        return actual
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
