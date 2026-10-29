# -*- coding: utf-8 -*-
"""Окно программы."""
from __future__ import annotations

import datetime
import os
import queue
import re
import shutil
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

from . import config as cfg
from .capture import AudioMonitor, Preview, Recorder
from .commands import (audio_args, build_filters, codec_args, dshow_args,
                       output_ext, preview_size)
from .config import (AUDIO_CODECS, CODECS, CODEC_INFO, CRF_RANGE, DEINT_INFO,
                     DEFAULTS, INPUT_LABELS, INPUT_PINS, LOG_FILE, PIN_AUTO,
                     PIN_COMPOSITE, PIN_SVIDEO, PROFILE_DIR, RECOMMENDED,
                     SETTINGS_FILE, s_fps)
from .device import FFMPEG, list_dshow_devices, probe_inputs, run_quiet
from .estimates import estimate_gb_per_hour, estimate_mbps, fmt_gb, hms, human_size

def human_size(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f ТБ" % n


def hms(sec):
    sec = int(sec)
    return "%02d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VHS Studio — захват с VHS (Rombica Pro Studio)")
        self.geometry("1400x980")
        self.minsize(1200, 900)

        self.s = dict(DEFAULTS)
        self.load_settings()

        self.preview = None
        self.recorder = None
        self.frame_q = queue.Queue(maxsize=2)
        self.events = queue.Queue()
        self.level = -70.0
        self.peak = -70.0
        self.audio_loud = True
        self.audio_last = 0.0
        self.audio_silent_since = None
        self._fps = 0.0
        self._fps_frames = 0
        self._fps_t0 = time.time()
        self.photo = None
        self.last_frame = None
        self.rec_start = 0.0
        self._restart_job = None
        self.monitor = AudioMonitor()
        self._last_frame_at = 0.0
        self._last_spread = 255
        self._no_signal_since = None
        self._reinit_tries = 0

        self._build_ui()
        self.refresh_devices()
        self.after(40, self._tick)
        self.after(500, self.start_preview)     # предпросмотр включён сразу
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        main = ttk.Frame(self, padding=8)
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main)
        left.pack(side="left", fill="y")
        right = ttk.Frame(main)
        right.pack(side="right", fill="both", expand=True, padx=(10, 0))

        # --- Устройство ---
        dev = ttk.LabelFrame(left, text="Устройство", padding=6)
        dev.pack(fill="x", pady=(0, 6))
        ttk.Label(dev, text="Видео:").grid(row=0, column=0, sticky="w")
        self.cb_video = ttk.Combobox(dev, width=30, state="readonly")
        self.cb_video.grid(row=0, column=1, sticky="we", pady=1)
        self.cb_video.bind("<<ComboboxSelected>>", lambda e: self.on_video_dev())
        ttk.Label(dev, text="Аудио:").grid(row=1, column=0, sticky="w")
        self.cb_audio = ttk.Combobox(dev, width=30, state="readonly")
        self.cb_audio.grid(row=1, column=1, sticky="we", pady=1)
        self.var_audio_on = tk.BooleanVar(value=bool(self.s.get("monitor", True)))
        ttk.Button(dev, text="Обновить", command=self.refresh_devices).grid(
            row=3, column=1, sticky="e", pady=(2, 0))

        # --- Источник ---
        src = ttk.LabelFrame(left, text="Источник сигнала", padding=6)
        src.pack(fill="x", pady=(0, 6))
        ttk.Label(src, text="Вход:").grid(row=0, column=0, sticky="w")
        self.cb_input = ttk.Combobox(src, width=16, state="readonly",
                                     values=INPUT_LABELS)
        self.cb_input.current(self._pin_to_index(self.s["input_pin"]))
        self.cb_input.grid(row=0, column=1, sticky="we", pady=1)
        self.cb_input.bind("<<ComboboxSelected>>", lambda e: self.on_change())
        ttk.Button(src, text="Определить", width=11,
                   command=self.detect_input).grid(row=0, column=2, sticky="w")
        ttk.Label(src, text="Стандарт:").grid(row=1, column=0, sticky="w")
        self.cb_std = ttk.Combobox(src, width=16, state="readonly",
                                   values=["PAL", "SECAM", "NTSC"])
        self.cb_std.set(self.s["standard"])
        self.cb_std.grid(row=1, column=1, sticky="we", pady=1)
        self.cb_std.bind("<<ComboboxSelected>>", lambda e: self.on_std())
        self.btn_reinit = ttk.Button(src, text="⟳ Перезапустить", command=self.reinit_signal)
        self.btn_reinit.grid(row=2, column=0, columnspan=2, sticky="we", pady=(4, 0))

        # --- Кодек ---
        enc = ttk.LabelFrame(left, text="Запись", padding=6)
        enc.pack(fill="x", pady=(0, 6))
        ttk.Label(enc, text="Кодек:").grid(row=0, column=0, sticky="w")
        self.cb_codec = ttk.Combobox(enc, width=30, state="readonly",
                                     values=list(CODECS.keys()))
        for _label, _meta in CODECS.items():
            if _meta["id"] == self.s["codec"]:
                self.cb_codec.set(_label)
                break
        else:
            self.cb_codec.current(1)
        self.cb_codec.grid(row=0, column=1, columnspan=2, sticky="we", pady=1)
        self.cb_codec.bind("<<ComboboxSelected>>", lambda e: self.on_codec())
        self.lbl_codec_info = ttk.Label(enc, text="", wraplength=300,
                                        foreground="#444444", justify="left")
        self.lbl_codec_info.grid(row=1, column=0, columnspan=3, sticky="w",
                                 pady=(0, 3))

        ttk.Label(enc, text="Качество:").grid(row=2, column=0, sticky="w")
        self.var_crf = tk.IntVar(value=self.s["crf"])
        self.sc_crf = ttk.Scale(enc, from_=10, to=28, orient="horizontal",
                                variable=self.var_crf, command=lambda v: self.on_change())
        self.sc_crf.grid(row=2, column=1, sticky="we", pady=1)
        self.lbl_crf = ttk.Label(enc, text="CRF 16", width=8)
        self.lbl_crf.grid(row=2, column=2, sticky="w")

        ttk.Label(enc, text="Звук:").grid(row=3, column=0, sticky="w")
        self.cb_acodec = ttk.Combobox(enc, width=22, state="readonly",
                                      values=list(AUDIO_CODECS.keys()))
        if self.s["audio_codec"] in AUDIO_CODECS:
            self.cb_acodec.set(self.s["audio_codec"])
        else:
            self.cb_acodec.current(0)
        self.cb_acodec.grid(row=3, column=1, columnspan=2, sticky="we", pady=1)
        self.cb_acodec.bind("<<ComboboxSelected>>", lambda e: self.on_change())

        ttk.Label(enc, text="Папка:").grid(row=4, column=0, sticky="w")
        self.var_out = tk.StringVar(value=self.s["out_dir"])
        e = ttk.Entry(enc, textvariable=self.var_out, width=22)
        e.grid(row=4, column=1, sticky="we", pady=1)
        e.bind("<FocusOut>", lambda ev: self.on_change())
        ttk.Button(enc, text="…", width=3, command=self.pick_dir).grid(row=4, column=2)

        ttk.Label(enc, text="Имя:").grid(row=5, column=0, sticky="w")
        f2 = ttk.Frame(enc)
        f2.grid(row=5, column=1, columnspan=2, sticky="we")
        self.var_tape = tk.StringVar(value=self.s["tape_name"])
        e2 = ttk.Entry(f2, textvariable=self.var_tape, width=14)
        e2.pack(side="left", fill="x", expand=True)
        e2.bind("<KeyRelease>", lambda ev: self.on_change())

        self.var_bake = tk.BooleanVar(value=self.s["f_bake"])
        ttk.Checkbutton(enc, text="Применять обработку при записи\n(иначе — только в предпросмотре)",
                        variable=self.var_bake, command=self.on_change).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.var_rprev = tk.BooleanVar(value=self.s["record_preview"])
        ttk.Checkbutton(enc, text="Показывать картинку во время записи",
                        variable=self.var_rprev, command=self.on_change).grid(
            row=7, column=0, columnspan=3, sticky="w")

        self.lbl_estimate = ttk.Label(enc, text="", foreground="#1a5fb4")
        self.lbl_estimate.grid(row=8, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # --- Обработка ---
        proc = ttk.LabelFrame(left, text="Обработка (видно в предпросмотре)", padding=6)
        proc.pack(fill="x")
        r = 0
        ttk.Label(proc, text="Деинтерлейс:").grid(row=r, column=0, sticky="w")
        self.cb_deint = ttk.Combobox(proc, width=18, state="readonly",
                                     values=["bwdif", "yadif", "estdif", "nnedi", "w3fdif", "нет"])
        self.cb_deint.set(self.s["f_deint"])
        self.cb_deint.grid(row=r, column=1, sticky="we", pady=1)
        self.cb_deint.bind("<<ComboboxSelected>>", lambda e: self.on_change())
        self.lbl_deint_info = ttk.Label(proc, text="", wraplength=300,
                                        foreground="#444444", justify="left")
        self.lbl_deint_info.grid(row=1, column=0, columnspan=3, sticky="w",
                                 pady=(0, 3))
        r = 1

        def slider(row, label, lo, hi, key, fmt="%.0f"):
            ttk.Label(proc, text=label).grid(row=row, column=0, sticky="w")
            var = tk.DoubleVar(value=float(self.s[key]))
            sc = ttk.Scale(proc, from_=lo, to=hi, orient="horizontal", variable=var,
                           command=lambda v: self.on_change())
            sc.grid(row=row, column=1, sticky="we", pady=1)
            lbl = ttk.Label(proc, text=fmt % float(self.s[key]), width=6)
            lbl.grid(row=row, column=2, sticky="w")
            setattr(self, "var_" + key, var)
            setattr(self, "lbl_" + key, lbl)
            return var

        slider(r + 1, "Шум (яркость)", 0, 20, "f_denoise")
        slider(r + 2, "Шум (цвет)", 0, 25, "f_chroma_dn")
        slider(r + 3, "Резкость", 0, 3, "f_sharpen", "%.1f")
        slider(r + 4, "Яркость", -0.3, 0.3, "f_bright", "%.2f")
        slider(r + 5, "Контраст", 0.6, 1.6, "f_contrast", "%.2f")
        slider(r + 6, "Насыщенность", 0.5, 1.8, "f_saturation", "%.2f")
        slider(r + 7, "Обрезка низа, px", 0, 40, "f_band")
        slider(r + 8, "Обрезка боков, px", 0, 20, "f_side")

        ttk.Label(proc, text="Масштаб:").grid(row=r + 9, column=0, sticky="w")
        self.cb_scale = ttk.Combobox(proc, width=18, state="readonly",
                                     values=["none", "960x720", "1440x1080"])
        self.cb_scale.set(self.s["f_scale"])
        self.cb_scale.grid(row=r + 9, column=1, sticky="we", pady=1)
        self.cb_scale.bind("<<ComboboxSelected>>", lambda e: self.on_change())

        ttk.Label(proc, text="Звук, дБ:").grid(row=r + 10, column=0, sticky="w")
        self.var_audio_gain = tk.DoubleVar(value=float(self.s.get("audio_gain", 0)))
        self.sc_gain = ttk.Scale(proc, from_=-12, to=12, orient="horizontal",
                                 variable=self.var_audio_gain,
                                 command=lambda v: self.on_change())
        self.sc_gain.grid(row=r + 10, column=1, sticky="we", pady=1)
        self.lbl_audio_gain = ttk.Label(proc, text="%+.1f" % float(self.s.get("audio_gain", 0)),
                                        width=6)
        self.lbl_audio_gain.grid(row=r + 10, column=2, sticky="w")

        # --- правая часть ---
        self.lbl_video = tk.Label(right, bg="#101010", text="Предпросмотр выключен",
                                  fg="#808080", width=64, height=24)
        self.lbl_video.pack(fill="both", expand=True)
        self.lbl_video.bind("<Configure>", self._on_video_resize)

        meter = ttk.Frame(right)
        meter.pack(fill="x", pady=6)
        ttk.Label(meter, text="Уровень звука:").pack(side="left")
        self.canvas_meter = tk.Canvas(meter, height=14, bg="#202020",
                                      highlightthickness=0)
        self.canvas_meter.pack(side="left", fill="x", expand=True, padx=6)
        self.bar = self.canvas_meter.create_rectangle(0, 0, 0, 14, fill="#3ecf3e")

        btns = ttk.Frame(right)
        btns.pack(fill="x")
        self.btn_prev = ttk.Button(btns, text="▶  Предпросмотр", command=self.toggle_preview)
        self.btn_prev.pack(side="left")
        self.btn_rec = ttk.Button(btns, text="●  Запись", command=self.toggle_record,
                                  state="disabled")
        self.btn_rec.pack(side="left", padx=6)
        self.chk_sound = ttk.Checkbutton(btns, text="🔊 Колонки", variable=self.var_audio_on,
                                         command=self.on_sound_toggle)
        self.chk_sound.pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Снимок PNG", command=self.snapshot).pack(side="left")
        ttk.Button(btns, text="⟳ Сигнал", command=self.reinit_signal).pack(side="left", padx=6)

        btns2 = ttk.Frame(right)
        btns2.pack(fill="x", pady=(4, 0))
        ttk.Button(btns2, text="⭐ Рекомендуемые настройки",
                   command=self.apply_recommended).pack(side="left")
        ttk.Button(btns2, text="Проверка сигнала", command=self.diagnose).pack(side="left", padx=6)
        ttk.Button(btns2, text="Открыть папку", command=self.open_folder).pack(side="left", padx=6)
        ttk.Button(btns2, text="Загрузить профиль", command=self.load_profile).pack(side="right")
        ttk.Button(btns2, text="Сохранить профиль", command=self.save_profile).pack(side="right", padx=6)

        self.lbl_status = ttk.Label(right, text="Готово", anchor="w",
                                    relief="sunken", padding=3)
        self.lbl_status.pack(fill="x", pady=(6, 0))
        self.lbl_stats = ttk.Label(right, text="", anchor="w")
        self.lbl_stats.pack(fill="x")

    # ---------------- настройки ----------------
    def load_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if k in self.s:
                    self.s[k] = v
        except Exception:  # noqa: BLE001
            pass

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.s, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass

    def update_estimate(self, s=None):
        s = s or self.collect()
        gb = estimate_gb_per_hour(s)
        mbps = estimate_mbps(s)
        line1 = "≈ %s/час · ≈ %s за 3-часовую кассету (%.1f Мбит/с)" % (
            fmt_gb(gb), fmt_gb(gb * 3.0), mbps)
        line2 = ""
        try:
            probe = s["out_dir"]
            while probe and not os.path.isdir(probe):
                probe = os.path.dirname(probe)
            free = shutil.disk_usage(probe or ".").free
            hours = free / (mbps * 1e6 / 8.0 * 3600.0) if mbps > 0 else 0
            line2 = "Свободно %s — при этих настройках хватит на ~%.1f ч" % (
                fmt_gb(free / 1024.0 ** 3), hours)
        except Exception:  # noqa: BLE001
            pass
        self.lbl_estimate.configure(text=line1 + ("\n" + line2 if line2 else ""))

    def apply_recommended(self):
        s = dict(self.s)
        s.update(RECOMMENDED)
        self.apply_settings(s)
        self.set_status("Поставлены рекомендуемые настройки: H.264 CRF 16, bwdif")

    def on_sound_toggle(self):
        """Колонки вкл/выкл. Запись в файл при этом не меняется — звук пишется всегда."""
        on = bool(self.var_audio_on.get())
        self.s["monitor"] = on
        if on:
            self.monitor.set_enabled(True)
        else:
            self.monitor.set_enabled(False)
        self.save_settings()
        self.set_status("Звук в колонках: " + ("включён" if on else "выключен"))

    @staticmethod
    def _pin_to_index(pin):
        try:
            return INPUT_PINS.index(int(pin))
        except (ValueError, TypeError):
            return 0

    def resolve_pin(self, s):
        """Если выбран «Авто» — пробуем найти живой вход."""
        if int(s.get("input_pin", PIN_AUTO)) != PIN_AUTO:
            return s
        self.set_status("Определяю вход (композит / S-Video)…")
        self.update_idletasks()
        best, scores = probe_inputs(s)
        if best is None:
            self.set_status("Сигнал не найден ни на одном входе — проверьте кабель "
                            "и что магнитофон включён")
            s["input_pin"] = PIN_COMPOSITE
        else:
            s["input_pin"] = best
            self.set_status("Вход определён: " +
                            ("композит" if best == PIN_COMPOSITE else "S-Video"))
        return s

    def _open_once(self, settings, seconds=0.6):
        """Один короткий проход открытия устройства — «дёргает» чип."""
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error"] + dshow_args(settings)
        cmd += ["-t", str(seconds), "-f", "null", "-"]
        run_quiet(cmd, timeout=20)

    def reinit_signal(self, auto=False):
        """Лечит глюк, когда Rombica отдаёт чёрный экран до смены стандарта.

        Открываем устройство в «чужом» стандарте и сразу возвращаемся назад —
        чип заново ловит развёртку.
        """
        if self.recorder:
            return
        s = self.collect()
        other = dict(s)
        if s["standard"] == "NTSC":
            other["width"], other["height"], other["fps"] = 720, 576, 25
        else:
            other["width"], other["height"], other["fps"] = 720, 480, 29.97
        self.set_status("Переинициализирую захват…")
        self.update_idletasks()
        was_preview = self.preview is not None
        if was_preview:
            self.preview.stop()
            self.preview = None
        time.sleep(0.4)
        self._open_once(other)
        time.sleep(0.3)
        self._open_once(s)
        time.sleep(0.2)
        self._no_signal_since = None
        if not auto:
            self._auto_reinit_done = False
        if was_preview:
            self.start_preview()
        self.set_status("Сигнал переинициализирован")

    def detect_input(self):
        if self.preview or self.recorder:
            messagebox.showinfo("Определить вход", "Сначала остановите предпросмотр.")
            return
        s = dict(self.s)
        s["input_pin"] = PIN_AUTO
        self.set_status("Проверяю входы…")
        self.update_idletasks()
        best, scores = probe_inputs(s)
        detail = "\n".join(
            "  пин %d (%s): яркость %.0f, разброс %.2f" %
            (p, "композит" if p == PIN_COMPOSITE else "S-Video", m, v)
            for p, (m, v) in sorted(scores.items()))
        if best is None:
            messagebox.showwarning("Определить вход",
                                   "Живого сигнала нет.\n" + detail)
            self.set_status("Сигнал не найден")
            return
        self.s["input_pin"] = best
        self.cb_input.current(self._pin_to_index(best))
        self.on_change()
        name = "композит (тюльпаны)" if best == PIN_COMPOSITE else "S-Video"
        messagebox.showinfo("Определить вход", "Выбран вход: %s\n\n%s" % (name, detail))
        self.set_status("Вход: " + name)

    def collect(self):
        s = dict(self.s)
        s["audio_enabled"] = bool(s.get("audio_device"))   # в файл звук пишем всегда
        s["monitor"] = bool(self.var_audio_on.get())
        s["video_device"] = self.cb_video.get() or self.s["video_device"]
        s["audio_device"] = self.cb_audio.get() or self.s["audio_device"]
        s["input_pin"] = INPUT_PINS[max(0, self.cb_input.current())]
        s["standard"] = self.cb_std.get()
        if s["standard"] == "NTSC":
            s["width"], s["height"], s["fps"] = 720, 480, 29.97
        else:
            # PAL и SECAM: у этого чипа они идут с одинаковой развёрткой 625/50
            s["width"], s["height"], s["fps"] = 720, 576, 25
        meta = CODECS[self.cb_codec.get()]
        s["codec"] = meta["id"]
        s["crf"] = int(self.var_crf.get())
        s["audio_codec"] = self.cb_acodec.get()
        s["out_dir"] = self.var_out.get()
        s["tape_name"] = self.var_tape.get() or "кассета"
        s["f_bake"] = bool(self.var_bake.get())
        s["record_preview"] = bool(self.var_rprev.get())
        s["f_deint"] = "" if self.cb_deint.get() == "нет" else self.cb_deint.get()
        for key in ("f_denoise", "f_chroma_dn", "f_sharpen", "f_bright",
                    "f_contrast", "f_saturation", "f_band", "f_side"):
            s[key] = float(getattr(self, "var_" + key).get())
        s["f_scale"] = self.cb_scale.get()
        s["audio_gain"] = float(self.var_audio_gain.get())
        return s

    def on_change(self):
        s = self.collect()
        self.s = s
        lo, hi, _ = CRF_RANGE.get(s["codec"], (10, 28, 16))
        self.sc_crf.configure(from_=lo, to=hi)
        self.lbl_crf.configure(text="CRF %d" % s["crf"])
        self.lbl_audio_gain.configure(text="%+.1f" % s["audio_gain"])
        self.lbl_codec_info.configure(text=CODEC_INFO.get(s["codec"], ""))
        self.lbl_deint_info.configure(text=DEINT_INFO.get(s.get("f_deint", ""), ""))
        self.update_estimate(s)
        for key in ("f_denoise", "f_chroma_dn", "f_sharpen", "f_bright",
                    "f_contrast", "f_saturation", "f_band", "f_side"):
            lbl = getattr(self, "lbl_" + key, None)
            if lbl:
                fmt = "%.1f" if key == "f_sharpen" else (
                    "%.2f" if key in ("f_bright", "f_contrast", "f_saturation") else "%.0f")
                lbl.configure(text=fmt % s[key])
        self.save_settings()
        # живое применение к предпросмотру (с задержкой, чтобы не дёргать ffmpeg)
        if self.preview:
            if self._restart_job:
                try:
                    self.after_cancel(self._restart_job)
                except Exception:  # noqa: BLE001
                    pass
            self._restart_job = self.after(450, self.restart_preview)

    def on_std(self):
        self.on_change()

    def on_codec(self):
        cid = CODECS[self.cb_codec.get()]["id"]
        lo, hi, dflt = CRF_RANGE.get(cid, (10, 28, 16))
        self.var_crf.set(dflt)
        self.on_change()

    def on_video_dev(self):
        self.on_change()

    def pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.var_out.get() or ".")
        if d:
            self.var_out.set(d)
            self.on_change()

    def open_folder(self):
        d = self.var_out.get()
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    # ---------------- устройства ----------------
    def refresh_devices(self):
        if not FFMPEG:
            messagebox.showerror("ffmpeg не найден",
                                 "Положите ffmpeg.exe рядом с программой "
                                 "или добавьте его в PATH.")
            return
        v, a = list_dshow_devices()
        self.cb_video["values"] = v
        self.cb_audio["values"] = a
        if v:
            pref = next((x for x in v if "Polaris" in x), v[0])
            self.cb_video.set(self.s["video_device"] if self.s["video_device"] in v else pref)
        if a:
            pref = next((x for x in a if "Polaris" in x), a[0])
            self.cb_audio.set(self.s["audio_device"] if self.s["audio_device"] in a else pref)
        self.on_change()

    # ---------------- предпросмотр ----------------
    def toggle_preview(self):
        if self.preview:
            self.stop_preview()
        else:
            self.start_preview()

    def _preview_target_width(self):
        """Ширина предпросмотра под текущий размер окна (не больше 1280)."""
        try:
            bw = self.lbl_video.winfo_width()
            bh = self.lbl_video.winfo_height()
        except Exception:  # noqa: BLE001
            bw, bh = 640, 480
        if bw < 200 or bh < 150:            # окно ещё не отрисовано
            bw, bh = 640, 480
        w = min(bw - 8, int((bh - 8) * 4 / 3), 1100)
        w = max(320, int(w) // 2 * 2)
        return w

    def _on_video_resize(self, event):
        if self.preview is None:
            return
        if abs(event.width - getattr(self, "_preview_w", 0)) < 40:
            return
        if getattr(self, "_resize_job", None):
            try:
                self.after_cancel(self._resize_job)
            except Exception:  # noqa: BLE001
                pass
        self._resize_job = self.after(600, self._restart_for_resize)

    def _restart_for_resize(self):
        self._resize_job = None
        if self.preview:
            self.restart_preview()

    def start_preview(self):
        if self.recorder or self.preview:
            return
        s = self.resolve_pin(self.collect())
        s["preview_width"] = self._preview_target_width()
        self._preview_w = s["preview_width"]
        self.s = s
        self.preview = Preview(s, self.push_frame, self.push_level,
                               self.on_preview_error,
                               on_audio=self.monitor.feed)
        if self.preview.start():
            self.monitor.set_enabled(bool(self.var_audio_on.get()))
            self.btn_prev.configure(text="■  Стоп предпросмотра")
            self.btn_rec.configure(state="normal")
            self.set_status("Предпросмотр: %dx%d, %s" % (s["width"], s["height"], s["standard"]))
        else:
            self.preview = None

    def on_preview_error(self, msg):
        self.events.put(("preview_gone", msg))

    def _preview_gone(self, msg):
        if self.preview:
            self.preview.stop()
            self.preview = None
        self.monitor.set_enabled(False)
        self.btn_prev.configure(text="▶  Предпросмотр")
        self.btn_rec.configure(state="disabled")
        self.lbl_video.configure(image="", text="Нет сигнала")
        self.photo = None
        self.set_status(msg)

    def stop_preview(self):
        if self.preview:
            self.preview.stop()
            self.preview = None
            if not self.recorder:
                self.monitor.set_enabled(False)
        self.btn_prev.configure(text="▶  Предпросмотр")
        self.btn_rec.configure(state="disabled")
        self.lbl_video.configure(image="", text="Предпросмотр выключен")
        self.photo = None
        self.set_status("Предпросмотр остановлен")

    def restart_preview(self):
        self._restart_job = None
        if self.preview:
            self.preview.stop()
            self.preview = None
            self.after(120, self.start_preview)

    def push_frame(self, data, w, h):
        if self.frame_q.full():
            try:
                self.frame_q.get_nowait()
            except queue.Empty:
                pass
        self.frame_q.put((data, w, h))

    def push_level(self, rms, peak):
        import math
        self.level = 20.0 * math.log10(max(rms, 1.0) / 32768.0)   # дБ
        pk = 20.0 * math.log10(max(peak, 1.0) / 32768.0)
        self.peak = max(self.peak - 0.4, pk)
        self.audio_last = time.time()
        self.audio_loud = rms > 40.0

    # ---------------- запись ----------------
    def toggle_record(self):
        if self.recorder:
            self._stop_recording()
            return
        s = self.collect()
        if int(s.get("input_pin", PIN_AUTO)) == PIN_AUTO:
            s = self.resolve_pin(s)
        self.s = s
        os.makedirs(s["out_dir"], exist_ok=True)
        free = shutil.disk_usage(s["out_dir"]).free
        if free < 2 * 1024 ** 3:
            if not messagebox.askyesno("Мало места",
                                       "Свободно %.1f ГБ. Продолжить?" % (free / 1024 ** 3)):
                return
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        name = s["name_template"].format(date=stamp, tape=s["tape_name"])
        name = re.sub(r'[\\/:*?"<>|]', "_", name)
        path = os.path.join(s["out_dir"], name + "." + output_ext(s))

        self.stop_preview()
        self.recorder = Recorder(s, path, self.push_frame, self.on_rec_stats,
                                 lambda ok, info: self.on_rec_done(ok, info, path),
                                 self.push_level, self.monitor.feed)
        if self.recorder.start():
            self.monitor.set_enabled(bool(self.var_audio_on.get()))
            self.btn_rec.configure(text="■  Стоп записи", state="normal")
            self.btn_prev.configure(state="disabled")
            self.rec_start = time.time()
            self.set_status("Запись: " + path)

    def _stop_recording(self):
        """Остановка в отдельном потоке, чтобы окно не подвисало."""
        rec = self.recorder
        if not rec:
            return
        self.btn_rec.configure(state="disabled", text="■  Останавливаю…")
        self.set_status("Останавливаю запись, дописываю файл…")
        threading.Thread(target=rec.stop, daemon=True).start()

    def on_rec_stats(self, elapsed, size):
        mbps = size * 8 / elapsed / 1e6 if elapsed > 0 else 0
        text = "%s   %s   %.1f Мбит/с" % (hms(elapsed), human_size(size), mbps)
        self.events.put(("stats", text))

    def on_rec_done(self, ok, info, path):
        self.events.put(("done", (ok, info, path)))

    def _finish_record(self, ok, info, path):
        self.recorder = None
        self.btn_rec.configure(text="●  Запись", state="normal")
        self.btn_prev.configure(state="normal")
        self.level = -70.0
        if isinstance(info, tuple):
            elapsed, size, rc = info
            if ok:
                self.set_status("Готово: %s (%s, %s)" % (os.path.basename(path),
                                                         hms(elapsed), human_size(size)))
            else:
                msg = "ffmpeg вернул код %s. См. %s" % (rc, LOG_FILE)
                self.set_status(msg)
                messagebox.showwarning("Запись завершилась с ошибкой", msg)
        else:
            self.set_status(str(info))
        # после записи возвращаем предпросмотр, чтобы устройство не простаивало
        if self.preview is None:
            self.after(400, self.start_preview)

    # ---------------- снимок / диагностика ----------------
    def snapshot(self):
        """Снимок в полном разрешении: на время снимка устройство освобождается."""
        s = self.collect()
        if int(s.get("input_pin", PIN_AUTO)) == PIN_AUTO:
            s = self.resolve_pin(s)
        d = self.var_out.get()
        os.makedirs(d, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(d, "snap_%s.png" % stamp)

        was_preview = self.preview is not None
        if was_preview:
            self.stop_preview()
            time.sleep(0.3)
        vf = build_filters(s, False)
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"] + dshow_args(s)
        cmd += ["-frames:v", "1", "-vf", vf + ",scale=in_range=tv:out_range=pc",
                "-update", "1", path]
        self.set_status("Делаю снимок…")
        self.update_idletasks()
        rc, err = run_quiet(cmd, timeout=45)
        if rc == 0 and os.path.exists(path):
            self.set_status("Снимок: " + path)
        else:
            self.set_status("Снимок не удался: " + err.strip()[:200])
        if was_preview:
            self.after(200, self.start_preview)

    def diagnose(self):
        if self.preview or self.recorder:
            messagebox.showinfo("Проверка", "Остановите предпросмотр/запись.")
            return
        s = self.collect()
        if int(s.get("input_pin", PIN_AUTO)) == PIN_AUTO:
            s = self.resolve_pin(s)
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "info"] + dshow_args(s)
        cmd += ["-t", "4"]
        if s.get("audio_enabled") and s.get("audio_device"):
            cmd += ["-af", "volumedetect"]
        cmd += ["-vf", "signalstats,metadata=print", "-f", "null", "-"]
        self.set_status("Проверка сигнала…")
        self.update_idletasks()
        rc, err = run_quiet(cmd, timeout=60)
        self.show_report(err)

    def show_report(self, err):
        lines = self.build_report(err)
        messagebox.showinfo("Проверка сигнала", "\n".join(lines))
        self.set_status("Проверка завершена")

    def build_report(self, err):
        def avg(key):
            vals = [float(m) for m in re.findall(key + r"=([\d.]+)", err)]
            return sum(vals) / len(vals) if vals else 0.0

        ymin, ymax = avg("YMIN"), avg("YMAX")
        yavg = avg("YAVG")
        sat = avg("SATAVG")
        uavg, vavg = avg("UAVG"), avg("VAVG")
        vm = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", err)
        mean = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", err)
        frames = len(re.findall(r"pts_time", err))

        lines = []
        lines.append("Яркость (Y): средн %.0f, диапазон %.0f…%.0f" % (yavg, ymin, ymax))
        if ymax < 200:
            lines.append("  ⚠ Картинка тусклая — поднимите яркость/контраст или уровень на магнитофоне.")
        if ymin > 40:
            lines.append("  ⚠ Нет глубокого чёрного — картинка «серая».")
        lines.append("Насыщенность: %.0f" % sat)
        if sat < 8:
            lines.append("  ⚠ Почти без цвета: если запись не чёрно-белая — проверьте кабель "
                         "и режим «цвет» на магнитофоне.")
        lines.append("Баланс цвета: U=%.0f V=%.0f (норма 128)" % (uavg, vavg))
        if abs(uavg - 128) > 25 or abs(vavg - 128) > 25:
            lines.append("  ⚠ Сильный сдвиг цвета — кассета «поплыла», поможет правка оттенка.")
        if mean and mean.group(1) != "-inf":
            lines.append("Звук: средний %s дБ, пик %s дБ" % (mean.group(1),
                                                             vm.group(1) if vm else "?"))
            if float(vm.group(1)) < -60:
                lines.append("  ⚠ Звука нет: проверьте белый/красный тюльпан и громкость магнитофона.")
            elif float(vm.group(1)) > -1.0:
                lines.append("  ⚠ Пики в 0 дБ — звук перегружен. Убавьте громкость на магнитофоне "
                             "или поставьте «Звук, дБ» в минус.")
        else:
            lines.append("Звук: тишина (нет сигнала)")
        lines.append("Кадров за 4 с: %d (ожидалось ~%d)" % (frames, int(4 * s_fps(self.s))))
        if frames < 4 * s_fps(self.s) * 0.9:
            lines.append("  ⚠ Возможны пропуски кадров.")
        return lines

    # ---------------- профили ----------------
    def save_profile(self):
        s = self.collect()
        name = self.var_tape.get() or "профиль"
        p = filedialog.asksaveasfilename(
            initialdir=PROFILE_DIR, defaultextension=".json",
            initialfile=re.sub(r'[\\/:*?"<>|]', "_", name) + ".json",
            filetypes=[("Профиль", "*.json")])
        if not p:
            return
        with open(p, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
        self.set_status("Профиль сохранён: " + p)

    def load_profile(self):
        p = filedialog.askopenfilename(initialdir=PROFILE_DIR,
                                       filetypes=[("Профиль", "*.json")])
        if not p:
            return
        with open(p, "r", encoding="utf-8") as f:
            s = json.load(f)
        self.apply_settings(s)
        self.set_status("Профиль загружен: " + p)

    def apply_settings(self, s):
        for k, v in s.items():
            if k in self.s:
                self.s[k] = v
        self.var_audio_on.set(bool(self.s.get("monitor", True)))
        self.cb_input.current(self._pin_to_index(self.s["input_pin"]))
        self.cb_std.set(self.s["standard"])
        cid = self.s["codec"]
        for label, meta in CODECS.items():
            if meta["id"] == cid:
                self.cb_codec.set(label)
        self.var_crf.set(self.s["crf"])
        self.cb_acodec.set(self.s["audio_codec"])
        self.var_out.set(self.s["out_dir"])
        self.var_tape.set(self.s["tape_name"])
        self.var_bake.set(self.s["f_bake"])
        self.var_rprev.set(self.s["record_preview"])
        self.cb_deint.set(self.s["f_deint"] or "нет")
        for key in ("f_denoise", "f_chroma_dn", "f_sharpen", "f_bright",
                    "f_contrast", "f_saturation", "f_band", "f_side"):
            getattr(self, "var_" + key).set(float(self.s[key]))
        self.cb_scale.set(self.s["f_scale"])
        self.var_audio_gain.set(float(self.s.get("audio_gain", 0)))
        self.on_change()

    # ---------------- цикл ----------------
    def set_status(self, text):
        self.lbl_status.configure(text=text)

    def _tick(self):
        if not self.winfo_exists():
            return
        self._check_signal()
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "stats":
                self.lbl_stats.configure(text=payload)
            elif kind == "done":
                self._finish_record(*payload)
            elif kind == "preview_gone":
                self._preview_gone(payload)
        try:
            data, w, h = self.frame_q.get_nowait()
            img = Image.frombytes("RGB", (w, h), data)
            self.last_frame = (data, w, h)
            self._last_frame_at = time.time()
            self._auto_reinit_done = False
            self._fps_frames = getattr(self, "_fps_frames", 0) + 1
            sample = data[::811]
            self._last_spread = max(sample) - min(sample) if sample else 0
            self.photo = ImageTk.PhotoImage(img)
            self.lbl_video.configure(image=self.photo, text="")
        except queue.Empty:
            pass
        cw = max(1, self.canvas_meter.winfo_width())
        # шкала -60…0 дБ
        v = max(0.0, min(1.0, (self.level + 60.0) / 60.0))
        p = max(0.0, min(1.0, (self.peak + 60.0) / 60.0))
        col = "#3ecf3e" if self.peak < -12 else ("#e8c33e" if self.peak < -3 else "#e34b4b")
        if self.level <= -59:
            col = "#3a3a3a"
        self.canvas_meter.coords(self.bar, 0, 0, int(cw * v), 14)
        self.canvas_meter.itemconfigure(self.bar, fill=col)
        now_t = time.time()
        if now_t - self._fps_t0 >= 1.0:
            dt = now_t - self._fps_t0
            self._fps = self._fps_frames / dt if dt > 0 else 0
            self._fps_t0 = now_t
            self._fps_frames = 0
        self._update_audio_hint()
        self._tick_n = getattr(self, "_tick_n", 0) + 1
        if self._tick_n % 250 == 0:          # раз в 10 секунд обновляем место на диске
            try:
                self.update_estimate()
            except Exception:  # noqa: BLE001
                pass
        self._tick_job = self.after(12, self._tick)

    def _update_audio_hint(self):
        """Пишет про тишину только если её действительно нет несколько секунд,
        и убирает надпись, как только звук появился."""
        if self.recorder:
            return                      # во время записи тут счётчик, не трогаем
        if not self.preview:
            self.audio_silent_since = None
            self.lbl_stats.configure(text="")
            return
        now = time.time()
        if getattr(self, "audio_loud", True):
            self.audio_silent_since = None
            self.lbl_stats.configure(text="предпросмотр: %.0f кадр/с" %
                                     getattr(self, "_fps", 0))
            return
        if self.audio_silent_since is None:
            self.audio_silent_since = now
        if now - self.audio_silent_since > 4.0:
            self.lbl_stats.configure(
                text="звук: тишина — проверьте белый/красный тюльпан "
                     "и громкость магнитофона")
            return
        self.lbl_stats.configure(text="предпросмотр: %.0f кадр/с" %
                                 getattr(self, "_fps", 0))

    def _check_signal(self):
        """Если кадров нет совсем долго — один раз пробуем переинициализировать чип.

        Срабатывает только на полное отсутствие кадров: любая эвристика по
        «серой картинке» ошибается на тёмных сценах и запускает ложные
        перезапуски.
        """
        if not self.preview or self.recorder:
            self._no_signal_since = None
            return
        now = time.time()
        if self._last_frame_at and (now - self._last_frame_at) < 2.0:
            self._no_signal_since = None      # кадры идут — всё хорошо
            return
        if self._no_signal_since is None:
            self._no_signal_since = now
            return
        if now - self._no_signal_since < 8.0:
            return
        if getattr(self, "_auto_reinit_done", False):
            self.set_status("Нет сигнала. Проверьте кабель и нажмите «⟳ Перезапустить»")
            return
        self._auto_reinit_done = True
        self.set_status("Картинки нет — переинициализирую захват…")
        self.after(50, lambda: self.reinit_signal(auto=True))

    def on_close(self):
        try:
            if getattr(self, "_tick_job", None):
                try:
                    self.after_cancel(self._tick_job)
                except Exception:  # noqa: BLE001
                    pass
            if self.recorder:
                self.recorder.stop()
            if self.preview:
                self.preview.stop()
            self.monitor.set_enabled(False)
        finally:
            self.save_settings()
            self.destroy()
