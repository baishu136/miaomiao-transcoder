# -*- coding: utf-8 -*-
"""
网易云音乐 .ncm 文件解密模块。

算法严格对齐 taurusxin/ncmdump（C++ 权威实现）：
  * key 段:   xor 0x64 -> AES-128-ECB 解密(sCoreKey) -> [17:] 作为 RC4 密钥
  * meta 段:  xor 0x63 -> 跳过前 22 字节 -> base64 解码 -> AES-128-ECB 解密(sModifyKey)
              -> 跳过 "music:"(6 字节) -> JSON
  * 音频段:   静态 RC4 密钥流（周期 256 字节），整个文件统一异或

纯标准库实现，AES 调用 Windows 自带 BCrypt (bcrypt.dll)，无需第三方库。
提供流式解密（mmap + 分块异或），适合大文件。
"""
import base64
import ctypes
import json
import mmap
import struct
from ctypes import wintypes
from typing import Callable, Optional

NCM_MAGIC = b"CTENFDAM"

# 网易云固定的两个 AES-128 密钥（16 字节）
CORE_KEY = bytes([0x68, 0x7A, 0x48, 0x52, 0x41, 0x6D, 0x73, 0x6F,
                  0x35, 0x6B, 0x49, 0x6E, 0x62, 0x61, 0x78, 0x57])  # "hzHRAmso5kInbaxW"
MODIFY_KEY = bytes([0x23, 0x31, 0x34, 0x6C, 0x6A, 0x6B, 0x5F, 0x21,
                    0x5C, 0x5D, 0x26, 0x30, 0x55, 0x3C, 0x27, 0x28])  # "#14ljk_!\]&0U<('"

_CHUNK = 0x10000  # 流式解密块大小


class NCMError(Exception):
    """NCM 文件解析/解密异常。"""


# --------------------------------------------------------------------------
# AES-128-ECB via Windows BCrypt（避免安装第三方加密库）
# --------------------------------------------------------------------------

class _BcryptAES:
    """用 Windows CNG/BCrypt 提供的 AES-128-ECB，支持解密与加密。"""

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
        bc.BCryptEncrypt.argtypes = bc.BCryptDecrypt.argtypes
        bc.BCryptDestroyKey.argtypes = [wintypes.HANDLE]
        bc.BCryptCloseAlgorithmProvider.argtypes = [wintypes.HANDLE, ctypes.c_ulong]

    def _run(self, key: bytes, data: bytes, decrypt: bool) -> bytes:
        if len(key) != 16:
            raise ValueError("AES-128 密钥必须为 16 字节")
        if not data or len(data) % 16 != 0:
            raise ValueError("AES-ECB 输入必须是 16 字节的倍数")
        bc = self._bcrypt
        h_alg = wintypes.HANDLE()
        status = bc.BCryptOpenAlgorithmProvider(
            ctypes.byref(h_alg), "AES", None, 0)
        if status:
            raise NCMError(f"BCryptOpenAlgorithmProvider 失败: {status:#x}")
        h_key = wintypes.HANDLE()
        try:
            # 显式设置为 ECB 模式（默认可能是 CBC）
            mode = ctypes.create_unicode_buffer("ChainingModeECB")
            status = bc.BCryptSetProperty(
                h_alg, "ChainingMode", mode,
                len(mode) * ctypes.sizeof(ctypes.c_wchar), 0)
            if status:
                raise NCMError(f"BCryptSetProperty 失败: {status:#x}")
            key_buf = ctypes.create_string_buffer(key, len(key))
            status = bc.BCryptGenerateSymmetricKey(
                h_alg, ctypes.byref(h_key), None, 0, key_buf, len(key), 0)
            if status:
                raise NCMError(f"BCryptGenerateSymmetricKey 失败: {status:#x}")
            in_buf = ctypes.create_string_buffer(data, len(data))
            out_buf = ctypes.create_string_buffer(len(data))
            done = ctypes.c_ulong(0)
            fn = bc.BCryptDecrypt if decrypt else bc.BCryptEncrypt
            # ECB 模式无 IV：pbIV=NULL, cbIV=0
            status = fn(h_key, in_buf, len(data), None, None, 0,
                        out_buf, len(data), ctypes.byref(done), 0)
            if status:
                raise NCMError(f"{'解密' if decrypt else '加密'}失败: {status:#x}")
            return out_buf.raw[:done.value]
        finally:
            if h_key.value:
                bc.BCryptDestroyKey(h_key)
            bc.BCryptCloseAlgorithmProvider(h_alg, 0)

    def decrypt(self, key: bytes, data: bytes) -> bytes:
        return self._run(key, data, decrypt=True)

    def encrypt(self, key: bytes, data: bytes) -> bytes:
        return self._run(key, data, decrypt=False)


_AES = None


def _aes() -> _BcryptAES:
    global _AES
    if _AES is None:
        _AES = _BcryptAES()
    return _AES


def aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    return _aes().decrypt(key, data)


def aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    return _aes().encrypt(key, data)


def _pkcs7_unpad(data: bytes) -> bytes:
    """按 taurusxin 实现去除 PKCS7 padding（最后一字节作为 pad 长度）。"""
    if not data:
        return data
    pad = data[-1]
    if 0 < pad <= 16:
        return data[:-pad]
    return data


def _pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    pad_len = block - (len(data) % block)
    return data + bytes([pad_len]) * pad_len


# --------------------------------------------------------------------------
# RC4 密钥流
# --------------------------------------------------------------------------

def build_key_box(key: bytes) -> bytes:
    """标准 RC4 KSA，返回 256 字节 S 盒。"""
    box = bytearray(range(256))
    j = 0
    klen = len(key)
    for i in range(256):
        j = (j + box[i] + key[i % klen]) & 0xFF
        box[i], box[j] = box[j], box[i]
    return bytes(box)


def build_keystream(box: bytes) -> bytes:
    """由 S 盒生成静态 256 字节密钥流（NCM 变体：索引从 1 开始，不改变 S 盒）。

    keystream[t] = box[(box[j] + box[(box[j] + j) & 0xFF]) & 0xFF], j = (t + 1) & 0xFF
    """
    ks = bytearray(256)
    for t in range(256):
        j = (t + 1) & 0xFF
        ks[t] = box[(box[j] + box[(box[j] + j) & 0xFF]) & 0xFF]
    return bytes(ks)


def xor_keystream(data: bytes, keystream: bytes) -> bytes:
    """用周期 256 字节的密钥流对整段数据异或（大整数异或，速度极快）。"""
    if not data:
        return b""
    n = len(data)
    rep = (keystream * ((n + 255) // 256))[:n]
    a = int.from_bytes(data, "little")
    b = int.from_bytes(rep, "little")
    return (a ^ b).to_bytes(n, "little")


# --------------------------------------------------------------------------
# NCM 头部解析（data 可以是 bytes 或 mmap）
# --------------------------------------------------------------------------

def _audio_head_valid(header: bytes) -> bool:
    """粗略判断一段数据是否像可解码的音频头。"""
    if not header:
        return False
    if header.startswith(b"ID3") or header.startswith(b"fLaC") or \
       header.startswith(b"OggS") or header.startswith(b"RIFF") or \
       header.startswith(b"MAC ") or header.startswith(b"wvpk"):
        return True
    # MPEG 帧头同步字: 0xFF 0xEx（且版本位非 01）
    if header[0] == 0xFF and (header[1] & 0xE0) == 0xE0 and (header[1] & 0x18) != 0x08:
        return True
    return False


def parse_header(data) -> dict:
    """解析 NCM 头部，返回不含音频内容的字典。

    返回: {
        'meta':        dict | None
        'image':       bytes          （封面图，可能为空）
        'format':      str            （'mp3' | 'flac'）
        'keystream':   bytes          （256 字节 RC4 密钥流）
        'audio_offset':int            （音频数据在文件中的偏移）
    }
    """
    if len(data) < 16 or data[:8] != NCM_MAGIC:
        raise NCMError("无效的 NCM 文件（头部不是 CTENFDAM）")

    pos = 8 + 2
    # ---- key 段 ----
    key_len = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    if key_len <= 0 or pos + key_len > len(data):
        raise NCMError("NCM key 段损坏")
    key_data = bytes(b ^ 0x64 for b in data[pos:pos + key_len])
    pos += key_len
    if len(key_data) % 16 != 0:
        raise NCMError("NCM key 数据长度非法")
    mkey = _pkcs7_unpad(aes_ecb_decrypt(CORE_KEY, key_data))
    if len(mkey) <= 17:
        raise NCMError("NCM key 解密结果过短")
    keystream = build_keystream(build_key_box(mkey[17:]))

    # ---- meta 段 ----
    meta = None
    if pos + 4 <= len(data):
        meta_len = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if meta_len > 0:
            if pos + meta_len > len(data):
                raise NCMError("NCM meta 段损坏")
            mdata = bytes(b ^ 0x63 for b in data[pos:pos + meta_len])
            pos += meta_len
            try:
                b64 = base64.b64decode(mdata[22:])
                dec = _pkcs7_unpad(aes_ecb_decrypt(MODIFY_KEY, b64))
                meta = json.loads(dec[6:])
            except Exception:
                meta = None

    # ---- 封面 / 163key 段：枚举可能的音频起始偏移 ----
    candidates = []
    p = pos + 5  # 跳过 crc32 + image version
    if p + 8 <= len(data):
        cover_frame_len = struct.unpack_from("<I", data, p)[0]
        img_len = struct.unpack_from("<I", data, p + 4)[0]
        image = bytes(data[p + 8:p + 8 + img_len])
        candidates.append((p + 8 + cover_frame_len, image))
    # pyNCMDUMP 变体偏移
    q = pos + 4 + 5
    if q + 4 <= len(data):
        img2_len = struct.unpack_from("<I", data, q)[0]
        image2 = bytes(data[q + 4:q + 4 + img2_len])
        candidates.append((q + 4 + img2_len, image2))
    # 无封面段的兜底偏移
    candidates.append((pos + 5, b""))

    # 用"解密后的头部"选择正确的偏移
    audio_offset, image = candidates[0]
    for off, img in candidates:
        if off + 32 <= len(data) and \
           _audio_head_valid(xor_keystream(data[off:off + 32], keystream)):
            audio_offset, image = off, img
            break

    # 格式判定：优先 meta，其次探测头部
    head = xor_keystream(data[audio_offset:audio_offset + 4], keystream)
    fmt = "flac" if head[:4] == b"fLaC" else "mp3"
    if isinstance(meta, dict) and meta.get("format") in ("mp3", "flac"):
        fmt = meta["format"]

    return {
        "meta": meta,
        "image": image,
        "format": fmt,
        "keystream": keystream,
        "audio_offset": audio_offset,
    }


def decrypt_data(data, progress_cb=None, cancel_event=None):
    """解密整段 NCM 数据，返回 {'audio': bytes, **header}。"""
    h = parse_header(data)
    off = h["audio_offset"]
    total = len(data) - off
    ks = h["keystream"]
    out = bytearray()
    pos = off
    while pos < len(data):
        chunk = data[pos:pos + _CHUNK]
        out += xor_keystream(chunk, ks)
        pos += len(chunk)
        if progress_cb and total:
            progress_cb((pos - off) * 100 // total)
        if cancel_event and cancel_event.is_set():
            return False
    h["audio"] = bytes(out)
    return h


def decrypt_file(in_path: str, out_path: str, write_audio: bool = True,
                 progress_cb: Callable[[int], None] = None,
                 cancel_event=None) -> Optional[dict]:
    """流式解密 NCM 文件（mmap 分块，内存占用小）。

    返回解析信息 dict（不含 audio 全量数据）；被取消时返回 False。
    """
    with open(in_path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            h = parse_header(mm)
            off = h["audio_offset"]
            total = len(mm) - off
            ks = h["keystream"]
            if write_audio:
                with open(out_path, "wb") as out:
                    pos = off
                    while pos < len(mm):
                        chunk = mm[pos:pos + _CHUNK]
                        out.write(xor_keystream(chunk, ks))
                        pos += len(chunk)
                        if progress_cb and total:
                            progress_cb(min(99, (pos - off) * 100 // total))
                        if cancel_event and cancel_event.is_set():
                            return False
            return h
