# -*- coding: utf-8 -*-
"""
KGM/KGG 解密测试：
  1. 按算法反向加密真实音频片段（与权威实现数学等价）
  2. 构造完整 KGG 文件（含头部 + 密文音频）
  3. kugou.decrypt_data 解密并逐字节对比明文
"""
import os
import random
import struct
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kugou  # noqa: E402
from kugou_tables import T1, T2, T3  # noqa: E402

_XOR_LOWER = bytes(b ^ ((b & 0x0f) << 4) for b in range(256))


def gen_mask(idx):
    idx_urs4 = idx >> 4
    h = 0
    while idx_urs4 >= 17:
        h ^= T1[idx_urs4 % 272]
        idx_urs4 >>= 4
        h ^= T2[idx_urs4 % 272]
        idx_urs4 >>= 4
    return T3[idx % 272] ^ h


def encrypt(plain, key):
    """加密（逆操作，与权威实现对称验证）。"""
    out = bytearray(len(plain))
    for i, b in enumerate(plain):
        msk = gen_mask(i)
        out[i] = _XOR_LOWER[_XOR_LOWER[msk] ^ b] ^ key[i % 17]
    return bytes(out)


def make_kgg(audio, key, seed=1):
    rng = random.Random(seed)
    # 头部布局对齐权威实现
    header_len = 0x2c
    header = bytearray(rng.randbytes(header_len))
    header[0:16] = kugou.KGM_MAGIC
    struct.pack_into("<I", header, 0x10, header_len)
    header[0x1c:0x2c] = key[:16]   # + b"\x00" 由解析补
    return bytes(header) + audio


def main():
    ok = True
    # 真实 kgg 的 file_key = 文件[0x1c:0x2c] 16 字节 + 补 0x00，故末尾为 0
    key = bytes(range(1, 17)) + b"\x00"
    cases = [
        ("mp3", 64 * 1024, b"ID3" + b"\x00" * 5 + b"\xFF\xFB\x90\x64"),
        ("flac", 48 * 1024, b"fLaC"),
        ("ogg", 40 * 1024, b"OggS\x00"),
    ]
    for name, n, head in cases:
        rng = random.Random(42)
        body = bytearray(rng.randbytes(n))
        body[:len(head)] = head
        plain = bytes(body)
        enc = encrypt(plain, key)
        kgg_data = make_kgg(enc, key)
        info = kugou.decrypt_data(kgg_data)
        passed = info["audio"] == plain
        fmt_ok = info["format"] == name
        status = "PASS" if passed and fmt_ok else "FAIL"
        if not passed or not fmt_ok:
            ok = False
        print(f"[{status}] {name}: 音频还原={'是' if passed else '否'} 格式={info['format']}")

    # 大文件性能测试（5MB）
    big = bytearray(random.Random(7).randbytes(5 * 1024 * 1024))
    big[:3] = b"ID3"
    plain_big = bytes(big)
    enc_big = encrypt(plain_big, key)
    kgg_big = make_kgg(enc_big, key)
    import time
    t0 = time.time()
    info = kugou.decrypt_data(kgg_big)
    dt = time.time() - t0
    ok_big = info["audio"] == plain_big
    if not ok_big:
        ok = False
    print(f"[{'PASS' if ok_big else 'FAIL'}] 5MB 解密: {dt:.2f}s，还原={'是' if ok_big else '否'}")

    # 文件级流式解密
    import tempfile
    tmp = tempfile.mkdtemp(prefix="kugou_test_")
    src = os.path.join(tmp, "测试.kgg")
    dst = os.path.join(tmp, "out.bin")
    with open(src, "wb") as f:
        f.write(kgg_data)
    h = kugou.decrypt_file(src, dst, write_audio=True)
    with open(dst, "rb") as f:
        got = f.read()
    ok_file = got == plain
    if not ok_file:
        ok = False
    print(f"[{'PASS' if ok_file else 'FAIL'}] 文件流式解密一致（header_len={h['audio_offset']}）")

    # 零字节下载占位文件必须变成可读错误，不能由 mmap 异常终止批任务。
    empty = os.path.join(tmp, "empty.kgg")
    open(empty, "wb").close()
    try:
        kugou.decrypt_file(empty, os.path.join(tmp, "empty.out"))
        empty_ok = False
    except kugou.KugouError as exc:
        empty_ok = "0 字节" in str(exc)
    ok &= empty_ok
    print(f"[{'PASS' if empty_ok else 'FAIL'}] 零字节 KGG 返回可诊断错误")

    print("\n总体结果:", "全部通过 ✓" if ok else "存在失败 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
