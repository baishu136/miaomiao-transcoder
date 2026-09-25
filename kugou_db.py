# -*- coding: utf-8 -*-
"""
酷狗 KGMusicV3.db 密钥数据库解密（SQLCipher 变体）。

算法对齐 kgg-dec / Kugo-Music-Converter：
  * 默认 master key（16 字节），可用环境变量 KGG_DB_MASTER_KEY(hex) 覆盖
  * 每页 0x400 字节，页密钥 = MD5(master + LE32(page) + LE32(0x546C4173))
  * 页 IV     = MD5(由 page+1 递推的 16 字节)
  * 页 1 特殊处理（头部校验 + 重排 + 恢复 SQLite 头）
  * 解密后查 ShareFileItems 表：EncryptionKeyId -> EncryptionKey
"""
import hashlib
import os
import sqlite3
import struct
import ctypes
from ctypes import wintypes

PAGE_SIZE = 0x400
SQLITE_HEADER = b"SQLite format 3\x00"
DEFAULT_MASTER_KEY = bytes([
    0x1D, 0x61, 0x31, 0x45, 0xB2, 0x47, 0xBF, 0x7F,
    0x3D, 0x18, 0x96, 0x72, 0x14, 0x4F, 0xE4, 0xBF,
])


class DBError(Exception):
    """酷狗数据库解密异常。"""


# ---------- AES-CBC via Windows BCrypt ----------

class _BcryptCBC:
    def __init__(self):
        self._bcrypt = ctypes.WinDLL("bcrypt")
        bc = self._bcrypt
        bc.BCryptOpenAlgorithmProvider.argtypes = [
            ctypes.POINTER(wintypes.HANDLE), ctypes.c_wchar_p,
            ctypes.c_wchar_p, ctypes.c_ulong]
        bc.BCryptSetProperty.argtypes = [
            wintypes.HANDLE, ctypes.c_wchar_p, ctypes.c_void_p,
            ctypes.c_ulong, ctypes.c_ulong]
        bc.BCryptGenerateSymmetricKey.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p,
            ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        bc.BCryptDecrypt.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
        bc.BCryptDestroyKey.argtypes = [wintypes.HANDLE]
        bc.BCryptCloseAlgorithmProvider.argtypes = [wintypes.HANDLE, ctypes.c_ulong]

    def decrypt_cbc(self, key: bytes, iv: bytes, data: bytes) -> bytes:
        if len(key) not in (16, 24, 32):
            raise DBError(f"AES 密钥长度非法: {len(key)}")
        if len(iv) != 16:
            raise DBError("AES-CBC IV 必须为 16 字节")
        if not data or len(data) % 16 != 0:
            raise DBError("AES-CBC 输入必须是 16 的倍数")
        bc = self._bcrypt
        h_alg = wintypes.HANDLE()
        status = bc.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0)
        if status:
            raise DBError(f"BCryptOpenAlgorithmProvider 失败: {status:#x}")
        h_key = wintypes.HANDLE()
        try:
            mode = ctypes.create_unicode_buffer("ChainingModeCBC")
            status = bc.BCryptSetProperty(
                h_alg, "ChainingMode", mode,
                len(mode) * ctypes.sizeof(ctypes.c_wchar), 0)
            if status:
                raise DBError(f"BCryptSetProperty 失败: {status:#x}")
            key_buf = ctypes.create_string_buffer(key, len(key))
            status = bc.BCryptGenerateSymmetricKey(
                h_alg, ctypes.byref(h_key), None, 0, key_buf, len(key), 0)
            if status:
                raise DBError(f"BCryptGenerateSymmetricKey 失败: {status:#x}")
            in_buf = ctypes.create_string_buffer(data, len(data))
            out_buf = ctypes.create_string_buffer(len(data))
            iv_buf = ctypes.create_string_buffer(iv, 16)
            done = ctypes.c_ulong(0)
            status = bc.BCryptDecrypt(
                h_key, in_buf, len(data), None, iv_buf, 16,
                out_buf, len(data), ctypes.byref(done), 0)
            if status:
                raise DBError(f"BCrypt AES-CBC 解密失败: {status:#x}")
            return out_buf.raw[:done.value]
        finally:
            if h_key.value:
                bc.BCryptDestroyKey(h_key)
            bc.BCryptCloseAlgorithmProvider(h_alg, 0)


_AES = None


def _aes() -> _BcryptCBC:
    global _AES
    if _AES is None:
        _AES = _BcryptCBC()
    return _AES


# ---------- 页密钥/IV 派生 ----------

def _next_page_iv(seed: int) -> int:
    seed &= 0xFFFFFFFF
    left = (seed * 0x9EF4) & 0xFFFFFFFF
    right = ((seed // 0xCE26) * 0x7FFFFF07) & 0xFFFFFFFF
    value = (left - right) & 0xFFFFFFFF
    if value & 0x80000000 == 0:
        return value
    return (value + 0x7FFFFF07) & 0xFFFFFFFF


def _derive_page_aes_key(page_no: int, master: bytes) -> bytes:
    buf = master + struct.pack("<I", page_no & 0xFFFFFFFF) + struct.pack("<I", 0x546C4173)
    return hashlib.md5(buf).digest()


def _derive_page_aes_iv(page_no: int) -> bytes:
    seed = (page_no + 1) & 0xFFFFFFFF
    iv = bytearray(16)
    for i in range(0, 16, 4):
        seed = _next_page_iv(seed)
        iv[i:i + 4] = struct.pack("<I", seed)
    return hashlib.md5(iv).digest()


def _decrypt_db_page(buffer: bytearray, page_no: int, master: bytes,
                     offset: int = 0, length: int = PAGE_SIZE):
    """解密 buffer[offset:offset+length]（就地写回，注意 Python 切片是副本需按偏移操作）。"""
    key = _derive_page_aes_key(page_no, master)
    iv = _derive_page_aes_iv(page_no)
    seg = bytes(buffer[offset:offset + length])
    dec = _aes().decrypt_cbc(key, iv, seg)
    buffer[offset:offset + length] = dec


def _validate_page1_header(header: bytes) -> bool:
    if len(header) < 0x18:
        return False
    o10 = struct.unpack_from("<I", header, 0x10)[0]
    o14 = struct.unpack_from("<I", header, 0x14)[0]
    v6 = ((o10 & 0xff) << 8) | ((o10 & 0xff00) << 16)
    return (o14 == 0x20204000 and (v6 - 0x200) <= 0xFE00 and ((v6 - 1) & v6) == 0)


def _decrypt_page1(page: bytearray, master: bytes):
    if not _validate_page1_header(page):
        raise DBError("kgg db: 无效的 page 1 头部")
    expected_hdr = bytes(page[0x10:0x18])
    page[0x10:0x18] = page[0x08:0x10]
    _decrypt_db_page(page, 1, master, offset=0x10, length=PAGE_SIZE - 0x10)
    if bytes(page[0x10:0x18]) != expected_hdr:
        raise DBError("kgg db: page 1 完整性校验失败")
    page[:0x10] = SQLITE_HEADER


def _master_key_candidates():
    candidates = []
    env = os.environ.get("KGG_DB_MASTER_KEY", "").strip()
    if env:
        try:
            raw = bytes.fromhex(env)
            if len(raw) == 16:
                candidates.append(raw)
        except ValueError:
            pass
    candidates.append(DEFAULT_MASTER_KEY)
    return candidates


def decrypt_database(buffer: bytearray):
    """解密整库（就地），成功返回 None。"""
    if buffer[:16] == SQLITE_HEADER:
        return  # 未加密
    if len(buffer) == 0 or len(buffer) % PAGE_SIZE != 0:
        raise DBError(f"kgg db: 数据库大小非法: {len(buffer)}")

    last_page = len(buffer) // PAGE_SIZE
    chosen = None
    for master in _master_key_candidates():
        trial = bytearray(buffer[:PAGE_SIZE])
        try:
            _decrypt_page1(trial, master)
        except DBError:
            continue
        buffer[:PAGE_SIZE] = trial
        chosen = master
        break
    if chosen is None:
        raise DBError("kgg db: 无法用候选 master key 解密 page 1")

    offset = PAGE_SIZE
    for page_no in range(2, last_page + 1):
        _decrypt_db_page(buffer, page_no, chosen, offset=offset, length=PAGE_SIZE)
        offset += PAGE_SIZE


def extract_key_mapping(buffer: bytes):
    """从解密后的库字节查询 ShareFileItems 表，返回 {audioHash: ekey}。"""
    if buffer[:16] != SQLITE_HEADER:
        raise DBError("kgg db: 库未解密")
    conn = sqlite3.connect(":memory:")
    try:
        try:
            conn.deserialize(buffer)
        except AttributeError:
            # 兼容无 deserialize 的 Python：写入临时文件再打开
            import tempfile
            fd, path = tempfile.mkstemp(suffix=".db")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(buffer)
                conn = sqlite3.connect(path)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        rows = conn.execute(
            "SELECT EncryptionKeyId, EncryptionKey FROM ShareFileItems "
            "WHERE EncryptionKeyId IS NOT NULL AND EncryptionKeyId != '' "
            "AND EncryptionKey IS NOT NULL AND EncryptionKey != ''")
        m = {}
        for rid, rkey in rows:
            if rid is None or rkey is None:
                continue
            # Current clients normally store a 32-char hexadecimal audio hash,
            # but older databases may expose it as bytes or mixed case.
            if isinstance(rid, bytes):
                rid = rid.decode("ascii", "ignore")
            if isinstance(rkey, bytes):
                rkey = rkey.decode("ascii", "ignore")
            rid = str(rid).strip()
            rkey = str(rkey).strip()
            if rid and rkey:
                m[rid] = rkey
                m[rid.lower()] = rkey
        return m
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_key_map(db_path: str) -> dict:
    """从 KGMusicV3.db 加载 audioHash -> ekey 映射。"""
    with open(db_path, "rb") as f:
        buffer = bytearray(f.read())
    decrypt_database(buffer)
    return extract_key_mapping(bytes(buffer))


# 自动定位数据库的常见路径
def find_database() -> str:
    roaming = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    home = os.path.expanduser("~")
    candidates = [
        os.environ.get("KGG_DB_PATH", "").strip().strip('"'),
        os.path.join(roaming, "KuGou8", "KGMusicV3.db"),
        os.path.join(local, "KuGou", "KGMusicV3.db"),
        os.path.join(local, "KuGou8", "KGMusicV3.db"),
        os.path.join(home, "AppData", "Roaming", "KuGou8", "KGMusicV3.db"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    # Portable and store builds sometimes add an intermediate account/version
    # directory.  Limit recursive discovery to vendor-specific roots so this
    # remains quick even on large profiles.
    for base in (os.path.join(roaming, "KuGou8"), os.path.join(local, "KuGou"),
                 os.path.join(local, "KuGou8")):
        if not os.path.isdir(base):
            continue
        for dirpath, dirs, names in os.walk(base):
            dirs[:] = dirs[:32]
            match = next((name for name in names if name.lower() == "kgmusicv3.db"), None)
            if match:
                return os.path.join(dirpath, match)
    return ""
