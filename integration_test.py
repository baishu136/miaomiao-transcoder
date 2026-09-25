# -*- coding: utf-8 -*-
"""
集成测试：真实音频链路
  1. 用 ffmpeg 生成真实 WAV（正弦波 3 秒）
  2. WAV → MP3
  3. 用真实 MP3 构造合成 NCM（含封面 + meta）
  4. process_one 解密 + 转换 → 输出目标格式
  5. 用 ffprobe 校验输出文件时长/格式正确
"""
import base64
import json
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import converter  # noqa: E402
import ncm  # noqa: E402
import test_kugou  # noqa: E402  (仅复用加密/构造函数)
from main import process_one  # noqa: E402

FFMPEG = converter.find_binaries()[0]


def run(cmd):
    return subprocess.run(cmd, capture_output=True)


def make_real_ncm(mp3_path, cover_path=None, seed=7):
    with open(mp3_path, "rb") as f:
        audio = f.read()
    rng = random.Random(seed)
    rc4_key = bytearray(rng.randbytes(128))
    rc4_key[17] = 17
    rc4_key[18:35] = rng.randbytes(17)
    mkey = bytes(rc4_key)

    padded = ncm._pkcs7_pad(mkey)
    key_data = bytes(b ^ 0x64 for b in ncm.aes_ecb_encrypt(ncm.CORE_KEY, padded))

    meta_json = json.dumps({
        "musicName": "集成测试曲", "artist": [["测试歌手"]],
        "album": "测试专辑", "format": "mp3", "bitrate": 192,
    }).encode("utf-8")
    enc = ncm.aes_ecb_encrypt(ncm.MODIFY_KEY, ncm._pkcs7_pad(b"music:" + meta_json))
    mdata = b"163 key(Don't modify):" + base64.b64encode(enc)
    meta_blob = bytes(b ^ 0x63 for b in mdata)

    if cover_path and os.path.isfile(cover_path):
        with open(cover_path, "rb") as f:
            cover = f.read()
    else:
        cover = b"\x89PNG\r\n\x1a\n" + rng.randbytes(200)  # 非法封面，用于降级测试
    img_len = len(cover)
    cover_frame_len = img_len + 4

    box = ncm.build_key_box(mkey[17:])
    ks = ncm.build_keystream(box)
    enc_audio = ncm.xor_keystream(audio, ks)

    return b"".join([
        b"CTENFDAM", b"\x01\x00",
        struct.pack("<I", len(key_data)), key_data,
        struct.pack("<I", len(meta_blob)), meta_blob,
        b"\x00\x00\x00\x00\x01",
        struct.pack("<I", cover_frame_len),
        struct.pack("<I", img_len), cover, b"MYMU",
        enc_audio,
    ])


def main():
    tmp = tempfile.mkdtemp(prefix="int_test_")
    try:
        # 1. 生成真实 WAV + MP3 + 合法 PNG 封面
        wav = os.path.join(tmp, "src.wav")
        mp3 = os.path.join(tmp, "src.mp3")
        png = os.path.join(tmp, "cover.png")
        r = run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=3", wav])
        assert r.returncode == 0, "生成 WAV 失败"
        r = run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", wav, "-c:a", "libmp3lame", "-b:a", "192k", mp3])
        assert r.returncode == 0, "生成 MP3 失败"
        r = run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "color=c=red:s=64x64",
                 "-frames:v", "1", png])
        assert r.returncode == 0, "生成 PNG 封面失败"
        print("[PASS] 生成真实 WAV/MP3/PNG")

        # 2. 构造 NCM（含合法封面）
        ncm_path = os.path.join(tmp, "真实歌曲.ncm")
        with open(ncm_path, "wb") as f:
            f.write(make_real_ncm(mp3, cover_path=png))
        print("[PASS] 构造合成 NCM（含合法封面）")
        out_dir = os.path.join(tmp, "out")
        os.makedirs(out_dir)
        item = {"path": ncm_path, "name": "真实歌曲.ncm", "ext": ".ncm"}
        ok, msg = process_one(item, tmp, out_dir, "flac", 0, 0, 0,
                              lambda p: None, None)
        assert ok, f"NCM→FLAC 失败: {msg}"
        flac_out = os.path.join(out_dir, "真实歌曲.flac")
        assert os.path.isfile(flac_out), "FLAC 输出不存在"
        info = converter.probe_info(flac_out)
        assert info and abs(info["duration"] - 3.0) < 0.3, f"FLAC 时长异常: {info}"
        print(f"[PASS] NCM→FLAC 转码成功，时长 {info['duration']:.2f}s，含封面")

        # 4. NCM → MP3（同格式流复制）
        item2 = {"path": ncm_path, "name": "真实歌曲.ncm", "ext": ".ncm"}
        ok2, msg2 = process_one(item2, tmp, out_dir, "mp3", 0, 0, 0,
                                lambda p: None, None)
        assert ok2, f"NCM→MP3 失败: {msg2}"
        mp3_out = os.path.join(out_dir, "真实歌曲.mp3")
        assert os.path.isfile(mp3_out), "MP3 输出不存在"
        info2 = converter.probe_info(mp3_out)
        assert info2 and abs(info2["duration"] - 3.0) < 0.3, f"MP3 时长异常: {info2}"
        print(f"[PASS] NCM→MP3 流复制成功，时长 {info2['duration']:.2f}s")

        # 5. 普通 WAV → OGG
        item3 = {"path": wav, "name": "src.wav", "ext": ".wav"}
        ok3, msg3 = process_one(item3, tmp, out_dir, "ogg", 192, 44100, 2,
                                lambda p: None, None)
        assert ok3, f"WAV→OGG 失败: {msg3}"
        ogg_out = os.path.join(out_dir, "src.ogg")
        info3 = converter.probe_info(ogg_out)
        assert info3 and abs(info3["duration"] - 3.0) < 0.3, f"OGG 时长异常: {info3}"
        print(f"[PASS] WAV→OGG 转码成功，时长 {info3['duration']:.2f}s")

        # 6. WAV → MP3 指定码率
        item4 = {"path": wav, "name": "src.wav", "ext": ".wav"}
        ok4, _ = process_one(item4, tmp, out_dir, "mp3", 128, 0, 0,
                             lambda p: None, None)
        assert ok4, "WAV→MP3 失败"
        print("[PASS] WAV→MP3(128kbps) 成功")

        # 7. 损坏封面 → 自动降级（去掉封面重试）仍成功
        bad_ncm = os.path.join(tmp, "损坏封面.ncm")
        with open(bad_ncm, "wb") as f:
            f.write(make_real_ncm(mp3, cover_path=None))
        item5 = {"path": bad_ncm, "name": "损坏封面.ncm", "ext": ".ncm"}
        ok5, msg5 = process_one(item5, tmp, out_dir, "mp3", 0, 0, 0,
                                lambda p: None, None)
        assert ok5, f"损坏封面降级失败: {msg5}"
        print("[PASS] 损坏封面自动降级，转换仍成功")

        # 8. KGG（真实 MP3 加密）→ FLAC
        with open(mp3, "rb") as f:
            real_mp3 = f.read()
        kg_key = bytes(range(1, 17)) + b"\x00"
        kgg_path = os.path.join(tmp, "酷狗歌曲.kgg")
        with open(kgg_path, "wb") as f:
            f.write(test_kugou.make_kgg(test_kugou.encrypt(real_mp3, kg_key), kg_key))
        item6 = {"path": kgg_path, "name": "酷狗歌曲.kgg", "ext": ".kgg"}
        ok6, msg6 = process_one(item6, tmp, out_dir, "flac", 0, 0, 0,
                                lambda p: None, None)
        assert ok6, f"KGG→FLAC 失败: {msg6}"
        kgg_flac = os.path.join(out_dir, "酷狗歌曲.flac")
        info6 = converter.probe_info(kgg_flac)
        assert info6 and abs(info6["duration"] - 3.0) < 0.3, f"KGG FLAC 时长异常: {info6}"
        print(f"[PASS] KGG→FLAC 转码成功，时长 {info6['duration']:.2f}s")

        # 9. KGG → MP3（同格式流复制）
        item7 = {"path": kgg_path, "name": "酷狗歌曲.kgg", "ext": ".kgg"}
        ok7, msg7 = process_one(item7, tmp, out_dir, "mp3", 0, 0, 0,
                                lambda p: None, None)
        assert ok7, f"KGG→MP3 失败: {msg7}"
        kgg_mp3 = os.path.join(out_dir, "酷狗歌曲.mp3")
        info7 = converter.probe_info(kgg_mp3)
        assert info7 and abs(info7["duration"] - 3.0) < 0.3, f"KGG MP3 时长异常: {info7}"
        print(f"[PASS] KGG→MP3 流复制成功，时长 {info7['duration']:.2f}s")

        # 10. QMC mflac（真实 unlock-music 测试数据）→ FLAC
        import test_qmc_v2 as _tqv2
        _qmc_raw = open(os.path.join(_tqv2.TD, "mflac_map_raw.bin"), "rb").read()
        _qmc_sfx = open(os.path.join(_tqv2.TD, "mflac_map_suffix.bin"), "rb").read()
        qmc_path = os.path.join(tmp, "QQ音乐.mflac")
        with open(qmc_path, "wb") as f:
            f.write(_qmc_raw + _qmc_sfx)
        item8 = {"path": qmc_path, "name": "QQ音乐.mflac", "ext": ".mflac"}
        ok8, msg8 = process_one(item8, tmp, out_dir, "flac", 0, 0, 0,
                                lambda p: None, None)
        assert ok8, f"QMC mflac→FLAC 失败: {msg8}"
        qmc_flac = os.path.join(out_dir, "QQ音乐.flac")
        info8 = converter.probe_info(qmc_flac)
        assert info8 and info8["duration"] > 0, f"QMC FLAC 无效: {info8}"
        print(f"[PASS] QMC mflac→FLAC 转码成功，时长 {info8['duration']:.2f}s")

        print("\n集成测试全部通过 ✓")
        return 0
    except AssertionError as e:
        print(f"\n[FAIL] {e}")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
