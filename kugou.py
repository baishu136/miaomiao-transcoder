# -*- coding: utf-8 -*-
"""
酷狗音乐 KGM/KGG/VPR 加密音频解密模块。

算法与数据严格对齐 @xhacker/kgmwasm（KgmLegacy）权威实现：
  * 文件头 0x10 处 4 字节小端 = header 长度，跳过 header 后为加密音频流
  * key1 = 文件偏移 0x1c 起 16 字节 + 补 1 个 0x00（共 17 字节）
  * 逐字节解密: out = xor_lower(mask(idx)) ^ xor_lower(cipher ^ key1[idx%17])
    - xor_lower(b) = b ^ ((b & 0x0f) << 4)
    - mask(idx) = table3[idx % 272] ^ h(idx)，h 由 table1/table2 递归生成
  * VPR 额外异或 VPR_MASK_DIFF

三张 272 字节表直接取自权威实现，无需外部数据文件。
"""
import base64
import math
import mmap
import os
import struct

from kugou_tables import T1, T2, T3

KGM_MAGIC = bytes([0x7C, 0xD5, 0x32, 0xEB, 0x86, 0x02, 0x7F, 0x4B,
                   0xA8, 0xAF, 0xA6, 0x8E, 0x0F, 0xFF, 0x99, 0x14])
VPR_MAGIC = bytes([0x05, 0x28, 0xBC, 0x96, 0xE9, 0xE4, 0x5A, 0x43,
                   0x91, 0xAA, 0xBD, 0xD0, 0x7A, 0xF5, 0x36, 0x31])
KGB1_MAGIC = b"KGB1"
VPR_MASK_DIFF = bytes([0x25, 0xDF, 0xE8, 0xA6, 0x75, 0x1E, 0x75, 0x0E,
                       0x2F, 0x80, 0xF3, 0x2D, 0xB8, 0xB6, 0xE3, 0x11, 0x00])

# 快速表：xor_lower 变换（自逆）
_XOR_LOWER = bytes(b ^ ((b & 0x0f) << 4) for b in range(256))


class KugouError(Exception):
    """酷狗加密文件解析/解密异常。"""


def _gen_mask(idx: int) -> int:
    """按权威实现生成掩码字节。"""
    idx_urs4 = idx >> 4
    h = 0
    while idx_urs4 >= 17:
        h ^= T1[idx_urs4 % 272]
        idx_urs4 >>= 4
        h ^= T2[idx_urs4 % 272]
        idx_urs4 >>= 4
    return T3[idx % 272] ^ h


def _xor_lower_translate(data: bytes, key: bytes) -> bytes:
    """对每字节 b 计算 xor_lower(b ^ key[i%17])。"""
    n = len(data)
    out = bytearray(n)
    # 逐 17 字节块用查找表加速
    for i in range(n):
        out[i] = _XOR_LOWER[data[i] ^ key[i % 17]]
    return bytes(out)


def detect(data) -> str:
    """检测酷狗加密类型，返回 'kgm' / 'vpr' / '' 。"""
    if data[:16] == KGM_MAGIC or data[:4] == KGB1_MAGIC:
        return "kgm"
    if data[:16] == VPR_MAGIC:
        return "vpr"
    return ""


def parse_header(data) -> dict:
    """解析 KGM/KGG/VPR 头部，返回 {file_key, audio_offset, is_vpr}。"""
    kind = detect(data)
    if not kind:
        raise KugouError("不是有效的酷狗加密文件（KGM/KGG/VPR 头部不匹配）")
    if len(data) < 0x2c:
        raise KugouError("KGM 文件头过短")

    header_len = struct.unpack_from("<I", data, 0x10)[0]
    if header_len > len(data) or header_len < 0x2c:
        # header 长度异常时，按最小可解析处理
        header_len = min(max(header_len, 0x2c), len(data))
    file_key = bytes(data[0x1c:0x2c]) + b"\x00"   # 17 字节
    return {
        "kind": kind,
        "file_key": file_key,
        "audio_offset": header_len,
        "is_vpr": kind == "vpr",
    }


def decrypt_data(data, progress_cb=None, cancel_event=None):
    """解密整段 KGM/KGG/VPR 数据，返回 {'audio': bytes, 'format': str, ...}。"""
    h = parse_header(data)
    audio_enc = data[h["audio_offset"]:]
    if not audio_enc:
        raise KugouError("KGM 文件中没有音频数据")
    if cancel_event and cancel_event.is_set():
        return False
    audio = _decrypt_stream(audio_enc, h["file_key"], h["is_vpr"], 0,
                            progress_cb, cancel_event)
    if audio is False:
        return False
    fmt = "flac" if audio[:4] == b"fLaC" else "mp3"
    if audio[:4] == b"OggS":
        fmt = "ogg"
    return {"audio": audio, "format": fmt, **h}


def _probe_format(head: bytes, strict: bool = False):
    """探测解密后音频流的格式。"""
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xff and
                               (head[1] & 0xe0) == 0xe0):
        return "mp3"
    return None if strict else "mp3"


def requires_database(in_path: str) -> bool:
    """Return whether a KGG file uses CryptoVersion 5 database keys."""
    try:
        with open(in_path, "rb") as stream:
            head = stream.read(0x18)
        return _is_v5(head)
    except OSError:
        return False


def decrypt_file(in_path: str, out_path: str, write_audio: bool = True,
                 progress_cb=None, cancel_event=None):
    """流式解密 KGM/KGG/VPR 文件（mmap 分块）。"""
    try:
        file_size = os.path.getsize(in_path)
    except OSError as exc:
        raise KugouError(f"无法读取酷狗文件: {exc}") from exc
    if file_size == 0:
        raise KugouError("KGG 文件为空（0 字节），请在酷狗客户端重新下载该歌曲")
    if file_size < 0x2c:
        raise KugouError(f"KGG 文件不完整（仅 {file_size} 字节），请重新下载")
    with open(in_path, "rb") as f:
        try:
            mm_ctx = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except (OSError, ValueError) as exc:
            raise KugouError(f"无法映射 KGG 文件: {exc}") from exc
        with mm_ctx as mm:
            # 新版 CryptoVersion=5：QMC2 + 数据库密钥
            if _is_v5(mm):
                h, dec, key_len = _prepare_kgg_v5(mm)
                offset = h["audio_offset"]
                total = len(mm) - offset
                if total <= 0:
                    raise KugouError("KGG 文件中没有音频数据")
                output = open(out_path, "wb") if write_audio else None
                try:
                    pos = 0
                    fmt = None
                    while pos < total:
                        if cancel_event and cancel_event.is_set():
                            return False
                        end = min(total, pos + 0x100000)
                        seg = bytearray(mm[offset + pos:offset + end])
                        dec.decrypt(seg, pos)
                        if fmt is None:
                            fmt = _probe_format(seg[:16], strict=True)
                            if fmt is None:
                                raise KugouError(
                                    "KGG 密钥已找到，但解密结果不是有效音频；密钥可能已失效")
                        if output:
                            output.write(seg)
                        pos = end
                        if progress_cb:
                            progress_cb(min(99, pos * 100 // total))
                finally:
                    if output:
                        output.close()
                if progress_cb:
                    progress_cb(100)
                return {**h, "format": fmt, "key_len": key_len,
                        "audio_size": total}
            h = parse_header(mm)
            offset = h["audio_offset"]
            total = len(mm) - offset
            if write_audio:
                with open(out_path, "wb") as out:
                    pos = offset
                    idx = 0
                    first = True
                    while pos < len(mm):
                        chunk = bytes(mm[pos:pos + 0x10000])
                        if not chunk:
                            break
                        dec = _decrypt_stream(chunk, h["file_key"], h["is_vpr"], idx,
                                              None, cancel_event)
                        if dec is False:
                            return False
                        if first:
                            h["format"] = _probe_format(dec[:4])
                            first = False
                        out.write(dec)
                        idx += len(chunk)
                        pos += len(chunk)
                        if progress_cb and total:
                            progress_cb(min(99, (pos - offset) * 100 // total))
                        if cancel_event and cancel_event.is_set():
                            return False
            return h


def _decrypt_stream(audio_enc: bytes, file_key: bytes, is_vpr: bool,
                    base_idx: int, progress_cb=None, cancel_event=None) -> bytes:
    """Decrypt a legacy KGM block with 16-byte mask-vector batching."""
    n = len(audio_enc)
    if n == 0:
        return b""
    stream = bytearray(n)
    key = file_key
    done = 0
    while done < n:
        idx = base_idx + done
        # h(idx) changes only when idx >> 4 changes.  Compute the recursive
        # component once for up to sixteen adjacent bytes instead of once per
        # byte; the 272-byte T3 and 17-byte file-key cycles remain exact.
        take = min(n - done, 16 - (idx & 0x0f))
        idx_urs4 = idx >> 4
        h = 0
        while idx_urs4 >= 17:
            h ^= T1[idx_urs4 % 272]
            idx_urs4 >>= 4
            h ^= T2[idx_urs4 % 272]
            idx_urs4 >>= 4
        for rel in range(take):
            absolute = idx + rel
            pre_vpr = _XOR_LOWER[VPR_MASK_DIFF[absolute % 17]] if is_vpr else 0
            stream[done + rel] = (T3[absolute % 272] ^ h ^
                                  key[absolute % 17] ^ pre_vpr)
        done += take
        if cancel_event and cancel_event.is_set():
            return False
        if progress_cb and n and (done == n or done % 0x20000 < 16):
            progress_cb(done * 100 // n)

    # xor_lower is linear over XOR.  One C-backed integer XOR plus a C-backed
    # translation replaces the remaining Python byte loop.
    mixed = (int.from_bytes(audio_enc, "little") ^
             int.from_bytes(stream, "little")).to_bytes(n, "little")
    return mixed.translate(_XOR_LOWER)



# ==========================================================================
# 酷狗新版 kgg（CryptoVersion=5）: QMC2 + ekey 派生 + 数据库密钥
# ==========================================================================

_key_map = None


def set_key_map(mapping):
    """注入 KGMusicV3.db 的 audioHash -> ekey 映射。"""
    global _key_map
    normalized = {}
    for key, value in (mapping or {}).items():
        if isinstance(key, bytes):
            key = key.decode("ascii", "ignore")
        key = str(key).strip()
        if key and value:
            normalized[key] = value
            normalized[key.lower()] = value
    _key_map = normalized


def get_key_map():
    return _key_map or {}


# ---------- TEA-CBC（salt=2/zero=7） ----------

_TEA_DELTA = 0x9E3779B9


def _tea_ecb_decrypt(cipher8, key16, rounds=32):
    v0 = int.from_bytes(cipher8[:4], "big")
    v1 = int.from_bytes(cipher8[4:8], "big")
    k0 = int.from_bytes(key16[:4], "big")
    k1 = int.from_bytes(key16[4:8], "big")
    k2 = int.from_bytes(key16[8:12], "big")
    k3 = int.from_bytes(key16[12:16], "big")
    sums = (_TEA_DELTA * (rounds // 2)) & 0xFFFFFFFF
    for _ in range(rounds // 2):
        v1 = (v1 - ((((v0 << 4) & 0xFFFFFFFF) + k2)
                     ^ ((v0 + sums) & 0xFFFFFFFF)
                     ^ (((v0 >> 5) & 0xFFFFFFFF) + k3))) & 0xFFFFFFFF
        v0 = (v0 - ((((v1 << 4) & 0xFFFFFFFF) + k0)
                     ^ ((v1 + sums) & 0xFFFFFFFF)
                     ^ (((v1 >> 5) & 0xFFFFFFFF) + k1))) & 0xFFFFFFFF
        sums = (sums - _TEA_DELTA) & 0xFFFFFFFF
    return v0.to_bytes(4, "big") + v1.to_bytes(4, "big")


def _tea_cbc_decrypt(cipherdata, key16, rounds=32):
    if len(cipherdata) < 16 or len(cipherdata) % 8 != 0:
        raise KugouError("invalid TEA CBC cipher length")
    dest = bytearray(_tea_ecb_decrypt(cipherdata[:8], key16, rounds))
    pad_len = dest[0] & 0x7
    out_len = len(cipherdata) - 1 - pad_len - 2 - 7
    if out_len < 0:
        raise KugouError("estimated plain length < 0")
    out = bytearray(out_len)
    iv_prev = bytearray(8)
    iv_cur = bytearray(cipherdata[:8])
    pos = 8
    dest_idx = 1 + pad_len

    def _crypt_block():
        nonlocal pos
        iv_prev[:] = iv_cur[:]
        iv_cur[:] = cipherdata[pos:pos + 8]
        dest[:] = bytearray(_tea_ecb_decrypt(
            bytes(x ^ y for x, y in zip(dest, iv_cur)), key16, rounds))
        pos += 8

    i = 1
    while i <= 2:
        if dest_idx < 8:
            dest_idx += 1
            i += 1
        elif dest_idx == 8:
            _crypt_block()
            dest_idx = 0
    op = 0
    while op < out_len:
        if dest_idx < 8:
            out[op] = dest[dest_idx] ^ iv_prev[dest_idx]
            dest_idx += 1
            op += 1
        elif dest_idx == 8:
            _crypt_block()
            dest_idx = 0
    return bytes(out)


# ---------- ekey 派生 ----------

_RAW_KEY_PREFIX_V2 = b"QQMusic EncV2,Key:"
_DERIVE_V2_KEY1 = bytes([0x33, 0x38, 0x36, 0x5A, 0x4A, 0x59, 0x21, 0x40,
                         0x23, 0x2A, 0x24, 0x25, 0x5E, 0x26, 0x29, 0x28])
_DERIVE_V2_KEY2 = bytes([0x2A, 0x2A, 0x23, 0x21, 0x28, 0x23, 0x24, 0x25,
                         0x26, 0x5E, 0x61, 0x31, 0x63, 0x5A, 0x2C, 0x54])


def _simple_make_key(salt, length):
    return [int(abs(math.tan(salt + i * 0.1)) * 100.0) & 0xff for i in range(length)]


def _derive_key_v1(raw_key_dec):
    if len(raw_key_dec) < 16:
        raise KugouError("key length is too short")
    simple_key = _simple_make_key(106, 8)
    tea_key = bytearray(16)
    for i in range(8):
        tea_key[i << 1] = simple_key[i]
        tea_key[(i << 1) + 1] = raw_key_dec[i]
    rs = _tea_cbc_decrypt(raw_key_dec[8:], bytes(tea_key), rounds=32)
    return raw_key_dec[:8] + rs


def _derive_key_v2(raw):
    buf = _tea_cbc_decrypt(raw, _DERIVE_V2_KEY1, rounds=32)
    buf = _tea_cbc_decrypt(buf, _DERIVE_V2_KEY2, rounds=32)
    return base64.b64decode(buf)


def _derive_key(ekey):
    raw_key_dec = base64.b64decode(ekey)
    if raw_key_dec.startswith(_RAW_KEY_PREFIX_V2):
        raw_key_dec = _derive_key_v2(raw_key_dec[len(_RAW_KEY_PREFIX_V2):])
    return _derive_key_v1(raw_key_dec)


# ---------- QMC2 MAP / RC4 ----------

class QMC2Map:
    def __init__(self, key):
        self.key = key
        self.size = len(key)
        self._table = bytearray(0x8000)
        for offset in range(0x8000):
            idx = (offset * offset + 71214) % self.size
            v = self.key[idx]
            r = ((idx & 0x7) + 4) % 8
            self._table[offset] = ((v << r) | (v >> r)) & 0xFF

    def get_mask(self, offset):
        if offset > 0x7FFF:
            offset %= 0x7FFF
        return self._table[offset]

    def decrypt(self, buf, base):
        table = self._table
        for i in range(len(buf)):
            o = base + i
            if o > 0x7FFF:
                o %= 0x7FFF
            buf[i] ^= table[o]


class QMC2RC4:
    first_segment_size = 128
    segment_size = 5120

    def __init__(self, key):
        self.key = key
        self.n = len(key)
        box = [i % 256 for i in range(self.n)]   # Go: byte(i)
        j = 0
        for i in range(self.n):
            j = (j + box[i] + key[i % self.n]) % self.n
            box[i], box[j] = box[j], box[i]
        self.box = box
        h = 1
        for v in key:
            if v == 0:
                continue
            nh = (h * v) & 0xFFFFFFFF
            if nh == 0 or nh <= h:
                break
            h = nh
        self.hash = h

    def _get_segment_skip(self, idx):
        seed = self.key[idx % self.n]
        val = int(float(self.hash) / float((idx + 1) * seed) * 100.0)
        return val % self.n

    def _enc_first(self, buf, start, count, base):
        for i in range(count):
            buf[start + i] ^= self.key[self._get_segment_skip(base + i)]

    def _enc_segment(self, buf, start, count, base):
        box = list(self.box)
        j, k = 0, 0
        skip_len = (base % self.segment_size) + self._get_segment_skip(base // self.segment_size)
        for i in range(-skip_len, count):
            j = (j + 1) % self.n
            k = (box[j] + k) % self.n
            box[j], box[k] = box[k], box[j]
            if i >= 0:
                buf[start + i] ^= box[(box[j] + box[k]) % self.n]

    def decrypt(self, buf, base):
        off = base
        processed = 0
        total = len(buf)
        if off < self.first_segment_size:
            block = min(total, self.first_segment_size - off)
            self._enc_first(buf, processed, block, off)
            off += block
            processed += block
            if processed >= total:
                return
        if off % self.segment_size != 0:
            block = min(total - processed, self.segment_size - (off % self.segment_size))
            self._enc_segment(buf, processed, block, off)
            off += block
            processed += block
            if processed >= total:
                return
        while total - processed > self.segment_size:
            self._enc_segment(buf, processed, self.segment_size, off)
            off += self.segment_size
            processed += self.segment_size
        if processed < total:
            self._enc_segment(buf, processed, total - processed, off)


def create_qmc2(ekey):
    key = _derive_key(ekey.encode("ascii"))
    if len(key) == 0:
        raise KugouError("invalid ekey")
    if len(key) > 300:
        return QMC2RC4(key), len(key)
    return QMC2Map(key), len(key)


# ---------- 新版 kgg v5 文件解析 ----------

def parse_header_v5(data):
    if len(data) < 0x48:
        raise KugouError("kgg v5 header too short")
    kind = detect(data)
    if not kind:
        raise KugouError("not a valid kugou file")
    header_len = struct.unpack_from("<I", data, 0x10)[0]
    crypto_version = struct.unpack_from("<I", data, 0x14)[0]
    if crypto_version != 5:
        raise KugouError(f"unsupported kugou crypto version: {crypto_version}")
    if header_len > len(data) or header_len < 0x48:
        raise KugouError("kgg v5 header_len invalid")
    hash_len = struct.unpack_from("<I", data, 0x44)[0]
    if hash_len <= 0 or hash_len > 256:
        raise KugouError(f"kgg v5 audioHash length invalid: {hash_len}")
    hash_end = 0x48 + hash_len
    if hash_end > header_len or hash_end > len(data):
        raise KugouError("kgg v5 audioHash 超出文件头范围")
    audio_hash = bytes(data[0x48:hash_end]).rstrip(b"\x00")
    if not audio_hash:
        raise KugouError("kgg v5 audioHash 为空")
    return {"kind": kind, "audio_offset": header_len,
            "audio_hash": audio_hash, "crypto_version": 5}


def _prepare_kgg_v5(data):
    h = parse_header_v5(data)
    key_map = get_key_map()
    if not key_map:
        raise KugouError("缺少酷狗密钥库（KGMusicV3.db），无法解密新版 kgg")
    audio_hash = h["audio_hash"].decode("ascii", "ignore").strip()
    ekey = key_map.get(audio_hash) or key_map.get(audio_hash.lower())
    if not ekey:
        raise KugouError(
            f"酷狗密钥库中没有此 KGG 的密钥（audioHash={audio_hash or '无法读取'}）。"
            "请先用当前 Windows 用户在酷狗客户端完整播放该歌曲，再重试")
    dec, key_len = create_qmc2(ekey)
    return h, dec, key_len


def decrypt_kgg_v5(data, progress_cb=None, cancel_event=None):
    h, dec, key_len = _prepare_kgg_v5(data)
    audio_enc = data[h["audio_offset"]:]
    out = bytearray(len(audio_enc))
    chunk = 0x20000
    pos = 0
    while pos < len(audio_enc):
        end = min(pos + chunk, len(audio_enc))
        seg = bytearray(audio_enc[pos:end])
        dec.decrypt(seg, pos)
        out[pos:end] = seg
        pos = end
        if progress_cb and len(audio_enc):
            progress_cb(min(99, pos * 100 // len(audio_enc)))
        if cancel_event and cancel_event.is_set():
            return False
    audio = bytes(out)
    fmt = _probe_format(audio[:16], strict=True)
    if fmt is None:
        raise KugouError("KGG 密钥已找到，但解密结果不是有效音频；密钥可能已失效")
    return {"audio": audio, "format": fmt, "kind": h["kind"],
            "audio_offset": h["audio_offset"], "key_len": key_len}


def _is_v5(data):
    try:
        return bool(detect(data)) and struct.unpack_from("<I", data, 0x14)[0] == 5
    except Exception:
        return False
