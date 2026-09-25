# -*- coding: utf-8 -*-
"""QQ Music MusicEx eKey provider for Windows.

The QQ Music 19.57+ MusicEx footer contains the song/media identifiers but no
longer embeds the QMC2 eKey.  This module resolves that key using the user's
already-running, logged-in QQ Music client and keeps only per-track eKeys in a
Windows-DPAPI protected cache.  Login cookies are never written to disk.
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import re
import struct
import sys
import threading
import time
import urllib.error
import urllib.request


class QQMusicKeyError(Exception):
    """A safe, user-facing MusicEx key resolution failure."""


_API_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
_COOKIE_MARKER = b"qqmusic_key="
_SESSION_TTL = 10 * 60
_session_lock = threading.Lock()
_session_cache = None
_session_cached_at = 0.0
_key_memory_cache = {}


def _cache_root() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "MiaomiaoTranscoder", "keys")


def _cache_path(media_id: str, media_file_name: str) -> str:
    identity = f"{media_id}\0{media_file_name}".encode("utf-8")
    name = hashlib.sha256(identity).hexdigest() + ".key"
    return os.path.join(_cache_root(), name)


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf


def _dpapi_protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise QQMusicKeyError("MusicEx 密钥缓存仅支持 Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    src, src_buf = _blob(data)
    entropy, entropy_buf = _blob(b"MiaomiaoTranscoder/MusicEx/eKey/v1")
    out = _DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(src), "Miaomiao MusicEx eKey",
                                    ctypes.byref(entropy), None, None, 0x1,
                                    ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)
        # Keep ctypes buffers alive until CryptProtectData has returned.
        _ = (src_buf, entropy_buf)


def _dpapi_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise QQMusicKeyError("MusicEx 密钥缓存仅支持 Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p,
        ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    src, src_buf = _blob(data)
    entropy, entropy_buf = _blob(b"MiaomiaoTranscoder/MusicEx/eKey/v1")
    out = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(src), None,
                                      ctypes.byref(entropy), None, None, 0x1,
                                      ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)
        _ = (src_buf, entropy_buf)


def _valid_ekey(value: str) -> bool:
    if not value or len(value) > 16384:
        return False
    try:
        raw = base64.b64decode(value + "=" * ((-len(value)) % 4), validate=False)
    except (ValueError, base64.binascii.Error):
        return False
    return len(raw) >= 16


def _load_cached_key(media_id: str, media_file_name: str):
    identity = (media_id, media_file_name)
    cached = _key_memory_cache.get(identity)
    if cached:
        return cached
    path = _cache_path(media_id, media_file_name)
    try:
        with open(path, "rb") as f:
            protected = f.read()
        record = json.loads(_dpapi_unprotect(protected).decode("utf-8"))
        if (record.get("v") == 1 and record.get("media_id") == media_id
                and record.get("media_file_name") == media_file_name
                and _valid_ekey(record.get("ekey", ""))):
            _key_memory_cache[identity] = record["ekey"]
            return record["ekey"]
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        return None
    return None


def _save_cached_key(media_id: str, media_file_name: str, ekey: str):
    identity = (media_id, media_file_name)
    _key_memory_cache[identity] = ekey
    record = json.dumps({
        "v": 1,
        "media_id": media_id,
        "media_file_name": media_file_name,
        "ekey": ekey,
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    protected = _dpapi_protect(record)
    root = _cache_root()
    os.makedirs(root, exist_ok=True)
    target = _cache_path(media_id, media_file_name)
    tmp = target + f".{os.getpid()}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(protected)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD),
    ]


def _qqmusic_pids(kernel32):
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid = ctypes.c_void_p(-1).value
    if snapshot == invalid:
        return []
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    result = []
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == "qqmusic.exe":
                result.append(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return result


def _parse_cookie_candidate(data: bytes):
    end = len(data)
    for sep in (b"\x00", b"\r", b"\n"):
        pos = data.find(sep)
        if pos >= 0:
            end = min(end, pos)
    text = data[:end].decode("utf-8", "ignore").strip()
    uin_match = re.search(r"(?:qqmusic_uin|uin|wxuin)=(\d{5,})", text)
    key_match = re.search(r"qqmusic_key=([^;\s]+)", text)
    if not key_match or not uin_match:
        return None
    # Reject implausibly long/corrupt memory matches without logging them.
    if len(text) > 4096 or len(key_match.group(1)) < 8:
        return None
    return {"cookie": text, "uin": uin_match.group(1)}


def _scan_process_for_session(kernel32, pid: int):
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
                                  False, pid)
    if not handle:
        return None
    try:
        mbi = _MEMORY_BASIC_INFORMATION()
        address = 0
        max_address = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8 - 1)) - 1
        readable = {0x02, 0x04, 0x20, 0x40}
        chunk_size = 2 * 1024 * 1024
        overlap = 4096
        while address < max_address:
            queried = kernel32.VirtualQueryEx(handle, ctypes.c_void_p(address),
                                               ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not queried:
                break
            base = mbi.BaseAddress or 0
            region_size = int(mbi.RegionSize or 0)
            if region_size <= 0:
                break
            protect = int(mbi.Protect)
            if (mbi.State == 0x1000 and (protect & 0xFF) in readable
                    and not (protect & 0x100)):
                offset = 0
                carry = b""
                while offset < region_size:
                    want = min(chunk_size, region_size - offset)
                    buf = ctypes.create_string_buffer(want)
                    got = ctypes.c_size_t()
                    ok = kernel32.ReadProcessMemory(
                        handle, ctypes.c_void_p(base + offset), buf, want,
                        ctypes.byref(got))
                    if ok and got.value:
                        block = carry + buf.raw[:got.value]
                        start = 0
                        while True:
                            pos = block.find(_COOKIE_MARKER, start)
                            if pos < 0:
                                break
                            parsed = _parse_cookie_candidate(block[pos:pos + 4096])
                            if parsed:
                                return parsed
                            start = pos + len(_COOKIE_MARKER)
                        carry = block[-overlap:]
                    else:
                        carry = b""
                    offset += want
            next_address = base + region_size
            if next_address <= address:
                break
            address = next_address
    finally:
        kernel32.CloseHandle(handle)
    return None


def _extract_session():
    if sys.platform != "win32":
        raise QQMusicKeyError("新版 MusicEx 自动取钥仅支持 Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Pointer-bearing Win32 APIs must have explicit signatures on 64-bit.
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                         ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                        ctypes.POINTER(_MEMORY_BASIC_INFORMATION),
                                        ctypes.c_size_t]
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    kernel32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_size_t,
                                           ctypes.POINTER(ctypes.c_size_t)]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    pids = _qqmusic_pids(kernel32)
    if not pids:
        raise QQMusicKeyError("请先启动 QQ 音乐并登录拥有该歌曲权限的账号")
    denied = False
    for pid in pids:
        session = _scan_process_for_session(kernel32, pid)
        if session:
            return session
        if ctypes.get_last_error() == 5:
            denied = True
    if denied:
        raise QQMusicKeyError("无法读取 QQ 音乐会话；请让两个程序以相同权限运行")
    raise QQMusicKeyError("未找到有效的 QQ 音乐登录会话；请确认客户端已登录并播放过歌曲")


def _get_session():
    global _session_cache, _session_cached_at
    with _session_lock:
        if _session_cache and time.monotonic() - _session_cached_at < _SESSION_TTL:
            return _session_cache
        _session_cache = _extract_session()
        _session_cached_at = time.monotonic()
        return _session_cache


def _fetch_ekey(media_id: str, media_file_name: str, session) -> str:
    request_data = {
        "comm": {
            "cv": 4747474, "ct": 24, "format": "json",
            "inCharset": "utf-8", "outCharset": "utf-8", "notice": 0,
            "platform": "yqq.json", "needNewCode": 1,
            "uin": int(session["uin"]), "g_tk_new_20200303": 5381,
            "g_tk": 5381,
        },
        "req_1": {
            "module": "vkey.GetVkeyServer", "method": "CgiGetVkey",
            "param": {
                "filename": [media_file_name], "guid": "10000",
                "songmid": [media_id], "songtype": [0],
                "uin": session["uin"], "loginflag": 1, "platform": "20",
            },
        },
    }
    req = urllib.request.Request(
        _API_URL,
        data=json.dumps(request_data, separators=(",", ":")).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Cookie": session["cookie"],
            "User-Agent": "QQMusic/22",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise QQMusicKeyError(f"QQ 音乐密钥服务返回 HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise QQMusicKeyError("无法连接 QQ 音乐密钥服务，请检查网络后重试") from None
    except (UnicodeError, json.JSONDecodeError):
        raise QQMusicKeyError("QQ 音乐密钥服务返回了无法识别的数据") from None

    info = result.get("req_1", {}).get("data", {}).get("midurlinfo", [])
    item = info[0] if info else {}
    ekey = item.get("ekey", "")
    if not _valid_ekey(ekey):
        code = item.get("result", result.get("code", "未知"))
        raise QQMusicKeyError(
            f"未取得该歌曲的 MusicEx 密钥（代码 {code}）；请确认账号仍有播放权限")
    return ekey


def resolve_ekey(media_id: str, media_file_name: str) -> str:
    """Resolve a MusicEx eKey, preferring the encrypted offline cache."""
    media_id = (media_id or "").strip()
    media_file_name = os.path.basename((media_file_name or "").strip())
    if (not re.fullmatch(r"[A-Za-z0-9]{10,64}", media_id)
            or not re.fullmatch(r"[A-Za-z0-9_-]{8,96}\.(?:mflac|mgg)",
                                media_file_name, re.IGNORECASE)):
        raise QQMusicKeyError("MusicEx 文件缺少有效的 Media ID 或内部媒体文件名")

    cached = _load_cached_key(media_id, media_file_name)
    if cached:
        return cached
    session = _get_session()
    ekey = _fetch_ekey(media_id, media_file_name, session)
    try:
        _save_cached_key(media_id, media_file_name, ekey)
    except OSError:
        # Conversion should still succeed when a locked-down system prevents
        # persistent caching; the key remains in the process-only cache.
        _key_memory_cache[(media_id, media_file_name)] = ekey
    return ekey
