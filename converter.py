# -*- coding: utf-8 -*-
"""
FFmpeg 音频转换封装。
- 自动定位 ffmpeg/ffprobe（优先项目目录，其次系统 PATH）
- 支持 mp3 / flac / wav / aac / ogg / m4a / opus 输出
- 支持从源文件提取并继承封面（mp3 / flac / m4a）
- 通过 -progress 管道解析实时进度，支持中途取消
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time


_ACTIVE_PROCESSES = set()
_ACTIVE_PROCESS_LOCK = threading.Lock()


def _background_process_kwargs():
    """Prevent FFmpeg/FFprobe console windows from flashing on Windows."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def pause_active_process(paused=True):
    """Pause/resume the active FFmpeg process on Windows.

    Decryption stages are paused cooperatively by the UI progress callback;
    this function handles the native FFmpeg process itself.
    """
    if sys.platform != "win32":
        return False
    with _ACTIVE_PROCESS_LOCK:
        processes = list(_ACTIVE_PROCESSES)
    changed = False
    try:
        import ctypes
        ntdll = ctypes.WinDLL("ntdll")
        fn = ntdll.NtSuspendProcess if paused else ntdll.NtResumeProcess
        for proc in processes:
            if proc.poll() is None:
                changed = (fn(int(proc._handle)) == 0) or changed
    except Exception:
        return False
    return changed


def _app_base():
    """程序根目录：打包后为 exe 所在目录，开发时为脚本目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


_FFMPEG = os.path.join(_app_base(), "ffmpeg", "ffmpeg.exe")
_FFPROBE = os.path.join(_app_base(), "ffmpeg", "ffprobe.exe")

# 输出格式 -> (音频编码器, 是否无损)
CODECS = {
    "mp3": ("libmp3lame", False),
    "flac": ("flac", True),
    "wav": ("pcm_s16le", True),
    "aac": ("aac", False),
    "ogg": ("libvorbis", False),
    "m4a": ("aac", False),
    "opus": ("libopus", False),
}
EXT = {f: f for f in CODECS}
EXT["m4a"] = "m4a"


class ConvertError(Exception):
    """转换失败。"""


def find_binaries():
    """返回可用的 (ffmpeg, ffprobe) 路径。"""
    ffmpeg = _FFMPEG if os.path.isfile(_FFMPEG) else shutil.which("ffmpeg")
    ffprobe = _FFPROBE if os.path.isfile(_FFPROBE) else shutil.which("ffprobe")
    return ffmpeg, ffprobe


def probe_info(path):
    """用 ffprobe 获取文件信息，返回 dict（失败返回 None）。"""
    _, ffprobe = find_binaries()
    if not ffprobe:
        return None
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "format=duration,format_name", "-of", "json", path],
            capture_output=True, timeout=30, **_background_process_kwargs())
        data = json.loads(r.stdout.decode("utf-8", "ignore") or "{}")
        fmt = data.get("format", {})
        return {
            "duration": float(fmt.get("duration") or 0),
            "format_name": fmt.get("format_name", ""),
        }
    except Exception:
        return None


def extract_cover(input_path, temp_dir):
    """提取源文件中明确标记为 attached_pic 的封面，返回临时 PNG 路径。"""
    ffmpeg, ffprobe = find_binaries()
    if not ffmpeg or not ffprobe or not os.path.isfile(input_path):
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=index:stream_disposition=attached_pic",
             "-of", "json", input_path], capture_output=True, timeout=20,
            **_background_process_kwargs())
        streams = json.loads(result.stdout.decode("utf-8", "ignore") or "{}").get("streams", [])
        stream = next((s for s in streams
                       if int(s.get("disposition", {}).get("attached_pic", 0)) == 1), None)
        if not stream:
            return None
        os.makedirs(temp_dir, exist_ok=True)
        cover_path = os.path.join(temp_dir, f"source_cover_{time.time_ns()}.png")
        extracted = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", input_path,
             "-map", f"0:{int(stream['index'])}", "-frames:v", "1", "-c:v", "png",
             cover_path], capture_output=True, timeout=30,
            **_background_process_kwargs())
        if (extracted.returncode == 0 and os.path.isfile(cover_path)
                and os.path.getsize(cover_path)):
            return cover_path
        try:
            os.remove(cover_path)
        except FileNotFoundError:
            pass
    except (OSError, ValueError, KeyError, json.JSONDecodeError,
            subprocess.SubprocessError):
        pass
    return None


def _cover_args(cover_path, out_fmt):
    """构造封面嵌入参数；返回参数列表或 None。"""
    if not cover_path or not os.path.isfile(cover_path):
        return None
    if out_fmt == "flac":
        return ["-map", "0:a:0", "-map", "1:v:0", "-c:v", "copy",
                "-disposition:v", "attached_pic"]
    if out_fmt == "mp3":
        return ["-map", "0:a:0", "-map", "1:v:0", "-c:v", "mjpeg",
                "-disposition:v", "attached_pic", "-id3v2_version", "3",
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)"]
    if out_fmt == "m4a":
        return ["-map", "0:a:0", "-map", "1:v:0", "-c:v", "mjpeg",
                "-disposition:v", "attached_pic"]
    return None


def _do_convert(input_path, output_path, out_fmt="mp3", bitrate=0,
                sample_rate=0, channels=0, cover_path=None,
                copy_stream=False, progress_cb=None, cancel_event=None):
    """转换一个音频文件。

    参数:
        input_path:   输入文件
        output_path:  输出文件（含扩展名）
        out_fmt:      mp3/flac/wav/aac/ogg/m4a/opus
        bitrate:      码率 kbps；0 = 使用默认；-1 = VBR 高质量（无损格式忽略）
        sample_rate:  采样率 Hz；0 = 保持原样
        channels:     声道数；0 = 保持原样
        cover_path:   可选封面图
        copy_stream:  流复制模式（源格式==目标格式时免二次转码，保留音质）
        progress_cb:  callable(percent) 进度回调（0~100 浮点）
        cancel_event: threading.Event；设置后停止转换
    """
    ffmpeg, _ = find_binaries()
    if not ffmpeg:
        raise ConvertError("未找到 ffmpeg.exe")

    if out_fmt not in CODECS:
        raise ConvertError(f"不支持的输出格式: {out_fmt}")
    codec, lossless = CODECS[out_fmt]

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    cmd += ["-i", input_path]
    if cover_path and os.path.isfile(cover_path) and out_fmt in {"mp3", "flac", "m4a"}:
        cmd += ["-i", cover_path]
    cmd += ["-progress", "pipe:1", "-nostdin"]

    extra = []
    if not copy_stream:
        if not lossless and bitrate == -1:
            extra += ["-q:a", "2"]            # VBR 高质量档
        elif not lossless and bitrate and bitrate > 0:
            extra += ["-b:a", f"{bitrate}k"]
        if sample_rate and sample_rate > 0:
            extra += ["-ar", str(sample_rate)]
        if channels and channels > 0:
            extra += ["-ac", str(channels)]

    cover = _cover_args(cover_path, out_fmt)
    if cover:
        cmd += cover
    else:
        cmd += ["-map", "0:a:0"]
    cmd += ["-map_metadata", "0", "-c:a", "copy" if copy_stream else codec] + extra + [output_path]

    total_ms = 0
    info = probe_info(input_path)
    if info and info["duration"] > 0:
        total_ms = int(info["duration"] * 1000)

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, **_background_process_kwargs())
    last_pct = -1
    ok = True
    for line in proc.stdout:
        line = line.decode("utf-8", "ignore").strip()
        if line.startswith("out_time_ms="):
            try:
                t = int(line.split("=", 1)[1])
                if total_ms > 0 and progress_cb:
                    pct = min(99.0, t / total_ms * 100.0)
                    if pct - last_pct >= 0.5 or pct >= 99.0:
                        last_pct = pct
                        progress_cb(pct)
            except ValueError:
                pass
        if cancel_event and cancel_event.is_set():
            proc.terminate()
            ok = False
            break
    proc.stdout.close()
    err = proc.stderr.read().decode("utf-8", "ignore")
    proc.stderr.close()
    proc.wait()
    if ok and proc.returncode != 0:
        raise ConvertError(f"ffmpeg 返回错误码 {proc.returncode}:\n{err.strip()[:400]}")
    if not ok:
        return False  # 已取消
    if progress_cb:
        progress_cb(100.0)
    return True


def _safe_do_convert(input_path, output_path, out_fmt="mp3", bitrate=0,
                     sample_rate=0, channels=0, cover_path=None,
                     copy_stream=False, progress_cb=None, cancel_event=None):
    """Deadlock-safe FFmpeg runner which atomically publishes finished files."""
    if cancel_event and cancel_event.is_set():
        return False
    ffmpeg, _ = find_binaries()
    if not ffmpeg:
        raise ConvertError("ffmpeg.exe was not found")
    if out_fmt not in CODECS:
        raise ConvertError(f"Unsupported output format: {out_fmt}")
    codec, lossless = CODECS[out_fmt]
    output_root, output_ext = os.path.splitext(output_path)
    part_path = output_root + ".part" + output_ext
    try:
        os.remove(part_path)
    except FileNotFoundError:
        pass
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", input_path]
    if cover_path and os.path.isfile(cover_path) and out_fmt in {"mp3", "flac", "m4a"}:
        cmd += ["-i", cover_path]
    cmd += ["-progress", "pipe:1", "-nostdin"]
    extra = []
    if not copy_stream:
        if not lossless and bitrate == -1:
            extra += ["-q:a", "2"]
        elif not lossless and bitrate > 0:
            extra += ["-b:a", f"{bitrate}k"]
        if sample_rate > 0:
            extra += ["-ar", str(sample_rate)]
        if channels > 0:
            extra += ["-ac", str(channels)]
    cmd += _cover_args(cover_path, out_fmt) or ["-map", "0:a:0"]
    cmd += ["-map_metadata", "0", "-c:a", "copy" if copy_stream else codec] + extra + [part_path]
    info = probe_info(input_path)
    total_ms = int(info["duration"] * 1000) if info and info["duration"] > 0 else 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, **_background_process_kwargs())
    with _ACTIVE_PROCESS_LOCK:
        _ACTIVE_PROCESSES.add(proc)
    stderr_chunks = []
    def drain_stderr():
        try:
            for chunk in iter(lambda: proc.stderr.read(8192), b""):
                stderr_chunks.append(chunk)
        except (OSError, ValueError):
            pass
    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()
    last_pct, cancelled = -1, False
    for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", "ignore").strip()
        if line.startswith("out_time_ms=") and total_ms and progress_cb:
            try:
                pct = min(99.0, int(line.split("=", 1)[1]) / total_ms * 100.0)
                if pct - last_pct >= 0.5 or pct >= 99.0:
                    last_pct = pct
                    progress_cb(pct)
            except ValueError:
                pass
        if cancel_event and cancel_event.is_set():
            cancelled = True
            proc.terminate()
            break
    proc.stdout.close()
    try:
        proc.wait(timeout=5 if cancelled else 30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        cancelled = True
    finally:
        with _ACTIVE_PROCESS_LOCK:
            _ACTIVE_PROCESSES.discard(proc)
    stderr_thread.join(timeout=2)
    proc.stderr.close()
    err = b"".join(stderr_chunks).decode("utf-8", "ignore")
    if cancelled:
        try: os.remove(part_path)
        except FileNotFoundError: pass
        return False
    if proc.returncode != 0:
        try: os.remove(part_path)
        except FileNotFoundError: pass
        raise ConvertError(f"ffmpeg exited with code {proc.returncode}:\n{err.strip()[:400]}")
    os.replace(part_path, output_path)
    if progress_cb:
        progress_cb(100.0)
    return True


def convert(input_path, output_path, out_fmt="mp3", bitrate=0,
            sample_rate=0, channels=0, cover_path=None,
            copy_stream=False, progress_cb=None, cancel_event=None):
    """转换一个音频文件（封面损坏时自动去掉封面重试，保证转换完成）。

    参数同 _do_convert。
    """
    try:
        return _safe_do_convert(input_path, output_path, out_fmt, bitrate, sample_rate,
                                channels, cover_path, copy_stream, progress_cb, cancel_event)
    except ConvertError:
        if cover_path and os.path.isfile(cover_path):
            # 封面无效/损坏导致失败时，去掉封面重试一次，音频转换仍能完成
            return _safe_do_convert(input_path, output_path, out_fmt, bitrate, sample_rate,
                                    channels, None, copy_stream, progress_cb, cancel_event)
        raise
