# -*- coding: utf-8 -*-
"""Захват: предпросмотр, запись, прослушивание звука."""
from __future__ import annotations

import array
import os
import queue
import re
import subprocess
import threading
import time

from .commands import build_filters, preview_size, codec_args, audio_args, dshow_args
from .config import CREATE_NO_WINDOW, LOG_FILE
from .device import FFMPEG, FFPLAY

def frame_is_blank(data, threshold=4.0):
    """Пустой кадр: синий экран, чёрное поле или застывшая картинка.

    Считаем разброс отдельно по каналам R/G/B на прореженной выборке.
    Синий экран даёт нулевой разброс по всем трём, живая картинка — нет.
    """
    if not data:
        return True
    step = max(3, (len(data) // 3 // 1500) * 3)      # ~1500 точек на кадр
    r = data[0::step]
    g = data[1::step]
    b = data[2::step]
    for ch in (r, g, b):
        if not ch:
            continue
        mean = sum(ch) / len(ch)
        var = sum((v - mean) ** 2 for v in ch) / len(ch)
        if var ** 0.5 > threshold:
            return False
    return True


class AudioMonitor:
    """Проигрывает захваченный звук в колонки. Запись при этом всегда идёт в файл."""

    def __init__(self, rate=48000, channels=2):
        self.rate = rate
        self.channels = channels
        self.proc = None
        self.enabled = False
        self.q = queue.Queue(maxsize=40)

    def set_enabled(self, on):
        self.enabled = bool(on)
        if self.enabled:
            self.start()
        else:
            self.stop()

    def start(self):
        if self.proc or not FFPLAY:
            return
        try:
            self.proc = subprocess.Popen(
                [FFPLAY, "-hide_banner", "-loglevel", "quiet", "-nodisp",
                 "-autoexit", "-fflags", "nobuffer",
                 "-f", "s16le", "-ar", str(self.rate),
                 "-ch_layout", "stereo", "-"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        except Exception:  # noqa: BLE001
            self.proc = None
            return
        threading.Thread(target=self._writer, daemon=True).start()

    def _writer(self):
        proc = self.proc
        while True:
            chunk = self.q.get()
            if chunk is None or proc is None:
                return
            try:
                proc.stdin.write(chunk)
                proc.stdin.flush()
            except Exception:  # noqa: BLE001
                return

    def feed(self, chunk):
        if not self.enabled or not self.proc:
            return
        try:
            self.q.put_nowait(chunk)
        except queue.Full:
            try:
                self.q.get_nowait()
                self.q.put_nowait(chunk)
            except queue.Empty:
                pass

    def stop(self):
        proc, self.proc = self.proc, None
        if not proc:
            return
        try:
            self.q.put_nowait(None)
        except queue.Full:
            pass
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


def run_quiet(args, timeout=30):
    try:
        p = subprocess.run(
            args, capture_output=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return p.returncode, (p.stderr or b"").decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def list_dshow_devices():
    """Возвращает (video_devices, audio_devices)."""
    rc, err = run_quiet([FFMPEG, "-hide_banner", "-loglevel", "info",
                         "-f", "dshow", "-list_devices", "true", "-i", "dummy"])
    video, audio = [], []
    for line in err.splitlines():
        m = re.search(r'"([^"]+)"\s*\(([^)]*)\)\s*$', line)
        if not m:
            continue
        name, kinds = m.group(1), m.group(2)
        if "video" in kinds and name not in video:
            video.append(name)
        if "audio" in kinds and name not in audio:
            audio.append(name)
    video = [d for d in video if "Virtual Camera" not in d]
    # устройство с видеопином не показываем в списке аудио — оно дублирует видеовход
    audio = [d for d in audio if d not in video]
    return video, audio


def list_video_options(device):
    """Список поддерживаемых (pix_fmt, w, h, fps)."""
    rc, err = run_quiet([FFMPEG, "-hide_banner", "-loglevel", "info", "-f", "dshow",
                         "-list_options", "true", "-i", "video=" + device])
    out = []
    for line in err.splitlines():
        m = re.search(r"pixel_format=(\S+)\s+min s=(\d+)x(\d+)\s+fps=([\d.]+)\s+"
                      r"max s=(\d+)x(\d+)\s+fps=([\d.]+)", line)
        if m:
            out.append((m.group(1), int(m.group(5)), int(m.group(6)), float(m.group(7))))
    return list(dict.fromkeys(out))


class Preview:
    def __init__(self, settings, on_frame, on_level, on_error, on_audio=None):
        self.s = dict(settings)
        self.on_frame = on_frame
        self.on_level = on_level
        self.on_error = on_error
        self.on_audio = on_audio
        self.proc = None
        self.threads = []
        self.stopping = False
        self.frame_bytes = 0
        self.pw, self.ph = preview_size(int(self.s.get("preview_width", 640)))

    def command(self):
        s = self.s
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "quiet", "-nostats"]
        if s.get("search"):
            # Режим поиска: во время перемотки сигнал рваный, а картинка нужна
            # «здесь и сейчас». Оставляем только масштаб — без деинтерлейса,
            # шумодава и резкости, они съедают кадры.
            vf = "scale=%d:%d:flags=fast_bilinear,setsar=1" % (self.pw, self.ph)
            cmd += ["-fflags", "nobuffer", "-flags", "low_delay"]
        else:
            vf = build_filters(s, True, self.pw)
        cmd += dshow_args(s)
        cmd += ["-map", "0:v"]
        cmd += ["-vf", vf, "-c:v", "rawvideo", "-pix_fmt", "rgb24",
                "-f", "rawvideo", "pipe:1"]
        if s.get("audio_enabled") and s.get("audio_device"):
            cmd += ["-map", "0:a:0", "-f", "s16le", "-ac", "2", "-ar", "48000", "pipe:2"]
        return cmd

    def start(self):
        self.frame_bytes = self.pw * self.ph * 3
        try:
            self.proc = subprocess.Popen(
                self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0, creationflags=CREATE_NO_WINDOW)
        except Exception as exc:  # noqa: BLE001
            self.on_error("Не удалось запустить ffmpeg: %s" % exc)
            return False
        threading.Thread(target=self._watch, daemon=True).start()
        t = threading.Thread(target=self._read_video, daemon=True)
        t.start()
        self.threads.append(t)
        if self.s.get("audio_enabled"):
            t = threading.Thread(target=self._read_audio, daemon=True)
            t.start()
            self.threads.append(t)
        return True

    def _watch(self):
        proc = self.proc
        if not proc:
            return
        rc = proc.wait()
        if self.stopping:
            return                      # штатная остановка, это не ошибка
        if rc == 0:
            return                      # ffmpeg завершился сам, без аварии
        self.on_error("Захват остановился (ffmpeg код %s). "
                      "Нажмите «▶ Предпросмотр»." % rc)

    def _read_video(self):
        n = self.frame_bytes
        try:
            while True:                      # читаем до EOF, иначе ffmpeg блокируется
                buf = bytearray()
                while len(buf) < n:
                    chunk = self.proc.stdout.read(n - len(buf))
                    if not chunk:
                        return
                    buf += chunk
                self.on_frame(bytes(buf), self.pw, self.ph)
        except Exception:  # noqa: BLE001
            pass

    def _read_audio(self):
        import array
        try:
            while True:
                chunk = self.proc.stderr.read(8192)
                if not chunk:
                    return
                a = array.array("h")
                a.frombytes(chunk[: len(chunk) // 2 * 2])
                if len(a):
                    peak = max(max(a), -min(a))
                    ss = sum(x * x for x in a[::7])
                    rms = (ss / max(1, len(a[::7]))) ** 0.5
                    self.on_level(rms, peak)
                    if self.on_audio:
                        self.on_audio(a.tobytes())
        except Exception:  # noqa: BLE001
            pass

    def stop(self):
        # флаг ставим ПЕРВЫМ: иначе поток _watch успевает увидеть смерть
        # процесса раньше и сообщает «захват остановился» при штатной остановке
        self.stopping = True
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            try:
                proc.stdin.write(b"q")
                proc.stdin.flush()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=4)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass


class Recorder:
    def __init__(self, settings, out_path, on_frame, on_stats, on_done,
                 on_level=None, on_audio=None):
        self.s = dict(settings)
        self.out_path = out_path
        self.on_frame = on_frame
        self.on_stats = on_stats
        self.on_done = on_done
        self.on_level = on_level or (lambda rms, peak: None)
        self.on_audio = on_audio
        self.proc = None
        self.stopping = False
        self.start_time = 0.0
        # во время записи картинку не раздуваем: лишняя нагрузка на шину и CPU
        self.pw, self.ph = preview_size(
            min(int(self.s.get("preview_width", 640)), 800))
        self.log_text = ""

    def command(self):
        s = self.s
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostats"]
        cmd += dshow_args(s)

        if s.get("f_bake"):
            vf_cap = build_filters(s, False)
        else:
            w = int(s.get("width", 720))
            h = int(s.get("height", 576))
            vf_cap = "setsar=%.6f" % ((4.0 / 3.0) * h / w)
        interlaced = "bwdif" not in vf_cap and "yadif" not in vf_cap \
                     and "nnedi" not in vf_cap and "w3fdif" not in vf_cap
        if s["codec"] in ("hevc_nvenc", "h264_nvenc"):
            vf_cap += ",format=nv12"
        elif s["codec"] in ("x264", "x265", "svtav1", "mpeg2"):
            vf_cap += ",format=yuv420p"

        cmd += ["-map", "0:v"]
        if s.get("audio_enabled") and s.get("audio_device"):
            cmd += ["-map", "0:a:0"]
        cmd += ["-vf", vf_cap]
        cmd += codec_args(s, interlaced)
        if s.get("audio_enabled") and s.get("audio_device"):
            gain = float(s.get("audio_gain") or 0)
            if abs(gain) > 0.05:
                cmd += ["-af", "volume=%.1fdB" % gain]
            cmd += audio_args(s)
        cmd += ["-max_muxing_queue_size", "1024", self.out_path]

        if s.get("record_preview"):
            cmd += ["-map", "0:v",
                    "-vf", "scale=%d:%d:flags=bilinear,setsar=1" % (self.pw, self.ph),
                    "-c:v", "rawvideo", "-pix_fmt", "rgb24",
                    "-f", "rawvideo", "pipe:1"]
        if s.get("audio_enabled") and s.get("audio_device"):
            # уровень звука во время записи считается из этого потока,
            # текстовые сообщения ffmpeg вылавливаются в лог отдельно
            cmd += ["-map", "0:a:0", "-f", "s16le", "-ac", "2", "-ar", "48000",
                    "pipe:2"]
        return cmd

    def start(self):
        try:
            self.proc = subprocess.Popen(
                self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0, creationflags=CREATE_NO_WINDOW)
        except Exception as exc:  # noqa: BLE001
            self.on_done(False, "Не удалось запустить ffmpeg: %s" % exc)
            return False
        self.start_time = time.time()
        threading.Thread(target=self._watch, daemon=True).start()
        if self.s.get("record_preview"):
            threading.Thread(target=self._read_video, daemon=True).start()
        if self.s.get("audio_enabled") and self.s.get("audio_device"):
            threading.Thread(target=self._read_audio, daemon=True).start()
        else:
            threading.Thread(target=self._read_log, daemon=True).start()
        return True

    def _read_video(self):
        n = self.pw * self.ph * 3
        try:
            while True:                     # дочитываем до EOF, иначе ffmpeg блокируется
                buf = bytearray()
                while len(buf) < n:
                    chunk = self.proc.stdout.read(n - len(buf))
                    if not chunk:
                        return
                    buf += chunk
                self.on_frame(bytes(buf), self.pw, self.ph)
        except Exception:  # noqa: BLE001
            pass

    def _read_log(self):
        try:
            data = self.proc.stderr.read()
            self.log_text = (data or b"").decode("utf-8", "replace")[-4000:]
        except Exception:  # noqa: BLE001
            pass

    def _read_audio(self):
        import array
        try:
            while True:
                chunk = self.proc.stderr.read(16384)
                if not chunk:
                    return
                txt = re.findall(rb"[\x20-\x7e\r\n]{12,}", chunk)
                for part in txt:
                    self.log_text = (self.log_text +
                                     part.decode("ascii", "ignore"))[-4000:]
                a = array.array("h")
                a.frombytes(chunk[: len(chunk) // 2 * 2])
                if len(a):
                    peak = max(max(a), -min(a))
                    ss = sum(x * x for x in a[::7])
                    rms = (ss / max(1, len(a[::7]))) ** 0.5
                    self.on_level(rms, peak)
                    if self.on_audio:
                        self.on_audio(a.tobytes())
        except Exception:  # noqa: BLE001
            pass

    def _watch(self):
        while self.proc.poll() is None:
            elapsed = time.time() - self.start_time
            try:
                size = os.path.getsize(self.out_path)
            except OSError:
                size = 0
            self.on_stats(elapsed, size)
            time.sleep(1.0)
        rc = self.proc.returncode
        elapsed = time.time() - self.start_time
        try:
            size = os.path.getsize(self.out_path)
        except OSError:
            size = 0
        self.on_done(rc == 0, (elapsed, size, rc))

    def stop(self):
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.stdin.write(b"q")
                proc.stdin.flush()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=6)       # ffmpeg дописывает контейнер
            except Exception:  # noqa: BLE001
                try:
                    proc.stdin.close()     # EOF — второй способ попросить выйти
                except Exception:  # noqa: BLE001
                    pass
                try:
                    proc.wait(timeout=4)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
        self.stopping = True
        if self.log_text:
            try:
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.write(self.log_text)
            except Exception:  # noqa: BLE001
                pass
