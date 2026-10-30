# -*- coding: utf-8 -*-
"""Окно программы."""
from __future__ import annotations

import datetime
import json
import os
import queue
import re
import shutil
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

from .capture import AudioMonitor, Preview, Recorder, frame_is_blank
from .commands import build_filters, dshow_args, output_ext
from .config import (CODECS, CODEC_INFO, CRF_RANGE, DEINT_INFO, DEFAULTS,
                     INPUT_PINS, LOG_FILE, PIN_AUTO, PIN_COMPOSITE, PROFILE_DIR,
                     RECOMMENDED, SETTINGS_FILE, s_fps)
from .device import FFMPEG, list_dshow_devices, probe_inputs, run_quiet
from .estimates import (estimate_gb_per_hour, estimate_mbps, fmt_gb, hms,
                        human_size)
from .ui_panels import DevicePanel, FilterPanel, PreviewPane, RecordPanel


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
        self._rec_sound_at = 0.0
        self._rec_pic_at = 0.0
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

        self.dev = DevicePanel(left, self)
        self.dev.pack(fill="x", pady=(0, 6))

        self.rec = RecordPanel(left, self)
        self.rec.pack(fill="x", pady=(0, 6))

        self.filt = FilterPanel(left, self)
        self.filt.pack(fill="x")

        self.pane = PreviewPane(right, self)
        self.pane.pack(fill="both", expand=True)

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
        self.rec.lbl_estimate.configure(text=line1 + ("\n" + line2 if line2 else ""))

    def apply_recommended(self):
        s = dict(self.s)
        s.update(RECOMMENDED)
        self.apply_settings(s)
        self.set_status("Поставлены рекомендуемые настройки: H.264 CRF 16, bwdif")

    def on_search_toggle(self):
        """Режим поиска: облегчённый предпросмотр без шумодава и деинтерлейса."""
        self.s["search"] = bool(self.pane.var_search.get())
        self.save_settings()
        if self.preview:
            self.restart_preview()
        self.set_status("Режим поиска: " +
                        ("включён (быстрый предпросмотр)" if self.s["search"]
                         else "выключен"))

    def on_sound_toggle(self):
        """Колонки вкл/выкл. Запись в файл при этом не меняется — звук пишется всегда."""
        on = bool(self.dev.var_audio_on.get())
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
        self.dev.cb_input.current(self._pin_to_index(best))
        self.on_change()
        name = "композит (тюльпаны)" if best == PIN_COMPOSITE else "S-Video"
        messagebox.showinfo("Определить вход", "Выбран вход: %s\n\n%s" % (name, detail))
        self.set_status("Вход: " + name)

    def collect(self):
        s = dict(self.s)
        s["audio_enabled"] = bool(s.get("audio_device"))   # в файл звук пишем всегда
        s["monitor"] = bool(self.dev.var_audio_on.get())
        s["video_device"] = self.dev.cb_video.get() or self.s["video_device"]
        s["audio_device"] = self.dev.cb_audio.get() or self.s["audio_device"]
        s["input_pin"] = INPUT_PINS[max(0, self.dev.cb_input.current())]
        s["standard"] = self.dev.cb_std.get()
        if s["standard"] == "NTSC":
            s["width"], s["height"], s["fps"] = 720, 480, 29.97
        else:
            # PAL и SECAM: у этого чипа они идут с одинаковой развёрткой 625/50
            s["width"], s["height"], s["fps"] = 720, 576, 25
        meta = CODECS[self.rec.cb_codec.get()]
        s["codec"] = meta["id"]
        s["crf"] = int(self.rec.var_crf.get())
        s["audio_codec"] = self.rec.cb_acodec.get()
        s["out_dir"] = self.rec.var_out.get()
        s["tape_name"] = self.rec.var_tape.get() or "кассета"
        s["f_bake"] = bool(self.rec.var_bake.get())
        s["record_preview"] = bool(self.rec.var_rprev.get())
        s["auto_stop"] = bool(self.rec.var_auto_stop.get())
        s["auto_stop_min"] = int(self.rec.var_auto_stop_min.get())
        s["search"] = bool(self.pane.var_search.get())
        s["f_deint"] = "" if self.filt.cb_deint.get() == "нет" else self.filt.cb_deint.get()
        for key in ("f_denoise", "f_chroma_dn", "f_sharpen", "f_bright",
                    "f_contrast", "f_saturation", "f_band", "f_side"):
            s[key] = float(getattr(self.filt, "var_" + key).get())
        s["f_scale"] = self.filt.cb_scale.get()
        s["audio_gain"] = float(self.filt.var_audio_gain.get())
        return s

    def on_change(self):
        s = self.collect()
        self.s = s
        lo, hi, _ = CRF_RANGE.get(s["codec"], (10, 28, 16))
        self.rec.sc_crf.configure(from_=lo, to=hi)
        self.rec.lbl_crf.configure(text="CRF %d" % s["crf"])
        self.filt.lbl_audio_gain.configure(text="%+.1f" % s["audio_gain"])
        self.rec.lbl_codec_info.configure(text=CODEC_INFO.get(s["codec"], ""))
        self.filt.lbl_deint_info.configure(text=DEINT_INFO.get(s.get("f_deint", ""), ""))
        self.update_estimate(s)
        self.update_warning(s)
        for key in ("f_denoise", "f_chroma_dn", "f_sharpen", "f_bright",
                    "f_contrast", "f_saturation", "f_band", "f_side"):
            lbl = getattr(self.filt, "lbl_" + key, None)
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

    def update_warning(self, s):
        """NVENC не умеет чересстрочность: без деинтерлейса картинка «рвётся»."""
        nvenc = s["codec"] in ("hevc_nvenc", "h264_nvenc")
        has_deint = bool(s.get("f_deint"))
        if not nvenc:
            msg = ""
        elif not s.get("f_bake"):
            msg = "⚠ Видеокарта чересстрочность не сохраняет. Включите «Применять " \
                  "обработку при записи», иначе на движении будет «гребёнка»."
        elif not has_deint:
            msg = "⚠ Выбрано «нет» в деинтерлейсе — с этим кодеком получится " \
                  "гребёнка. Поставьте bwdif."
        else:
            msg = ""
        self.rec.lbl_warn.configure(text=msg)

    def on_std(self):
        self.on_change()

    def on_codec(self):
        cid = CODECS[self.rec.cb_codec.get()]["id"]
        lo, hi, dflt = CRF_RANGE.get(cid, (10, 28, 16))
        self.rec.var_crf.set(dflt)
        self.on_change()

    def on_video_dev(self):
        self.on_change()

    def pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.rec.var_out.get() or ".")
        if d:
            self.rec.var_out.set(d)
            self.on_change()

    def open_folder(self):
        d = self.rec.var_out.get()
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
        self.dev.cb_video["values"] = v
        self.dev.cb_audio["values"] = a
        if v:
            pref = next((x for x in v if "Polaris" in x), v[0])
            self.dev.cb_video.set(self.s["video_device"] if self.s["video_device"] in v else pref)
        if a:
            pref = next((x for x in a if "Polaris" in x), a[0])
            self.dev.cb_audio.set(self.s["audio_device"] if self.s["audio_device"] in a else pref)
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
            bw = self.pane.lbl_video.winfo_width()
            bh = self.pane.lbl_video.winfo_height()
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
        self._preview_started_at = time.time()
        self.preview = Preview(s, self.push_frame, self.push_level,
                               self.on_preview_error,
                               on_audio=self.monitor.feed)
        if self.preview.start():
            self._preview_retries = 0
            self.monitor.set_enabled(bool(self.dev.var_audio_on.get()))
            self.pane.btn_prev.configure(text="■  Стоп предпросмотра")
            self.pane.btn_rec.configure(state="normal")
            self.set_status("Предпросмотр: %dx%d, %s" % (s["width"], s["height"], s["standard"]))
        else:
            self.preview = None

    def on_preview_error(self, msg):
        self.events.put(("preview_gone", msg))

    def _preview_gone(self, msg):
        if self.recorder:
            return                      # идёт запись — кнопку стопа не трогаем
        # если захват не поднялся с первого раза (чип занят/не отдал поток) —
        # пробуем ещё до двух раз, прежде чем показать ошибку
        started = getattr(self, "_preview_started_at", 0)
        if time.time() - started < 5 and getattr(self, "_preview_retries", 0) < 2:
            self._preview_retries = getattr(self, "_preview_retries", 0) + 1
            if self.preview:
                self.preview.stop()
                self.preview = None
            self.set_status("Захват не поднялся, повторяю попытку…")
            self.after(1500, self.start_preview)
            return
        if self.preview:
            self.preview.stop()
            self.preview = None
        self.monitor.set_enabled(False)
        self.pane.btn_prev.configure(text="▶  Предпросмотр")
        self.pane.btn_rec.configure(state="disabled")
        self.pane.lbl_video.configure(image="", text="Нет сигнала")
        self.photo = None
        self.set_status(msg)

    def stop_preview(self):
        self._preview_retries = 0
        if self.preview:
            self.preview.stop()
            self.preview = None
            if not self.recorder:
                self.monitor.set_enabled(False)
        self.pane.btn_prev.configure(text="▶  Предпросмотр")
        self.pane.btn_rec.configure(state="disabled")
        self.pane.lbl_video.configure(image="", text="Предпросмотр выключен")
        self.photo = None
        self.set_status("Предпросмотр остановлен")

    def restart_preview(self):
        self._restart_job = None
        if self.preview:
            self.preview.stop()
            self.preview = None
            self.after(120, self.start_preview)

    def push_frame(self, data, w, h):
        if self.recorder:
            # кадр считается «живым», только если он не пустой
            if not frame_is_blank(data):
                self._rec_pic_at = time.time()
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
        if self.audio_loud and self.recorder:
            self._rec_sound_at = time.time()

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
            now = time.time()
            self._rec_sound_at = now
            self._rec_pic_at = now
            self.monitor.set_enabled(bool(self.dev.var_audio_on.get()))
            self.pane.btn_rec.configure(text="■  Стоп записи", state="normal")
            self.pane.btn_prev.configure(state="disabled")
            self.rec_start = time.time()
            self.set_status("Запись: " + path)

    def _stop_recording(self):
        """Остановка в отдельном потоке, чтобы окно не подвисало."""
        rec = self.recorder
        if not rec:
            return
        self.pane.btn_rec.configure(state="disabled", text="■  Останавливаю…")
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
        self.pane.btn_rec.configure(text="●  Запись", state="normal")
        self.pane.btn_prev.configure(state="normal")
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
        d = self.rec.var_out.get()
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
        name = self.rec.var_tape.get() or "профиль"
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
        self.dev.var_audio_on.set(bool(self.s.get("monitor", True)))
        self.dev.cb_input.current(self._pin_to_index(self.s["input_pin"]))
        self.dev.cb_std.set(self.s["standard"])
        cid = self.s["codec"]
        for label, meta in CODECS.items():
            if meta["id"] == cid:
                self.rec.cb_codec.set(label)
        self.rec.var_crf.set(self.s["crf"])
        self.rec.cb_acodec.set(self.s["audio_codec"])
        self.rec.var_out.set(self.s["out_dir"])
        self.rec.var_tape.set(self.s["tape_name"])
        self.rec.var_bake.set(self.s["f_bake"])
        self.rec.var_rprev.set(self.s["record_preview"])
        self.rec.var_auto_stop.set(bool(self.s.get("auto_stop", True)))
        self.rec.var_auto_stop_min.set(int(self.s.get("auto_stop_min", 15)))
        self.filt.cb_deint.set(self.s["f_deint"] or "нет")
        for key in ("f_denoise", "f_chroma_dn", "f_sharpen", "f_bright",
                    "f_contrast", "f_saturation", "f_band", "f_side"):
            getattr(self.filt, "var_" + key).set(float(self.s[key]))
        self.filt.cb_scale.set(self.s["f_scale"])
        self.filt.var_audio_gain.set(float(self.s.get("audio_gain", 0)))
        self.on_change()

    # ---------------- цикл ----------------
    def set_status(self, text):
        self.pane.lbl_status.configure(text=text)

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
                self.pane.lbl_stats.configure(text=payload)
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
            self.pane.lbl_video.configure(image=self.photo, text="")
        except queue.Empty:
            pass
        cw = max(1, self.pane.canvas_meter.winfo_width())
        # шкала -60…0 дБ
        v = max(0.0, min(1.0, (self.level + 60.0) / 60.0))
        p = max(0.0, min(1.0, (self.peak + 60.0) / 60.0))
        col = "#3ecf3e" if self.peak < -12 else ("#e8c33e" if self.peak < -3 else "#e34b4b")
        if self.level <= -59:
            col = "#3a3a3a"
        self.pane.canvas_meter.coords(self.pane.bar, 0, 0, int(cw * v), 14)
        self.pane.canvas_meter.itemconfigure(self.pane.bar, fill=col)
        now_t = time.time()
        if now_t - self._fps_t0 >= 1.0:
            dt = now_t - self._fps_t0
            self._fps = self._fps_frames / dt if dt > 0 else 0
            self._fps_t0 = now_t
            self._fps_frames = 0
            if self.preview and not self.recorder:
                self.pane.lbl_stats.configure(text="предпросмотр: %.0f кадр/с" % self._fps)
                idle = now_t - self._last_frame_at if self._last_frame_at else 0
                if idle > 2.5:
                    self.set_status(
                        "Нет кадров %.0f с — вероятно, идёт перемотка. "
                        "Остановите её, картинка вернётся." % idle)
        self._check_auto_stop(now_t)
        self._tick_n = getattr(self, "_tick_n", 0) + 1
        if self._tick_n % 250 == 0:          # раз в 10 секунд обновляем место на диске
            try:
                self.update_estimate()
            except Exception:  # noqa: BLE001
                pass
        self._tick_job = self.after(12, self._tick)

    def _check_auto_stop(self, now):
        """Останавливает запись, если долго нет ни звука, ни картинки.

        Именно «и», а не «или»: на повреждённых участках кассеты картинка
        пропадает, но звук остаётся — такую запись обрывать нельзя.
        """
        if not self.recorder:
            return
        minutes = int(getattr(self.rec, "var_auto_stop_min").get())
        if not self.rec.var_auto_stop.get():
            self._auto_stop_said = False
            return
        last = max(self._rec_sound_at, self._rec_pic_at)
        idle = now - last
        if idle < 60:
            self._auto_stop_said = False
            return
        if idle >= minutes * 60:
            self.set_status("Автостоп: %d мин без звука и картинки — запись остановлена"
                            % minutes)
            self._stop_recording()

    def _check_signal(self):
        """Если кадров нет совсем долго — один раз пробуем переинициализировать чип.

        Срабатывает только на полное отсутствие кадров: любая эвристика по
        «серой картинке» ошибается на тёмных сценах и запускает ложные
        перезапуски.
        """
        if not self.preview or self.recorder:
            self._no_signal_since = None
            return
        if self.s.get("search"):
            # в режиме поиска кадров может не быть из-за перемотки — это нормально
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
            self.set_status("Нет кадров. Проверьте кабель, стандарт (PAL/SECAM/NTSC) "
                            "и нажмите «⟳ Перезапустить»")
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
