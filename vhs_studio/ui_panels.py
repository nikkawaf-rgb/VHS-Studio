# -*- coding: utf-8 -*-
"""Панели интерфейса: устройство, запись, обработка и область предпросмотра.

Каждая панель строит свои виджеты и хранит их как атрибуты с прежними
именами (cb_*, var_*, lbl_*), поэтому App общается с ними единообразно.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .config import (AUDIO_CODECS, CODECS, INPUT_LABELS, INPUT_PINS,
                     PIN_COMPOSITE)


class DevicePanel(ttk.Frame):
    """Выбор устройств, входа и стандарта."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        s = app.s

        dev = ttk.LabelFrame(self, text="Устройство", padding=6)
        dev.pack(fill="x", pady=(0, 6))

        ttk.Label(dev, text="Видео:").grid(row=0, column=0, sticky="w")
        self.cb_video = ttk.Combobox(dev, width=30, state="readonly")
        self.cb_video.grid(row=0, column=1, sticky="we", pady=1)
        self.cb_video.bind("<<ComboboxSelected>>", lambda e: app.on_change())

        ttk.Label(dev, text="Аудио:").grid(row=1, column=0, sticky="w")
        self.cb_audio = ttk.Combobox(dev, width=30, state="readonly")
        self.cb_audio.grid(row=1, column=1, sticky="we", pady=1)
        self.cb_audio.bind("<<ComboboxSelected>>", lambda e: app.on_change())

        self.var_audio_on = tk.BooleanVar(value=bool(s.get("monitor", True)))
        ttk.Button(dev, text="Обновить", command=app.refresh_devices).grid(
            row=3, column=1, sticky="e", pady=(2, 0))

        src = ttk.LabelFrame(self, text="Источник сигнала", padding=6)
        src.pack(fill="x", pady=(0, 6))
        self.src = src

        ttk.Label(src, text="Вход:").grid(row=0, column=0, sticky="w")
        self.cb_input = ttk.Combobox(src, width=16, state="readonly",
                                     values=INPUT_LABELS)
        self.cb_input.current(self._pin_index(s["input_pin"]))
        self.cb_input.grid(row=0, column=1, sticky="we", pady=1)
        self.cb_input.bind("<<ComboboxSelected>>", lambda e: app.on_change())
        ttk.Button(src, text="Определить", width=11,
                   command=app.detect_input).grid(row=0, column=2, sticky="w")

        ttk.Label(src, text="Стандарт:").grid(row=1, column=0, sticky="w")
        self.cb_std = ttk.Combobox(src, width=16, state="readonly",
                                   values=["PAL", "SECAM", "NTSC"])
        self.cb_std.set(s["standard"])
        self.cb_std.grid(row=1, column=1, sticky="we", pady=1)
        self.cb_std.bind("<<ComboboxSelected>>", lambda e: app.on_std())

        self.btn_reinit = ttk.Button(src, text="⟳ Перезапустить",
                                     command=app.reinit_signal)
        self.btn_reinit.grid(row=2, column=0, columnspan=2, sticky="we",
                             pady=(4, 0))

    @staticmethod
    def _pin_index(pin):
        try:
            return INPUT_PINS.index(int(pin))
        except (ValueError, TypeError):
            return 0

    def collect(self, out):
        out["video_device"] = self.cb_video.get() or out.get("video_device", "")
        out["audio_device"] = self.cb_audio.get() or out.get("audio_device", "")
        out["input_pin"] = INPUT_PINS[max(0, self.cb_input.current())]
        out["standard"] = self.cb_std.get()
        out["monitor"] = bool(self.var_audio_on.get())
        return out

    def apply(self, s):
        self.cb_input.current(self._pin_index(s["input_pin"]))
        self.cb_std.set(s["standard"])
        self.var_audio_on.set(bool(s.get("monitor", True)))


class RecordPanel(ttk.LabelFrame):
    """Кодек, качество, звук и место сохранения."""

    def __init__(self, master, app):
        super().__init__(master, text="Запись", padding=6)
        self.app = app
        s = app.s

        ttk.Label(self, text="Кодек:").grid(row=0, column=0, sticky="w")
        self.cb_codec = ttk.Combobox(self, width=30, state="readonly",
                                     values=list(CODECS.keys()))
        for label, meta in CODECS.items():
            if meta["id"] == s["codec"]:
                self.cb_codec.set(label)
                break
        else:
            self.cb_codec.current(1)
        self.cb_codec.grid(row=0, column=1, columnspan=2, sticky="we", pady=1)
        self.cb_codec.bind("<<ComboboxSelected>>", lambda e: app.on_codec())

        self.lbl_codec_info = ttk.Label(self, text="", wraplength=300,
                                        foreground="#444444", justify="left")
        self.lbl_codec_info.grid(row=1, column=0, columnspan=3, sticky="w",
                                 pady=(0, 3))

        ttk.Label(self, text="Качество:").grid(row=2, column=0, sticky="w")
        self.var_crf = tk.IntVar(value=s["crf"])
        self.sc_crf = ttk.Scale(self, from_=10, to=28, orient="horizontal",
                                variable=self.var_crf,
                                command=lambda v: app.on_change())
        self.sc_crf.grid(row=2, column=1, sticky="we", pady=1)
        self.lbl_crf = ttk.Label(self, text="CRF 16", width=8)
        self.lbl_crf.grid(row=2, column=2, sticky="w")

        ttk.Label(self, text="Звук:").grid(row=3, column=0, sticky="w")
        self.cb_acodec = ttk.Combobox(self, width=22, state="readonly",
                                      values=list(AUDIO_CODECS.keys()))
        if s["audio_codec"] in AUDIO_CODECS:
            self.cb_acodec.set(s["audio_codec"])
        else:
            self.cb_acodec.current(0)
        self.cb_acodec.grid(row=3, column=1, columnspan=2, sticky="we", pady=1)
        self.cb_acodec.bind("<<ComboboxSelected>>", lambda e: app.on_change())

        ttk.Label(self, text="Папка:").grid(row=4, column=0, sticky="w")
        self.var_out = tk.StringVar(value=s["out_dir"])
        entry = ttk.Entry(self, textvariable=self.var_out, width=22)
        entry.grid(row=4, column=1, sticky="we", pady=1)
        entry.bind("<FocusOut>", lambda ev: app.on_change())
        ttk.Button(self, text="…", width=3,
                   command=app.pick_dir).grid(row=4, column=2)

        ttk.Label(self, text="Имя:").grid(row=5, column=0, sticky="w")
        name_box = ttk.Frame(self)
        name_box.grid(row=5, column=1, columnspan=2, sticky="we")
        self.var_tape = tk.StringVar(value=s["tape_name"])
        name_entry = ttk.Entry(name_box, textvariable=self.var_tape, width=14)
        name_entry.pack(side="left", fill="x", expand=True)
        name_entry.bind("<KeyRelease>", lambda ev: app.on_change())

        self.var_bake = tk.BooleanVar(value=s["f_bake"])
        ttk.Checkbutton(self, text="Применять обработку при записи\n"
                                   "(иначе — только в предпросмотре)",
                        variable=self.var_bake,
                        command=app.on_change).grid(row=6, column=0, columnspan=3,
                                                    sticky="w", pady=(4, 0))

        self.var_rprev = tk.BooleanVar(value=s["record_preview"])
        ttk.Checkbutton(self, text="Показывать картинку во время записи",
                        variable=self.var_rprev,
                        command=app.on_change).grid(row=7, column=0, columnspan=3,
                                                    sticky="w")

        stop_box = ttk.Frame(self)
        stop_box.grid(row=8, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self.var_auto_stop = tk.BooleanVar(value=bool(s.get("auto_stop", True)))
        ttk.Checkbutton(stop_box, text="Останавливать запись, если нет",
                        variable=self.var_auto_stop,
                        command=app.on_change).pack(side="left")
        self.var_auto_stop_min = tk.IntVar(value=int(s.get("auto_stop_min", 15)))
        ttk.Spinbox(stop_box, from_=1, to=240, width=4,
                    textvariable=self.var_auto_stop_min,
                    command=app.on_change).pack(side="left", padx=4)
        ttk.Label(stop_box, text="мин звука и картинки").pack(side="left")

        # предупреждение: кодек не умеет чересстрочность, а обработка выключена
        self.lbl_warn = ttk.Label(self, text="", foreground="#b00020",
                                  wraplength=300, justify="left")
        self.lbl_warn.grid(row=9, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.lbl_estimate = ttk.Label(self, text="", foreground="#1a5fb4")
        self.lbl_estimate.grid(row=10, column=0, columnspan=3, sticky="w",
                               pady=(2, 0))

    def collect(self, out):
        meta = CODECS[self.cb_codec.get()]
        out["codec"] = meta["id"]
        out["crf"] = int(self.var_crf.get())
        out["audio_codec"] = self.cb_acodec.get()
        out["out_dir"] = self.var_out.get()
        out["tape_name"] = self.var_tape.get() or "кассета"
        out["f_bake"] = bool(self.var_bake.get())
        out["record_preview"] = bool(self.var_rprev.get())
        return out

    def apply(self, s):
        for label, meta in CODECS.items():
            if meta["id"] == s["codec"]:
                self.cb_codec.set(label)
                break
        self.var_crf.set(s["crf"])
        if s["audio_codec"] in AUDIO_CODECS:
            self.cb_acodec.set(s["audio_codec"])
        self.var_out.set(s["out_dir"])
        self.var_tape.set(s["tape_name"])
        self.var_bake.set(s["f_bake"])
        self.var_rprev.set(s["record_preview"])


class FilterPanel(ttk.LabelFrame):
    """Деинтерлейс, шумодав, цвет, обрезка, масштаб и усиление звука."""

    SLIDERS = (
        ("f_denoise", "Шум (яркость)", 0, 20, "%.0f"),
        ("f_chroma_dn", "Шум (цвет)", 0, 25, "%.0f"),
        ("f_sharpen", "Резкость", 0, 3, "%.1f"),
        ("f_bright", "Яркость", -0.3, 0.3, "%.2f"),
        ("f_contrast", "Контраст", 0.6, 1.6, "%.2f"),
        ("f_saturation", "Насыщенность", 0.5, 1.8, "%.2f"),
        ("f_band", "Обрезка низа, px", 0, 40, "%.0f"),
        ("f_side", "Обрезка боков, px", 0, 20, "%.0f"),
    )

    def __init__(self, master, app):
        super().__init__(master, text="Обработка (видно в предпросмотре)",
                         padding=6)
        self.app = app
        s = app.s

        ttk.Label(self, text="Деинтерлейс:").grid(row=0, column=0, sticky="w")
        self.cb_deint = ttk.Combobox(
            self, width=18, state="readonly",
            values=["bwdif", "yadif", "estdif", "nnedi", "w3fdif", "нет"])
        self.cb_deint.set(s["f_deint"])
        self.cb_deint.grid(row=0, column=1, sticky="we", pady=1)
        self.cb_deint.bind("<<ComboboxSelected>>", lambda e: app.on_change())

        self.lbl_deint_info = ttk.Label(self, text="", wraplength=300,
                                        foreground="#444444", justify="left")
        self.lbl_deint_info.grid(row=1, column=0, columnspan=3, sticky="w",
                                 pady=(0, 3))

        row = 1
        for key, label, lo, hi, fmt in self.SLIDERS:
            row += 1
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w")
            var = tk.DoubleVar(value=float(s[key]))
            scale = ttk.Scale(self, from_=lo, to=hi, orient="horizontal",
                              variable=var, command=lambda v: app.on_change())
            scale.grid(row=row, column=1, sticky="we", pady=1)
            value = ttk.Label(self, text=fmt % float(s[key]), width=6)
            value.grid(row=row, column=2, sticky="w")
            setattr(self, "var_" + key, var)
            setattr(self, "lbl_" + key, value)

        row += 1
        ttk.Label(self, text="Масштаб:").grid(row=row, column=0, sticky="w")
        self.cb_scale = ttk.Combobox(self, width=18, state="readonly",
                                     values=["none", "960x720", "1440x1080"])
        self.cb_scale.set(s["f_scale"])
        self.cb_scale.grid(row=row, column=1, sticky="we", pady=1)
        self.cb_scale.bind("<<ComboboxSelected>>", lambda e: app.on_change())

        row += 1
        ttk.Label(self, text="Звук, дБ:").grid(row=row, column=0, sticky="w")
        self.var_audio_gain = tk.DoubleVar(value=float(s.get("audio_gain", 0)))
        self.sc_gain = ttk.Scale(self, from_=-12, to=12, orient="horizontal",
                                 variable=self.var_audio_gain,
                                 command=lambda v: app.on_change())
        self.sc_gain.grid(row=row, column=1, sticky="we", pady=1)
        self.lbl_audio_gain = ttk.Label(
            self, text="%+.1f" % float(s.get("audio_gain", 0)), width=6)
        self.lbl_audio_gain.grid(row=row, column=2, sticky="w")

    def collect(self, out):
        out["f_deint"] = "" if self.cb_deint.get() == "нет" else self.cb_deint.get()
        for key, _label, _lo, _hi, _fmt in self.SLIDERS:
            out[key] = float(getattr(self, "var_" + key).get())
        out["f_scale"] = self.cb_scale.get()
        out["audio_gain"] = float(self.var_audio_gain.get())
        return out

    def apply(self, s):
        self.cb_deint.set(s["f_deint"] or "нет")
        for key, _label, _lo, _hi, fmt in self.SLIDERS:
            getattr(self, "var_" + key).set(float(s[key]))
            getattr(self, "lbl_" + key).configure(text=fmt % float(s[key]))
        self.cb_scale.set(s["f_scale"])
        self.var_audio_gain.set(float(s.get("audio_gain", 0)))
        self.lbl_audio_gain.configure(text="%+.1f" % float(s.get("audio_gain", 0)))


class PreviewPane(ttk.Frame):
    """Картинка, индикатор уровня и кнопки действий."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app

        self.lbl_video = tk.Label(self, bg="#101010",
                                 text="Предпросмотр выключен",
                                 fg="#808080", width=64, height=24)
        self.lbl_video.pack(fill="both", expand=True)
        self.lbl_video.bind("<Configure>", app._on_video_resize)

        meter = ttk.Frame(self)
        meter.pack(fill="x", pady=6)
        ttk.Label(meter, text="Уровень звука:").pack(side="left")
        self.canvas_meter = tk.Canvas(meter, height=14, bg="#202020",
                                      highlightthickness=0)
        self.canvas_meter.pack(side="left", fill="x", expand=True, padx=6)
        self.bar = self.canvas_meter.create_rectangle(0, 0, 0, 14, fill="#3ecf3e")

        btns = ttk.Frame(self)
        btns.pack(fill="x")
        self.btn_prev = ttk.Button(btns, text="▶  Предпросмотр",
                                   command=app.toggle_preview)
        self.btn_prev.pack(side="left")
        self.btn_rec = ttk.Button(btns, text="●  Запись",
                                  command=app.toggle_record, state="disabled")
        self.btn_rec.pack(side="left", padx=6)
        self.chk_sound = ttk.Checkbutton(
            btns, text="🔊 Колонки", variable=app.dev.var_audio_on,
            command=app.on_sound_toggle)
        self.chk_sound.pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Снимок PNG",
                   command=app.snapshot).pack(side="left")
        ttk.Button(btns, text="⟳ Сигнал",
                   command=app.reinit_signal).pack(side="left", padx=6)
        self.var_search = tk.BooleanVar(value=False)
        self.chk_search = ttk.Checkbutton(btns, text="⏩ Поиск",
                                          variable=self.var_search,
                                          command=app.on_search_toggle)
        self.chk_search.pack(side="left", padx=(0, 6))

        btns2 = ttk.Frame(self)
        btns2.pack(fill="x", pady=(4, 0))
        ttk.Button(btns2, text="⭐ Рекомендуемые настройки",
                   command=app.apply_recommended).pack(side="left")
        ttk.Button(btns2, text="Проверка сигнала",
                   command=app.diagnose).pack(side="left", padx=6)
        ttk.Button(btns2, text="Открыть папку",
                   command=app.open_folder).pack(side="left", padx=6)
        ttk.Button(btns2, text="Загрузить профиль",
                   command=app.load_profile).pack(side="right")
        ttk.Button(btns2, text="Сохранить профиль",
                   command=app.save_profile).pack(side="right", padx=6)

        self.lbl_status = ttk.Label(self, text="Готово", anchor="w",
                                    relief="sunken", padding=3)
        self.lbl_status.pack(fill="x", pady=(6, 0))
        self.lbl_stats = ttk.Label(self, text="", anchor="w")
        self.lbl_stats.pack(fill="x")


__all__ = ["DevicePanel", "RecordPanel", "FilterPanel", "PreviewPane",
           "PIN_COMPOSITE"]
