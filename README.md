# 喵喵转码器

一个便于使用的图形界面音频批量转换工具，基于 FFmpeg 与纯 Python 实现。

## 功能

- 🖼️ **原生 Qt 6 工作台**：独立 PyQt6 GUI 框架、自绘矢量图标、交互动画、模型驱动列表与高 DPI 渲染；窗口缩放时自动暂停装饰动画，分隔栏采用延迟布局以保持拖动流畅
- 🎵 **格式转换**：输入任意音频或视频容器（mp3/flac/wav/aac/ogg/m4a/mp4/wma/ape/aiff/opus…），输出 **mp3 / flac / wav / aac / ogg / m4a / opus**
- 🔐 **NCM 网易云解密**：`.ncm` 加密文件自动解密还原，并保留封面与歌曲信息
- 🎶 **KGM/KGG 酷狗解密**：`.kgm / .vpr` 老格式（算法与权威实现 KgmWasm 一致）+ `.kgg` 新版（**自动读取酷狗密钥库 `KGMusicV3.db`**，无需手动导入）
- 🎧 **QMC QQ音乐解密**：`.qmc0/.qmc2/.qmc3/.qmcflac/.qmcogg`（QMCv1 内置 mask）+ `.mflac/.mgg/.mflac0/.mgg0`（QMCv2/MusicEx 密钥+RC4）
- ⚙️ **参数设置**：同步转换数量（默认 2，可选 4/8/16/24/32）、码率（CBR/VBR）、采样率、声道
- 🖼️ **封面继承**：自动提取并继承源文件封面（MP4 输入除外），写入 MP3/FLAC/M4A；封面损坏时自动降级，不影响音频转换
- 🧹 **手动去重**：点击“自动去重”后，移除体积完全一致、文件名相似度达到 90%，或名称尾部带 `(1)` 的队列项目；导入时不会自行删除
- 📝 **LRC 歌词匹配**：可选开启；扫描已完成项目及后续输出，在源文件同目录查找同名 `.lrc`，并复制为与输出音频同名的歌词文件
- 🔄 **完整重置**：停止当前任务并恢复队列、结果、错误、参数、进度和布局到应用刚启动时的状态
- ⏹️ **可取消**：随时中断批量任务
- ⚡ **高效**：有界并发任务池、同名输出安全占位、NCM 流式解密（mmap 分块），FFmpeg 全程后台运行并显示聚合进度

## 快速开始

### 源码运行

需要 Windows、Python 3.10+、PyQt6，以及 FFmpeg（`ffmpeg` 和 `ffprobe`）。可将 FFmpeg 可执行文件放入项目旁的 `ffmpeg/` 目录，或配置到系统 `PATH`。本仓库不包含 FFmpeg 二进制文件。

安装 PyQt6 后双击 `run.bat`（或执行 `python qt_main.py`）。

操作步骤：
1. 点击 **添加文件 / 添加文件夹** 选择要处理的音频
2. 设置输出格式、码率等参数，选择输出目录（默认桌面 `sunwoo_output`）
3. 点击 **开始转换**，进度条与日志会实时显示
4. 完成后在输出目录查看结果

## 重新打包 exe

```bash
# 安装构建依赖
python -m pip install PyInstaller PyQt6

# 打包（也可直接双击 build_exe.bat）
build_exe.bat
```

## 项目结构

```
audio-tool/
├── qt_main.py         # PyQt6 图形界面主程序
├── main.py            # 音频任务流程与旧界面兼容层
├── ncm.py             # NCM 解密模块（AES via Windows BCrypt + RC4）
├── converter.py       # FFmpeg 转换封装
├── ffmpeg/            # 本地放置 ffmpeg.exe / ffprobe.exe（不随仓库发布）
├── run.bat            # 双击启动
├── test_roundtrip.py  # NCM 算法自测（加密/解密往返）
└── integration_test.py# 真实音频端到端测试
```

## 依赖

- **Python 3.10+** 与 **PyQt6**（仅源码运行需要；打包版已内置 Qt 运行库）
- **FFmpeg**（需单独安装或放入 `ffmpeg/` 目录，并提供 ffmpeg 与 ffprobe）

## 测试

```bash
python test_roundtrip.py      # NCM 加密/解密往返自测
python integration_test.py    # 真实音频端到端集成测试
```

## 技术说明

### NCM 解密
算法严格对齐开源项目 [taurusxin/ncmdump](https://github.com/taurusxin/ncmdump)：

| 段 | 处理 |
|---|---|
| key 段 | `xor 0x64` → AES-128-ECB（密钥 `hzHRAmso5kInbaxW`）→ 偏移 17 起作为 RC4 密钥 |
| meta 段 | `xor 0x63` → 跳过 22 字节 → base64 → AES-128-ECB（密钥 `#14ljk_!\]&0U<('`）→ 跳过 `music:` → JSON |
| 音频段 | 静态 RC4 密钥流（周期 256 字节）全文件统一异或，流式分块处理 |

AES 通过 Windows 系统自带 `bcrypt.dll` 调用，不依赖任何第三方加密库。

### KGM/KGG 解密
算法与数据严格对齐 [@xhacker/kgmwasm](https://www.npmjs.com/package/@xhacker/kgmwasm) 权威实现：

| 项 | 值 |
|---|---|
| 文件头 | `0x7C 0xD5 0x32 0xEB ...`（16 字节），或 `KGB1` |
| header 长度 | 偏移 `0x10` 处 4 字节小端 |
| file_key | 偏移 `0x1c` 起 16 字节 + `0x00`（共 17 字节） |
| 解密公式 | `out = xor_lower(mask) ^ xor_lower(cipher ^ key)`，`xor_lower(b) = b ^ ((b&0xf)<<4)` |
| mask 生成 | `table3[idx%272] ^ h(idx)`，`h` 由 table1/table2 递归计算 |

三张 272 字节表内置在 `kugou_tables.py`（提取自权威实现），无需外部数据文件。
VPR 格式额外异或 `VPR_MASK_DIFF`。

### 酷狗新版 kgg（CryptoVersion=5）
新版 kgg 采用 QMC2 加密，**需要密钥库**：
- 自动读取酷狗客户端密钥库 `%APPDATA%\KuGou8\KGMusicV3.db`（SQLCipher 魔改加密，默认 master key 或环境变量 `KGG_DB_MASTER_KEY`）
- 从文件头 `0x44` 提取 `audioHash`，在数据库 `ShareFileItems` 表查 ekey
- ekey → TC-TEA-CBC 解密（`simpleMakeKey(106,8)` + V1/V2 派生）→ 密钥
- `len(key) > 300` → 改进版 RC4；否则 → MAP 掩码（`(v<<r)|(v>>r)` 非循环移位）
- 若某首歌解密失败，先在酷狗客户端播放一次（生成密钥缓存）再试

### QMC 解密
算法对齐 unlock-music（qmc.ts / qmcMask.ts / qmc_key.ts）：

| 格式 | 方式 |
|---|---|
| qmc0/qmc3/qmcflac/qmcogg/qmc2/bkcmp3/tkm | QMCv1 内置默认 mask（44 字节矩阵 → 128 字节）周期异或 |
| mflac/mgg 老格式 | QMCv1 从音频头检测 mask（Mflac 逐块 / Mgg 置信度） |
| mflac/mgg/mflac0/mgg0 新格式 | **QMCv2**：尾部 Base64 key → `simpleMakeKey(106,8)`+密文头组 TEA key → TC-TEA-CBC 解密（rounds=32）→ 256 字节→Mask128 / 512 字节→改进版 RC4，音频异或解密 |

QQ 音乐 19.57+ 的 MusicEx 文件不再内嵌 eKey。软件在 Windows 上会优先读取当前用户的 DPAPI 加密密钥缓存；首次转换时需要 QQ 音乐客户端正在运行、已登录有该歌曲权限的账号并可访问网络。取到的单曲 eKey 会加密缓存在 `%LOCALAPPDATA%\MiaomiaoTranscoder\keys`，不会把登录 Cookie 写入磁盘，之后同一文件可离线转换。

QMCv2 密钥生成完全自包含（无需外部固定密钥），TEA 为 TarsCpp 魔改版（big-endian，32 位截断）。
新版 `QQMusic EncV2,Key:`（QTag v2）加密需 garble_keys，暂不支持。

## ☕ 赞助与支持

如果您觉得喵喵音频转换器对您有所帮助，欢迎赞助支持作者！您的支持是项目持续维护与带来更多新特性的最大动力。

| 微信支付 (WeChat Pay) | 支付宝 (Alipay) |
| :---: | :---: |
| <img src="assets/sponsor_wechat.jpg" width="260" alt="微信支付" /> | <img src="assets/sponsor_alipay.jpg" width="260" alt="支付宝" /> |

