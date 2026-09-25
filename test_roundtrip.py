# -*- coding: utf-8 -*-
"""
NCM 加密/解密 roundtrip 自测：按网易云加密流程反向构造合成 NCM，
再用 ncm.py 解密，验证还原的音频与原始数据完全一致。
"""
import base64
import json
import os
import random
import struct
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ncm  # noqa: E402


def make_audio(fmt, seed):
    rng = random.Random(seed)
    if fmt == "mp3":
        # 伪 MP3：ID3v2 头 + 一个合法 MPEG 帧头 + 随机数据
        n = 64 * 1024
        body = bytearray(rng.randbytes(n))
        body[0:3] = b"ID3"
        # 一个 MPEG1 Layer3 帧头 0xFFFBxxxx
        body[10:13] = b"\xFF\xFB\x90\x64"
        return bytes(body)
    else:
        n = 64 * 1024
        body = bytearray(rng.randbytes(n))
        body[0:4] = b"fLaC"
        return bytes(body)


def make_ncm(fmt="mp3", with_meta=True, with_cover=True, seed=42):
    rng = random.Random(seed)
    audio = make_audio(fmt, seed)
    rc4_key = rng.randbytes(128)
    rc4_key = bytearray(rc4_key)
    rc4_key[17] = 17                      # 指示 RC4 key 长度
    rc4_key[18:35] = rng.randbytes(17)    # 17 字节 RC4 key
    mkey = bytes(rc4_key)

    # key 段：PKCS7 pad -> AES-128-ECB(CORE_KEY) -> xor 0x64
    padded = ncm._pkcs7_pad(mkey)
    key_data = bytes(b ^ 0x64 for b in ncm.aes_ecb_encrypt(ncm.CORE_KEY, padded))

    # meta 段
    meta_blob = b""
    if with_meta:
        meta_json = json.dumps({
            "musicName": "测试歌曲",
            "artist": [["歌手A"], ["歌手B"]],
            "album": "测试专辑",
            "format": fmt,
            "bitrate": 320,
            "duration": 220,
        }).encode("utf-8")
        enc = ncm.aes_ecb_encrypt(ncm.MODIFY_KEY, ncm._pkcs7_pad(b"music:" + meta_json))
        mdata = b"163 key(Don't modify):" + base64.b64encode(enc)
        meta_blob = bytes(b ^ 0x63 for b in mdata)

    # 封面段
    cover = b""
    if with_cover:
        cover = b"\x89PNG\r\n\x1a\n" + rng.randbytes(500)
    img_len = len(cover)
    cover_frame_len = img_len + 4 if with_cover else 0
    frame_tail = b"MYMU" if with_cover else b""

    # 音频加密：keystream 异或（keystream 由 RC4 key 决定）
    box = ncm.build_key_box(mkey[17:])
    ks = ncm.build_keystream(box)
    enc_audio = ncm.xor_keystream(audio, ks)

    parts = [
        b"CTENFDAM",
        b"\x01\x00",
        struct.pack("<I", len(key_data)), key_data,
        struct.pack("<I", len(meta_blob)), meta_blob,
        b"\x00\x00\x00\x00\x01",
        struct.pack("<I", cover_frame_len),
        struct.pack("<I", img_len),
        cover,
        frame_tail,
        enc_audio,
    ]
    return b"".join(parts)


def main():
    ok = True
    cases = [
        ("mp3", True, True),
        ("flac", True, True),
        ("mp3", False, True),   # 无 meta
        ("mp3", True, False),   # 无封面
        ("flac", False, False),
    ]
    for fmt, with_meta, with_cover in cases:
        data = make_ncm(fmt, with_meta, with_cover)
        info = ncm.decrypt_data(data)
        passed = info["audio"] == info and False  # placeholder, replaced below
        audio_ok = info["audio"] == make_audio(fmt, seed_for(fmt))
        fmt_ok = info["format"] == fmt
        meta_ok = True
        if with_meta:
            meta_ok = isinstance(info["meta"], dict) and info["meta"]["format"] == fmt
        cover_ok = not with_cover or info["image"].startswith(b"\x89PNG")
        passed = audio_ok and fmt_ok and meta_ok and cover_ok
        status = "PASS" if passed else "FAIL"
        if not passed:
            ok = False
        print(f"[{status}] fmt={fmt} meta={with_meta} cover={with_cover} "
              f"audio_ok={audio_ok} fmt_ok={fmt_ok} meta_ok={meta_ok} cover_ok={cover_ok}")

    # 单独用固定 seed 复现音频做精确字节对比
    data = make_ncm("mp3", True, True, seed=1234)
    info = ncm.decrypt_data(data)
    expect = make_audio("mp3", seed=1234)
    if info["audio"] == expect:
        print("[PASS] 音频 128KB 逐字节一致 (seed=1234)")
    else:
        ok = False
        print("[FAIL] 音频字节不一致")
    if info["image"].startswith(b"\x89PNG\r\n\x1a\n") and len(info["image"]) == 508:
        print("[PASS] 封面图片还原一致")
    else:
        ok = False
        print("[FAIL] 封面图片还原不一致")
    if info["meta"]["musicName"] == "测试歌曲":
        print("[PASS] 元数据解析正确")
    else:
        ok = False
        print("[FAIL] 元数据解析错误")

    print("\n总体结果:", "全部通过 ✓" if ok else "存在失败 ✗")
    return 0 if ok else 1


def seed_for(fmt):
    # make_ncm 内部用 seed=42 生成音频，这里用对应 seed 复现
    return 42


if __name__ == "__main__":
    sys.exit(main())
