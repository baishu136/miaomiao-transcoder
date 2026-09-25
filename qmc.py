# -*- coding: utf-8 -*-
"""
QQ 音乐 QMC 加密音频解密模块（QMCv1 + QMCv2）。

算法与常量严格对齐 unlock-music 的 qmc.ts / qmcMask.ts / qmc_key.ts：
  * QMCv1（qmc0/qmc2/qmc3/qmcflac/qmcogg/bkcmp3/bkcflac/tkm）：
      内置默认 mask（44 字节矩阵 -> 128 字节）直接逐字节异或
  * QMCv1 检测型（mflac/mgg 老格式）：从音频头检测 128 字节 mask
  * QMCv2（mflac/mgg 新格式）：文件尾带加密 key，
      - 尾部 Base64 key -> TEA-CBC 解密 -> 256 字节 RC4 原始密钥
      - 256 字节 -> 掩码 Mask128；512 字节 -> 改进版 RC4 (HardenedRC4)
"""
import base64
import math
import os
import struct

# ---------- 常量（来自 unlock-music qmcMask.ts） ----------

FLAC_HEADER = b"fLaC"
OGG_HEADER = b"OggS"

QMCDefaultMaskMatrix = bytes([
    0xde, 0x51, 0xfa, 0xc3, 0x4a, 0xd6, 0xca, 0x90,
    0x7e, 0x67, 0x5e, 0xf7, 0xd5, 0x52, 0x84, 0xd8,
    0x47, 0x95, 0xbb, 0xa1, 0xaa, 0xc6, 0x66, 0x23,
    0x92, 0x62, 0xf3, 0x74, 0xa1, 0x9f, 0xf4, 0xa0,
    0x1d, 0x3f, 0x5b, 0xf0, 0x13, 0x0e, 0x09, 0x3d,
    0xf9, 0xbc, 0x00, 0x11,
])

QMOggPublicHeader1 = bytes([
    0x4f, 0x67, 0x67, 0x53, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff,
    0xff, 0xff, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff, 0x01, 0x1e, 0x01, 0x76, 0x6f, 0x72,
    0x62, 0x69, 0x73, 0x00, 0x00, 0x00, 0x00, 0x02, 0x44, 0xac, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0xee, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0xb8, 0x01, 0x4f, 0x67, 0x67, 0x53, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff, 0x01, 0x00, 0x00, 0x00,
    0xff, 0xff, 0xff, 0xff])
QMOggPublicHeader2 = bytes([
    0x03, 0x76, 0x6f, 0x72, 0x62, 0x69, 0x73, 0x2c, 0x00, 0x00, 0x00, 0x58, 0x69, 0x70, 0x68, 0x2e,
    0x4f, 0x72, 0x67, 0x20, 0x6c, 0x69, 0x62, 0x56, 0x6f, 0x72, 0x62, 0x69, 0x73, 0x20, 0x49, 0x20,
    0x32, 0x30, 0x31, 0x35, 0x30, 0x31, 0x30, 0x35, 0x20, 0x28, 0xe2, 0x9b, 0x84, 0xe2, 0x9b, 0x84,
    0xe2, 0x9b, 0x84, 0xe2, 0x9b, 0x84, 0x29, 0xff, 0x00, 0x00, 0x00, 0xff, 0x00, 0x00, 0x00, 0x54,
    0x49, 0x54, 0x4c, 0x45, 0x3d])
QMOggPublicConf1 = [
    9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 0, 0,
    0, 0, 9, 9, 9, 9, 0, 0, 0, 0, 9, 9, 9, 9, 9, 9,
    9, 9, 9, 9, 9, 9, 9, 6, 3, 3, 3, 3, 6, 6, 6, 6,
    3, 3, 3, 3, 6, 6, 6, 6, 6, 9, 9, 9, 9, 9, 9, 9,
    9, 9, 9, 9, 9, 9, 9, 9, 0, 0, 0, 0, 9, 9, 9, 9,
    0, 0, 0, 0]
QMOggPublicConf2 = [
    3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3,
    3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3,
    3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3,
    3, 3, 3, 3, 3, 3, 3, 0, 1, 3, 3, 0, 1, 3, 3, 3,
    3, 3, 3, 3, 3]

# ---------- 128/44 mask 映射 ----------

def _build_mapping():
    slots = [[] for _ in range(256)]
    for i in range(128):
        slots[(i * i + 27) % 256].append(i)
    nonempty = [s for s in slots if s]     # 按 realIdx 升序，对应 JS forEach 跳过空洞
    mask128to44 = [0] * 128
    for idx44, s in enumerate(nonempty):
        for i128 in s:
            mask128to44[i128] = idx44
    return nonempty, mask128to44


_ALL_MAPPING, _MASK128TO44 = _build_mapping()


def generate128(matrix44: bytes) -> bytes:
    """44 字节 mask -> 128 字节（QmcMask._generate128）。"""
    if len(matrix44) != 44:
        raise ValueError("mask-44 长度必须为 44")
    m128 = bytearray(128)
    for idx44, s in enumerate(_ALL_MAPPING):
        for i128 in s:
            m128[i128] = matrix44[idx44]
    return bytes(m128)


class QmcMask:
    def __init__(self, matrix):
        matrix = bytes(matrix)
        if len(matrix) == 44:
            self.matrix128 = generate128(matrix)
        elif len(matrix) == 128:
            self.matrix128 = matrix
        else:
            raise ValueError("invalid mask length")

    def get_matrix128(self) -> bytes:
        return self.matrix128


def _xor_repeat(data, key: bytes, key_offset: int = 0) -> bytes:
    """Use CPython's C-backed big integers for a fast repeating XOR."""
    if not data:
        return b""
    key_offset %= len(key)
    rotated = key[key_offset:] + key[:key_offset]
    stream = (rotated * ((len(data) + len(key) - 1) // len(key)))[:len(data)]
    value = int.from_bytes(data, "little") ^ int.from_bytes(stream, "little")
    return value.to_bytes(len(data), "little")


class QmcStaticCipher:
    """Streaming QMCv1 cipher retaining the 0x8000 discontinuity state."""

    def __init__(self, matrix128: bytes):
        if len(matrix128) != 128:
            raise ValueError("QMC mask length must be 128")
        self.mask = bytes(matrix128)
        self.index = -1

    def _next_jump(self):
        if self.index < 0x8000:
            return 0x8000
        return ((self.index + 1) // 0x8000 + 1) * 0x8000 - 1

    def decrypt(self, data) -> bytes:
        data = memoryview(data)
        out = bytearray()
        pos = 0
        while pos < len(data):
            jump = self._next_jump()
            # Ordinary input bytes advance index by one.  The byte at a jump
            # advances it twice before applying the mask.
            normal = min(len(data) - pos, max(0, jump - (self.index + 1)))
            if normal:
                out.extend(_xor_repeat(data[pos:pos + normal], self.mask,
                                       (self.index + 1) % 128))
                pos += normal
                self.index += normal
            if pos < len(data):
                self.index += 2
                out.append(data[pos] ^ self.mask[self.index % 128])
                pos += 1
        return bytes(out)


def qmc_decrypt(data, matrix128: bytes) -> bytes:
    """QmcMask.Decrypt：带 0x8000 跳变规则的周期异或。"""
    return QmcStaticCipher(matrix128).decrypt(data)


def qmc_mask_get_default() -> QmcMask:
    return QmcMask(QMCDefaultMaskMatrix)


# ---------- 检测 ----------

def detect_mflac(data) -> QmcMask:
    """从音频数据头部检测出 mask（使解密后头部成为合法 FLAC）。"""
    search_len = min(0x8000, len(data))
    mask = None
    for block_idx in range(0, search_len, 128):
        try:
            m = QmcMask(data[block_idx:block_idx + 128])
            if qmc_decrypt(data[:4], m.matrix128) == FLAC_HEADER:
                mask = m
                break
        except Exception:
            pass
    return mask


def _calc_mask_from_confidence(conf) -> int:
    if not conf:
        raise ValueError("can not match at least one key")
    best = 0
    best_conf = -1
    for val, c in conf.items():
        if c > best_conf:
            best_conf = c
            best = val
    return best


def detect_mgg(data) -> QmcMask:
    """从音频数据头部检测出 mask（Ogg 置信度统计）。"""
    if len(data) < 0x100:
        return None
    matrix_confidence = [dict() for _ in range(44)]
    page2 = data[0x54] ^ data[0x0c] ^ QMOggPublicHeader1[0x0c]
    sp_header = _gen_ogg_header(page2)
    sp_conf = _gen_ogg_conf(page2)
    for idx128 in range(len(sp_header)):
        if sp_conf[idx128] == 0:
            continue
        idx44 = _MASK128TO44[idx128 % 128]
        _m = data[idx128] ^ sp_header[idx128]
        c = sp_conf[idx128]
        matrix_confidence[idx44][_m] = matrix_confidence[idx44].get(_m, 0) + c
    matrix = bytearray(44)
    try:
        for i in range(44):
            matrix[i] = _calc_mask_from_confidence(matrix_confidence[i])
    except ValueError:
        return None
    mask = QmcMask(matrix)
    if qmc_decrypt(data[:4], mask.matrix128) != OGG_HEADER:
        return None
    return mask


def _gen_ogg_header(page2: int) -> bytes:
    spec = bytes([page2, 0xFF] + [0xFF] * max(0, page2 - 2) + [0xFF])
    return QMOggPublicHeader1 + spec + QMOggPublicHeader2


def _gen_ogg_conf(page2: int) -> list:
    spec_conf = [6, 0] + [4] * max(0, page2 - 2) + [0]
    return QMOggPublicConf1 + spec_conf + QMOggPublicConf2


# ---------- 文件级解密 ----------

# 各扩展名 -> (检测方式, 输出格式)
QMC_HANDLERS = {
    "mflac": ("detect_flac", "flac"),
    "mflac0": ("detect_flac", "flac"),
    "mflac1": ("detect_flac", "flac"),
    "mgg": ("detect_ogg", "ogg"),
    "mgg0": ("detect_ogg", "ogg"),
    "mgg1": ("detect_ogg", "ogg"),
    "mggl": ("detect_ogg", "ogg"),
    "qmc0": ("default", "mp3"),
    "qmc1": ("default", "mp3"),
    "qmc2": ("default", "ogg"),
    "qmc3": ("default", "mp3"),
    "qmc4": ("default", "ogg"),
    "qmc6": ("default", "ogg"),
    "qmc8": ("default", "ogg"),
    "qmcflac": ("default", "flac"),
    "qmcogg": ("default", "ogg"),
    "bkcmp3": ("default", "mp3"),
    "bkcm4a": ("default", "m4a"),
    "bkcflac": ("default", "flac"),
    "bkcwav": ("default", "wav"),
    "bkcape": ("default", "ape"),
    "bkcogg": ("default", "ogg"),
    "bkcwma": ("default", "wma"),
    "tkm": ("default", "m4a"),
    "tm0": ("default", "mp3"),
    "tm2": ("default", "m4a"),
    "tm3": ("default", "mp3"),
    "tm6": ("default", "m4a"),
    "666c6163": ("default", "flac"),
    "6d7033": ("default", "mp3"),
    "6f6767": ("default", "ogg"),
    "6d3461": ("default", "m4a"),
    "776176": ("default", "wav"),
}


class QmcError(Exception):
    """QMC 文件解析/解密异常。"""


MUSICEX_MAGIC = b"musicex\x00"
MUSICEX_MIN_TAG_SIZE = 0xC0


def _read_musicex_footer(data: bytes):
    """Return the last MusicEx v1 footer, or ``None`` when it is absent.

    QQ Music 19.57+ may store the media key in an external MMKV vault and
    append a 192-byte descriptor to the encrypted audio.  Some transferred
    files contain the same descriptor more than once, so callers must not
    assume that stripping a single footer exposes the audio payload.
    """
    if len(data) < 16 or data[-8:] != MUSICEX_MAGIC:
        return None
    tag_size, version = struct.unpack_from("<II", data, len(data) - 16)
    if version != 1 or tag_size < MUSICEX_MIN_TAG_SIZE or tag_size > len(data):
        raise QmcError("MusicEx 尾部结构损坏或版本不受支持")
    start = len(data) - tag_size
    tag = data[start:]
    if len(tag) < 0xB0:
        raise QmcError("MusicEx 尾部数据不完整")

    def _utf16_ascii(offset, byte_length):
        raw = tag[offset:offset + byte_length]
        return raw.decode("utf-16le", "replace").split("\x00", 1)[0]

    return {
        "start": start,
        "size": tag_size,
        "version": version,
        "song_id": struct.unpack_from("<I", tag, 0)[0],
        "media_id": _utf16_ascii(0x0C, 30 * 2),
        "media_file_name": _utf16_ascii(0x48, 50 * 2),
    }


def _strip_musicex_footers(data: bytes):
    """Strip all contiguous MusicEx descriptors and return their metadata."""
    footers = []
    payload = data
    while True:
        footer = _read_musicex_footer(payload)
        if footer is None:
            break
        footers.append(footer)
        payload = payload[:footer["start"]]
    return payload, footers


def _musicex_key_required(footer):
    media_name = footer.get("media_file_name") or "未知媒体"
    media_id = footer.get("media_id") or "未知"
    return QmcError(
        "该文件是新版 QQ 音乐 MusicEx 加密格式，音频密钥不在文件内。"
        "需要下载该文件时一同生成的 MMKVStreamEncryptId 密钥库才能转换。"
        f"媒体文件: {media_name}；Media ID: {media_id}"
    )


def _musicex_master_key(footer):
    """Resolve and derive the QMC2 master key for a MusicEx descriptor."""
    try:
        import qqmusic_key
        ekey = qqmusic_key.resolve_ekey(
            footer.get("media_id") or "",
            footer.get("media_file_name") or "",
        )
        raw = _decode_qmc_base64(ekey.encode("ascii"))
        master = decrypt_qmc2_key(raw)
    except ImportError:
        raise _musicex_key_required(footer)
    except Exception as exc:
        if exc.__class__.__name__ == "QQMusicKeyError":
            raise QmcError(str(exc)) from None
        raise QmcError(f"MusicEx 密钥解析失败: {exc}") from None
    if not master:
        raise QmcError("MusicEx 密钥服务返回了空密钥")
    return master


def _cipher_from_master(master: bytes):
    """Create a seekable QMC2 block decryptor from a derived master key."""
    if len(master) <= 300:
        # Current MusicEx MapCipher operates over the full derived key.  The
        # historical 256-byte path is exactly equivalent to Mask128.
        def map_cipher(block, offset):
            out = bytearray(len(block))
            key_len = len(master)
            for i, value in enumerate(block):
                absolute = offset + i
                mapped = absolute % 0x7FFF if absolute > 0x7FFF else absolute
                idx = (mapped * mapped + 71214) % key_len
                shift = ((idx & 7) + 4) % 8
                mask = ((master[idx] << shift) | (master[idx] >> shift)) & 0xFF
                out[i] = value ^ mask
            return bytes(out)
        return map_cipher
    rc4 = HardenedRC4(master)
    return lambda block, off: _xor_hardened_rc4(block, rc4, off)


def _decode_qmc_base64(value: bytes) -> bytes:
    """Decode a QMC Base64 field, tolerating omitted RFC 4648 padding.

    QQ Music files seen in the wild may omit one or two trailing ``=``
    characters.  Whitespace is harmless, but a length congruent to one
    modulo four can never be valid Base64 and is rejected explicitly.
    """
    compact = b"".join(bytes(value).split())
    remainder = len(compact) % 4
    if remainder == 1:
        raise ValueError("Base64 长度非法")
    if remainder:
        compact += b"=" * (4 - remainder)
    return base64.b64decode(compact, validate=False)


def decrypt_data(data, ext: str, progress_cb=None, cancel_event=None):
    """解密 QMC 数据，返回 {'audio': bytes, 'format': str, 'mask': bytes}。"""
    ext = (ext or "").lower().lstrip(".")
    if ext not in QMC_HANDLERS:
        raise QmcError(f"不支持的 QMC 扩展名: {ext}")
    mode, default_ext = QMC_HANDLERS[ext]

    # MusicEx must be checked before the legacy four-byte key length.  Its
    # suffix is ``cex\0`` which, interpreted as a little-endian integer,
    # looks like a plausible 7.5 MiB QMCv2 key and used to cause the
    # misleading ``Incorrect padding`` error.
    _musicex_audio, musicex_footers = _strip_musicex_footers(data)
    if musicex_footers:
        master = _musicex_master_key(musicex_footers[0])
        cipher = _cipher_from_master(master)
        audio = cipher(_musicex_audio, 0)
        fmt = _probe_format(audio[:16])
        if fmt is None:
            raise QmcError("MusicEx 密钥已取得，但与该文件不匹配或账号权限已失效")
        return {"audio": audio, "format": fmt, "master_key_len": len(master)}

    # QQ Music reuses qmc0/qmc3/qmcflac/qmcogg suffixes for both versions.
    # An explicit QTag/STag is authoritative; a plausible legacy length tail
    # is attempted first and may safely fall back to QMCv1.
    if _looks_like_qmcv2(data):
        marked = data[-4:] in (b"QTag", b"STag")
        try:
            return _decrypt_qmcv2(data, ext)
        except QmcError:
            if marked:
                raise

    if mode == "detect_flac":
        audio = _decrypt_detect(data, detect_mflac, "flac")
        if audio is None:
            raise QmcError(f"{ext}：无法识别 QMCv1 掩码，且未找到可用的 QMCv2 密钥")
    elif mode == "detect_ogg":
        audio = _decrypt_detect(data, detect_mgg, "ogg")
        if audio is None:
            raise QmcError(f"{ext}：无法识别 QMCv1 掩码，且未找到可用的 QMCv2 密钥")
    else:
        mask = qmc_mask_get_default().matrix128
        audio = qmc_decrypt(data, mask)

    fmt = _probe_format(audio[:16])
    if fmt is None:
        raise QmcError(f"{ext} 解密后不是有效音频（可能是新版 STag/EncV2 或文件已损坏）")
    return {"audio": audio, "format": fmt, "mask": bytes(mask) if 'mask' in locals() else None}


def _decrypt_detect(data, detect_fn, want_head: str):
    """检测型：尾部 4 字节为 key 长度，剥离后检测 mask 并解密。"""
    if len(data) < 8:
        return None
    key_len = struct.unpack_from("<I", data, len(data) - 4)[0]
    key_pos = len(data) - 4 - key_len
    if key_pos < 0 or key_len > len(data):
        return None
    audio = data[:key_pos]
    mask = detect_fn(audio)
    if mask is None:
        return None
    dec = qmc_decrypt(audio, mask.matrix128)
    if not dec[:4] == want_head.encode():
        return None
    return dec


def _probe_format(head: bytes):
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:4] == b"RIFF":
        return "wav"
    if head[:4] == b"MAC ":
        return "ape"
    if head[:4] == b"0&\xb2u":
        return "wma"
    return None


# ==========================================================================
# QMCv2：TEA 密钥解密 + Mask128 / HardenedRC4 音频解密
# ==========================================================================

_TEA_DELTA = 0x9E3779B9
_ENCV2_PREFIX = b"QQMusic EncV2,Key:"
_ENCV2_KEY1 = b"386ZJY!@#*$%^&)("
_ENCV2_KEY2 = b"*$%^&)(#@!386ZJY"


def _tea_ecb_decrypt(cipher8: bytes, key16: bytes, rounds: int = 32) -> bytes:
    """TarsCpp TEA ECB 解密（8 字节块，16 字节 key，big-endian）。

    注意：JS 的 `<<`/`^` 都是 32 位截断，Python 需显式 `& 0xFFFFFFFF`。
    """
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


def _tea_ecb_encrypt(plain8: bytes, key16: bytes, rounds: int = 32) -> bytes:
    """TarsCpp TEA ECB 加密（JS `<<` 32 位截断语义）。"""
    v0 = int.from_bytes(plain8[:4], "big")
    v1 = int.from_bytes(plain8[4:8], "big")
    k0 = int.from_bytes(key16[:4], "big")
    k1 = int.from_bytes(key16[4:8], "big")
    k2 = int.from_bytes(key16[8:12], "big")
    k3 = int.from_bytes(key16[12:16], "big")
    sums = 0
    for _ in range(rounds // 2):
        sums = (sums + _TEA_DELTA) & 0xFFFFFFFF
        v0 = (v0 + ((((v1 << 4) & 0xFFFFFFFF) + k0)
                     ^ ((v1 + sums) & 0xFFFFFFFF)
                     ^ (((v1 >> 5) & 0xFFFFFFFF) + k1))) & 0xFFFFFFFF
        v1 = (v1 + ((((v0 << 4) & 0xFFFFFFFF) + k2)
                     ^ ((v0 + sums) & 0xFFFFFFFF)
                     ^ (((v0 >> 5) & 0xFFFFFFFF) + k3))) & 0xFFFFFFFF
    return v0.to_bytes(4, "big") + v1.to_bytes(4, "big")


def _tea_cbc_decrypt(cipherdata: bytes, key16: bytes, rounds: int = 32) -> bytes:
    """TarsCpp TEA CBC 解密（PadLen/Padding/Salt/Body/Zero 结构）。"""
    if len(cipherdata) < 16 or len(cipherdata) % 8 != 0:
        raise ValueError("invalid TEA CBC cipher length")
    dest = bytearray(_tea_ecb_decrypt(cipherdata[:8], key16, rounds))
    pad_len = dest[0] & 0x7
    out_len = len(cipherdata) - pad_len - 2 - 7 - 1
    if out_len < 0:
        raise ValueError("estimated plain length < 0")
    out = bytearray(out_len)
    iv_prev = bytearray(8)
    iv_cur = bytearray(cipherdata[:8])
    pos = 8
    dest_idx = 1 + pad_len

    def _crypt_block():
        nonlocal pos
        iv_prev[:] = iv_cur[:]
        iv_cur[:] = cipherdata[pos:pos + 8]
        dest[:] = bytearray(_tea_ecb_decrypt(bytes(x ^ y for x, y in zip(dest, iv_cur)), key16, rounds))
        pos += 8

    i = 1
    while i <= 2:  # 跳过 Salt
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


def _tea_cbc_encrypt(plaindata: bytes, key16: bytes, rounds: int = 32) -> bytes:
    """TarsCpp TEA CBC 加密（用于构造测试数据）。"""
    import random as _rng
    salt_zero_body = len(plaindata) + 2 + 7 + 1
    pad_len = (-salt_zero_body) % 8
    out_len = salt_zero_body + pad_len

    src = bytearray(8)
    src[0] = (_rng.randrange(256) & 0xF8) | pad_len
    src_idx = 1
    for _ in range(pad_len):
        src[src_idx] = _rng.randrange(256)
        src_idx += 1
    iv_plain = bytearray(8)
    iv_crypt = bytearray(8)
    out = bytearray(out_len)
    op = 0

    def _crypt_block():
        nonlocal src_idx, op
        src[:] = bytearray(x ^ y for x, y in zip(src, iv_crypt))
        out[op:op + 8] = _tea_ecb_encrypt(bytes(src), key16, rounds)
        out[op:op + 8] = bytearray(x ^ y for x, y in zip(out[op:op + 8], iv_plain))
        iv_plain[:] = src[:]
        iv_crypt[:] = out[op:op + 8]
        op += 8

    i = 1
    while i <= 2:  # Salt
        if src_idx < 8:
            src[src_idx] = _rng.randrange(256)
            src_idx += 1
            i += 1
        if src_idx == 8:
            _crypt_block()
            src_idx = 0
    for b in plaindata:
        src[src_idx] = b
        src_idx += 1
        if src_idx == 8:
            _crypt_block()
            src_idx = 0
    i = 1
    while i <= 7:  # Zero
        if src_idx < 8:
            src[src_idx] = 0
            src_idx += 1
            i += 1
        if src_idx == 8:
            _crypt_block()
            src_idx = 0
    return bytes(out)


def _simple_make_key(salt: int, length: int):
    return [int(abs(math.tan(salt + i * 0.1) * 100)) for i in range(length)]


def decrypt_qmc2_key(raw: bytes) -> bytes:
    """QMCv2 密钥解密（unlock-music qmc_key.ts DecryptKey）。

    返回 8 字节头 + 解密后的 RC4 原始密钥（256 或 512 字节）。
    """
    if raw.startswith(_ENCV2_PREFIX):
        payload = raw[len(_ENCV2_PREFIX):]
        try:
            payload = _tea_cbc_decrypt(payload, _ENCV2_KEY1, rounds=32)
            payload = _tea_cbc_decrypt(payload, _ENCV2_KEY2, rounds=32)
            raw = _decode_qmc_base64(payload)
        except Exception as exc:
            raise ValueError(f"QMC EncV2 密钥解包失败: {exc}") from exc
    if len(raw) < 16:
        raise ValueError("QMCv2 key 数据过短")
    raw_dec = bytearray(raw)
    simple_key = _simple_make_key(106, 8)          # [0x69,0x56,0x46,0x38,0x2b,0x20,0x15,0x0b]
    tea_key = bytearray(16)
    for i in range(8):
        tea_key[i * 2] = simple_key[i]
        tea_key[i * 2 + 1] = raw_dec[i]
    sub = _tea_cbc_decrypt(bytes(raw_dec[8:]), bytes(tea_key), rounds=32)
    raw_dec[8:8 + len(sub)] = sub
    return bytes(raw_dec[:8 + len(sub)])


def encrypt_qmc2_key(rc4_key: bytes) -> bytes:
    """QMCv2 密钥加密（构造测试数据用）：8 字节随机头 + TEA-CBC 密文。"""
    import random as _rng
    raw8 = bytes(_rng.randrange(256) for _ in range(8))
    simple_key = _simple_make_key(106, 8)
    tea_key = bytearray(16)
    for i in range(8):
        tea_key[i * 2] = simple_key[i]
        tea_key[i * 2 + 1] = raw8[i]
    body = _tea_cbc_encrypt(rc4_key, bytes(tea_key), rounds=32)
    return raw8 + body


# ---------- Mask128（QMCv2 256 字节 key -> 128 掩码） ----------

def key256_to_mask128(key256: bytes) -> bytes:
    """unlock-music Mask128.from_qmcv2_key256。"""
    if len(key256) != 256:
        raise ValueError("key256 长度必须为 256")
    m128 = bytearray(128)
    for idx128 in range(128):
        idx = (idx128 * idx128 + 71214) % 256
        value = key256[idx]
        rotate = ((idx & 7) + 4) % 8
        m128[idx128] = ((value << rotate) % 256) | ((value >> rotate) % 256)
    return bytes(m128)


def _mask128_keystream(mask128: bytes, offset: int, nbytes: int):
    """Mask128 密钥流（libtakiyasha cls_keystream）。"""
    firstblk = mask128 * 256
    secondblk = firstblk[1:-1]
    startblk = firstblk + secondblk
    startblk_len = len(startblk)
    commonblk = firstblk[:-1]
    commonblk_len = len(commonblk)

    if 0 <= offset < startblk_len:
        take = min(nbytes, startblk_len - offset)
        yield from startblk[offset:offset + take]
        nbytes -= take
        if nbytes <= 0:
            return
        offset = 0
    else:
        offset -= startblk_len
    t_off = offset % commonblk_len
    before = 0 if t_off == 0 else commonblk_len - t_off
    yield from commonblk[t_off:t_off + before]
    nbytes -= before
    whole, rest = divmod(nbytes, commonblk_len)
    for _ in range(whole):
        yield from commonblk
    yield from commonblk[:rest]


def _xor_mask128(data: bytes, mask128: bytes, base_offset: int = 0) -> bytes:
    ks = _mask128_keystream(mask128, base_offset, len(data))
    return bytes(a ^ b for a, b in zip(data, ks))


# ---------- HardenedRC4（QMCv2 512 字节 key） ----------

class HardenedRC4:
    """QMC 改进版 RC4（libtakiyasha HardenedRC4）。"""

    first_segment_size = 128
    common_segment_size = 5120

    def __init__(self, key: bytes):
        if not key or b"\x00" in key:
            raise ValueError("HardenedRC4 key 不能为空或含 NUL")
        self.key = key
        key_len = len(key)
        box = bytearray(i % 256 for i in range(key_len))
        j = 0
        for i in range(key_len):
            j = (j + box[i] + key[i]) % key_len
            box[i], box[j] = box[j], box[i]
        self.box = box
        base = 1
        for v in key:
            if v == 0:
                continue
            nxt = (base * v) & 0xFFFFFFFF
            if nxt == 0 or nxt <= base:
                break
            base = nxt
        self.hash_base = base

    def _get_segment_skip(self, value: int) -> int:
        seed = self.key[value % len(self.key)]
        idx = int(self.hash_base / ((value + 1) * seed) * 100)
        return idx % len(self.key)

    def _yield_first(self, blksize: int, offset: int):
        for i in range(offset, offset + blksize):
            yield self.key[self._get_segment_skip(i)]

    def _yield_common(self, blksize: int, offset: int):
        key_len = len(self.key)
        box = self.box.copy()
        j, k = 0, 0
        skip_len = offset % self.common_segment_size + self._get_segment_skip(
            offset // self.common_segment_size)
        for i in range(-skip_len, blksize):
            j = (j + 1) % key_len
            k = (box[j] + k) % key_len
            box[j], box[k] = box[k], box[j]
            if i >= 0:
                yield box[(box[j] + box[k]) % key_len]

    def keystream(self, offset: int, nbytes: int):
        if 0 <= offset < self.first_segment_size:
            take = min(nbytes, self.first_segment_size - offset)
            yield from self._yield_first(take, offset)
            nbytes -= take
            offset += take
            if nbytes <= 0:
                return
        if offset % self.common_segment_size != 0:
            take = min(nbytes, self.common_segment_size - (offset % self.common_segment_size))
            yield from self._yield_common(take, offset)
            nbytes -= take
            offset += take
            if nbytes <= 0:
                return
        while nbytes > self.common_segment_size:
            yield from self._yield_common(self.common_segment_size, offset)
            nbytes -= self.common_segment_size
            offset += self.common_segment_size
        if nbytes > 0:
            yield from self._yield_common(nbytes, offset)


def _xor_hardened_rc4(data: bytes, rc4: HardenedRC4, base_offset: int = 0) -> bytes:
    ks = rc4.keystream(base_offset, len(data))
    return bytes(a ^ b for a, b in zip(data, ks))


def _extract_qmcv2_key(data: bytes):
    """从文件尾部提取加密 key，返回 (audio_enc, raw_key 字节)。支持老式 / QTag / STag。"""
    if len(data) < 20:
        raise QmcError("QMCv2 文件过短")

    tail = data[-4:]
    if tail == b"QTag":
        tag_len = struct.unpack_from(">I", data, len(data) - 8)[0]
        tag_start = len(data) - 8 - tag_len
        if tag_start < 0:
            raise QmcError("QMCv2 QTag 长度非法")
        tag = data[tag_start:len(data) - 8]
        parts = tag.split(b",")
        if len(parts) != 3:
            raise QmcError("QMCv2 QTag 结构非法")
        key_b64 = parts[0]
        if key_b64.startswith(b"QQMusic EncV2,Key:"):
            raise QmcError("QMCv2 使用新版 EncV2 加密（需 garble_keys），暂不支持")
        raw = _decode_qmc_base64(key_b64)
        return data[:tag_start], raw

    if tail == b"STag":
        raise QmcError("QMCv2 STag 格式需要外部主密钥，暂不支持")

    # 老式：尾部 4 字节 LE = Base64 key 长度
    b64_len = struct.unpack_from("<I", data, len(data) - 4)[0]
    key_pos = len(data) - 4 - b64_len
    if key_pos < 0 or b64_len <= 0:
        raise QmcError("QMCv2 尾部 key 长度非法")
    try:
        raw = _decode_qmc_base64(data[key_pos:-4])
    except Exception:
        raise QmcError("QMCv2 尾部 key 不是合法 Base64")
    return data[:key_pos], raw


def _decrypt_qmcv2(data: bytes, ext: str):
    """QMCv2 文件解密：尾部 key + TEA 解密 + RC4/Mask128 音频解密。"""
    audio_enc, raw = _extract_qmcv2_key(data)
    decrypted = decrypt_qmc2_key(raw)
    # master_key = 完整解密结果（256B -> Mask128 / 512B -> RC4，含 8B 原始头）
    master_key = decrypted

    if len(master_key) == 256:
        mask128 = key256_to_mask128(master_key)
        audio = _xor_mask128(audio_enc, mask128)
    elif len(master_key) == 512:
        rc4 = HardenedRC4(master_key)
        audio = _xor_hardened_rc4(audio_enc, rc4)
    else:
        raise QmcError(f"QMCv2 密钥长度异常: {len(master_key)}")

    fmt = _probe_format(audio[:16])
    if fmt is None:
        raise QmcError(f"{ext} 的 QMCv2 密钥已解析，但解密结果不是有效音频")
    return {"audio": audio, "format": fmt, "master_key_len": len(master_key)}


def _looks_like_qmcv2(data: bytes) -> bool:
    """判断文件是否为 QMCv2（QTag/STag 标记，或尾部 4 字节像是 Base64 key 长度）。"""
    if len(data) < 20:
        return False
    if data[-8:] == MUSICEX_MAGIC:
        return False
    if data[-4:] in (b"QTag", b"STag"):
        return True
    try:
        n = struct.unpack_from("<I", data, len(data) - 4)[0]
    except struct.error:
        return False
    return 0 < n < len(data) - 16


def decrypt_file(in_path: str, out_path: str, ext: str, write_audio: bool = True,
                 progress_cb=None, cancel_event=None):
    """Decrypt QMC and stream the plaintext to disk to keep memory bounded."""
    ext = (ext or "").lower().lstrip(".")
    if ext not in QMC_HANDLERS:
        raise QmcError(f"不支持的 QMC 扩展名: {ext}")
    size = os.path.getsize(in_path)
    if size < 8:
        raise QmcError("QMC 文件过短")

    # Inspect the fixed-size MusicEx trailer without loading the audio into
    # memory.  Its eKey is resolved from the current QQ Music session or the
    # current-user DPAPI cache; the suffix must not reach the legacy parser.
    musicex_footer = None
    musicex_audio_size = None
    with open(in_path, "rb") as probe:
        probe.seek(-16, os.SEEK_END)
        musicex_tail = probe.read(16)
        if musicex_tail[-8:] == MUSICEX_MAGIC:
            tag_size, version = struct.unpack_from("<II", musicex_tail, 0)
            if version != 1 or tag_size < MUSICEX_MIN_TAG_SIZE or tag_size > size:
                raise QmcError("MusicEx 尾部结构损坏或版本不受支持")
            probe.seek(size - tag_size)
            tag = probe.read(tag_size)
            _payload, footers = _strip_musicex_footers(tag)
            if not footers:
                raise QmcError("MusicEx 尾部数据不完整")
            musicex_footer = footers[0]
            musicex_audio_size = size - tag_size

    # Read only enough metadata to decide the cipher.  Explicit tags are
    # authoritative; length-tail false positives are allowed to fall back.
    with open(in_path, "rb") as src:
        src.seek(max(0, size - 8))
        tail8 = src.read(8)
        marker = tail8[-4:]
        key_len = (struct.unpack("<I", marker)[0]
                   if marker not in (b"QTag", b"STag") else 0)
        looks_v2 = (musicex_footer is None and
                    (marker in (b"QTag", b"STag") or 0 < key_len < size - 16))

        audio_size = musicex_audio_size if musicex_audio_size is not None else size
        cipher = None
        master_key_len = None
        v2_error = None
        if musicex_footer is not None:
            master = _musicex_master_key(musicex_footer)
            master_key_len = len(master)
            cipher = _cipher_from_master(master)
        elif looks_v2:
            try:
                if marker == b"STag":
                    raise QmcError("QMCv2 STag 文件不包含离线密钥，当前无法转换")
                if marker == b"QTag":
                    tag_len = struct.unpack(">I", tail8[:4])[0]
                    key_pos = size - 8 - tag_len
                    if key_pos < 0:
                        raise QmcError("QMCv2 QTag 长度非法")
                    src.seek(key_pos)
                    tag = src.read(tag_len)
                    parts = tag.split(b",")
                    if len(parts) != 3:
                        raise QmcError("QMCv2 QTag 结构非法")
                    key_b64 = parts[0]
                    if key_b64.startswith(b"QQMusic EncV2,Key:"):
                        raise QmcError("QMC EncV2 需要设备密钥，当前无法离线转换")
                    raw = _decode_qmc_base64(key_b64)
                    audio_size = key_pos
                else:
                    key_pos = size - 4 - key_len
                    src.seek(key_pos)
                    raw = _decode_qmc_base64(src.read(key_len))
                    audio_size = key_pos
                master = decrypt_qmc2_key(raw)
                master_key_len = len(master)
                if master_key_len == 256:
                    mask128 = key256_to_mask128(master)
                    cipher = lambda block, off: _xor_mask128(block, mask128, off)
                elif master_key_len == 512:
                    rc4 = HardenedRC4(master)
                    cipher = lambda block, off: _xor_hardened_rc4(block, rc4, off)
                else:
                    raise QmcError(f"QMCv2 密钥长度异常: {master_key_len}")
            except Exception as exc:
                v2_error = exc if isinstance(exc, QmcError) else QmcError(f"QMCv2 密钥解析失败: {exc}")
                if marker in (b"QTag", b"STag"):
                    raise v2_error
                cipher = None
                audio_size = size

        mode, _default_ext = QMC_HANDLERS[ext]
        if cipher is None:
            if mode.startswith("detect_"):
                # Legacy detect formats store the mask in the encrypted audio
                # and finish with key bytes + a 4-byte length.
                src.seek(size - 4)
                legacy_key_len = struct.unpack("<I", src.read(4))[0]
                legacy_audio_size = size - 4 - legacy_key_len
                if legacy_audio_size <= 0:
                    if v2_error:
                        raise v2_error
                    raise QmcError(f"{ext} 尾部长度非法")
                src.seek(0)
                sample = src.read(min(0x8000, legacy_audio_size))
                detector = detect_mflac if mode == "detect_flac" else detect_mgg
                detected = detector(sample)
                if detected is None:
                    if v2_error:
                        raise v2_error
                    raise QmcError(f"{ext}：未检测出有效 QMC 掩码")
                audio_size = legacy_audio_size
                stream_cipher = QmcStaticCipher(detected.matrix128)
            else:
                stream_cipher = QmcStaticCipher(qmc_mask_get_default().matrix128)
            cipher = lambda block, _off: stream_cipher.decrypt(block)

        src.seek(0)
        info = {"format": None, "audio_size": audio_size,
                "master_key_len": master_key_len}
        first_plain = b""
        out = open(out_path, "wb") if write_audio else None
        try:
            pos = 0
            while pos < audio_size:
                if cancel_event and cancel_event.is_set():
                    return False
                block = src.read(min(0x100000, audio_size - pos))
                if not block:
                    raise QmcError("QMC 文件被截断")
                plain = cipher(block, pos)
                if not first_plain:
                    first_plain = plain[:16]
                    info["format"] = _probe_format(first_plain)
                    if info["format"] is None:
                        raise QmcError(f"{ext} 解密后不是有效音频（密钥不匹配或文件已损坏）")
                if out:
                    out.write(plain)
                pos += len(block)
                if progress_cb:
                    progress_cb(min(99, pos * 100 // max(1, audio_size)))
        finally:
            if out:
                out.close()
        if progress_cb:
            progress_cb(100)
        return info
