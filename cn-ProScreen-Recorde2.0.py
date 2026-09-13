# -*- coding: utf-8 -*-
import sys
import os
import subprocess
import importlib
import platform
import logging
import threading
import traceback
import datetime
import time
import queue
import json
import re
import wave

APP_NAME = "ProScreenRecorder"
APP_VERSION = "2.0.0"
LOG_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
LOG_FILE = os.path.join(LOG_DIR, "app.log")

REQUIRED_DEPS = [
    ("cv2",         "opencv-python", None),
    ("mss",         "mss",           None),
    ("PIL",         "pillow",        None),
    ("numpy",       "numpy",         None),
    ("sounddevice", "sounddevice",   ">=0.4.6"),
]


def _enable_dpi_awareness():
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _hide_console():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0)
        except Exception:
            pass


def _setup_logging(level=logging.INFO):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass
    handlers = []
    try:
        handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
    except Exception:
        pass
    handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers)
    logging.info("=" * 50)
    logging.info("启动 %s %s", APP_NAME, APP_VERSION)
    logging.info("Python %s on %s", sys.version.split()[0], platform.platform())


def _parse_version(v):
    parts = re.findall(r"\d+", str(v))
    return tuple(int(x) for x in parts[:3]) if parts else (0, 0, 0)


def _check_one(imp_name, req):
    try:
        mod = importlib.import_module(imp_name)
    except ImportError:
        return False, "未安装"
    except Exception as e:
        return False, f"import 失败: {e}"
    if req:
        ver = getattr(mod, "__version__", None)
        if ver is None:
            try:
                ver = mod.version.__version__
            except Exception:
                ver = None
        if ver is not None:
            if _parse_version(ver) < _parse_version(req.lstrip(">=").strip()):
                return False, f"{ver} 需要 {req}"
    return True, ""


def _pip_install(pip_name, upgrade=False):
    cmd = [sys.executable, "-m", "pip", "install", pip_name]
    if upgrade:
        cmd.append("--upgrade")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           creationflags=flags, timeout=900)
        return r.returncode == 0, r.stdout.decode("utf-8", errors="ignore")
    except subprocess.TimeoutExpired:
        return False, "pip 安装超时(15 分钟)"
    except Exception as e:
        return False, str(e)


def _bootstrap_deps(progress_cb=None):
    missing = []
    for imp_name, pip_name, req in REQUIRED_DEPS:
        ok, why = _check_one(imp_name, req)
        if not ok:
            missing.append((imp_name, pip_name, req, why))
    if not missing:
        if progress_cb: progress_cb("done", "依赖齐全")
        return True, "依赖齐全"

    for imp_name, pip_name, req, why in missing:
        if progress_cb:
            progress_cb("install", f"正在安装 {pip_name}  ({why})")
        ok, out = _pip_install(pip_name, upgrade=bool(req))
        if not ok:
            if progress_cb:
                progress_cb("error", f"{pip_name} 安装失败:\n{out[-400:]}")
            return False, f"{pip_name} 安装失败"

    still = []
    for imp_name, pip_name, req in REQUIRED_DEPS:
        ok, why = _check_one(imp_name, req)
        if not ok:
            still.append(f"{pip_name}: {why}")
    if still:
        return False, "仍有依赖未满足:\n" + "\n".join(still)

    if progress_cb:
        progress_cb("done", "依赖安装完成")
    return True, "依赖安装完成"


cv2 = None
mss = None
np = None
Image = None
ImageTk = None
sd = None


def _load_libs():
    global cv2, mss, np, Image, ImageTk, sd
    import cv2 as _cv2;             cv2 = _cv2
    import mss as _mss;             mss = _mss
    import numpy as _np;            np = _np
    from PIL import Image as _Im;   Image = _Im
    from PIL import ImageTk as _ITk; ImageTk = _ITk
    try:
        import sounddevice as _sd;  sd = _sd
    except Exception:
        sd = None
    logging.info("第三方库加载完成")


DEFAULT_FPS = 15
MAX_FPS = 60
QUEUE_SIZE = 12
PREVIEW_MAX_W = 520
PREVIEW_MAX_H = 360
AUDIO_OUT_SR = 44100
AUDIO_BLOCK = 2048


class Capabilities:
    def __init__(self):
        self.cpu_cores = os.cpu_count() or 1
        self.ram_gb = self._get_ram()
        self.ffmpeg_path = self._find_ffmpeg()
        self.has_ffmpeg = self.ffmpeg_path is not None
        self.os = platform.system()
        self.tier = self._classify()
        logging.info("能力: cores=%d ram=%.1fGB ffmpeg=%s tier=%s",
                     self.cpu_cores, self.ram_gb,
                     self.has_ffmpeg, self.tier)

    @staticmethod
    def _get_ram():
        try:
            if sys.platform.startswith("win"):
                import ctypes
                class MSX(ctypes.Structure):
                    _fields_ = [("dwLength", ctypes.c_ulong),
                                ("dwMemoryLoad", ctypes.c_ulong),
                                ("ullTotalPhys", ctypes.c_ulonglong),
                                ("ullAvailPhys", ctypes.c_ulonglong),
                                ("ullTotalPageFile", ctypes.c_ulonglong),
                                ("ullAvailPageFile", ctypes.c_ulonglong),
                                ("ullTotalVirtual", ctypes.c_ulonglong),
                                ("ullAvailVirtual", ctypes.c_ulonglong),
                                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
                st = MSX(); st.dwLength = ctypes.sizeof(st)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
                return st.ullTotalPhys / (1024 ** 3)
            return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / (1024 ** 3)
        except Exception:
            return 8.0

    @staticmethod
    def _find_ffmpeg():
        from shutil import which
        p = which("ffmpeg")
        if p:
            return p
        for d in [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin"),
        ]:
            exe = os.path.join(d, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if os.path.isfile(exe):
                return exe
        return None

    def _classify(self):
        if self.ram_gb < 4 or self.cpu_cores <= 2:
            return "low"
        if self.ram_gb < 8 or self.cpu_cores <= 4:
            return "mid"
        return "high"

    def recommend_fps(self):
        return {"low": 10, "mid": 15, "high": 30}[self.tier]

    def recommend_queue(self):
        return {"low": 4, "mid": 8, "high": QUEUE_SIZE}[self.tier]

    def summary(self):
        ff = "有" if self.has_ffmpeg else "无"
        return (f"{self.cpu_cores}核 / {self.ram_gb:.1f}GB / "
                f"ffmpeg:{ff} / 档位:{self.tier}")


def pick_font(root):
    try:
        import tkinter.font as tkfont
        avail = set(tkfont.families(root))
        for f in ["Microsoft YaHei UI", "Microsoft YaHei",
                  "PingFang SC", "Segoe UI", "Tahoma", "Arial"]:
            if f in avail:
                return f
    except Exception:
        pass
    return "TkDefaultFont"


class Theme:
    DARK = {
        "bg": "#1e1e1e", "fg": "#e0e0e0", "muted": "#9e9e9e",
        "accent": "#4a9eff", "success": "#4caf50", "warning": "#ff9800",
        "danger": "#f44336", "card": "#2a2a2a", "input": "#141414",
        "border": "#3a3a3a",
    }

    def __init__(self, root):
        self.colors = dict(self.DARK)
        family = pick_font(root)
        import tkinter.font as tkfont
        self.fonts = {
            "title":  tkfont.Font(family=family, size=14, weight="bold"),
            "body":   tkfont.Font(family=family, size=9),
            "small":  tkfont.Font(family=family, size=8),
            "button": tkfont.Font(family=family, size=9, weight="bold"),
            "timer":  tkfont.Font(family=family, size=22, weight="bold"),
        }
        logging.info("字体: %s", family)


class TimestampedWriter:
    def __init__(self, path, fps, size):
        self.path = path
        self.fps = float(fps)
        self.interval = 1.0 / self.fps
        self.size = size
        self.out = None
        self.next_pts = 0.0
        self.t0 = None
        self.frames_written = 0
        self.frames_dropped = 0
        self._open()

    def _open(self):
        w, h = self.size
        w = w - (w % 2)
        h = h - (h % 2)
        self.size = (w, h)
        candidates = ["mp4v", "XVID", "MJPG"]
        if self.path.lower().endswith(".avi"):
            candidates = ["XVID", "MJPG", "mp4v"]
        for c in candidates:
            try:
                fourcc = cv2.VideoWriter_fourcc(*c)
                out = cv2.VideoWriter(self.path, fourcc, self.fps, (w, h))
                if out.isOpened():
                    self.out = out
                    logging.info("VideoWriter OK codec=%s path=%s", c, self.path)
                    return
                out.release()
            except Exception:
                logging.exception("VideoWriter 尝试 %s 失败", c)
        self.path = os.path.splitext(self.path)[0] + ".avi"
        self.out = cv2.VideoWriter(
            self.path, cv2.VideoWriter_fourcc(*"XVID"), self.fps, (w, h))
        logging.warning("兜底切到 AVI: %s", self.path)

    def write(self, frame, ts):
        if self.out is None:
            return
        if self.t0 is None:
            self.t0 = ts
        pts = ts - self.t0
        if pts < self.next_pts - self.interval:
            self.frames_dropped += 1
            return
        while self.next_pts <= pts:
            self.out.write(frame)
            self.frames_written += 1
            self.next_pts += self.interval

    def close(self):
        if self.out:
            try:
                self.out.release()
            except Exception:
                logging.exception("VideoWriter release 失败")
            self.out = None
        logging.info("writer 关闭: 写入 %d 帧, 丢弃 %d 帧",
                     self.frames_written, self.frames_dropped)


class EncoderBackend:
    def start(self, path, fps, size, options): raise NotImplementedError
    def write(self, frame, ts): raise NotImplementedError
    def stop(self): raise NotImplementedError


class OpenCvBackend(EncoderBackend):
    def __init__(self):
        self.writer = None

    def start(self, path, fps, size, options):
        self.writer = TimestampedWriter(path, fps, size)

    def write(self, frame, ts):
        if self.writer:
            self.writer.write(frame, ts)

    def stop(self):
        if self.writer:
            self.writer.close()
            return self.writer.path
        return None


class FfmpegBackend(EncoderBackend):
    def __init__(self, ffmpeg_path, options=None):
        self.ffmpeg = ffmpeg_path
        self.proc = None
        self.options = options or {}
        self.path = None
        self.size = None
        self.fps = None
        self.preset = self.options.get("preset", "medium")
        self.crf = self.options.get("crf", 23)
        self.codec = self.options.get("codec", "libx264")

    def start(self, path, fps, size, options=None):
        if options:
            self.options.update(options)
            self.preset = self.options.get("preset", self.preset)
            self.crf = self.options.get("crf", self.crf)
            self.codec = self.options.get("codec", self.codec)
        self.path = path
        self.fps = fps
        w, h = size
        w = w - (w % 2)
        h = h - (h % 2)
        self.size = (w, h)

        cmd = [
            self.ffmpeg, "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{w}x{h}",
            "-r", str(fps),
            "-i", "pipe:0",
            "-an",
            "-c:v", self.codec,
            "-preset", self.preset,
            "-crf", str(self.crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            path,
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=flags)
            logging.info("FfmpegBackend 启动: %s ...", " ".join(cmd[:10]))
        except Exception:
            logging.exception("ffmpeg 启动失败")
            self.proc = None

    def write(self, frame, ts):
        if not self.proc or not self.proc.stdin:
            return
        fh, fw = frame.shape[:2]
        tw, th = self.size
        if (fw, fh) != (tw, th):
            frame = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
        try:
            self.proc.stdin.write(frame.tobytes())
        except Exception:
            logging.exception("ffmpeg 写帧失败")

    def stop(self):
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.wait(timeout=20)
            except Exception:
                logging.exception("ffmpeg 结束异常")
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        return self.path


def pick_encoder(caps, user_pref="auto", options=None):
    options = options or {}
    if user_pref == "opencv":
        return OpenCvBackend()
    if user_pref == "ffmpeg":
        if not caps.ffmpeg_path:
            raise RuntimeError("用户要求 ffmpeg 但未找到 ffmpeg")
        return FfmpegBackend(caps.ffmpeg_path, options)
    if caps.ffmpeg_path:
        if caps.tier == "low":
            options.setdefault("preset", "ultrafast")
            options.setdefault("crf", 26)
        elif caps.tier == "mid":
            options.setdefault("preset", "veryfast")
            options.setdefault("crf", 23)
        else:
            options.setdefault("preset", "medium")
            options.setdefault("crf", 21)
        return FfmpegBackend(caps.ffmpeg_path, options)
    return OpenCvBackend()


def audio_available():
    if sd is None:
        return False, "未安装 sounddevice"
    if _parse_version(getattr(sd, "__version__", "0")) < (0, 4, 6):
        return False, f"sounddevice {sd.__version__} 过旧，需要 >=0.4.6"
    try:
        sd.WasapiSettings(loopback=True)
    except Exception as e:
        return False, f"WASAPI loopback 不可用: {e}"
    return True, ""


def list_audio_sources():
    if sd is None:
        return [], "未安装 sounddevice"
    sources = []
    try:
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                sources.append({
                    "id": i, "name": d["name"], "kind": "mic",
                    "native_sr": int(d.get("default_samplerate", 44100)),
                    "channels": min(2, int(d["max_input_channels"])),
                })
    except Exception:
        logging.exception("枚举输入设备失败")

    ok, why = audio_available()
    if ok:
        try:
            hostapis = sd.query_hostapis()
            for api in hostapis:
                if "WASAPI" in api["name"]:
                    out_id = api.get("default_output_device", -1)
                    if out_id >= 0:
                        dinfo = sd.query_devices(out_id)
                        sources.append({
                            "id": out_id,
                            "name": f"[系统声] {dinfo['name']}",
                            "kind": "system",
                            "native_sr": int(dinfo.get("default_samplerate", 48000)),
                            "channels": min(2, int(dinfo.get("max_output_channels", 2))),
                        })
                    break
        except Exception:
            logging.exception("枚举 loopback 失败")
    else:
        logging.warning("系统声不可用: %s", why)

    if not sources:
        return [], "没有可用音频设备"
    return sources, ""


class AudioRecorder:
    def __init__(self, source="mic", mic_id=None, system_id=None,
                 out_sr=AUDIO_OUT_SR, block=AUDIO_BLOCK):
        self.source = source
        self.mic_id = mic_id
        self.system_id = system_id
        self.out_sr = out_sr
        self.block = block
        self.frames = []
        self.recording = False
        self.paused = False
        self.t0 = None
        self._lock = threading.Lock()
        self.stream_mic = None
        self.stream_sys = None
        self.mic_sr = None
        self.sys_sr = None

    def _device_native_sr(self, device_id, kind):
        try:
            if device_id is None:
                info = sd.query_devices(kind="input" if kind == "mic" else "output")
            else:
                info = sd.query_devices(device_id)
            return int(info.get("default_samplerate", 44100))
        except Exception:
            return 44100

    def _make_cb(self, tag):
        def cb(indata, frames, time_info, status):
            if not self.recording or self.paused:
                return
            ts = time.perf_counter() - self.t0
            with self._lock:
                self.frames.append((tag, indata.copy(), ts))
        return cb

    def start(self):
        if sd is None:
            raise RuntimeError("未安装 sounddevice")
        self.frames = []
        self.recording = True
        self.paused = False
        self.t0 = time.perf_counter()

        if self.source in ("mic", "both"):
            sr = self._device_native_sr(self.mic_id, "mic")
            info = sd.query_devices(self.mic_id) if self.mic_id is not None \
                else sd.query_devices(kind="input")
            ch = min(2, max(1, int(info.get("max_input_channels", 1))))
            self.stream_mic = sd.InputStream(
                samplerate=sr, channels=ch, blocksize=self.block,
                device=self.mic_id, dtype="float32",
                callback=self._make_cb("mic"))
            self.stream_mic.start()
            self.mic_sr = sr

        if self.source in ("system", "both"):
            if self.system_id is None:
                raise RuntimeError("未指定系统声设备")
            sr = self._device_native_sr(self.system_id, "system")
            extra = sd.WasapiSettings(loopback=True)
            self.stream_sys = sd.InputStream(
                samplerate=sr, channels=2, blocksize=self.block,
                device=self.system_id,
                extra_settings=extra, dtype="float32",
                callback=self._make_cb("sys"))
            self.stream_sys.start()
            self.sys_sr = sr

    def pause(self):  self.paused = True
    def resume(self): self.paused = False

    def stop(self):
        self.recording = False
        for s in (self.stream_mic, self.stream_sys):
            try:
                if s:
                    s.stop(); s.close()
            except Exception:
                logging.exception("音频流关闭失败")
        self.stream_mic = self.stream_sys = None
        with self._lock:
            frames = list(self.frames)
        if not frames:
            return None
        return self._rebuild(frames)

    def _rebuild(self, frames):
        sr_map = {}
        if self.mic_sr: sr_map["mic"] = self.mic_sr
        if self.sys_sr: sr_map["sys"] = self.sys_sr
        last_tag, last_buf, last_ts = frames[-1]
        last_sr = sr_map.get(last_tag, self.out_sr)
        total_dur = last_ts + len(last_buf) / last_sr
        total_samples = int(total_dur * self.out_sr) + 1
        out = np.zeros((total_samples, 2), dtype=np.float32)

        for tag, buf, ts in frames:
            src_sr = sr_map.get(tag, self.out_sr)
            if src_sr != self.out_sr:
                buf = self._resample(buf, src_sr, self.out_sr)
            start = int(ts * self.out_sr)
            end = min(start + len(buf), total_samples)
            if end <= start:
                continue
            chunk = buf[:end - start]
            if chunk.ndim == 1:
                out[start:end, 0] += chunk
                out[start:end, 1] += chunk
            elif chunk.shape[1] == 1:
                out[start:end, 0] += chunk[:, 0]
                out[start:end, 1] += chunk[:, 0]
            else:
                out[start:end] += chunk[:, :2]
        np.clip(out, -1.0, 1.0, out=out)
        return out

    @staticmethod
    def _resample(buf, src_sr, dst_sr):
        if src_sr == dst_sr:
            return buf
        n_src = len(buf)
        n_dst = int(n_src * dst_sr / src_sr)
        if n_dst <= 0:
            return buf
        idx = np.linspace(0, n_src - 1, n_dst)
        i0 = np.floor(idx).astype(np.int64)
        i1 = np.minimum(i0 + 1, n_src - 1)
        frac = (idx - i0).astype(np.float32)
        if buf.ndim == 1:
            return buf[i0] * (1 - frac) + buf[i1] * frac
        f = frac[:, None]
        return buf[i0] * (1 - f) + buf[i1] * f

    def save_wav(self, path, data):
        if data is None:
            return False
        pcm = np.clip(data, -1.0, 1.0)
        pcm = (pcm * 32767).astype(np.int16)
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(self.out_sr)
            w.writeframes(pcm.tobytes())
        return True


def merge_av_ffmpeg(ffmpeg_path, video_path, wav_path, out_path=None):
    if not ffmpeg_path or not os.path.isfile(ffmpeg_path):
        return None
    if out_path is None:
        base, _ = os.path.splitext(video_path)
        out_path = base + "_av.mp4"
    cmd = [ffmpeg_path, "-y",
           "-i", video_path,
           "-i", wav_path,
           "-c:v", "copy",
           "-c:a", "aac", "-b:a", "128k",
           "-shortest",
           "-movflags", "+faststart",
           out_path]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           creationflags=flags, timeout=600)
        if r.returncode == 0 and os.path.isfile(out_path):
            return out_path
        logging.error("合并失败: %s", r.stdout.decode("utf-8", errors="ignore")[-500:])
    except Exception:
        logging.exception("合并异常")
    return None


def ffmpeg_export_copy(ffmpeg_path, in_path, out_path, start_sec, dur_sec):
    if not ffmpeg_path or not os.path.isfile(ffmpeg_path):
        return False
    cmd = [ffmpeg_path, "-y",
           "-ss", f"{start_sec:.3f}",
           "-i", in_path,
           "-t", f"{dur_sec:.3f}",
           "-c", "copy",
           "-avoid_negative_ts", "make_zero",
           out_path]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           creationflags=flags, timeout=600)
        return r.returncode == 0 and os.path.isfile(out_path)
    except Exception:
        logging.exception("copy 导出失败")
        return False


class VideoCapturer:
    def __init__(self):
        self.monitors = []
        self.virt = {"left": 0, "top": 0, "width": 0, "height": 0}
        self.refresh()

    def refresh(self):
        try:
            with mss.mss() as sct:
                self.monitors = list(sct.monitors)
            if self.monitors:
                self.virt = dict(self.monitors[0])
            logging.info("检测到 %d 个显示器", len(self.monitors))
            for i, m in enumerate(self.monitors):
                logging.info("  [%d] %dx%d @(%d,%d)",
                             i, m["width"], m["height"], m["left"], m["top"])
        except Exception:
            logging.exception("mss 刷新失败")
            self.monitors = []
        return self.monitors

    def monitor_label(self, i):
        if i >= len(self.monitors):
            return ""
        m = self.monitors[i]
        tag = "全部屏幕合成" if i == 0 else f"屏幕 {i}"
        return f"{tag}  {m['width']}x{m['height']} @({m['left']},{m['top']})"

    def grab(self, mode, monitor_index=1, region=None):
        try:
            with mss.mss() as sct:
                if mode == "full":
                    idx = monitor_index
                    if idx >= len(sct.monitors):
                        idx = 1 if len(sct.monitors) > 1 else 0
                    cap = sct.monitors[idx]
                else:
                    if not region:
                        return None, None
                    x, y, w, h = region
                    cap = {"left": int(x), "top": int(y),
                           "width": int(w), "height": int(h)}
                shot = sct.grab(cap)
                arr = np.frombuffer(shot.raw, dtype=np.uint8)
                arr = arr.reshape((shot.height, shot.width, 4))
                frame_bgr = arr[:, :, :3].copy()
                return frame_bgr, time.perf_counter()
        except Exception:
            logging.exception("grab 失败")
            return None, None


import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font as tkfont


class DependencySplash:
    def __init__(self):
        self.result = {"ok": False, "cancelled": False}

    def show(self):
        root = tk.Tk()
        root.title(f"{APP_NAME} · 准备中")
        root.geometry("440x200")
        root.resizable(False, False)
        root.configure(bg="#1e1e1e")

        tk.Label(root, text="正在检查运行环境…", bg="#1e1e1e", fg="#e0e0e0",
                 font=("Tahoma", 10, "bold")).pack(pady=(20, 6))
        status = tk.Label(root, text="", bg="#1e1e1e", fg="#9e9e9e",
                          font=("Tahoma", 9), wraplength=400, justify="center")
        status.pack(pady=4)
        bar = ttk.Progressbar(root, mode="indeterminate", length=360)
        bar.pack(pady=8)
        bar.start(12)

        def cb(stage, msg):
            def upd():
                status.config(text=msg)
                if stage == "done":
                    bar.stop()
                    self.result["ok"] = True
                    root.after(400, root.destroy)
                elif stage == "error":
                    bar.stop()
                    messagebox.showerror("安装失败", msg, parent=root)
                    root.after(300, root.destroy)
            try:
                root.after(0, upd)
            except Exception:
                pass

        def worker():
            ok, msg = _bootstrap_deps(progress_cb=cb)
            if not ok:
                self.result["ok"] = False
                try:
                    root.after(0, lambda: messagebox.showerror("失败", msg, parent=root))
                except Exception:
                    pass
                try:
                    root.after(500, root.destroy)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()
        root.protocol("WM_DELETE_WINDOW",
                      lambda: (self.result.update(cancelled=True), root.destroy()))
        root.mainloop()
        return self.result["ok"]


class RegionSelector:
    def __init__(self, parent, virt):
        self.parent = parent
        self.virt = virt
        self.result = None

    def show(self):
        self.parent.withdraw()
        time.sleep(0.2)
        v = self.virt
        win = tk.Toplevel()
        win.geometry(f"{v['width']}x{v['height']}+{v['left']}+{v['top']}")
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="black")

        cv = tk.Canvas(win, cursor="crosshair", bg="black", highlightthickness=0)
        cv.pack(fill="both", expand=True)
        cv.create_text(v["width"] // 2, 30,
                       text="拖拽选择区域  |  ESC 取消  |  Enter 确认  (跨屏可用)",
                       fill="white", font=("Tahoma", 12, "bold"))

        state = {"start": None, "rect": None, "region": None}

        def on_press(e):
            state["start"] = (e.x, e.y)
            if state["rect"]:
                cv.delete(state["rect"])

        def on_drag(e):
            if not state["start"]:
                return
            if state["rect"]:
                cv.delete(state["rect"])
            x1, y1 = state["start"]
            state["rect"] = cv.create_rectangle(
                x1, y1, e.x, e.y, outline="#4a9eff", width=2)

        def on_release(e):
            if not state["start"]:
                return
            x1, y1 = state["start"]
            x2, y2 = e.x, e.y
            ax1 = v["left"] + min(x1, x2)
            ay1 = v["top"] + min(y1, y2)
            w = abs(x2 - x1)
            h = abs(y2 - y1)
            state["region"] = (ax1, ay1, w, h)

        def cancel(_=None):
            win.destroy(); self.parent.deiconify()

        def confirm(_=None):
            r = state["region"]
            if r and r[2] > 10 and r[3] > 10:
                self.result = r
            win.destroy(); self.parent.deiconify()

        cv.bind("<ButtonPress-1>", on_press)
        cv.bind("<B1-Motion>", on_drag)
        cv.bind("<ButtonRelease-1>", on_release)
        win.bind("<Escape>", cancel)
        win.bind("<Return>", confirm)
        win.focus_force()
        win.wait_window()
        return self.result


class RecorderPage:
    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self.frame = tk.Frame(parent, bg=app.theme.colors["card"])
        self.mode_var = tk.StringVar(value="full")
        self.monitor_index = 1
        self.fps_var = tk.IntVar(value=app.settings.get("fps", DEFAULT_FPS))
        self.codec_var = tk.StringVar(value=app.settings.get("codec", "mp4"))
        self.path_var = tk.StringVar(value=app.settings.get("output_path", ""))
        self.encoder_var = tk.StringVar(value=app.settings.get("encoder", "auto"))
        self.crf_var = tk.IntVar(value=app.settings.get("crf", 23))
        self.audio_mode_var = tk.StringVar(value=app.settings.get("audio_mode", "none"))
        self.screen_region = None
        self.audio_sources = []
        self.mic_source = None
        self.system_source = None
        self._build()
        self._refresh_audio()

    def _section(self, title):
        wrap = tk.Frame(self.frame, bg=self.app.theme.colors["card"])
        tk.Label(wrap, text=title, font=self.app.theme.fonts["body"],
                 bg=self.app.theme.colors["card"],
                 fg=self.app.theme.colors["accent"]
                 ).pack(anchor="w", padx=8, pady=(6, 2))
        tk.Frame(wrap, bg=self.app.theme.colors["border"], height=1
                 ).pack(fill="x", padx=8)
        inner = tk.Frame(wrap, bg=self.app.theme.colors["card"])
        inner.pack(fill="both", expand=True, padx=8, pady=6)
        return wrap, inner

    def _build(self):
        c = self.app.theme.colors
        f = self.app.theme.fonts

        w1, i1 = self._section("视频模式")
        w1.pack(fill="x", pady=(4, 6))
        tk.Radiobutton(i1, text="全屏", variable=self.mode_var, value="full",
                       bg=c["card"], fg=c["fg"], selectcolor=c["input"],
                       font=f["body"], activebackground=c["card"], cursor="hand2",
                       command=self._on_mode
                       ).pack(side="left", padx=6)
        tk.Radiobutton(i1, text="区域", variable=self.mode_var, value="region",
                       bg=c["card"], fg=c["fg"], selectcolor=c["input"],
                       font=f["body"], activebackground=c["card"], cursor="hand2",
                       command=self._on_mode
                       ).pack(side="left", padx=6)
        self.sel_region_btn = tk.Button(
            i1, text="选择区域", command=self._select_region,
            bg=c["accent"], fg="#fff", relief="flat",
            padx=10, pady=3, cursor="hand2", font=f["small"])
        self.sel_region_btn.pack(side="left", padx=10)
        self.region_label = tk.Label(i1, text="未选择", bg=c["card"],
                                     fg=c["muted"], font=f["small"])
        self.region_label.pack(side="left", padx=4)

        row = tk.Frame(self.frame, bg=c["card"])
        row.pack(fill="x", padx=14, pady=(0, 6))
        tk.Label(row, text="显示器:", bg=c["card"], fg=c["fg"],
                 font=f["body"]).pack(side="left")
        self.monitor_var = tk.StringVar()
        self.monitor_combo = ttk.Combobox(row, textvariable=self.monitor_var,
                                          state="readonly", width=44)
        self.monitor_combo.pack(side="left", padx=6)
        tk.Button(row, text="刷新", command=self._reload_monitors,
                  bg=c["card"], fg=c["fg"], relief="flat",
                  padx=10, pady=2, cursor="hand2", font=f["small"]
                  ).pack(side="left", padx=4)
        self._reload_monitors()

        w2, i2 = self._section("音频")
        w2.pack(fill="x", pady=6)
        row = tk.Frame(i2, bg=c["card"])
        row.pack(fill="x", pady=2)
        tk.Label(row, text="输入源:", bg=c["card"], fg=c["fg"],
                 font=f["body"]).pack(side="left")
        ttk.Combobox(row, textvariable=self.audio_mode_var, state="readonly",
                     width=14, values=["none", "mic", "system", "both"]
                     ).pack(side="left", padx=6)
        tk.Button(row, text="刷新设备", command=self._refresh_audio,
                  bg=c["card"], fg=c["fg"], relief="flat",
                  padx=10, pady=2, cursor="hand2", font=f["small"]
                  ).pack(side="left", padx=4)
        self.audio_info = tk.Label(i2, text="", font=f["small"],
                                   bg=c["card"], fg=c["muted"],
                                   wraplength=780, justify="left")
        self.audio_info.pack(anchor="w", padx=4, pady=(2, 0))

        w3, i3 = self._section("录制参数")
        w3.pack(fill="x", pady=6)
        i3.grid_columnconfigure(1, weight=1)

        tk.Label(i3, text="FPS:", bg=c["card"], fg=c["fg"], font=f["body"]
                 ).grid(row=0, column=0, sticky="w", padx=4, pady=4)
        tk.Spinbox(i3, from_=5, to=MAX_FPS, textvariable=self.fps_var, width=6,
                   bg=c["input"], fg=c["fg"], font=f["body"], relief="flat"
                   ).grid(row=0, column=1, sticky="w", padx=4, pady=4)
        tk.Label(i3, text=f"(推荐 {self.app.caps.recommend_fps()})",
                 bg=c["card"], fg=c["muted"], font=f["small"]
                 ).grid(row=0, column=2, sticky="w", padx=4)

        tk.Label(i3, text="编码器:", bg=c["card"], fg=c["fg"], font=f["body"]
                 ).grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Combobox(i3, textvariable=self.encoder_var, state="readonly",
                     width=10, values=["auto", "ffmpeg", "opencv"]
                     ).grid(row=1, column=1, sticky="w", padx=4)
        ff_txt = "有 ffmpeg" if self.app.caps.has_ffmpeg else "无 ffmpeg(用 OpenCV)"
        tk.Label(i3, text=ff_txt, bg=c["card"], fg=c["muted"], font=f["small"]
                 ).grid(row=1, column=2, sticky="w", padx=4)

        tk.Label(i3, text="画质 CRF:", bg=c["card"], fg=c["fg"], font=f["body"]
                 ).grid(row=2, column=0, sticky="w", padx=4, pady=4)
        tk.Spinbox(i3, from_=15, to=35, textvariable=self.crf_var, width=6,
                   bg=c["input"], fg=c["fg"], font=f["body"], relief="flat"
                   ).grid(row=2, column=1, sticky="w", padx=4)
        tk.Label(i3, text="(小=好, 18~28 推荐, 仅 ffmpeg)",
                 bg=c["card"], fg=c["muted"], font=f["small"]
                 ).grid(row=2, column=2, sticky="w", padx=4)

        tk.Label(i3, text="格式:", bg=c["card"], fg=c["fg"], font=f["body"]
                 ).grid(row=3, column=0, sticky="w", padx=4, pady=4)
        ttk.Combobox(i3, textvariable=self.codec_var, values=["mp4", "avi"],
                     state="readonly", width=8
                     ).grid(row=3, column=1, sticky="w", padx=4)

        tk.Label(i3, text="输出:", bg=c["card"], fg=c["fg"], font=f["body"]
                 ).grid(row=4, column=0, sticky="w", padx=4, pady=4)
        pf = tk.Frame(i3, bg=c["card"])
        pf.grid(row=4, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        pf.grid_columnconfigure(0, weight=1)
        tk.Entry(pf, textvariable=self.path_var, bg=c["input"], fg=c["fg"],
                 font=f["body"], relief="flat"
                 ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        tk.Button(pf, text="浏览", command=self._browse,
                  bg=c["card"], fg=c["fg"], relief="flat",
                  padx=10, pady=2, cursor="hand2", font=f["small"]
                  ).grid(row=0, column=1)

        ctrl = tk.Frame(self.frame, bg=c["card"])
        ctrl.pack(pady=10)
        self.start_btn = tk.Button(ctrl, text="● 开始录制",
                                   command=self.app.start_recording,
                                   bg=c["success"], fg="#fff", relief="flat",
                                   padx=18, pady=8, cursor="hand2",
                                   font=f["button"])
        self.start_btn.pack(side="left", padx=4)
        self.pause_btn = tk.Button(ctrl, text="⏸ 暂停",
                                   command=self.app.pause_recording,
                                   bg=c["warning"], fg="#fff", relief="flat",
                                   padx=18, pady=8, cursor="hand2",
                                   font=f["button"], state="disabled")
        self.pause_btn.pack(side="left", padx=4)
        self.stop_btn = tk.Button(ctrl, text="■ 停止",
                                  command=self.app.stop_recording,
                                  bg=c["danger"], fg="#fff", relief="flat",
                                  padx=18, pady=8, cursor="hand2",
                                  font=f["button"], state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        w4, i4 = self._section("状态")
        w4.pack(fill="both", expand=True, pady=(6, 4))
        self.status_label = tk.Label(i4, text="就绪", font=f["body"],
                                     bg=c["card"], fg=c["success"])
        self.status_label.pack(pady=4)
        self.info_label = tk.Label(i4, text=self.app.caps.summary(),
                                   font=f["small"], bg=c["card"],
                                   fg=c["muted"], wraplength=780, justify="left")
        self.info_label.pack(pady=2)
        self.timer_label = tk.Label(i4, text="00:00", font=f["timer"],
                                    bg=c["card"], fg=c["accent"])
        self.timer_label.pack(pady=8, expand=True)
        self._on_mode()

    def _on_mode(self):
        if self.mode_var.get() == "region":
            self.sel_region_btn.config(state="normal")
            self.monitor_combo.config(state="disabled")
        else:
            self.sel_region_btn.config(state="disabled")
            self.monitor_combo.config(state="readonly")

    def _reload_monitors(self):
        self.app.capturer.refresh()
        labels = [self.app.capturer.monitor_label(i)
                  for i in range(len(self.app.capturer.monitors))]
        self.monitor_combo["values"] = labels
        if labels:
            if self.monitor_index >= len(labels):
                self.monitor_index = 1 if len(labels) > 1 else 0
            self.monitor_combo.current(self.monitor_index)
        self.monitor_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: setattr(self, "monitor_index", self.monitor_combo.current()))

    def _refresh_audio(self):
        self.audio_sources, msg = list_audio_sources()
        self.mic_source = None
        self.system_source = None
        for s in self.audio_sources:
            if s["kind"] == "mic" and self.mic_source is None:
                self.mic_source = s
            if s["kind"] == "system" and self.system_source is None:
                self.system_source = s
        parts = []
        if self.mic_source:
            parts.append(f"麦克风: {self.mic_source['name']}")
        if self.system_source:
            parts.append(f"系统声: {self.system_source['name']}")
        if not parts:
            parts.append(f"无可用音频设备 ({msg})")
        self.audio_info.config(text=" | ".join(parts))

    def _select_region(self):
        sel = RegionSelector(self.app.root, self.app.capturer.virt)
        r = sel.show()
        if r:
            self.screen_region = r
            self.region_label.config(
                text=f"{r[2]}x{r[3]} @({r[0]},{r[1]})",
                fg=self.app.theme.colors["success"])

    def _browse(self):
        ext = self.codec_var.get()
        fn = filedialog.asksaveasfilename(
            defaultextension=f".{ext}",
            filetypes=[(f"{ext.upper()} files", f"*.{ext}"), ("所有文件", "*.*")],
            initialfile=f"recording_{datetime.datetime.now():%Y%m%d_%H%M%S}.{ext}")
        if fn:
            self.path_var.set(fn)


class EditorPage:
    def __init__(self, parent, app):
        self.app = app
        self.parent = parent
        self.frame = tk.Frame(parent, bg=app.theme.colors["card"])
        self.video_path = None
        self.cap = None
        self.cap_lock = threading.Lock()
        self.current_frame = 0
        self.total_frames = 0
        self.clip_start = 0
        self.clip_end = 0
        self.playing = False
        self.video_fps = 25.0
        self.video_w = 0
        self.video_h = 0
        self.is_exporting = False
        self._photo = None
        self.video_path_var = tk.StringVar()
        self._build()

    def _section(self, title):
        wrap = tk.Frame(self.frame, bg=self.app.theme.colors["card"])
        tk.Label(wrap, text=title, font=self.app.theme.fonts["body"],
                 bg=self.app.theme.colors["card"],
                 fg=self.app.theme.colors["accent"]
                 ).pack(anchor="w", padx=8, pady=(6, 2))
        tk.Frame(wrap, bg=self.app.theme.colors["border"], height=1
                 ).pack(fill="x", padx=8)
        inner = tk.Frame(wrap, bg=self.app.theme.colors["card"])
        inner.pack(fill="both", expand=True, padx=8, pady=6)
        return wrap, inner

    def _build(self):
        c = self.app.theme.colors
        f = self.app.theme.fonts

        w1, i1 = self._section("视频文件")
        w1.pack(fill="x", pady=(4, 6))
        i1.grid_columnconfigure(0, weight=1)
        tk.Entry(i1, textvariable=self.video_path_var, bg=c["input"], fg=c["fg"],
                 font=f["body"], relief="flat"
                 ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        tk.Button(i1, text="加载", command=self.load_video,
                  bg=c["accent"], fg="#fff", relief="flat",
                  padx=12, pady=3, cursor="hand2", font=f["small"]
                  ).grid(row=0, column=1)

        w2, i2 = self._section("预览")
        w2.pack(fill="both", expand=True, pady=6)
        self.preview_label = tk.Label(i2, text="加载视频后预览",
                                      bg=c["input"], fg=c["muted"], font=f["body"])
        self.preview_label.pack(fill="both", expand=True)

        w3, i3 = self._section("时间线")
        w3.pack(fill="x", pady=6)
        row = tk.Frame(i3, bg=c["card"])
        row.pack(fill="x")
        self.cur_label = tk.Label(row, text="00:00", font=f["small"],
                                  bg=c["card"], fg=c["accent"])
        self.cur_label.pack(side="left")
        self.total_label = tk.Label(row, text="00:00", font=f["small"],
                                    bg=c["card"], fg=c["muted"])
        self.total_label.pack(side="right")
        self.timeline = tk.Scale(i3, from_=0, to=100, orient="horizontal",
                                 bg=c["card"], fg=c["fg"], highlightthickness=0,
                                 troughcolor=c["input"], showvalue=0,
                                 command=self._on_timeline)
        self.timeline.pack(fill="x", pady=4)
        proll = tk.Frame(i3, bg=c["card"])
        proll.pack(pady=(2, 6))
        self.play_btn = tk.Button(proll, text="▶ 播放", command=self.play,
                                  bg=c["success"], fg="#fff", relief="flat",
                                  padx=14, pady=4, cursor="hand2", font=f["small"])
        self.play_btn.pack(side="left", padx=4)
        self.pause_btn = tk.Button(proll, text="⏸ 暂停", command=self.pause,
                                   bg=c["warning"], fg="#fff", relief="flat",
                                   padx=14, pady=4, cursor="hand2", font=f["small"])
        self.pause_btn.pack(side="left", padx=4)

        w4, i4 = self._section("剪辑")
        w4.pack(fill="x", pady=(6, 4))
        crow = tk.Frame(i4, bg=c["card"])
        crow.pack(pady=4)
        tk.Button(crow, text="设为起点", command=self.set_start,
                  bg=c["accent"], fg="#fff", relief="flat",
                  padx=12, pady=4, cursor="hand2", font=f["small"]
                  ).pack(side="left", padx=4)
        tk.Button(crow, text="设为终点", command=self.set_end,
                  bg=c["accent"], fg="#fff", relief="flat",
                  padx=12, pady=4, cursor="hand2", font=f["small"]
                  ).pack(side="left", padx=4)
        self.export_btn = tk.Button(crow, text="✂ 导出片段", command=self.export,
                                    bg=c["danger"], fg="#fff", relief="flat",
                                    padx=12, pady=4, cursor="hand2", font=f["small"])
        self.export_btn.pack(side="left", padx=4)
        self.clip_info = tk.Label(i4, text="", font=f["small"],
                                  bg=c["card"], fg=c["muted"])
        self.clip_info.pack(pady=(0, 4))

    def load_video(self):
        fn = filedialog.askopenfilename(
            filetypes=[("视频", "*.mp4 *.avi *.mov *.mkv"), ("所有文件", "*.*")])
        if not fn:
            return
        self.video_path = fn
        self.video_path_var.set(fn)
        try:
            with self.cap_lock:
                if self.cap:
                    self.cap.release()
                self.cap = cv2.VideoCapture(fn)
                if not self.cap.isOpened():
                    messagebox.showerror("错误", "无法打开视频")
                    return
                self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
                self.video_fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
                self.video_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                self.video_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self.timeline.config(to=max(1, self.total_frames))
            ts = self.total_frames / self.video_fps
            self.total_label.config(text=f"{int(ts//60):02d}:{int(ts%60):02d}")
            self.clip_start = 0
            self.clip_end = self.total_frames
            self._update_clip_info()
            self._show_frame(0)
            messagebox.showinfo(
                "加载成功",
                f"{self.video_w}x{self.video_h}  {self.video_fps:.2f}fps  "
                f"{self.total_frames} 帧")
        except Exception as e:
            logging.exception("加载视频失败")
            messagebox.showerror("错误", str(e))

    def _show_frame(self, n):
        with self.cap_lock:
            if not self.cap:
                return
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(n))
            ret, frame = self.cap.read()
        if not ret:
            return
        self.current_frame = int(n)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        scale = min(PREVIEW_MAX_W / w, PREVIEW_MAX_H / h, 1.0)
        if scale < 1.0:
            rgb = cv2.resize(rgb, (int(w * scale), int(h * scale)),
                             interpolation=cv2.INTER_AREA)
        img = Image.fromarray(rgb)
        self._photo = ImageTk.PhotoImage(img)
        self.preview_label.config(image=self._photo, text="")
        s = self.current_frame / self.video_fps
        self.cur_label.config(text=f"{int(s//60):02d}:{int(s%60):02d}")

    def _on_timeline(self, value):
        if self.cap:
            self._show_frame(int(float(value)))

    def play(self):
        if not self.cap or self.playing:
            return
        self.playing = True
        self._play_tick()

    def _play_tick(self):
        if not self.playing or not self.cap:
            return
        if self.current_frame >= self.total_frames - 1:
            self.playing = False
            return
        self._show_frame(self.current_frame + 1)
        self.timeline.set(self.current_frame)
        interval = int(1000 / max(self.video_fps, 1))
        self.app.root.after(max(15, interval), self._play_tick)

    def pause(self):
        self.playing = False

    def set_start(self):
        if self.cap:
            self.clip_start = self.current_frame
            self._update_clip_info()

    def set_end(self):
        if self.cap:
            self.clip_end = self.current_frame
            self._update_clip_info()

    def _update_clip_info(self):
        if not self.cap:
            return
        ss = self.clip_start / self.video_fps
        es = self.clip_end / self.video_fps
        dur = es - ss
        self.clip_info.config(
            text=f"起点 {int(ss//60):02d}:{int(ss%60):02d} | "
                 f"终点 {int(es//60):02d}:{int(es%60):02d} | "
                 f"时长 {int(dur//60):02d}:{int(dur%60):02d}")

    def export(self):
        if self.is_exporting or not self.cap:
            return
        if self.clip_start >= self.clip_end:
            messagebox.showwarning("提示", "起点必须早于终点")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".mp4",
            filetypes=[("MP4", "*.mp4"), ("AVI", "*.avi"), ("所有文件", "*.*")],
            initialfile=f"clip_{datetime.datetime.now():%Y%m%d_%H%M%S}.mp4")
        if not out:
            return
        self.is_exporting = True
        self.export_btn.config(state="disabled")

        def worker():
            try:
                start_s = self.clip_start / self.video_fps
                dur_s = (self.clip_end - self.clip_start) / self.video_fps
                if self.app.caps.ffmpeg_path and self.video_path.lower().endswith(
                        (".mp4", ".mkv", ".mov")):
                    ok = ffmpeg_export_copy(self.app.caps.ffmpeg_path,
                                            self.video_path, out,
                                            start_s, dur_s)
                    if ok:
                        self.app.root.after(0, lambda: messagebox.showinfo(
                            "完成", f"已导出(无损)\n{out}"))
                        return
                cap = cv2.VideoCapture(self.video_path)
                cap.set(cv2.CAP_PROP_POS_FRAMES, self.clip_start)
                writer = TimestampedWriter(out, self.video_fps,
                                           (self.video_w, self.video_h))
                t0 = time.perf_counter()
                i = self.clip_start
                while i < self.clip_end:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    ts = t0 + (i - self.clip_start) / self.video_fps
                    writer.write(frame, ts)
                    i += 1
                writer.close()
                cap.release()
                self.app.root.after(0, lambda: messagebox.showinfo(
                    "完成", f"已导出(重编码)\n{writer.path}"))
            except Exception as e:
                logging.exception("导出失败")
                self.app.root.after(0, lambda: messagebox.showerror("失败", str(e)))
            finally:
                self.is_exporting = False
                self.app.root.after(0,
                    lambda: self.export_btn.config(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def release(self):
        self.playing = False
        with self.cap_lock:
            if self.cap:
                self.cap.release()
                self.cap = None


class SettingsDialog:
    def __init__(self, app):
        self.app = app
        self.win = None

    def show(self):
        c = self.app.theme.colors
        f = self.app.theme.fonts
        self.win = tk.Toplevel(self.app.root)
        self.win.title("设置")
        self.win.geometry("480x400")
        self.win.configure(bg=c["bg"])
        self.win.transient(self.app.root)
        self.win.grab_set()
        self.win.resizable(False, False)

        tk.Label(self.win, text="运行环境", font=f["body"],
                 bg=c["bg"], fg=c["accent"]).pack(anchor="w", padx=15, pady=(15, 4))
        tk.Label(self.win, text=self.app.caps.summary(), font=f["small"],
                 bg=c["bg"], fg=c["fg"]).pack(anchor="w", padx=15)

        tk.Label(self.win, text="ffmpeg", font=f["body"],
                 bg=c["bg"], fg=c["accent"]).pack(anchor="w", padx=15, pady=(15, 4))
        ff = self.app.caps.ffmpeg_path or "未找到 (将使用 OpenCV 后端，导出会重编码)"
        tk.Label(self.win, text=ff, font=f["small"], bg=c["bg"], fg=c["muted"],
                 wraplength=440, justify="left").pack(anchor="w", padx=15)

        tk.Label(self.win, text="日志文件", font=f["body"],
                 bg=c["bg"], fg=c["accent"]).pack(anchor="w", padx=15, pady=(15, 4))
        tk.Label(self.win, text=LOG_FILE, font=f["small"],
                 bg=c["bg"], fg=c["muted"], wraplength=440,
                 justify="left").pack(anchor="w", padx=15)

        def open_log():
            try:
                if os.name == "nt":
                    os.startfile(LOG_FILE)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", LOG_FILE])
                else:
                    subprocess.Popen(["xdg-open", LOG_FILE])
            except Exception as e:
                messagebox.showerror("错误", str(e))

        btn_row = tk.Frame(self.win, bg=c["bg"])
        btn_row.pack(pady=20)
        tk.Button(btn_row, text="打开日志", command=open_log,
                  bg=c["card"], fg=c["fg"], relief="flat",
                  padx=14, pady=6, cursor="hand2", font=f["small"]
                  ).pack(side="left", padx=6)
        tk.Button(btn_row, text="关闭", command=self.win.destroy,
                  bg=c["accent"], fg="#fff", relief="flat",
                  padx=20, pady=6, cursor="hand2", font=f["button"]
                  ).pack(side="left", padx=6)


class ScreenRecorderApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("900x760")
        self.root.minsize(760, 620)

        self.caps = Capabilities()
        self.theme = Theme(self.root)
        self.capturer = VideoCapturer()

        self.settings = {
            "fps": self.caps.recommend_fps(),
            "output_path": "",
            "codec": "mp4",
            "encoder": "auto",
            "crf": 23,
            "audio_mode": "none",
        }
        self._load_settings()

        self.recording = False
        self.paused = False
        self.recording_thread = None
        self.writer_thread = None
        self.frame_queue = queue.Queue(maxsize=self.caps.recommend_queue())
        self.recording_lock = threading.Lock()
        self.capture_error_count = 0
        self.max_capture_errors = 5
        self.fps = self.settings["fps"]
        self.output_path = ""
        self.recording_start_time = None
        self.paused_time_total = 0.0
        self.pause_started_at = None
        self.backend = None
        self.audio_recorder = None
        self.audio_wav_path = None

        self._build_ui()

    def _settings_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "settings.json")

    def _load_settings(self):
        try:
            p = self._settings_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    self.settings.update(json.load(f))
        except Exception:
            logging.exception("读设置失败")

    def _save_settings(self):
        try:
            self.settings.update({
                "fps": self.recorder_page.fps_var.get(),
                "output_path": self.recorder_page.path_var.get(),
                "codec": self.recorder_page.codec_var.get(),
                "encoder": self.recorder_page.encoder_var.get(),
                "crf": self.recorder_page.crf_var.get(),
                "audio_mode": self.recorder_page.audio_mode_var.get(),
            })
            with open(self._settings_path(), "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
        except Exception:
            logging.exception("写设置失败")

    def _build_ui(self):
        c = self.theme.colors
        f = self.theme.fonts
        self.root.configure(bg=c["bg"])

        top = tk.Frame(self.root, bg=c["bg"])
        top.pack(fill="x", padx=10, pady=(10, 5))
        tk.Label(top, text=APP_NAME, font=f["title"],
                 bg=c["bg"], fg=c["accent"]).pack(side="left")
        tk.Label(top, text=f"v{APP_VERSION}", font=f["small"],
                 bg=c["bg"], fg=c["muted"]).pack(side="left", padx=8)
        tk.Button(top, text="⚙ 设置", command=lambda: SettingsDialog(self).show(),
                  bg=c["card"], fg=c["fg"], relief="flat",
                  padx=12, pady=4, cursor="hand2", font=f["small"]
                  ).pack(side="right")

        tab_bar = tk.Frame(self.root, bg=c["bg"])
        tab_bar.pack(fill="x", padx=10)
        self.tabs = {}
        for name, label in [("recorder", "录制"), ("editor", "剪辑")]:
            b = tk.Button(tab_bar, text=label, font=f["body"],
                          bg=c["card"], fg=c["fg"], relief="flat",
                          padx=18, pady=6, cursor="hand2",
                          command=lambda n=name: self._show_tab(n))
            b.pack(side="left", padx=(0, 4))
            self.tabs[name] = b

        self.content = tk.Frame(self.root, bg=c["card"])
        self.content.pack(fill="both", expand=True, padx=10, pady=10)

        self.recorder_page = RecorderPage(self.content, self)
        self.editor_page = EditorPage(self.content, self)
        self._show_tab("recorder")

    def _show_tab(self, name):
        c = self.theme.colors
        for n, b in self.tabs.items():
            b.config(bg=c["accent"] if n == name else c["card"],
                     fg="#fff" if n == name else c["fg"])
        if name == "recorder":
            self.editor_page.frame.pack_forget()
            self.recorder_page.frame.pack(fill="both", expand=True)
        else:
            self.recorder_page.frame.pack_forget()
            self.editor_page.frame.pack(fill="both", expand=True)

    def start_recording(self):
        if self.recording:
            return
        rp = self.recorder_page
        if not rp.path_var.get():
            ext = rp.codec_var.get()
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.isdir(desktop):
                desktop = os.path.expanduser("~")
            rp.path_var.set(os.path.join(
                desktop, f"recording_{datetime.datetime.now():%Y%m%d_%H%M%S}.{ext}"))
        self.output_path = rp.path_var.get()
        if rp.mode_var.get() == "region" and not rp.screen_region:
            messagebox.showwarning("提示", "请先选择区域")
            return

        self.fps = max(5, min(MAX_FPS, int(rp.fps_var.get())))
        self.recording_start_time = time.perf_counter()
        self.paused_time_total = 0.0
        self.pause_started_at = None
        self.capture_error_count = 0

        while True:
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                break

        options = {"crf": int(rp.crf_var.get())}
        try:
            self.backend = pick_encoder(self.caps, rp.encoder_var.get(), options)
        except Exception as e:
            messagebox.showerror("编码器选择失败", str(e))
            return

        self.audio_recorder = None
        self.audio_wav_path = None
        mode = rp.audio_mode_var.get()
        if mode != "none":
            try:
                mic_id = rp.mic_source["id"] if rp.mic_source else None
                sys_id = rp.system_source["id"] if rp.system_source else None
                if mode == "mic" and mic_id is None:
                    raise RuntimeError("没有可用的麦克风")
                if mode == "system" and sys_id is None:
                    raise RuntimeError("没有可用的系统声设备")
                if mode == "both" and (mic_id is None or sys_id is None):
                    raise RuntimeError("混音需要麦克风和系统声都可用")
                self.audio_recorder = AudioRecorder(
                    source=mode, mic_id=mic_id, system_id=sys_id)
                self.audio_recorder.start()
                logging.info("音频已启动: %s", mode)
            except Exception as e:
                logging.exception("音频启动失败")
                messagebox.showwarning("音频不可用",
                    f"将只录制视频\n\n{e}")
                self.audio_recorder = None

        self.recording = True
        self.paused = False
        rp.start_btn.config(state="disabled")
        rp.pause_btn.config(state="normal", text="⏸ 暂停")
        rp.stop_btn.config(state="normal")
        rp.status_label.config(text="录制中...", fg=self.theme.colors["success"])

        self.recording_thread = threading.Thread(
            target=self._record_loop, daemon=True)
        self.recording_thread.start()
        self.writer_thread = threading.Thread(
            target=self._write_loop, daemon=True)
        self.writer_thread.start()
        self._tick_timer()

    def _tick_timer(self):
        if not self.recording:
            return
        if not self.paused and self.recording_start_time is not None:
            el = time.perf_counter() - self.recording_start_time - self.paused_time_total
            m, s = int(el // 60), int(el % 60)
            self.recorder_page.timer_label.config(text=f"{m:02d}:{s:02d}")
        self.root.after(500, self._tick_timer)

    def _record_loop(self):
        interval = 1.0 / self.fps
        next_t = time.perf_counter()
        while self.recording:
            if self.paused:
                time.sleep(0.05)
                next_t = time.perf_counter()
                continue
            rp = self.recorder_page
            frame, ts = self.capturer.grab(
                rp.mode_var.get(), rp.monitor_index, rp.screen_region)
            if frame is None:
                self.capture_error_count += 1
                if self.capture_error_count >= self.max_capture_errors:
                    logging.error("采集失败超过 %d 次，停止", self.max_capture_errors)
                    self.root.after(0, self.stop_recording)
                    break
                time.sleep(0.05)
                continue
            self.capture_error_count = 0
            if self.frame_queue.full():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass
            try:
                self.frame_queue.put_nowait((frame, ts))
            except queue.Full:
                pass
            next_t += interval
            now = time.perf_counter()
            if next_t > now:
                time.sleep(next_t - now)
            else:
                next_t = now

    def _write_loop(self):
        first = None
        while first is None and self.recording:
            try:
                first = self.frame_queue.get(timeout=0.5)
            except queue.Empty:
                if not self.recording:
                    return
                continue
        if first is None:
            return
        frame0, ts0 = first
        h, w = frame0.shape[:2]
        self.backend.start(self.output_path, self.fps, (w, h), {})
        self.backend.write(frame0, ts0)
        while self.recording or not self.frame_queue.empty():
            try:
                f, ts = self.frame_queue.get(timeout=0.5)
                self.backend.write(f, ts)
            except queue.Empty:
                continue
        path = self.backend.stop()
        if path:
            self.output_path = path
        self.root.after(0, self._on_recording_finished)

    def _on_recording_finished(self):
        if self.audio_recorder:
            try:
                data = self.audio_recorder.stop()
                if data is not None and len(data) > 0:
                    wav_path = os.path.splitext(self.output_path)[0] + ".wav"
                    if self.audio_recorder.save_wav(wav_path, data):
                        self.audio_wav_path = wav_path
                        logging.info("音频保存: %s", wav_path)
            except Exception:
                logging.exception("音频保存失败")
                self.audio_wav_path = None
            self.audio_recorder = None

        merged_path = None
        if self.audio_wav_path and self.caps.ffmpeg_path:
            try:
                merged_path = merge_av_ffmpeg(
                    self.caps.ffmpeg_path, self.output_path, self.audio_wav_path)
                if merged_path:
                    logging.info("已合并: %s", merged_path)
            except Exception:
                logging.exception("合并失败")

        self.recording = False
        self.paused = False
        rp = self.recorder_page
        rp.start_btn.config(state="normal")
        rp.pause_btn.config(state="disabled", text="⏸ 暂停")
        rp.stop_btn.config(state="disabled")
        rp.status_label.config(text="录制完成", fg=self.theme.colors["fg"])

        msg = f"视频: {self.output_path}"
        if self.audio_wav_path:
            msg += f"\n音频: {self.audio_wav_path}"
        if merged_path:
            msg += f"\n合并: {merged_path}"
        rp.info_label.config(text=msg.replace("\n", " | "))
        messagebox.showinfo("完成", msg)

    def pause_recording(self):
        if not self.recording:
            return
        self.paused = not self.paused
        rp = self.recorder_page
        if self.paused:
            self.pause_started_at = time.perf_counter()
            rp.pause_btn.config(text="▶ 继续")
            rp.status_label.config(text="已暂停", fg=self.theme.colors["warning"])
            if self.audio_recorder:
                self.audio_recorder.pause()
        else:
            if self.pause_started_at:
                self.paused_time_total += time.perf_counter() - self.pause_started_at
                self.pause_started_at = None
            rp.pause_btn.config(text="⏸ 暂停")
            rp.status_label.config(text="录制中...", fg=self.theme.colors["success"])
            if self.audio_recorder:
                self.audio_recorder.resume()

    def stop_recording(self):
        if self.recording:
            self.recording = False
            self.recorder_page.status_label.config(
                text="正在停止...", fg=self.theme.colors["warning"])

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        try:
            self._save_settings()
        except Exception:
            logging.exception("关闭时保存失败")
        self.recording = False
        try:
            if self.audio_recorder:
                self.audio_recorder.stop()
        except Exception:
            pass
        try:
            self.editor_page.release()
        except Exception:
            pass
        self.root.destroy()


def main():
    _enable_dpi_awareness()
    _hide_console()
    _setup_logging()

    splash = DependencySplash()
    if not splash.show():
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showerror(
                "无法启动",
                "依赖未就绪，请手动运行:\n\n"
                'pip install opencv-python mss pillow numpy "sounddevice>=0.4.6"')
            r.destroy()
        except Exception:
            pass
        return

    _load_libs()

    try:
        app = ScreenRecorderApp()
        app.run()
    except Exception as e:
        logging.exception("主程序异常")
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showerror("启动失败", f"{e}\n\n日志: {LOG_FILE}")
            r.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()