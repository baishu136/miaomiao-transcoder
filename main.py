# -*- coding: utf-8 -*-
"""
喵喵转码器 —— 图形界面批量音频转换工具。

功能：
  * 批量添加文件 / 文件夹（递归扫描）
  * 支持 .ncm 网易云加密文件自动解密
  * 转换 mp3 / flac / wav / aac / ogg / m4a / opus
  * 码率 / 采样率 / 声道设置，进度显示，可取消
纯标准库 + FFmpeg（项目内 ffmpeg\ffmpeg.exe），无需安装任何第三方库。
"""
import os
import difflib
import re
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import converter
import kugou
import kugou_db
import ncm
import qmc


APP_NAME = "喵喵转码器"
APP_DPI_SCALE = 1.0
DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "sunwoo_output")


def enable_high_dpi_awareness():
    """Opt into native-resolution Per-Monitor V2 rendering before Tk starts."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Miaomiao.Transcoder.Desktop.1")
    except Exception:
        pass
    try:
        import ctypes
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4.  Unlike legacy
        # system-DPI mode this keeps text sharp when moving between monitors.
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def configure_tk_for_dpi(root):
    """Calibrate Tk fonts and pixel scaling to the window's real monitor DPI."""
    import tkinter.font as tkfont

    global APP_DPI_SCALE
    dpi = 96
    if sys.platform == "win32":
        try:
            import ctypes
            dpi = int(ctypes.windll.user32.GetDpiForWindow(root.winfo_id())) or 96
        except Exception:
            dpi = 96
    root.tk.call("tk", "scaling", dpi / 72.0)
    APP_DPI_SCALE = max(1.0, dpi / 96.0)
    for name, size, weight in (
            ("TkDefaultFont", 10, "normal"), ("TkTextFont", 10, "normal"),
            ("TkMenuFont", 10, "normal"), ("TkHeadingFont", 10, "bold"),
            ("TkCaptionFont", 10, "normal"), ("TkSmallCaptionFont", 9, "normal")):
        try:
            tkfont.nametofont(name).configure(family="Microsoft YaHei UI", size=size,
                                              weight=weight)
        except Exception:
            pass
    return dpi


def configure_window_geometry(root, width=1320, height=800, min_width=1060, min_height=680):
    """Keep the same apparent size across 100%-200% Windows scaling."""
    scale = APP_DPI_SCALE
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    target_w = min(round(width * scale), round(screen_w * 0.92))
    target_h = min(round(height * scale), round(screen_h * 0.88))
    minimum_w = min(round(min_width * scale), target_w)
    minimum_h = min(round(min_height * scale), target_h)
    x = max(0, (screen_w - target_w) // 2)
    y = max(0, (screen_h - target_h) // 2)
    root.geometry(f"{target_w}x{target_h}+{x}+{y}")
    root.minsize(minimum_w, minimum_h)


def apply_window_icons(root):
    """Set crisp small/large taskbar icons for the window's current DPI."""
    try:
        from PIL import Image, ImageTk

        dpi_scale = APP_DPI_SCALE
        small_px = min((16, 20, 24, 28, 32, 40, 48), key=lambda n: abs(n - 16 * dpi_scale))
        large_px = min((32, 40, 48, 56, 64, 96), key=lambda n: abs(n - 32 * dpi_scale))
        small = ImageTk.PhotoImage(Image.open(resource_path(os.path.join("assets", f"icon_{small_px}.png"))))
        large = ImageTk.PhotoImage(Image.open(resource_path(os.path.join("assets", f"icon_{large_px}.png"))))
        root.iconphoto(False, large, small)
        # Keep PhotoImage objects alive for the lifetime of the window.
        root._app_icon_images = (large, small)
        if sys.platform == "win32":
            import ctypes
            root.update_idletasks()
            user32 = ctypes.windll.user32
            hwnd = root.winfo_id()
            dpi = user32.GetDpiForWindow(hwnd) or 96
            get_metric = getattr(user32, "GetSystemMetricsForDpi", None)
            if get_metric:
                small_size = get_metric(49, dpi)  # SM_CXSMICON
                large_size = get_metric(11, dpi)  # SM_CXICON
            else:
                small_size, large_size = user32.GetSystemMetrics(49), user32.GetSystemMetrics(11)
            ico_path = resource_path(os.path.join("assets", "miaomiao_icon.ico"))
            load_image = user32.LoadImageW
            load_image.restype = ctypes.c_void_p
            small_handle = load_image(None, ico_path, 1, small_size, small_size, 0x10)
            large_handle = load_image(None, ico_path, 1, large_size, large_size, 0x10)
            if small_handle:
                user32.SendMessageW(hwnd, 0x0080, 0, small_handle)  # WM_SETICON/SMALL
                user32.SendMessageW(hwnd, 0x0080, 2, small_handle)  # WM_SETICON/SMALL2
            if large_handle:
                user32.SendMessageW(hwnd, 0x0080, 1, large_handle)  # WM_SETICON/BIG
            root._native_icon_handles = (small_handle, large_handle)
    except Exception:
        try:
            root.iconbitmap(resource_path(os.path.join("assets", "miaomiao_icon.ico")))
        except Exception:
            pass


def resource_path(relative_path):
    """Resolve bundled resources in development and PyInstaller builds."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def load_ui_icon(name, size=(20, 20), tint=None):
    """Load a bundled line icon for CustomTkinter buttons."""
    try:
        import customtkinter as ctk
        from PIL import Image
        image = Image.open(resource_path(os.path.join("assets", "icons", name + ".png"))).convert("RGBA")
        if tint:
            from PIL import ImageColor
            color = ImageColor.getrgb(tint)
            alpha = image.getchannel("A")
            image = Image.new("RGBA", image.size, color + (0,))
            image.putalpha(alpha)
        return ctk.CTkImage(light_image=image, dark_image=image, size=size)
    except Exception:
        return None

AUDIO_EXTS = {
    ".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".m4b",
    ".wma", ".ape", ".aiff", ".aif", ".opus", ".amr", ".caf",
    ".mka", ".mp4", ".alac", ".oga",
}
NCM_EXT = ".ncm"
KUGOU_EXTS = {".kgg", ".kgm", ".vpr"}
QMC_EXTS = {".qmc0", ".qmc1", ".qmc2", ".qmc3", ".qmc4", ".qmc6", ".qmc8",
            ".qmcflac", ".qmcogg", ".mflac", ".mflac0", ".mflac1",
            ".mgg", ".mgg0", ".mgg1", ".mggl", ".bkcmp3", ".bkcm4a",
            ".bkcflac", ".bkcwav", ".bkcape", ".bkcogg", ".bkcwma",
            ".tkm", ".tm0", ".tm2", ".tm3", ".tm6"}

OUT_FMTS = list(converter.CODECS.keys())  # mp3/flac/wav/aac/ogg/m4a/opus

BITRATE_OPTS = [
    ("默认", 0), ("128 kbps", 128), ("192 kbps", 192),
    ("256 kbps", 256), ("320 kbps", 320), ("VBR 高质量", -1),
]
SR_OPTS = [("保持不变", 0), ("44100 Hz", 44100), ("48000 Hz", 48000),
           ("22050 Hz", 22050), ("16000 Hz", 16000)]
CH_OPTS = [("保持不变", 0), ("立体声 (2)", 2), ("单声道 (1)", 1)]


class Worker:
    """后台转换工作线程的状态容器（UI 主线程通过轮询读取）。"""

    def __init__(self):
        self.running = False
        self.cancel = threading.Event()
        self.pause = threading.Event()
        self.files = []          # [{'path','name','size','ext','item'}]
        self.path_keys = set()   # normalized source paths currently represented
        self.import_queue = deque()
        self.scan_done = deque()
        self.scanning = 0
        self.import_generation = 0
        self.total = 0
        self.done = 0
        self.errors = 0
        self.current = ""
        self.current_pct = 0.0
        self.active_tasks = {}  # item id -> {name, pct}
        self.max_workers = 2
        self.state_lock = threading.RLock()
        self.log = deque()       # (kind, text)  kind: info/success/error
        self.status = {}         # item_id -> 状态文字
        self.completed = deque() # (source item id, output path, source name, source path)
        self.failures = deque()  # (source item id, source name, error text)


worker = Worker()

# 酷狗密钥库加载状态
_kugou_keys_loaded = False
_kugou_keys_error = ""
_kugou_db_signature = None
_kugou_keys_lock = threading.Lock()


def _ensure_kugou_keys():
    """确保已从 KGMusicV3.db 加载酷狗密钥（供新版 kgg 解密）。"""
    global _kugou_keys_loaded, _kugou_keys_error, _kugou_db_signature
    with _kugou_keys_lock:
        db = kugou_db.find_database()
        if not db:
            _kugou_keys_error = "未找到酷狗密钥库 KGMusicV3.db"
            return False
        try:
            stat = os.stat(db)
            signature = (os.path.normcase(os.path.realpath(db)), stat.st_size,
                         stat.st_mtime_ns)
        except OSError as exc:
            _kugou_keys_error = f"无法读取酷狗密钥库: {exc}"
            return False
        if _kugou_keys_loaded and kugou.get_key_map() and signature == _kugou_db_signature:
            return True
        try:
            km = kugou_db.load_key_map(db)
            if not km:
                raise kugou_db.DBError("密钥库中没有歌曲密钥；请先在酷狗客户端完整播放歌曲")
            kugou.set_key_map(km)
            _kugou_keys_loaded = True
            _kugou_db_signature = signature
            _kugou_keys_error = ""
            return True
        except Exception as e:
            _kugou_keys_error = f"酷狗密钥库解密失败: {e}"
            return False


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------

def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def unique_path(path: str, reserved=None) -> str:
    """若目标已存在，自动追加 (1)、(2)… 避免覆盖。"""
    reserved = reserved if reserved is not None else set()
    key = os.path.normcase(os.path.abspath(path))
    if not os.path.exists(path) and key not in reserved:
        reserved.add(key)
        return path
    base, ext = os.path.splitext(path)
    i = 1
    candidate = f"{base} ({i}){ext}"
    while (os.path.exists(candidate)
           or os.path.normcase(os.path.abspath(candidate)) in reserved):
        i += 1
        candidate = f"{base} ({i}){ext}"
    reserved.add(os.path.normcase(os.path.abspath(candidate)))
    return candidate


def _normalized_file_stem(name: str) -> str:
    """Normalize a filename stem for stable, case-insensitive fuzzy matching."""
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = unicodedata.normalize("NFKC", stem).casefold()
    return re.sub(r"[^\w]+", "", stem, flags=re.UNICODE)


def _duplicate_reason(name: str, size: int, existing_items=None):
    """Return the manual de-duplication reason for a queue item."""
    stem = os.path.splitext(os.path.basename(name))[0]
    if re.search(r"\s*[（(]\s*1\s*[)）]\s*$", unicodedata.normalize("NFKC", stem)):
        return "文件名尾部带 (1)"
    normalized = _normalized_file_stem(name)
    for existing in worker.files if existing_items is None else existing_items:
        if size == existing.get("size"):
            return f"文件体积与“{existing['name']}”完全一致"
        other = existing.get("normalized_name") or _normalized_file_stem(existing["name"])
        if not normalized or not other:
            continue
        # A length-ratio prefilter avoids expensive comparisons that can never
        # reach the requested 90% threshold when importing large folders.
        if min(len(normalized), len(other)) / max(len(normalized), len(other)) < 0.9:
            continue
        similarity = difflib.SequenceMatcher(None, normalized, other, autojunk=False).ratio()
        if similarity >= 0.9:
            return f"文件名与“{existing['name']}”相似度 {similarity:.0%}"
    return None


def find_matching_lrc(source_path: str):
    """Find an LRC with the same normalized stem in the source file's folder."""
    source_dir = os.path.dirname(os.path.abspath(source_path))
    wanted = _normalized_file_stem(os.path.basename(source_path))
    if not wanted or not os.path.isdir(source_dir):
        return None
    try:
        candidates = sorted(
            (entry.path for entry in os.scandir(source_dir)
             if entry.is_file() and os.path.splitext(entry.name)[1].casefold() == ".lrc"),
            key=lambda path: os.path.basename(path).casefold())
    except OSError:
        return None
    # Prefer a literal case-insensitive stem match, then the punctuation/
    # full-width-normalized match used by queue de-duplication.
    literal = os.path.splitext(os.path.basename(source_path))[0].casefold()
    for candidate in candidates:
        if os.path.splitext(os.path.basename(candidate))[0].casefold() == literal:
            return candidate
    for candidate in candidates:
        if _normalized_file_stem(candidate) == wanted:
            return candidate
    return None


def copy_matching_lrc(source_path: str, output_path: str):
    """Copy a matched lyric beside output_path and return its path, else None."""
    lrc_source = find_matching_lrc(source_path)
    if not lrc_source:
        return None
    target = os.path.splitext(output_path)[0] + ".lrc"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        if (os.path.exists(target)
                and os.path.samefile(lrc_source, target)):
            return target
    except OSError:
        pass
    shutil.copy2(lrc_source, target)
    return target


# --------------------------------------------------------------------------
# 单个文件处理
# --------------------------------------------------------------------------

def _write_cover(cover_data: bytes, temp_dir: str) -> str:
    ext = ".png" if cover_data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    p = os.path.join(temp_dir, "cover" + ext)
    with open(p, "wb") as f:
        f.write(cover_data)
    return p


def process_one(item, temp_dir, out_dir, out_fmt, bitrate, sr, ch,
                progress_cb, cancel_event, output_path=None):
    """处理一个文件，返回 (ok, message)。"""
    src = item["path"]
    base, _ = os.path.splitext(os.path.basename(src))
    is_ncm = item["ext"] == NCM_EXT
    is_kugou = item["ext"] in KUGOU_EXTS
    is_qmc = item["ext"] in QMC_EXTS

    out_ext = converter.EXT[out_fmt]
    out_path = output_path or unique_path(os.path.join(out_dir, f"{base}.{out_ext}"))

    input_path = src
    cover_path = None
    orig_fmt = None

    if is_ncm:
        # 解密 NCM 到临时文件
        tmp_audio = os.path.join(temp_dir, f"{id(src)}_{base}_dec.tmp")
        h = ncm.decrypt_file(src, tmp_audio, write_audio=True,
                             progress_cb=progress_cb, cancel_event=cancel_event)
        if h is False:
            return False, "已取消"
        orig_fmt = h["format"]
        if h["image"]:
            cover_path = _write_cover(h["image"], temp_dir)
        input_path = tmp_audio
    elif is_kugou:
        # 尝试加载酷狗密钥库（新版 kgg v5 需要；老版 KGM 无需）
        if kugou.requires_database(src) and not _ensure_kugou_keys():
            return False, _kugou_keys_error or "无法加载酷狗 KGG 密钥库"
        # 解密酷狗 KGM/KGG 到临时文件
        tmp_audio = os.path.join(temp_dir, f"{id(src)}_{base}_kgm.tmp")
        try:
            h = kugou.decrypt_file(src, tmp_audio, write_audio=True,
                                   progress_cb=progress_cb,
                                   cancel_event=cancel_event)
        except kugou.KugouError as e:
            return False, str(e)
        if h is False:
            return False, "已取消"
        orig_fmt = h["format"]
        input_path = tmp_audio
    elif is_qmc:
        # 解密 QQ 音乐 QMC 到临时文件
        tmp_audio = os.path.join(temp_dir, f"{id(src)}_{base}_qmc.tmp")
        try:
            h = qmc.decrypt_file(src, tmp_audio, item["ext"],
                                 progress_cb=progress_cb,
                                 cancel_event=cancel_event)
        except qmc.QmcError as e:
            return False, str(e)
        if h is False:
            return False, "已取消"
        orig_fmt = h["format"]
        input_path = tmp_audio

    # Preserve the source album artwork for every non-MP4 input.  NCM's own
    # decoded image takes priority; other formats are inspected for an actual
    # attached-picture stream (ordinary video frames are never used).
    if cover_path is None and item["ext"] != ".mp4":
        cover_path = converter.extract_cover(input_path, temp_dir)

    copy_stream = (is_ncm or is_kugou or is_qmc) and orig_fmt == out_fmt
    try:
        ok = converter.convert(
            input_path, out_path, out_fmt=out_fmt, bitrate=bitrate,
            sample_rate=sr, channels=ch, cover_path=cover_path,
            copy_stream=copy_stream, progress_cb=progress_cb,
            cancel_event=cancel_event)
    except converter.ConvertError as e:
        return False, str(e)
    if ok is False:
        return False, "已取消"
    if copy_stream and cover_path is None and orig_fmt:
        # 流复制模式若未嵌入封面且无封面，无需额外处理
        pass
    return True, out_path


# --------------------------------------------------------------------------
# Tkinter 界面
# --------------------------------------------------------------------------

def build_gui(root):
    import tkinter as tk
    from tkinter import filedialog, ttk
    import customtkinter as ctk

    colors = {
        "bg": "#0b1220", "sidebar": "#111b2e", "panel": "#151f32",
        "field": "#202c42", "line": "#2b3952", "text": "#f1f5f9",
        "muted": "#91a1b7", "accent": "#6366f1", "accent_hover": "#7477f5",
        "danger": "#ef6b73", "success": "#4ade80",
    }
    root.configure(bg=colors["bg"])
    style = ttk.Style(root)
    style.theme_use("clam")
    font = ("Microsoft YaHei UI", 10)
    style.configure(".", background=colors["bg"], foreground=colors["text"], font=font)
    style.configure("TFrame", background=colors["bg"])
    style.configure("Sidebar.TFrame", background=colors["sidebar"])
    style.configure("Panel.TFrame", background=colors["panel"])
    style.configure("TLabel", background=colors["bg"], foreground=colors["text"])
    style.configure("Sidebar.TLabel", background=colors["sidebar"], foreground=colors["text"])
    style.configure("Panel.TLabel", background=colors["panel"], foreground=colors["text"])
    style.configure("Muted.TLabel", background=colors["bg"], foreground=colors["muted"])
    style.configure("PanelMuted.TLabel", background=colors["panel"], foreground=colors["muted"])
    style.configure("SidebarMuted.TLabel", background=colors["sidebar"], foreground=colors["muted"])
    style.configure("TButton", background=colors["field"], foreground=colors["text"],
                    borderwidth=0, padding=(12, 9), font=("Microsoft YaHei UI", 9, "bold"))
    style.map("TButton", background=[("active", colors["line"]), ("disabled", "#1a2435")],
              foreground=[("disabled", "#637086")])
    style.configure("Primary.TButton", background=colors["accent"], foreground="#ffffff",
                    padding=(18, 12), font=("Microsoft YaHei UI", 10, "bold"))
    style.map("Primary.TButton", background=[("active", colors["accent_hover"]),
                                               ("disabled", "#30365c")])
    style.configure("Sidebar.TButton", background=colors["field"], foreground=colors["text"],
                    borderwidth=0, padding=(14, 11), anchor="w")
    style.map("Sidebar.TButton", background=[("active", colors["accent"])])
    style.configure("Danger.TButton", background="#392634", foreground="#fca5a5")
    style.map("Danger.TButton", background=[("active", "#51303d")])
    style.configure("Treeview", background=colors["panel"], fieldbackground=colors["panel"],
                    foreground=colors["text"], rowheight=38, borderwidth=0, relief="flat")
    style.configure("Treeview.Heading", background=colors["field"], foreground=colors["muted"],
                    relief="flat", padding=(8, 9), font=("Microsoft YaHei UI", 9, "bold"))
    style.map("Treeview", background=[("selected", "#343b74")],
              foreground=[("selected", "#ffffff")])
    style.configure("TCombobox", fieldbackground=colors["field"], background=colors["field"],
                    foreground=colors["text"], arrowcolor=colors["muted"], borderwidth=0,
                    padding=(8, 7))
    style.map("TCombobox", fieldbackground=[("readonly", colors["field"])],
              selectbackground=[("readonly", colors["field"])],
              selectforeground=[("readonly", colors["text"])])
    style.configure("TEntry", fieldbackground=colors["field"], foreground=colors["text"],
                    insertcolor=colors["text"], borderwidth=0, padding=(9, 8))
    style.configure("Horizontal.TProgressbar", troughcolor=colors["field"],
                    background=colors["accent"], borderwidth=0, thickness=7)
    style.configure("Vertical.TScrollbar", background=colors["field"], troughcolor=colors["panel"],
                    borderwidth=0, arrowcolor=colors["muted"])

    def update_queue_count():
        count = len(worker.files)
        queue_count.set(f"{count} 个文件" if count else "等待添加文件")

    def add_files():
        paths = filedialog.askopenfilenames(
            title="选择音频文件",
            filetypes=[("音频文件", "*.mp3 *.flac *.wav *.aac *.ogg *.m4a *.m4b "
                                 "*.wma *.ape *.aiff *.aif *.opus *.amr *.caf *.mka "
                                 "*.mp4 *.ncm *.kgg *.kgm *.vpr "
                                 "*.qmc0 *.qmc2 *.qmc3 *.qmcflac *.qmcogg "
                                 "*.mflac *.mflac0 *.mflac1 *.mgg *.mgg0 *.mgg1"),
                       ("网易云加密", "*.ncm"),
                       ("酷狗加密", "*.kgg *.kgm *.vpr"),
                       ("QQ音乐加密", "*.qmc0 *.qmc2 *.qmc3 *.qmcflac *.qmcogg "
                                     "*.mflac *.mgg"),
                       ("所有文件", "*.*")])
        for p in paths:
            _add_file(gui, p)
        update_queue_count()

    def add_folder():
        d = filedialog.askdirectory(title="选择文件夹（递归扫描音频）")
        if d:
            _scan_folder(gui, d)
            update_queue_count()

    def remove_selected():
        if worker.running:
            return
        sel = gui["tree"].selection()
        for iid in sel:
            gui["tree"].delete(iid)
            worker.files = [f for f in worker.files if f["item"] != iid]
        update_queue_count()

    def clear_all():
        if worker.running:
            return
        gui["tree"].delete(*gui["tree"].get_children())
        worker.files.clear()
        worker.done = 0
        worker.total = 0
        update_queue_count()

    def browse_out():
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            out_dir_var.set(d)

    def open_output():
        path = out_dir_var.get().strip()
        if path:
            os.makedirs(path, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(path)

    def on_cancel():
        if worker.running:
            worker.cancel.set()
            log(gui, "info", "正在取消…")

    root.title("Sunwoo 音频转换工具")
    root.geometry("1120x760")
    root.minsize(920, 640)
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(1, weight=1)

    # 左侧操作栏
    sidebar = ttk.Frame(root, style="Sidebar.TFrame", width=228, padding=(20, 24))
    sidebar.grid(row=0, column=0, sticky="nsew")
    sidebar.grid_propagate(False)
    ttk.Label(sidebar, text="SUNWOO", style="Sidebar.TLabel",
              font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
    ttk.Label(sidebar, text="AUDIO STUDIO", style="SidebarMuted.TLabel",
              font=("Microsoft YaHei UI", 8, "bold")).pack(anchor="w", pady=(0, 30))
    ttk.Label(sidebar, text="导入音频", style="SidebarMuted.TLabel",
              font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 8))
    ttk.Button(sidebar, text="＋  添加文件", style="Sidebar.TButton",
               command=add_files).pack(fill="x", pady=(0, 8))
    ttk.Button(sidebar, text="▣  添加文件夹", style="Sidebar.TButton",
               command=add_folder).pack(fill="x")
    ttk.Separator(sidebar).pack(fill="x", pady=24)
    ttk.Label(sidebar, text="支持格式", style="SidebarMuted.TLabel",
              font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
    ttk.Label(sidebar, text="MP3  FLAC  WAV  AAC\nOGG  M4A  OPUS  MP4\n\nNCM  KGM/KGG  QMC\nMFLAC  MGG",
              style="SidebarMuted.TLabel", justify="left").pack(anchor="w", pady=(10, 0))
    ttk.Label(sidebar, text="本地转换 · 文件不会上传", style="SidebarMuted.TLabel",
              font=("Microsoft YaHei UI", 8)).pack(side="bottom", anchor="w")

    # 右侧主工作区
    main_area = ttk.Frame(root, padding=(24, 20, 24, 18))
    main_area.grid(row=0, column=1, sticky="nsew")
    main_area.grid_columnconfigure(0, weight=1)
    main_area.grid_rowconfigure(1, weight=1)

    header = ttk.Frame(main_area)
    header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
    ttk.Label(header, text="音频转换", font=("Microsoft YaHei UI", 20, "bold")).pack(side="left")
    queue_count = tk.StringVar(value="等待添加文件")
    ttk.Label(header, textvariable=queue_count, style="Muted.TLabel").pack(side="left", padx=14, pady=(7, 0))
    ttk.Button(header, text="清空队列", command=clear_all).pack(side="right")
    ttk.Button(header, text="移除选中", command=remove_selected).pack(side="right", padx=(0, 8))

    # 队列卡片
    queue_panel = ttk.Frame(main_area, style="Panel.TFrame", padding=1)
    queue_panel.grid(row=1, column=0, sticky="nsew")
    queue_panel.grid_rowconfigure(0, weight=1)
    queue_panel.grid_columnconfigure(0, weight=1)
    out_dir_var = tk.StringVar(value=DEFAULT_OUTPUT_DIR)
    cols = ("name", "ext", "size", "status")
    tree = ttk.Treeview(queue_panel, columns=cols, show="headings", selectmode="extended")
    tree.heading("name", text="文件名")
    tree.heading("ext", text="格式")
    tree.heading("size", text="大小")
    tree.heading("status", text="状态")
    tree.column("name", width=500, minwidth=240)
    tree.column("ext", width=90, minwidth=70, anchor="center", stretch=False)
    tree.column("size", width=100, minwidth=85, anchor="e", stretch=False)
    tree.column("status", width=150, minwidth=120, anchor="center", stretch=False)
    vsb = ttk.Scrollbar(queue_panel, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")

    # 参数卡片
    settings = ttk.Frame(main_area, style="Panel.TFrame", padding=(16, 13))
    settings.grid(row=2, column=0, sticky="ew", pady=(14, 0))
    for column in range(4):
        settings.grid_columnconfigure(column, weight=1, uniform="setting")
    ttk.Label(settings, text="输出格式", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(settings, text="码率", style="PanelMuted.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))
    ttk.Label(settings, text="采样率", style="PanelMuted.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 0))
    ttk.Label(settings, text="声道", style="PanelMuted.TLabel").grid(row=0, column=3, sticky="w", padx=(12, 0))
    fmt_var = tk.StringVar(value="mp3")
    br_var = tk.StringVar(value="默认")
    sr_var = tk.StringVar(value="保持不变")
    ch_var = tk.StringVar(value="保持不变")
    combo_specs = ((fmt_var, OUT_FMTS), (br_var, [o[0] for o in BITRATE_OPTS]),
                   (sr_var, [o[0] for o in SR_OPTS]), (ch_var, [o[0] for o in CH_OPTS]))
    for column, (variable, values) in enumerate(combo_specs):
        ttk.Combobox(settings, textvariable=variable, values=values, state="readonly").grid(
            row=1, column=column, sticky="ew", padx=(12 if column else 0, 0), pady=(5, 12))
    ttk.Label(settings, text="输出位置", style="PanelMuted.TLabel").grid(
        row=2, column=0, columnspan=4, sticky="w")
    output_row = ttk.Frame(settings, style="Panel.TFrame")
    output_row.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(5, 0))
    output_row.grid_columnconfigure(0, weight=1)
    ttk.Entry(output_row, textvariable=out_dir_var).grid(row=0, column=0, sticky="ew")
    ttk.Button(output_row, text="浏览…", command=browse_out).grid(row=0, column=1, padx=(8, 0))
    ttk.Button(output_row, text="打开目录", command=open_output).grid(row=0, column=2, padx=(8, 0))

    # 可展开活动日志
    activity = ttk.Frame(main_area, style="Panel.TFrame", padding=(14, 10))
    activity.grid(row=3, column=0, sticky="ew", pady=(10, 0))
    activity.grid_columnconfigure(0, weight=1)
    log_visible = tk.BooleanVar(value=False)
    log_text = tk.Text(activity, height=5, state="disabled", wrap="word",
                       font=("Consolas", 9))
    log_text.configure(background=colors["panel"], foreground=colors["text"],
                       insertbackground=colors["text"], borderwidth=0, padx=8, pady=8)
    log_text.tag_configure("log_info", foreground=colors["muted"])
    log_text.tag_configure("log_ok", foreground=colors["success"])
    log_text.tag_configure("log_err", foreground="#fca5a5")
    def toggle_log():
        if log_visible.get():
            log_text.grid_remove()
            log_visible.set(False)
            log_toggle.configure(text="查看日志 ︾")
        else:
            log_text.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
            log_visible.set(True)
            log_toggle.configure(text="收起日志 ︽")
    cur_lbl = ttk.Label(activity, text="尚未开始转换", style="Panel.TLabel", anchor="w")
    cur_lbl.grid(row=0, column=0, sticky="ew")
    log_toggle = ttk.Button(activity, text="查看日志 ︾", command=toggle_log)
    log_toggle.grid(row=0, column=2, sticky="e")

    # 固定底部转换栏
    controls = ttk.Frame(main_area, padding=(0, 14, 0, 0))
    controls.grid(row=4, column=0, sticky="ew")
    controls.grid_columnconfigure(0, weight=1)
    status_var = tk.StringVar(value="就绪")
    ttk.Label(controls, textvariable=status_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
    bar_cur = ttk.Progressbar(controls, maximum=100, value=0)
    bar_cur.grid(row=1, column=0, sticky="ew", pady=(7, 4))
    bar_total = ttk.Progressbar(controls, maximum=100, value=0)
    bar_total.grid(row=2, column=0, sticky="ew")
    cancel_btn = ttk.Button(controls, text="取消", style="Danger.TButton",
                            command=on_cancel, state="disabled")
    cancel_btn.grid(row=0, column=1, rowspan=3, padx=(14, 8), sticky="ns")
    start_btn = ttk.Button(controls, text="开始转换", style="Primary.TButton",
                           command=lambda: start_convert(root, tree, start_btn, cur_lbl,
                                                         bar_cur, bar_total, log_text))
    start_btn.grid(row=0, column=2, rowspan=3, sticky="ns")

    gui = {
        "tree": tree, "out_dir": out_dir_var, "fmt": fmt_var, "br": br_var,
        "sr": sr_var, "ch": ch_var, "start_btn": start_btn,
        "cancel_btn": cancel_btn, "cur_lbl": cur_lbl, "bar_cur": bar_cur,
        "bar_total": bar_total, "log": log_text, "status": status_var,
        "queue_count": queue_count,
    }
    root._gui = gui

    root.after(100, lambda: poll_ui(root, gui))
    return gui


class DualQueueView:
    """One logical queue rendered as two synchronized, side-by-side pages."""

    def __init__(self, parent, colors, row_height=37):
        from tkinter import ttk
        import customtkinter as ctk

        self.items = []
        self.values = {}
        self.selected_ids = set()
        self.offset = 0
        self.row_height = row_height
        self._syncing_selection = False
        self._render_pending = False
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self._colors = colors
        self._split = None
        self._drag_start = None
        self._drag_split = None
        self._active_trees = ("left", "right")
        self._merge_threshold = round(225 * APP_DPI_SCALE)
        self._divider_width = max(8, round(10 * APP_DPI_SCALE))
        self.left_card = ctk.CTkFrame(self.frame, corner_radius=18, fg_color=colors["panel"],
                                      bg_color=colors["panel"], border_width=1,
                                      border_color=colors["highlight"])
        self.right_card = ctk.CTkFrame(self.frame, corner_radius=18, fg_color=colors["panel"],
                                       bg_color=colors["panel"], border_width=1,
                                       border_color=colors["highlight"])
        self.divider = ctk.CTkFrame(self.frame, width=self._divider_width,
                                    corner_radius=5, fg_color="transparent",
                                    cursor="sb_h_double_arrow")
        self.divider_line = ctk.CTkFrame(self.divider, width=2, corner_radius=1,
                                         fg_color=colors["line"])
        self.divider_line.place(relx=.5, rely=.5, relheight=.96, anchor="center")
        self.divider.bind("<ButtonPress-1>", self._begin_split_drag)
        self.divider.bind("<B1-Motion>", self._drag_splitter)
        self.divider.bind("<ButtonRelease-1>", self._end_split_drag)
        self.divider_line.bind("<ButtonPress-1>", self._begin_split_drag)
        self.divider_line.bind("<B1-Motion>", self._drag_splitter)
        self.divider_line.bind("<ButtonRelease-1>", self._end_split_drag)
        self.frame.bind("<Configure>", self._layout_panes)
        for card in (self.left_card, self.right_card):
            card.grid_rowconfigure(0, weight=1)
            card.grid_columnconfigure(0, weight=1)

        self.left = ttk.Treeview(self.left_card, columns=("name", "status"), show="tree",
                                 selectmode="extended", style="Queue.Treeview")
        self.right = ttk.Treeview(self.right_card, columns=("name", "status"), show="tree",
                                  selectmode="extended", style="Queue.Treeview")
        for tree in (self.left, self.right):
            tree.column("#0", width=0, stretch=False)
            tree.column("name", width=330, minwidth=120, anchor="w")
            tree.column("status", width=90, minwidth=75, anchor="center", stretch=False)
            tree.grid(row=0, column=0, sticky="nsew", padx=7, pady=7)
            tree.bind("<MouseWheel>", self._on_wheel)
            tree.bind("<<TreeviewSelect>>", self._on_select)
            tree.bind("<Configure>", self._on_resize)

        self.left_scroll = ctk.CTkScrollbar(self.left_card, width=10, corner_radius=5,
                                            fg_color="transparent", button_color=colors["line"],
                                            button_hover_color=colors["muted"], command=self.yview)
        self.right_scroll = ctk.CTkScrollbar(self.right_card, width=10, corner_radius=5,
                                             fg_color="transparent", button_color=colors["line"],
                                             button_hover_color=colors["muted"], command=self.yview)
        self.left_scroll.grid(row=0, column=1, sticky="ns", pady=8, padx=(0, 5))
        self.right_scroll.grid(row=0, column=1, sticky="ns", pady=8, padx=(0, 5))

    def grid(self, *args, **kwargs):
        return self.frame.grid(*args, **kwargs)

    def _begin_split_drag(self, event):
        self._drag_start = event.x_root
        self._drag_split = self._split or self.frame.winfo_width() // 2

    def _drag_splitter(self, event):
        if self._drag_start is None:
            return
        width = max(1, self.frame.winfo_width())
        self._split = max(4, min(width - 4,
                          self._drag_split + event.x_root - self._drag_start))
        self._layout_panes()

    def _end_split_drag(self, _event=None):
        self._drag_start = self._drag_split = None
        self._layout_panes()

    def _layout_panes(self, _event=None):
        """Lay out two pages or merge them when either page becomes unusable."""
        width, height = self.frame.winfo_width(), self.frame.winfo_height()
        if width <= 2 or height <= 2:
            return
        if self._split is None:
            self._split = width / 2
        self._split = max(4, min(width - 4, self._split))
        gap = self._divider_width
        left_w = max(0, self._split - gap // 2)
        right_x = self._split + gap // 2
        right_w = max(0, width - right_x)

        def place_px(widget, x, y, pane_width, pane_height):
            # CTk scales coordinates but not configure(width/height) uniformly
            # across versions.  The underlying Tk place command accepts the
            # physical winfo pixel coordinates used here.
            widget.tk.call("place", "configure", widget._w,
                           "-x", round(x), "-y", round(y),
                           "-width", max(1, round(pane_width)),
                           "-height", max(1, round(pane_height)))
            widget._canvas.configure(width=max(1, round(pane_width)),
                                     height=max(1, round(pane_height)))
            widget._canvas.delete("background_parts")
            widget._draw(no_color_updates=True)

        if left_w < self._merge_threshold:
            self.left_card.place_forget()
            self.right_card.place(x=0, y=0)
            place_px(self.right_card, gap, 0, width - gap, height)
            self.divider.place(x=0, y=0)
            place_px(self.divider, 0, 0, gap, height)
            active = ("right",)
        elif right_w < self._merge_threshold:
            self.right_card.place_forget()
            self.left_card.place(x=0, y=0)
            place_px(self.left_card, 0, 0, width - gap, height)
            self.divider.place(x=0, y=0)
            place_px(self.divider, width - gap, 0, gap, height)
            active = ("left",)
        else:
            self.left_card.place(x=0, y=0)
            place_px(self.left_card, 0, 0, left_w, height)
            self.divider.place(x=0, y=0)
            place_px(self.divider, left_w, 0, gap, height)
            self.right_card.place(x=0, y=0)
            place_px(self.right_card, right_x, 0, right_w, height)
            active = ("left", "right")
        if active != self._active_trees:
            self._active_trees = active
            self.offset = min(self.offset, self._max_offset())
            self._schedule_render()

    def _capacity(self):
        trees = [self.left if name == "left" else self.right for name in self._active_trees]
        height = max([tree.winfo_height() for tree in trees] + [self.row_height * 4])
        return max(1, height // self.row_height)

    def _max_offset(self):
        return max(0, len(self.items) - self._capacity() * len(self._active_trees))

    def _on_resize(self, _event=None):
        self.offset = min(self.offset, self._max_offset())
        self._schedule_render()

    def _schedule_render(self):
        """Coalesce bursts of inserts/status changes into one idle render."""
        if self._render_pending:
            return
        self._render_pending = True
        self.frame.after_idle(self._render)

    def _on_wheel(self, event):
        steps = -int(event.delta / 120) if event.delta else 0
        self.offset = min(self._max_offset(), max(0, self.offset + steps * 3))
        self._render()
        return "break"

    def yview(self, *args):
        if not args:
            total = max(1, len(self.items))
            visible = min(total, self._capacity() * len(self._active_trees))
            return self.offset / total, min(1.0, (self.offset + visible) / total)
        if args[0] == "moveto":
            self.offset = round(float(args[1]) * self._max_offset())
        elif args[0] == "scroll":
            amount = int(args[1]) * (self._capacity() if args[2] == "pages" else 3)
            self.offset = min(self._max_offset(), max(0, self.offset + amount))
        self._render()

    def _on_select(self, event):
        if self._syncing_selection:
            return
        visible = set(self.left.get_children()) | set(self.right.get_children())
        self.selected_ids.difference_update(visible)
        self.selected_ids.update(self.left.selection())
        self.selected_ids.update(self.right.selection())

    def _render(self):
        self._render_pending = False
        cap = self._capacity()
        if len(self._active_trees) == 1:
            left_ids = self.items[self.offset:self.offset + cap] if self._active_trees[0] == "left" else []
            right_ids = self.items[self.offset:self.offset + cap] if self._active_trees[0] == "right" else []
        else:
            left_ids = self.items[self.offset:self.offset + cap]
            right_ids = self.items[self.offset + cap:self.offset + cap * 2]
        self._syncing_selection = True
        try:
            for tree, ids in ((self.left, left_ids), (self.right, right_ids)):
                tree.delete(*tree.get_children())
                for iid in ids:
                    tree.insert("", "end", iid=iid, values=self.values[iid])
                chosen = [iid for iid in ids if iid in self.selected_ids]
                if chosen:
                    tree.selection_set(chosen)
        finally:
            self._syncing_selection = False
        first, last = self.yview()
        self.left_scroll.set(first, last)
        self.right_scroll.set(first, last)

    def insert(self, _parent, _index, values=()):
        iid = f"q{time.time_ns()}"
        self.items.append(iid)
        self.values[iid] = tuple(values)
        self._schedule_render()
        return iid

    def delete(self, *iids):
        for iid in iids:
            if iid in self.items:
                self.items.remove(iid)
            self.values.pop(iid, None)
            self.selected_ids.discard(iid)
        self.offset = min(self.offset, self._max_offset())
        self._render()

    def selection(self):
        return tuple(iid for iid in self.items if iid in self.selected_ids)

    def exists(self, iid):
        return iid in self.values

    def get_children(self):
        return tuple(self.items)

    def item(self, iid, **kwargs):
        if "values" in kwargs:
            self.values[iid] = tuple(kwargs["values"])
            self._schedule_render()
        return {"values": self.values.get(iid, ())}


class SingleQueueView:
    """One full-width pending queue with the small Treeview API used by the app."""

    def __init__(self, parent, colors):
        from tkinter import ttk
        import customtkinter as ctk

        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame.grid_rowconfigure(0, weight=1)
        self.frame.grid_columnconfigure(0, weight=1)
        self.card = ctk.CTkFrame(
            self.frame, corner_radius=18, fg_color=colors["panel"],
            border_width=1, border_color=colors["highlight"])
        self.card.grid(row=0, column=0, sticky="nsew")
        self.card.grid_rowconfigure(0, weight=1)
        self.card.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            self.card, columns=("name", "status"), show="tree",
            selectmode="extended", style="Queue.Treeview")
        self.tree.column("#0", width=0, stretch=False)
        self.tree.column("name", width=660, minwidth=180, anchor="w")
        self.tree.column("status", width=105, minwidth=78, anchor="center",
                         stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 2), pady=8)
        self.scroll = ctk.CTkScrollbar(
            self.card, width=10, corner_radius=5, fg_color="transparent",
            button_color=colors["line"], button_hover_color=colors["muted"],
            command=self.tree.yview)
        self.scroll.grid(row=0, column=1, sticky="ns", pady=9, padx=(0, 6))
        self.tree.configure(yscrollcommand=self.scroll.set)

        # Compatibility aliases for shared drag-and-drop registration.
        self.left = self.right = self.tree
        self.left_card = self.right_card = self.card

    def grid(self, *args, **kwargs):
        return self.frame.grid(*args, **kwargs)

    def insert(self, parent, index, values=()):
        return self.tree.insert(parent, index, values=values)

    def delete(self, *iids):
        existing = [iid for iid in iids if self.tree.exists(iid)]
        if existing:
            self.tree.delete(*existing)

    def selection(self):
        return self.tree.selection()

    def exists(self, iid):
        return self.tree.exists(iid)

    def get_children(self):
        return self.tree.get_children()

    def item(self, iid, **kwargs):
        return self.tree.item(iid, **kwargs)


class HoverNotice:
    """Accessible hover/focus notice used for the QQ Music prerequisite."""

    def __init__(self, root, widget, text, colors):
        self.root = root
        self.widget = widget
        self.text = text
        self.colors = colors
        self.popup = None
        self.after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<FocusIn>", self.show, add="+")
        widget.bind("<FocusOut>", self.hide, add="+")

    def _schedule(self, _event=None):
        self.after_id = self.root.after(220, self.show)

    def show(self, _event=None):
        import tkinter as tk
        import customtkinter as ctk

        if self.popup is not None or not self.widget.winfo_exists():
            return
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.popup = tk.Toplevel(self.root)
        self.popup.overrideredirect(True)
        try:
            self.popup.attributes("-topmost", True)
        except tk.TclError:
            pass
        panel = ctk.CTkFrame(
            self.popup, width=390, corner_radius=14,
            fg_color=self.colors["accent"], border_width=1,
            border_color=self.colors["highlight"])
        panel.pack(fill="both", expand=True)
        ctk.CTkLabel(
            panel, text="QQ 音乐加密格式转换说明", anchor="w",
            text_color="#ffffff", font=("Microsoft YaHei UI", 11, "bold")).pack(
                fill="x", padx=16, pady=(13, 5))
        ctk.CTkLabel(
            panel, text=self.text, justify="left", anchor="w", wraplength=350,
            text_color="#e9f2f6", font=("Microsoft YaHei UI", 9)).pack(
                fill="x", padx=16, pady=(0, 14))
        self.popup.update_idletasks()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 10
        y = self.widget.winfo_rooty() - 8
        screen_w = self.popup.winfo_screenwidth()
        if x + self.popup.winfo_reqwidth() > screen_w - 12:
            x = self.widget.winfo_rootx() - self.popup.winfo_reqwidth() - 10
        self.popup.geometry(f"+{max(8, x)}+{max(8, y)}")

    def hide(self, _event=None):
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        if self.popup is not None:
            try:
                self.popup.destroy()
            except Exception:
                pass
            self.popup = None


class RoundedProgress:
    """Crisp rounded progress rail with a truly empty zero state."""

    def __init__(self, parent, colors):
        import tkinter as tk
        self.colors = colors
        self.value = 0.0
        self.widget = tk.Canvas(parent, height=8, bg=colors["bg"],
                                bd=0, highlightthickness=0)
        self.widget.bind("<Configure>", self._draw, add="+")

    def _rounded_bar(self, x1, y1, x2, y2, color):
        radius = max(1, (y2 - y1) / 2)
        self.widget.create_rectangle(x1 + radius, y1, x2 - radius, y2,
                                     fill=color, outline="")
        self.widget.create_oval(x1, y1, x1 + radius * 2, y2,
                                fill=color, outline="")
        self.widget.create_oval(x2 - radius * 2, y1, x2, y2,
                                fill=color, outline="")

    def _draw(self, _event=None):
        self.widget.delete("all")
        width = max(1, self.widget.winfo_width())
        height = max(2, self.widget.winfo_height())
        self._rounded_bar(0, 0, width, height, self.colors["field"])
        if self.value > 0:
            fill_width = max(height, width * self.value / 100.0)
            self._rounded_bar(0, 0, min(width, fill_width), height,
                              self.colors["accent"])

    def grid(self, *args, **kwargs):
        return self.widget.grid(*args, **kwargs)

    def configure(self, **kwargs):
        if "value" in kwargs:
            self.value = max(0, min(100, float(kwargs.pop("value"))))
            self._draw()
        if kwargs:
            self.widget.configure(**kwargs)


class GlassComboBox:
    """CTk combobox wrapper with a crisp, high-contrast custom chevron."""

    def __init__(self, parent, variable, values, colors):
        import tkinter as tk
        import customtkinter as ctk

        self.variable = variable
        self.values = list(values)
        self.frame = ctk.CTkFrame(parent, height=38, corner_radius=12,
                                  fg_color=colors["field"], border_width=1,
                                  border_color=colors["line"])
        self.frame.grid_propagate(False)
        self.frame.grid_columnconfigure(0, weight=1)
        self.label = ctk.CTkLabel(self.frame, textvariable=variable, anchor="w",
                                  text_color=colors["text"])
        self.label.grid(row=0, column=0, sticky="nsew", padx=(12, 4), pady=1)
        self.menu = tk.Menu(self.frame, tearoff=False, relief="flat", bd=0,
                            bg=colors["panel"], fg=colors["text"],
                            activebackground=colors["field"],
                            activeforeground=colors["text"],
                            font=("Microsoft YaHei UI", 10))
        for value in self.values:
            self.menu.add_command(label=value,
                                  command=lambda selected=value: self.variable.set(selected))
        self.arrow = ctk.CTkButton(
            self.frame, text="⌄", width=34, height=34, corner_radius=10,
            fg_color=colors.get("accent_soft", colors["field"]),
            hover_color=colors["line"], text_color=colors.get("accent", colors["text"]),
            font=("Segoe UI Symbol", 16, "bold"),
            command=self._open_menu)
        self.arrow.grid(row=0, column=1, sticky="ns", padx=(0, 2), pady=2)
        self.label.bind("<Button-1>", self._open_menu)
        self.frame.bind("<Button-1>", self._open_menu)

    def _open_menu(self, _event=None):
        self.frame.update_idletasks()
        self.menu.tk_popup(self.frame.winfo_rootx(),
                           self.frame.winfo_rooty() + self.frame.winfo_height())

    def pack(self, *args, **kwargs):
        return self.frame.pack(*args, **kwargs)


def build_gui_v2(root):
    """Three-column workspace: import/errors, pending queue, controls/results."""
    import tkinter as tk
    from tkinter import filedialog, ttk
    import customtkinter as ctk

    # Airy desktop-workbench palette inspired by the supplied reference:
    # neutral canvas, floating white cards, hairline borders and one clear
    # blue action colour.  Keeping every layer opaque avoids redraw artefacts
    # while retaining the visual character of translucent glass.
    c = {"bg": "#f6f8fb", "side": "#fbfcfe", "panel": "#ffffff",
         "field": "#f3f6fa", "line": "#dfe5ee", "highlight": "#e7ebf2",
         "text": "#182230", "muted": "#718096", "accent": "#3478f6",
         "accent_soft": "#eaf2ff", "danger": "#fff0f1", "shadow": "#e9edf3"}
    root.configure(bg=c["bg"])
    root.title(APP_NAME)
    apply_window_icons(root)
    configure_window_geometry(root, 1440, 860, 1100, 720)
    root.grid_rowconfigure(0, weight=0)
    root.grid_rowconfigure(1, weight=1)
    root.grid_rowconfigure(2, weight=0)
    root.grid_columnconfigure(0, weight=1)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=c["bg"], foreground=c["text"],
                    font=("Microsoft YaHei UI", 10))
    style.configure("TFrame", background=c["bg"])
    style.configure("Side.TFrame", background=c["side"])
    style.configure("Panel.TFrame", background=c["panel"])
    style.configure("TLabel", background=c["bg"], foreground=c["text"])
    style.configure("Side.TLabel", background=c["side"], foreground=c["text"])
    style.configure("SideMuted.TLabel", background=c["side"], foreground=c["muted"])
    style.configure("Panel.TLabel", background=c["panel"], foreground=c["text"])
    style.configure("PanelMuted.TLabel", background=c["panel"], foreground=c["muted"])
    style.configure("TButton", background=c["field"], foreground=c["text"],
                    borderwidth=0, padding=(12, 10), font=("Microsoft YaHei UI", 9, "bold"))
    style.map("TButton", background=[("active", "#eaf2ff"), ("disabled", "#f4f6f8")],
              foreground=[("disabled", "#a5afbc")])
    style.configure("Primary.TButton", background=c["accent"], foreground="white",
                    padding=(16, 14), font=("Microsoft YaHei UI", 11, "bold"))
    style.map("Primary.TButton", background=[("active", "#2468de"), ("disabled", "#afc7ee")])
    style.configure("Danger.TButton", background=c["danger"], foreground="#b74b59")
    style.configure("Treeview", background=c["panel"], fieldbackground=c["panel"],
                    foreground=c["text"], rowheight=37, borderwidth=0)
    style.configure("Queue.Treeview", background=c["panel"], fieldbackground=c["panel"],
                    foreground=c["text"], rowheight=37, borderwidth=0, relief="flat")
    tree_layout = [("Treeview.field", {"sticky": "nswe", "border": "0", "children": [
        ("Treeview.padding", {"sticky": "nswe", "children": [
            ("Treeview.treearea", {"sticky": "nswe"})]})]})]
    style.layout("Queue.Treeview", tree_layout)
    style.configure("Rounded.Treeview", background=c["panel"], fieldbackground=c["panel"],
                    foreground=c["text"], rowheight=37, borderwidth=0, relief="flat")
    style.layout("Rounded.Treeview", tree_layout)
    style.configure("Rounded.Treeview.Heading", background=c["field"], foreground=c["muted"],
                    relief="flat", borderwidth=0, padding=(8, 9))
    style.configure("Treeview.Heading", background=c["field"], foreground=c["muted"],
                    relief="flat", padding=(8, 9), font=("Microsoft YaHei UI", 9, "bold"))
    style.map("Treeview", background=[("selected", c["accent_soft"])], foreground=[("selected", c["text"])])
    style.configure("TCombobox", fieldbackground=c["field"], background=c["field"],
                    foreground=c["text"], arrowcolor=c["muted"], borderwidth=0, padding=(7, 6))
    style.map("TCombobox", fieldbackground=[("readonly", c["field"])],
              selectbackground=[("readonly", c["field"])], selectforeground=[("readonly", c["text"])])
    style.configure("TEntry", fieldbackground=c["field"], foreground=c["text"],
                    insertcolor=c["text"], borderwidth=0, padding=(8, 7))
    style.configure("Horizontal.TProgressbar", troughcolor=c["field"], background=c["accent"],
                    borderwidth=0, thickness=7)

    out_dir_var = tk.StringVar(value=DEFAULT_OUTPUT_DIR)
    fmt_var, br_var = tk.StringVar(value="mp3"), tk.StringVar(value="默认")
    sr_var, ch_var = tk.StringVar(value="保持不变"), tk.StringVar(value="保持不变")
    parallel_var = tk.StringVar(value="2x")
    queue_count = tk.StringVar(value="等待添加文件")
    status_var = tk.StringVar(value="就绪")
    lrc_enabled_var = tk.BooleanVar(value=False)
    completed_paths, completed_records, error_details = {}, {}, {}
    copied_lrc_paths = set()
    copied_lrc_lock = threading.Lock()
    icons = {name: load_ui_icon(name, (20, 20), c["text"]) for name in
             ("file_add", "folder_add", "remove", "clear", "browse", "folder_open",
              "pause", "reset")}
    icons["play"] = load_ui_icon("play", (24, 24), "#ffffff")

    def update_count():
        queue_count.set(f"待转换 {len(worker.files)} 个文件" if worker.files else "等待添加文件")
        if "empty_state" in gui:
            if worker.files:
                gui["empty_state"].place_forget()
            elif not gui["empty_state"].winfo_ismapped():
                gui["empty_state"].place(relx=0.5, rely=0.47, anchor="center")
                gui["empty_state"].lift()

    def import_paths(paths):
        for path in paths:
            if os.path.isdir(path):
                _scan_folder(gui, path)
            elif os.path.isfile(path):
                _add_file(gui, path)
        update_count()

    def add_files():
        paths = filedialog.askopenfilenames(
            title="选择音频文件",
            filetypes=[("支持的音频",
                        "*.mp3 *.flac *.wav *.aac *.ogg *.m4a *.m4b *.wma *.ape "
                        "*.aiff *.opus *.ncm *.kgg *.kgm *.vpr *.qmc0 *.qmc1 *.qmc2 "
                        "*.qmc3 *.qmc4 *.qmc6 *.qmc8 *.qmcflac *.qmcogg *.mflac "
                        "*.mflac0 *.mflac1 *.mgg *.mgg0 *.mgg1 *.mggl *.bkcmp3 "
                        "*.bkcm4a *.bkcflac *.bkcwav *.bkcape *.bkcogg *.bkcwma "
                        "*.tkm *.tm0 *.tm2 *.tm3 *.tm6"),
                       ("所有文件", "*.*")])
        import_paths(paths)

    def add_folder():
        path = filedialog.askdirectory(title="选择文件夹（递归扫描）")
        if path:
            import_paths((path,))

    def remove_selected():
        if worker.running:
            return
        selected = set(pending.selection())
        for iid in selected:
            if pending.exists(iid):
                pending.delete(iid)
        removed = [item for item in worker.files if item["item"] in selected]
        worker.files = [item for item in worker.files if item["item"] not in selected]
        worker.path_keys.difference_update(item["path_key"] for item in removed)
        update_count()

    def clear_queue():
        if worker.running:
            return
        worker.import_generation += 1
        worker.scanning = 0
        pending.delete(*pending.get_children())
        worker.files.clear()
        worker.path_keys.clear()
        worker.import_queue.clear()
        worker.scan_done.clear()
        update_count()

    def deduplicate_queue():
        """Apply the three requested rules only when the user presses the button."""
        if worker.running:
            status_var.set("转换进行中，暂时无法整理队列")
            return
        if worker.scanning or worker.import_queue:
            status_var.set("文件夹仍在扫描，请稍后再执行去重")
            return
        kept, removed = [], []
        for item in worker.files:
            reason = _duplicate_reason(item["name"], item["size"], kept)
            if reason:
                removed.append((item, reason))
            else:
                kept.append(item)
        if not removed:
            status_var.set("未发现重复文件")
            worker.log.append(("info", "手动去重完成：未发现重复文件"))
            return
        for item, reason in removed:
            if pending.exists(item["item"]):
                pending.delete(item["item"])
            worker.path_keys.discard(item["path_key"])
            worker.status.pop(item["item"], None)
            worker.log.append(("info", f"手动去重：已移除 {item['name']}（{reason}）"))
        worker.files = kept
        update_count()
        status_var.set(f"去重完成：已移除 {len(removed)} 个文件")

    def browse_output():
        path = filedialog.askdirectory(title="选择输出位置")
        if path:
            out_dir_var.set(path)

    def open_output():
        path = out_dir_var.get().strip()
        if path:
            os.makedirs(path, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(path)

    def copy_lrc_records(records, announce=False):
        copied = 0
        for source_path, output_path in records:
            key = os.path.normcase(os.path.realpath(output_path))
            expected_lrc = os.path.splitext(output_path)[0] + ".lrc"
            with copied_lrc_lock:
                if key in copied_lrc_paths and os.path.isfile(expected_lrc):
                    continue
                copied_lrc_paths.discard(key)
            try:
                lrc_path = copy_matching_lrc(source_path, output_path)
            except OSError as exc:
                worker.log.append(("error", f"歌词复制失败: {os.path.basename(source_path)} — {exc}"))
                continue
            if lrc_path:
                with copied_lrc_lock:
                    copied_lrc_paths.add(key)
                copied += 1
                worker.log.append(("success", f"歌词已复制: {os.path.basename(lrc_path)}"))
        if announce:
            worker.log.append(("info", f"LRC 扫描完成：复制 {copied} 个歌词文件"))

    def copy_lrc_for_completed(source_path, output_path):
        """Called on the Tk thread whenever one output enters the completed list."""
        if not lrc_enabled_var.get():
            return
        threading.Thread(
            target=copy_lrc_records, args=([(source_path, output_path)],),
            name="lrc-auto-copy", daemon=True).start()

    def toggle_lrc_copy():
        if not lrc_enabled_var.get():
            status_var.set("已关闭自动匹配 LRC 歌词")
            return
        records = list(completed_records.values())
        status_var.set("已开启自动匹配 LRC 歌词")
        # Enabling the feature also scans outputs that finished earlier.
        threading.Thread(
            target=copy_lrc_records, args=(records, True),
            name="lrc-completed-scan", daemon=True).start()

    def open_completed(_event=None):
        selected = completed.selection()
        if not selected:
            return
        path = completed_paths.get(selected[0])
        if path and os.path.exists(path) and sys.platform == "win32":
            subprocess_path = os.path.dirname(path)
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    def copy_error(_event=None):
        selected = errors.selection()
        if not selected:
            return
        detail = error_details.get(selected[0])
        if detail:
            root.clipboard_clear()
            root.clipboard_append(detail)
            root.update_idletasks()
            status_var.set("错误详情已复制到剪贴板")

    def toggle_pause():
        if not worker.running:
            return
        paused = not worker.pause.is_set()
        if paused:
            worker.pause.set()
            converter.pause_active_process(True)
            pause_btn.configure(text="继续", image=icons["play"])
            status_var.set("已暂停")
        else:
            converter.pause_active_process(False)
            worker.pause.clear()
            pause_btn.configure(text="暂停", image=icons["pause"])
            status_var.set("继续转换…")

    reset_in_progress = {"active": False}

    def reset_all():
        """Restore every volatile UI/worker field to its startup state."""
        if reset_in_progress["active"]:
            return
        reset_in_progress["active"] = True
        # Invalidate folder scanners immediately so stale discoveries cannot
        # repopulate the freshly reset list.
        worker.import_generation += 1
        worker.import_queue.clear()
        worker.scan_done.clear()
        worker.scanning = 0
        if worker.running:
            converter.pause_active_process(False)
            worker.pause.clear()
            worker.cancel.set()
            start_btn.configure(state="disabled")
            pause_btn.configure(state="disabled", text="暂停", image=icons["pause"])
            status_var.set("正在停止当前任务并重置…")

        def finish_when_idle():
            if worker.running:
                root.after(60, finish_when_idle)
                return
            pending.delete(*pending.get_children())
            completed.delete(*completed.get_children())
            errors.delete(*errors.get_children())
            completed_paths.clear()
            completed_records.clear()
            error_details.clear()
            with copied_lrc_lock:
                copied_lrc_paths.clear()
            worker.files.clear()
            worker.path_keys.clear()
            worker.import_queue.clear()
            worker.scan_done.clear()
            worker.scanning = 0
            worker.total = worker.done = worker.errors = 0
            worker.current = ""
            worker.current_pct = 0.0
            with worker.state_lock:
                worker.active_tasks.clear()
                worker.max_workers = 2
            worker.log.clear()
            worker.status.clear()
            worker.completed.clear()
            worker.failures.clear()
            worker.cancel.clear()
            worker.pause.clear()
            fmt_var.set("mp3")
            br_var.set("默认")
            sr_var.set("保持不变")
            ch_var.set("保持不变")
            parallel_var.set("2x")
            lrc_enabled_var.set(False)
            out_dir_var.set(DEFAULT_OUTPUT_DIR)
            queue_count.set("等待添加文件")
            cur_lbl.configure(text="就绪")
            status_var.set("就绪")
            bar_cur.configure(value=0)
            bar_total.configure(value=0)
            start_btn.configure(state="normal")
            pause_btn.configure(state="disabled", text="暂停", image=icons["pause"])
            log_text.configure(state="normal")
            log_text.delete("1.0", "end")
            log_text.configure(state="disabled")

            def restore_default_split():
                workspace.update_idletasks()
                total = workspace.winfo_width()
                sash_w = int(workspace.cget("sashwidth"))
                workspace.sash_place(0, max(center_min_width,
                                             total - right_width - sash_w), 0)
                stabilize_workspace()

            root.after_idle(restore_default_split)
            reset_in_progress["active"] = False

        finish_when_idle()

    def handle_drop(event):
        try:
            paths = root.tk.splitlist(event.data)
        except Exception:
            paths = (event.data,)
        import_paths(paths)

    # Top command bar: identity and the three high-frequency import actions.
    topbar = ctk.CTkFrame(root, height=76, corner_radius=0, fg_color=c["panel"],
                          border_width=0)
    topbar.grid(row=0, column=0, sticky="ew")
    topbar.grid_propagate(False)
    topbar.grid_columnconfigure(1, weight=1)
    try:
        from PIL import Image
        brand_icon = ctk.CTkImage(
            Image.open(resource_path(os.path.join("assets", "miaomiao_avatar_rounded.png"))),
            size=(42, 42))
    except Exception:
        brand_icon = None
    brand = ctk.CTkFrame(topbar, fg_color="transparent")
    brand.grid(row=0, column=0, sticky="w", padx=(22, 12), pady=14)
    ctk.CTkLabel(brand, text="", image=brand_icon, width=44).pack(side="left")
    brand_text = ctk.CTkFrame(brand, fg_color="transparent")
    brand_text.pack(side="left", padx=(10, 0))
    ctk.CTkLabel(brand_text, text="喵喵转码器", text_color=c["text"],
                 font=("Microsoft YaHei UI", 16, "bold"), anchor="w").pack(anchor="w")
    ctk.CTkLabel(brand_text, text="本地音频转换工作台", text_color=c["muted"],
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(anchor="w")

    quick_actions = ctk.CTkFrame(topbar, fg_color="transparent")
    quick_actions.grid(row=0, column=2, sticky="e", padx=(8, 22), pady=14)
    secondary_button = {"height": 40, "corner_radius": 13, "fg_color": c["panel"],
                        "text_color": c["text"], "border_width": 1,
                        "border_color": c["line"], "hover_color": c["field"]}
    ctk.CTkButton(quick_actions, text="添加文件", image=icons["file_add"],
                  command=add_files, **secondary_button).pack(side="left", padx=(0, 8))
    ctk.CTkButton(quick_actions, text="添加文件夹", image=icons["folder_add"],
                  command=add_folder, **secondary_button).pack(side="left", padx=(0, 8))
    ctk.CTkButton(quick_actions, text="整理重复项", image=icons["clear"],
                  command=deduplicate_queue, **secondary_button).pack(side="left")

    # The two-part workspace remains width-adjustable, but each side uses a
    # stable opaque surface so dragging the divider stays smooth on Windows.
    workspace = tk.PanedWindow(
        root, orient="horizontal", sashwidth=6, sashrelief="flat", bd=0,
        bg=c["bg"], opaqueresize=False, proxybackground=c["accent"],
        proxyborderwidth=0, proxyrelief="flat", cursor="sb_h_double_arrow")
    workspace.grid(row=1, column=0, sticky="nsew", padx=18, pady=(16, 8))

    queue_shell = ctk.CTkFrame(workspace, corner_radius=20, fg_color=c["panel"],
                               border_width=1, border_color=c["line"])
    queue_shell.grid_rowconfigure(1, weight=1)
    queue_shell.grid_columnconfigure(0, weight=1)
    queue_header = ctk.CTkFrame(queue_shell, height=72, fg_color="transparent")
    queue_header.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 4))
    queue_header.grid_propagate(False)
    queue_header.grid_columnconfigure(0, weight=1)
    title_box = ctk.CTkFrame(queue_header, fg_color="transparent")
    title_box.grid(row=0, column=0, sticky="w")
    ctk.CTkLabel(title_box, text="转换队列", text_color=c["text"],
                 font=("Microsoft YaHei UI", 17, "bold"), anchor="w").pack(anchor="w")
    ctk.CTkLabel(title_box, textvariable=queue_count, text_color=c["muted"],
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(anchor="w", pady=(2, 0))
    queue_tools = ctk.CTkFrame(queue_header, fg_color="transparent")
    queue_tools.grid(row=0, column=1, sticky="e")
    ctk.CTkButton(queue_tools, text="移除", image=icons["remove"], width=82,
                  command=remove_selected, **secondary_button).pack(side="left", padx=(0, 8))
    ctk.CTkButton(queue_tools, text="清空", image=icons["clear"], width=82,
                  command=clear_queue, **secondary_button).pack(side="left")

    queue_body = ctk.CTkFrame(queue_shell, fg_color="transparent")
    queue_body.grid(row=1, column=0, sticky="nsew", padx=14)
    queue_body.grid_rowconfigure(0, weight=1)
    queue_body.grid_columnconfigure(0, weight=1)
    pending = SingleQueueView(queue_body, c)
    pending.grid(row=0, column=0, sticky="nsew")
    empty_state = ctk.CTkFrame(queue_body, corner_radius=18,
                               fg_color=c["field"], border_width=1,
                               border_color=c["line"])
    empty_state.place(relx=0.5, rely=0.47, anchor="center")
    ctk.CTkLabel(empty_state, text="＋", text_color=c["accent"],
                 fg_color=c["accent_soft"], width=44, height=44,
                 corner_radius=14,
                 font=("Microsoft YaHei UI", 22)).pack(padx=72, pady=(20, 8))
    ctk.CTkLabel(empty_state, text="拖放音频文件或文件夹到这里",
                 text_color=c["text"],
                 font=("Microsoft YaHei UI", 11, "bold")).pack()
    ctk.CTkLabel(empty_state, text="也可以使用顶部的导入按钮",
                 text_color=c["muted"],
                 font=("Microsoft YaHei UI", 9)).pack(pady=(4, 20))
    root.after_idle(empty_state.lift)
    queue_footer = ctk.CTkFrame(queue_shell, height=42, fg_color="transparent")
    queue_footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(3, 10))
    queue_footer.grid_propagate(False)
    queue_footer.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(queue_footer,
                 text="拖放文件或文件夹到队列  ·  支持 MP3 / FLAC / WAV / AAC / MP4 / NCM / KGM / KGG / QMC / MFLAC / MGG",
                 text_color=c["muted"], font=("Microsoft YaHei UI", 8),
                 anchor="w").grid(row=0, column=0, sticky="w")
    qq_notice = ctk.CTkLabel(
        queue_footer, text="  QQ 加密格式说明  ", cursor="hand2",
        text_color=c["accent"], fg_color=c["accent_soft"], corner_radius=10,
        height=28, font=("Microsoft YaHei UI", 8, "bold"))
    qq_notice.grid(row=0, column=1, sticky="e")
    root._qq_notice = HoverNotice(
        root, qq_notice,
        "因 QQ 音乐加密格式的特殊性，转换前需要先运行本机 QQ 音乐客户端，"
        "并确保待转换文件来自该客户端，且当前登录账号仍拥有该歌曲的播放权限。\n\n"
        "首次成功取得密钥后，同一歌曲可使用本机加密缓存离线转换。",
        c)

    # Right hub: compact settings and a dedicated result centre.  The two
    # cards share the same visual rhythm but remain logically independent.
    right = ctk.CTkFrame(workspace, corner_radius=20, fg_color=c["bg"])
    right.grid_rowconfigure(0, weight=1)
    right.grid_columnconfigure(0, weight=0, minsize=292)
    right.grid_columnconfigure(1, weight=1, minsize=250)

    controls_col = ctk.CTkFrame(right, width=300, corner_radius=20,
                                fg_color=c["panel"], border_width=1,
                                border_color=c["line"])
    controls_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    controls_col.grid_propagate(False)
    ctk.CTkLabel(controls_col, text="转换设置", text_color=c["text"],
                 font=("Microsoft YaHei UI", 16, "bold"), anchor="w").pack(
                     fill="x", padx=18, pady=(18, 2))
    ctk.CTkLabel(controls_col, text="调整输出参数与并行任务数量", text_color=c["muted"],
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(
                     fill="x", padx=18, pady=(0, 12))

    settings_grid = ctk.CTkFrame(controls_col, fg_color="transparent")
    settings_grid.pack(fill="x", padx=14)
    settings_grid.grid_columnconfigure((0, 1), weight=1, uniform="settings")
    settings = (("输出格式", fmt_var, OUT_FMTS),
                ("同步转换", parallel_var, ["2x", "4x", "8x", "16x"]),
                ("码率", br_var, [o[0] for o in BITRATE_OPTS]),
                ("采样率", sr_var, [o[0] for o in SR_OPTS]),
                ("声道", ch_var, [o[0] for o in CH_OPTS]))
    for index, (label, variable, values) in enumerate(settings):
        row, column = divmod(index, 2)
        cell = ctk.CTkFrame(settings_grid, fg_color="transparent")
        cell.grid(row=row, column=column, sticky="ew", padx=4, pady=(0, 8))
        ctk.CTkLabel(cell, text=label, text_color=c["muted"], anchor="w",
                     height=18, font=("Microsoft YaHei UI", 9)).pack(fill="x", pady=(0, 3))
        GlassComboBox(cell, variable, values, c).pack(fill="x")

    ctk.CTkLabel(controls_col, text="输出位置", text_color=c["muted"], anchor="w",
                 font=("Microsoft YaHei UI", 9)).pack(fill="x", padx=18, pady=(2, 4))
    ctk.CTkEntry(controls_col, textvariable=out_dir_var, height=38, corner_radius=12,
                 fg_color=c["field"], text_color=c["text"], border_width=1,
                 border_color=c["line"]).pack(fill="x", padx=18)
    output_actions = ctk.CTkFrame(controls_col, fg_color="transparent")
    output_actions.pack(fill="x", padx=14, pady=(8, 0))
    output_actions.grid_columnconfigure((0, 1), weight=1, uniform="output")
    ctk.CTkButton(output_actions, text="浏览", image=icons["browse"], height=36,
                  corner_radius=12, fg_color=c["field"], text_color=c["text"],
                  border_width=0, hover_color=c["accent_soft"], command=browse_output).grid(
                      row=0, column=0, sticky="ew", padx=4)
    ctk.CTkButton(output_actions, text="打开目录", image=icons["folder_open"], height=36,
                  corner_radius=12, fg_color=c["field"], text_color=c["text"],
                  border_width=0, hover_color=c["accent_soft"], command=open_output).grid(
                      row=0, column=1, sticky="ew", padx=4)
    ctk.CTkSwitch(
        controls_col, text="输出后自动匹配 LRC 歌词", variable=lrc_enabled_var,
        command=toggle_lrc_copy, height=30, switch_width=38, switch_height=20,
        corner_radius=10, border_width=0, fg_color=c["line"],
        progress_color=c["accent"], button_color="#ffffff",
        button_hover_color="#ffffff", text_color=c["text"],
        font=("Microsoft YaHei UI", 9)).pack(fill="x", padx=18, pady=(12, 8))

    action_area = ctk.CTkFrame(controls_col, fg_color="transparent")
    action_area.pack(side="bottom", fill="x", padx=14, pady=14)
    start_btn = ctk.CTkButton(
        action_area, text="开始转换", image=icons["play"], height=48,
        corner_radius=15, fg_color=c["accent"], hover_color="#2468de",
        text_color="#ffffff", compound="left",
        command=lambda: start_convert(root, pending, start_btn, cur_lbl,
                                      bar_cur, bar_total, log_text))
    start_btn.pack(fill="x", padx=4, pady=(0, 8))
    secondary_actions = ctk.CTkFrame(action_area, fg_color="transparent")
    secondary_actions.pack(fill="x")
    secondary_actions.grid_columnconfigure((0, 1), weight=1, uniform="secondary")
    pause_btn = ctk.CTkButton(
        secondary_actions, text="暂停", image=icons["pause"], height=38,
        corner_radius=12, fg_color=c["field"], text_color=c["text"],
        border_width=0, hover_color=c["accent_soft"], command=toggle_pause,
        state="disabled")
    pause_btn.grid(row=0, column=0, sticky="ew", padx=4)
    ctk.CTkButton(
        secondary_actions, text="重置", image=icons["reset"], height=38,
        corner_radius=12, fg_color=c["danger"], text_color="#a43e4b",
        hover_color="#ffe1e4", command=reset_all).grid(
            row=0, column=1, sticky="ew", padx=4)

    results_col = ctk.CTkFrame(right, corner_radius=20, fg_color=c["panel"],
                               border_width=1, border_color=c["line"])
    results_col.grid(row=0, column=1, sticky="nsew")
    results_col.grid_rowconfigure(2, weight=1)
    results_col.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(results_col, text="结果中心", text_color=c["text"],
                 font=("Microsoft YaHei UI", 16, "bold"), anchor="w").grid(
                     row=0, column=0, sticky="ew", padx=18, pady=(18, 8))
    activity_var = tk.StringVar(value="已完成")

    result_stack = ctk.CTkFrame(results_col, fg_color="transparent")
    result_stack.grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 12))
    result_stack.grid_rowconfigure(0, weight=1)
    result_stack.grid_columnconfigure(0, weight=1)
    completed_panel = ctk.CTkFrame(result_stack, corner_radius=14,
                                   fg_color=c["field"], border_width=0)
    error_panel = ctk.CTkFrame(result_stack, corner_radius=14,
                               fg_color=c["field"], border_width=0)
    for panel in (completed_panel, error_panel):
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)
    completed = ttk.Treeview(completed_panel, columns=("name",), show="tree",
                             selectmode="browse", style="Rounded.Treeview")
    completed.column("#0", width=0, stretch=False)
    completed.column("name", width=240, anchor="w")
    completed.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
    completed.bind("<Double-1>", open_completed)
    errors = ttk.Treeview(error_panel, columns=("file",), show="headings",
                          selectmode="browse", style="Rounded.Treeview")
    errors.heading("file", text="点击文件名复制错误详情")
    errors.column("file", width=240, anchor="w")
    errors.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
    errors.bind("<ButtonRelease-1>", copy_error)

    def show_activity(value):
        if value == "错误日志":
            error_panel.tkraise()
        else:
            completed_panel.tkraise()

    activity_tabs = ctk.CTkSegmentedButton(
        results_col, values=["已完成", "错误日志"], variable=activity_var,
        command=show_activity, height=36, corner_radius=12,
        fg_color=c["field"], selected_color=c["accent_soft"],
        selected_hover_color=c["accent_soft"], unselected_color=c["field"],
        unselected_hover_color="#e9edf4", text_color=c["text"],
        font=("Microsoft YaHei UI", 9, "bold"))
    activity_tabs.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 4))
    activity_tabs.set("已完成")
    completed_panel.tkraise()

    center_min_width = round(390 * APP_DPI_SCALE)
    right_min_width = round(540 * APP_DPI_SCALE)
    right_width = round(620 * APP_DPI_SCALE)
    workspace.add(queue_shell, minsize=center_min_width, stretch="always")
    workspace.add(right, minsize=right_min_width, width=right_width, stretch="never")

    # Clamp the sash after both window resize and user drag.  PanedWindow's
    # minsize normally enforces this, while this guard also covers DPI changes
    # and maximized/restored transitions where Tk can briefly report stale sizes.
    workspace_resize_job = {"id": None}

    def stabilize_workspace(_event=None):
        if workspace_resize_job["id"] is not None:
            try:
                root.after_cancel(workspace_resize_job["id"])
            except Exception:
                pass

        def apply_stable_layout():
            workspace_resize_job["id"] = None
            if not workspace.winfo_exists() or len(workspace.panes()) < 2:
                return
            total = workspace.winfo_width()
            if total <= center_min_width + right_min_width:
                return
            sash_x = workspace.sash_coord(0)[0]
            sash_w = int(workspace.cget("sashwidth"))
            low = center_min_width
            high = max(low, total - right_min_width - sash_w)
            target = max(low, min(high, sash_x))
            if abs(target - sash_x) > 1:
                workspace.sash_place(0, target, 0)
            # A single final invalidation clears any pixels exposed by the sash.
            workspace.update_idletasks()
            right.configure(fg_color=c["panel"])

        workspace_resize_job["id"] = root.after(45, apply_stable_layout)

    workspace.bind("<Configure>", stabilize_workspace, add="+")
    workspace.bind("<ButtonRelease-1>", stabilize_workspace, add="+")
    root.after(120, stabilize_workspace)

    # Bottom status rail: compact, calm and readable during long batches.
    bottom = ttk.Frame(root, padding=(20, 3, 20, 12))
    bottom.grid(row=2, column=0, sticky="ew")
    bottom.grid_columnconfigure(0, weight=1)
    cur_lbl = ttk.Label(bottom, text="就绪", anchor="w")
    cur_lbl.grid(row=0, column=0, sticky="ew")
    status_label = ttk.Label(bottom, textvariable=status_var, foreground=c["muted"])
    status_label.grid(row=0, column=1, sticky="e")
    bar_cur = RoundedProgress(bottom, c)
    bar_cur.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 4))
    bar_total = RoundedProgress(bottom, c)
    bar_total.grid(row=2, column=0, columnspan=2, sticky="ew")
    log_text = tk.Text(root, height=1, state="disabled")

    gui = {"tree": pending, "completed_tree": completed, "errors_tree": errors,
           "completed_paths": completed_paths, "completed_records": completed_records,
           "error_details": error_details,
           "out_dir": out_dir_var, "fmt": fmt_var, "br": br_var, "sr": sr_var, "ch": ch_var,
           "parallel": parallel_var,
           "lrc_enabled": lrc_enabled_var, "copy_lrc": copy_lrc_for_completed,
           "start_btn": start_btn, "pause_btn": pause_btn, "cancel_btn": pause_btn,
           "cur_lbl": cur_lbl, "bar_cur": bar_cur, "bar_total": bar_total,
           "log": log_text, "status": status_var, "queue_count": queue_count,
           "workspace": workspace, "right_panel": right,
           "empty_state": empty_state,
           "activity_tabs": activity_tabs, "activity_var": activity_var,
           "show_activity": show_activity,
           "reset_action": reset_all, "dedupe_action": deduplicate_queue,
           "lrc_toggle_action": toggle_lrc_copy}
    root._gui = gui

    # tkinterdnd2 provides native Explorer file/folder drops when available.
    try:
        from tkinterdnd2 import DND_FILES
        for target in dict.fromkeys((pending.left, pending.right,
                                     pending.left_card, pending.right_card,
                                     empty_state)):
            target.drop_target_register(DND_FILES)
            target.dnd_bind("<<Drop>>", handle_drop)
    except Exception:
        pass
    root.after(100, lambda: poll_ui(root, gui))
    return gui


# Use the redesigned workspace while keeping the original implementation as a
# fallback reference during maintenance.
build_gui = build_gui_v2


def _add_file(gui, path):
    if worker.running:
        return False
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    if ext == NCM_EXT or ext in KUGOU_EXTS or ext in QMC_EXTS:
        pass
    elif ext not in AUDIO_EXTS:
        return False
    try:
        path = os.path.abspath(path)
        path_key = os.path.normcase(os.path.realpath(path))
        size = os.path.getsize(path)
    except OSError:
        return False
    type_label = ("NCM" if ext == NCM_EXT
                  else "KGM" if ext in KUGOU_EXTS
                  else "QMC" if ext in QMC_EXTS
                  else ext.lstrip(".").upper())
    item = gui["tree"].insert("", "end", values=(
        (name, "排队中") if "completed_tree" in gui
        else (name, type_label, human_size(size), "排队中")))
    worker.files.append({"path": path, "name": name, "ext": ext,
                         "size": size, "item": item, "path_key": path_key,
                         "normalized_name": _normalized_file_stem(name)})
    worker.path_keys.add(path_key)
    if "queue_count" in gui:
        gui["queue_count"].set(f"待转换 {len(worker.files)} 个文件")
    return True


def _scan_folder(gui, folder):
    """Recursively discover files off the Tk thread; poll_ui imports batches."""
    if worker.running:
        return
    generation = worker.import_generation
    worker.scanning += 1

    def scan():
        found = 0
        try:
            for dirpath, dirs, names in os.walk(folder):
                if worker.running or generation != worker.import_generation:
                    break
                dirs.sort(key=str.casefold)
                for name in sorted(names, key=str.casefold):
                    if generation != worker.import_generation:
                        break
                    ext = os.path.splitext(name)[1].lower()
                    if (ext in AUDIO_EXTS or ext == NCM_EXT or ext in KUGOU_EXTS or
                            ext in QMC_EXTS):
                        worker.import_queue.append((generation, os.path.join(dirpath, name)))
                        found += 1
        except OSError as exc:
            if generation == worker.import_generation:
                worker.log.append(("error", f"扫描文件夹失败: {exc}"))
        finally:
            worker.scan_done.append((generation, folder, found))

    threading.Thread(target=scan, name="audio-folder-scan", daemon=True).start()


def log(gui, kind, text):
    import tkinter as tk
    tag = {"info": "log_info", "success": "log_ok", "error": "log_err"}.get(kind, "log_info")
    ts = time.strftime("%H:%M:%S")
    gui["log"].configure(state="normal")
    gui["log"].insert("end", f"[{ts}] {text}\n", tag)
    gui["log"].configure(state="disabled")
    gui["log"].see("end")


# --------------------------------------------------------------------------
# 转换流程
# --------------------------------------------------------------------------

def start_convert(root, tree, start_btn, cur_lbl, bar_cur, bar_total, log_text):
    gui = root._gui
    if worker.running:
        return
    if worker.scanning or worker.import_queue:
        log(gui, "info", "文件夹仍在扫描，请等待扫描完成后再开始转换")
        return
    if not worker.files:
        log(gui, "error", "请先添加文件")
        return

    out_dir = gui["out_dir"].get().strip()
    if not out_dir:
        log(gui, "error", "请先选择输出目录")
        return
    os.makedirs(out_dir, exist_ok=True)

    def val(opts, name):
        for label, v in opts:
            if label == name:
                return v
        return 0

    out_fmt = gui["fmt"].get()
    bitrate = val(BITRATE_OPTS, gui["br"].get())
    sr = val(SR_OPTS, gui["sr"].get())
    ch = val(CH_OPTS, gui["ch"].get())
    try:
        parallelism = int(gui.get("parallel").get().rstrip("xX"))
    except (AttributeError, TypeError, ValueError):
        parallelism = 2
    parallelism = max(2, min(16, parallelism))

    worker.cancel.clear()
    worker.pause.clear()
    worker.running = True
    worker.done = 0
    worker.errors = 0
    worker.total = len(worker.files)
    worker.max_workers = parallelism
    with worker.state_lock:
        worker.active_tasks.clear()
    worker.log.clear()
    worker.status.clear()
    worker.completed.clear()
    worker.failures.clear()
    gui["start_btn"].configure(state="disabled")
    gui["cancel_btn"].configure(state="normal")
    log(gui, "info", f"开始转换 {worker.total} 个文件 → {out_fmt.upper()} · {parallelism}x 同步 → {out_dir}")

    t = threading.Thread(target=_run_batch, args=(
        root, gui, out_dir, out_fmt, bitrate, sr, ch, parallelism), daemon=True)
    t.start()


def _run_batch(root, gui, out_dir, out_fmt, bitrate, sr, ch, parallelism=2):
    """Run a bounded pool of independent conversions with shared UI state."""
    try:
        items = list(worker.files)
        reserved_paths = set()
        jobs = []
        out_ext = converter.EXT[out_fmt]
        for item in items:
            base = os.path.splitext(os.path.basename(item["path"]))[0]
            output_path = unique_path(
                os.path.join(out_dir, f"{base}.{out_ext}"), reserved_paths)
            jobs.append((item, output_path))

        def run_item(item, output_path):
            if worker.cancel.is_set():
                return
            item_id = item["item"]
            temp_dir = tempfile.mkdtemp(prefix="miaomiao_task_")
            with worker.state_lock:
                worker.active_tasks[item_id] = {"name": item["name"], "pct": 0.0}

            def pct(p):
                while worker.pause.is_set() and not worker.cancel.is_set():
                    time.sleep(0.08)
                with worker.state_lock:
                    task = worker.active_tasks.get(item_id)
                    if task is not None:
                        task["pct"] = float(p)

            try:
                try:
                    ok, msg = process_one(
                        item, temp_dir, out_dir, out_fmt, bitrate, sr, ch,
                        pct, worker.cancel, output_path=output_path)
                except Exception as exc:
                    # One malformed source must never terminate sibling tasks.
                    ok = False
                    msg = f"{type(exc).__name__}: {exc}"
                with worker.state_lock:
                    worker.done += 1
                    worker.active_tasks.pop(item_id, None)
                    if worker.cancel.is_set() and not ok:
                        worker.status[item_id] = "已取消"
                    elif ok:
                        worker.status[item_id] = "✓ 完成"
                        worker.completed.append((item_id, msg, item["name"], item["path"]))
                        worker.log.append(("success", f"完成: {item['name']} → {os.path.basename(msg)}"))
                    else:
                        worker.errors += 1
                        worker.status[item_id] = "✗ 失败"
                        worker.failures.append((item_id, item["name"], str(msg)))
                        worker.log.append(("error", f"失败: {item['name']} — {msg}"))
            finally:
                with worker.state_lock:
                    worker.active_tasks.pop(item_id, None)
                shutil.rmtree(temp_dir, ignore_errors=True)

        pool_size = max(1, min(parallelism, len(jobs)))
        with ThreadPoolExecutor(max_workers=pool_size,
                                thread_name_prefix="audio-convert") as executor:
            futures = [executor.submit(run_item, item, output_path)
                       for item, output_path in jobs]
            for future in as_completed(futures):
                # run_item contains per-file exception isolation; result() still
                # surfaces programming errors to the batch log without hanging.
                try:
                    future.result()
                except Exception as exc:
                    worker.log.append(("error", f"并发任务异常: {type(exc).__name__}: {exc}"))
    finally:
        worker.current = ""
        worker.current_pct = 0.0
        with worker.state_lock:
            worker.active_tasks.clear()
        if worker.cancel.is_set():
            worker.log.append(("info", f"已取消（完成 {worker.done}/{worker.total}）"))
        elif worker.errors:
            worker.log.append(("info", f"全部结束：成功 {worker.done - worker.errors}，失败 {worker.errors}"))
        else:
            worker.log.append(("info", f"全部完成：共 {worker.done} 个文件 ✓"))
        # Publish the idle state last so a pending full reset cannot clear the
        # queues before this worker has finished its final status writes.
        worker.running = False


def poll_ui(root, gui):
    """每 100ms 从工作线程读取状态并渲染到界面。"""
    import tkinter as tk
    try:
        # Folder discovery runs in a background thread.  Limit work per tick so
        # a folder containing thousands of tracks never monopolises Tk.
        imported = 0
        while worker.import_queue and imported < 120 and not worker.running:
            generation, import_path = worker.import_queue.popleft()
            if generation != worker.import_generation:
                continue
            _add_file(gui, import_path)
            imported += 1
        while worker.scan_done:
            generation, folder, found = worker.scan_done.popleft()
            if generation != worker.import_generation:
                continue
            worker.scanning = max(0, worker.scanning - 1)
            worker.log.append(("info", f"文件夹扫描完成：发现 {found} 个支持的文件 — {folder}"))
        if worker.scanning and not worker.running:
            gui["status"].set(f"正在扫描文件夹… 已导入 {len(worker.files)} 个")

        while worker.log:
            kind, text = worker.log.popleft()
            log(gui, kind, text)
        if worker.running:
            while worker.completed:
                source_iid, output_path, source_name, source_path = worker.completed.popleft()
                if gui["tree"].exists(source_iid):
                    gui["tree"].delete(source_iid)
                removed = [item for item in worker.files if item["item"] == source_iid]
                worker.files = [item for item in worker.files if item["item"] != source_iid]
                worker.path_keys.difference_update(item["path_key"] for item in removed)
                if "completed_tree" in gui:
                    target_iid = gui["completed_tree"].insert("", "end", values=(os.path.basename(output_path),))
                    gui["completed_paths"][target_iid] = output_path
                    if "completed_records" in gui:
                        gui["completed_records"][target_iid] = (source_path, output_path)
                    if "copy_lrc" in gui:
                        gui["copy_lrc"](source_path, output_path)
            while worker.failures:
                source_iid, source_name, detail = worker.failures.popleft()
                if gui["tree"].exists(source_iid):
                    gui["tree"].delete(source_iid)
                removed = [item for item in worker.files if item["item"] == source_iid]
                worker.files = [item for item in worker.files if item["item"] != source_iid]
                worker.path_keys.difference_update(item["path_key"] for item in removed)
                if "errors_tree" in gui:
                    error_iid = gui["errors_tree"].insert("", "end", values=(source_name,))
                    gui["error_details"][error_iid] = f"文件: {source_name}\n错误: {detail}"
            if "queue_count" in gui:
                gui["queue_count"].set(f"待转换 {len(worker.files)} 个文件" if worker.files else "等待添加文件")
            if "pause_btn" in gui:
                gui["pause_btn"].configure(state="normal")
            with worker.state_lock:
                active = [dict(task) for task in worker.active_tasks.values()]
                done, total, max_workers = worker.done, worker.total, worker.max_workers
            active_count = len(active)
            names = "、".join(task["name"] for task in active[:2])
            if active_count > 2:
                names += f" 等 {active_count} 个"
            avg_pct = (sum(task["pct"] for task in active) / active_count
                       if active_count else 0.0)
            total_pct = min(100.0, (done + sum(task["pct"] for task in active) / 100.0)
                            * 100.0 / max(1, total))
            worker.current = names
            worker.current_pct = avg_pct
            gui["cur_lbl"].configure(
                text=(f"同步处理中: {names}  ({active_count}/{max_workers})"
                      if names else f"正在调度任务…  ({done}/{total})"))
            gui["bar_cur"].configure(value=avg_pct)
            gui["bar_total"].configure(value=total_pct)
            gui["status"].set(
                f"{max_workers}x 同步转换中 · 活动 {active_count} · 完成 {done}/{total}")
        else:
            while worker.completed:
                source_iid, output_path, source_name, source_path = worker.completed.popleft()
                if gui["tree"].exists(source_iid):
                    gui["tree"].delete(source_iid)
                removed = [item for item in worker.files if item["item"] == source_iid]
                worker.files = [item for item in worker.files if item["item"] != source_iid]
                worker.path_keys.difference_update(item["path_key"] for item in removed)
                if "completed_tree" in gui:
                    target_iid = gui["completed_tree"].insert("", "end", values=(os.path.basename(output_path),))
                    gui["completed_paths"][target_iid] = output_path
                    if "completed_records" in gui:
                        gui["completed_records"][target_iid] = (source_path, output_path)
                    if "copy_lrc" in gui:
                        gui["copy_lrc"](source_path, output_path)
            while worker.failures:
                source_iid, source_name, detail = worker.failures.popleft()
                if gui["tree"].exists(source_iid):
                    gui["tree"].delete(source_iid)
                removed = [item for item in worker.files if item["item"] == source_iid]
                worker.files = [item for item in worker.files if item["item"] != source_iid]
                worker.path_keys.difference_update(item["path_key"] for item in removed)
                if "errors_tree" in gui:
                    error_iid = gui["errors_tree"].insert("", "end", values=(source_name,))
                    gui["error_details"][error_iid] = f"文件: {source_name}\n错误: {detail}"
            if "queue_count" in gui:
                gui["queue_count"].set(f"待转换 {len(worker.files)} 个文件" if worker.files else "等待添加文件")
            for iid, text in worker.status.items():
                if not gui["tree"].exists(iid):
                    continue
                item = gui["tree"].item(iid)
                vals = list(item["values"])
                vals[-1] = text
                gui["tree"].item(iid, values=vals)
            worker.status.clear()
            if not worker.current:
                gui["cur_lbl"].configure(text="就绪")
                gui["bar_cur"].configure(value=0)
            if not worker.running:
                gui["start_btn"].configure(state="normal")
                gui["cancel_btn"].configure(state="disabled")
                if "pause_btn" in gui:
                    gui["pause_btn"].configure(text="暂停", state="disabled")
                if worker.done:
                    gui["bar_total"].configure(value=worker.done * 100.0 / max(1, worker.total))
        if "empty_state" in gui:
            empty_state = gui["empty_state"]
            if worker.files:
                if empty_state.winfo_ismapped():
                    empty_state.place_forget()
            elif not empty_state.winfo_ismapped():
                empty_state.place(relx=0.5, rely=0.47, anchor="center")
                empty_state.lift()
    except tk.TclError:
        pass
    root.after(100, lambda: poll_ui(root, gui))


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def main():
    enable_high_dpi_awareness()
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        import tkinter as tk
        root = tk.Tk()
    configure_tk_for_dpi(root)
    build_gui(root)
    root.mainloop()


if __name__ == "__main__":
    if sys.platform == "win32":
        # 避免控制台闪退窗口
        pass
    main()
