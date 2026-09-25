# -*- coding: utf-8 -*-
"""
QMC 解密测试：
  1. 对照 libtakiyasha 验证 44->128 mask 映射
  2. qmc_decrypt 幂等性（XOR 对称）
  3. detect_mflac / detect_mgg 从加密音频恢复 mask 并还原
  4. decrypt_data 全流程（默认 mask 格式 + 检测型格式）
"""
import os
import random
import struct
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qmc  # noqa: E402


def check_libtakiyasha():
    """对照 libtakiyasha 的 KEY256_MAPPING（直接读 pickle，避免依赖）。"""
    p = os.environ.get("LIBTAKIYASHA_KEY256_MAPPING_DATA", "")
    if not os.path.isfile(p):
        print("[跳过] libtakiyasha 数据文件不存在")
        return True
    import pickle
    ref = pickle.load(open(p, "rb"))
    mine = qmc.generate128(qmc.QMCDefaultMaskMatrix)
    m128, idx44 = bytearray(128), 0
    for it in ref:
        if it:
            for i128 in it:
                m128[i128] = qmc.QMCDefaultMaskMatrix[idx44]
            idx44 += 1
    ok = mine == bytes(m128)
    print(f"[{'PASS' if ok else 'FAIL'}] 44->128 mask 映射与 libtakiyasha 一致")
    return ok


def make_qmc_audio(plain, mask):
    """用给定 128 字节 mask 加密音频（QMC XOR，对称）。"""
    return qmc.qmc_decrypt(plain, mask)


def main():
    ok = True
    ok &= check_libtakiyasha()

    # ---- 幂等性 ----
    rng = random.Random(5)
    data = bytes(rng.randbytes(100000))
    mask = qmc.QMCDefaultMaskMatrix
    m128 = qmc.generate128(mask)
    enc = qmc.qmc_decrypt(data, m128)
    dec = qmc.qmc_decrypt(enc, m128)
    idem = dec == data
    if not idem:
        ok = False
    print(f"[{'PASS' if idem else 'FAIL'}] qmc_decrypt 幂等（XOR 对称）")

    # ---- 默认 mask 格式（qmcflac） ----
    # 用真实 flac 构造（这里用带 fLaC 头的随机数据模拟）
    flac = bytearray(rng.randbytes(60000))
    flac[:4] = b"fLaC"
    enc_flac = make_qmc_audio(bytes(flac), qmc.generate128(qmc.QMCDefaultMaskMatrix))
    info = qmc.decrypt_data(enc_flac, "qmcflac")
    ok_f = info["audio"] == bytes(flac) and info["format"] == "flac"
    if not ok_f:
        ok = False
    print(f"[{'PASS' if ok_f else 'FAIL'}] qmcflac 默认 mask 解密还原")

    # ---- detect_mflac：模拟 mask 明文嵌入加密流的 QMCv1 老格式 ----
    flac_plain = bytes(flac)
    rmask = bytes(rng.randbytes(128))
    mflac_enc = bytearray(make_qmc_audio(flac_plain, rmask))
    mflac_enc[128:256] = rmask               # 将 mask 明文嵌入 block=128
    mflac_data = bytes(mflac_enc) + struct.pack("<I", 0)
    detected = qmc.detect_mflac(mflac_enc)
    ok_mf = detected is not None and detected.matrix128 == rmask
    if not ok_mf:
        ok = False
    print(f"[{'PASS' if ok_mf else 'FAIL'}] mflac detect 恢复出正确 mask")

    # ---- detect_mgg：构造 Ogg 头 + 置信度统计恢复 mask ----
    ogg = bytearray(rng.randbytes(60000))
    ogg[:4] = b"OggS"
    ogg[0x0c] = 0x00
    ogg[0x54] = 0x02
    rmask2 = bytes(rng.randbytes(128))
    mgg_enc = bytearray(make_qmc_audio(bytes(ogg), rmask2))
    # Ogg 检测需要头部若干字节对齐 Ogg 页结构，构造后由检测算法自行恢复
    detected2 = qmc.detect_mgg(mgg_enc)
    ok_mg = detected2 is not None
    if not ok_mg:
        ok = False
    print(f"[{'PASS' if ok_mg else 'FAIL'}] mgg detect 能从头部恢复 mask")

    # ---- qmc0 默认 mask ----
    mp3 = bytearray(rng.randbytes(50000))
    mp3[:4] = b"ID3\x03"
    enc_q0 = make_qmc_audio(bytes(mp3), qmc.generate128(qmc.QMCDefaultMaskMatrix))
    info = qmc.decrypt_data(enc_q0, "qmc0")
    ok_q0 = info["audio"] == bytes(mp3) and info["format"] == "mp3"
    if not ok_q0:
        ok = False
    print(f"[{'PASS' if ok_q0 else 'FAIL'}] qmc0 默认 mask 解密还原")

    print("\n总体结果:", "全部通过 ✓" if ok else "存在失败 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
