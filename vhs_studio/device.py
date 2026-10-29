# -*- coding: utf-8 -*-
"""Обнаружение ffmpeg и устройств DirectShow, проверка входов."""
from __future__ import annotations

import os
import re
import shutil
import subprocess

from .config import CREATE_NO_WINDOW, PIN_COMPOSITE, PIN_SVIDEO, BASE_DIR

def find_ffmpeg():
    for name in ("ffmpeg.exe", "ffmpeg"):
        p = os.path.join(BASE_DIR, name)
        if os.path.isfile(p):
            return p
    return shutil.which("ffmpeg")


FFMPEG = find_ffmpeg()


def find_ffplay():
    if not FFMPEG:
        return None
    cand = os.path.join(os.path.dirname(FFMPEG), "ffplay.exe")
    if os.path.isfile(cand):
        return cand
    return shutil.which("ffplay")


FFPLAY = find_ffplay()


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


def probe_inputs(s, pins=(PIN_COMPOSITE, PIN_SVIDEO), seconds=1.2):
    """Определяет, на каком пине кроссбара есть живой сигнал.

    Возвращает (лучший_пин, {пин: (YAVG, разброс)}) или (None, {}).
    Синий экран (нет сигнала) даёт постоянную яркость и нулевой разброс.
    """
    scores = {}
    for pin in pins:
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "info", "-f", "dshow",
               "-crossbar_video_input_pin_number", str(pin),
               "-video_size", "%dx%d" % (int(s["width"]), int(s["height"])),
               "-framerate", str(s["fps"]), "-pixel_format", "uyvy422",
               "-i", "video=" + s["video_device"], "-t", str(seconds),
               "-vf", "signalstats,metadata=print", "-f", "null", "-"]
        rc, err = run_quiet(cmd, timeout=30)
        ys = [float(v) for v in re.findall(r"YAVG=([\d.]+)", err)]
        if not ys:
            scores[pin] = (0.0, 0.0)
            continue
        mean = sum(ys) / len(ys)
        var = (sum((y - mean) ** 2 for y in ys) / len(ys)) ** 0.5
        scores[pin] = (mean, var)
    # «живой» = яркость заметно отличается от синего экрана (35) или меняется
    best, best_score = None, -1.0
    for pin, (mean, var) in scores.items():
        score = var + (0.0 if 30 <= mean <= 45 and var < 0.5 else 1.0)
        if score > best_score:
            best, best_score = pin, score
    if best is not None and scores.get(best, (0, 0))[1] < 0.05:
        return None, scores
    return best, scores


