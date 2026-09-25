# -*- coding: utf-8 -*-
"""
QMCv2（mflac/mgg 新版）解密验证：
  1. 用 unlock-music 真实测试数据：key_raw -> key、raw+suffix -> target
  2. 与 node 官方实现（@xhacker/qmcwasm）交叉验证
"""
import base64
import os
import struct
import subprocess
import sys
import tempfile
from unittest import mock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qmc  # noqa: E402

TD = os.path.expandvars(r"%LOCALAPPDATA%\Temp\unm\testdata")
QMCLEGACY = os.path.expandvars(r"%LOCALAPPDATA%\Temp\qmcwasm\package\QmcLegacy.js")


def node_decrypt(path):
    script = r"""
const K = require(%r);
K({}).then(M => {
  const fs = require('fs');
  const data = new Uint8Array(fs.readFileSync(process.argv[1]));
  const pBuf = M._malloc(data.length);
  M.writeArrayToMemory(data, pBuf);
  const tailSize = M.preDec(pBuf, data.length, %r);
  if (tailSize == -1) { console.error('PREDEC_ERR:' + M.getErr()); process.exit(1); }
  const decLen = data.length - tailSize;
  const out = M.HEAPU8.slice(pBuf, pBuf + M.decBlob(pBuf, decLen, 0));
  fs.writeFileSync(process.argv[2], Buffer.from(out));
  console.log('OK');
}).catch(e => { console.error('REJECT:' + e.message); process.exit(1); });
""" % (QMCLEGACY, os.path.splitext(os.path.basename(path))[0])
    tmp_js = os.path.join(tempfile.gettempdir(), "qmc_v2_check.js")
    with open(tmp_js, "w", encoding="utf-8") as f:
        f.write(script)
    out_bin = path + ".dec"
    r = subprocess.run(["node", tmp_js, path, out_bin], capture_output=True, timeout=60)
    if r.returncode != 0:
        return None, (r.stdout + r.stderr).decode("utf-8", "ignore")
    with open(out_bin, "rb") as f:
        return f.read(), None


def main():
    ok = True

    # QQ Music occasionally omits RFC 4648 padding from embedded keys.
    # The decoder must restore it without accepting impossible Base64.
    padded = qmc._decode_qmc_base64(b"YWJjZA") == b"abcd"
    ok &= padded
    print(f"[{'PASS' if padded else 'FAIL'}] QMCv2 Base64 缺失补位兼容")

    # MusicEx files keep their media key in an external MMKV vault.  Its
    # cex\0 suffix must never be interpreted as a multi-megabyte key length.
    tag = bytearray(0xC0)
    tag[0x0C:0x0C + len("media-id".encode("utf-16le"))] = "media-id".encode("utf-16le")
    tag[0x48:0x48 + len("track.mflac".encode("utf-16le"))] = "track.mflac".encode("utf-16le")
    struct.pack_into("<II", tag, 0xB0, 0xC0, 1)
    tag[0xB8:0xC0] = b"musicex\x00"
    with mock.patch.object(qmc, "_musicex_master_key", return_value=b"test-key"), \
            mock.patch.object(qmc, "_cipher_from_master",
                              return_value=lambda block, offset: block):
        musicex_info = qmc.decrypt_data(b"fLaC" + b"encrypted-audio" + bytes(tag) * 2,
                                        "mflac")
    musicex_ok = musicex_info["format"] == "flac"
    ok &= musicex_ok
    print(f"[{'PASS' if musicex_ok else 'FAIL'}] MusicEx 尾部识别并进入外部取钥流程")

    tmp = tempfile.mkdtemp(prefix="qmc_v2_real_")
    cases = [
        ("mflac_map", "mflac", "flac"),
        ("mflac_rc4", "mflac", "flac"),
        ("mflac0_rc4", "mflac", "flac"),
        ("mgg_map", "mgg", "ogg"),
    ]
    for name, ext, want_fmt in cases:
        key_raw = open(os.path.join(TD, f"{name}_key_raw.bin"), "rb").read()
        key_exp = open(os.path.join(TD, f"{name}_key.bin"), "rb").read()
        raw = open(os.path.join(TD, f"{name}_raw.bin"), "rb").read()
        suffix = open(os.path.join(TD, f"{name}_suffix.bin"), "rb").read()
        target = open(os.path.join(TD, f"{name}_target.bin"), "rb").read()

        # 1) 密钥解密
        decoded = qmc.decrypt_qmc2_key(base64.b64decode(key_raw))
        key_ok = decoded == key_exp
        ok &= key_ok
        print(f"[{'PASS' if key_ok else 'FAIL'}] {name}: 密钥解密还原 ({len(key_exp)}B key)")

        # 2) 完整文件解密（raw + suffix）
        full = raw + suffix
        try:
            info = qmc.decrypt_data(full, ext)
            audio_ok = info["audio"] == target
        except qmc.QmcError as e:
            audio_ok = False
            print(f"  ERROR: {e}")
        ok &= audio_ok
        print(f"[{'PASS' if audio_ok else 'FAIL'}] {name}: 音频解密还原 ({want_fmt})")

        # 3) node 官方实现交叉验证
        fpath = os.path.join(tmp, f"{name}.{ext}")
        with open(fpath, "wb") as f:
            f.write(full)
        nd, err = node_decrypt(fpath)
        if nd is not None:
            node_ok = nd == target
            ok &= node_ok
            print(f"[{'PASS' if node_ok else 'FAIL'}] {name}: node 官方交叉验证")
        else:
            # QmcLegacy 新版不识别老测试数据格式（需 downgrade），非算法错误
            print(f"[SKIP] {name}: node 交叉验证（QmcLegacy 不支持老格式测试数据: {err[:60]}）")

    # QMCv1 默认 mask 真实数据（qmc0_static）
    try:
        raw0 = open(os.path.join(TD, "qmc0_static_raw.bin"), "rb").read()
        tgt0 = open(os.path.join(TD, "qmc0_static_target.bin"), "rb").read()
        info = qmc.decrypt_data(raw0, "qmc0")
        q0 = info["audio"] == tgt0
        ok &= q0
        print(f"[{'PASS' if q0 else 'FAIL'}] qmc0_static: QMCv1 默认 mask 解密还原")
    except Exception as e:
        ok = False
        print(f"[FAIL] qmc0_static: {e}")

    print("\n总体结果:", "全部通过 ✓" if ok else "存在失败 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
