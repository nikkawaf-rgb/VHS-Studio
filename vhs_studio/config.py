# -*- coding: utf-8 -*-
"""Константы: настройки по умолчанию, кодеки, описания, пути."""
from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DIR = os.path.join(BASE_DIR, "profiles")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
LOG_FILE = os.path.join(BASE_DIR, "ffmpeg_last.log")

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

os.makedirs(PROFILE_DIR, exist_ok=True)

# --------------------------------------------------------------------------- #
#  Модель настроек
# --------------------------------------------------------------------------- #
# Кроссбар Conexant Polaris: пин 1 = композит (тюльпаны), пин 0 = S-Video.
PIN_COMPOSITE = 1
PIN_SVIDEO = 0
PIN_AUTO = -1
INPUT_LABELS = ["Авто", "Композит (тюльпаны)", "S-Video"]
INPUT_PINS = [PIN_AUTO, PIN_COMPOSITE, PIN_SVIDEO]

DEFAULTS = {
    "video_device": "",
    "audio_device": "",
    "audio_enabled": True,
    "input_pin": PIN_COMPOSITE,   # 1 = композит (тюльпаны), 0 = S-Video, -1 = авто
    "standard": "PAL",         # PAL | NTSC
    "width": 720,
    "height": 576,
    "fps": 25,
    "codec": "x264",
    "crf": 16,
    "audio_codec": "flac",
    "audio_bitrate": 192,
    "audio_gain": 0.0,
    "monitor": True,
    "out_dir": os.path.join(os.path.expanduser("~"), "Videos", "VHS"),
    "name_template": "{tape}_{date}",
    "tape_name": "кассета",
    # фильтры (предпросмотр всегда, запись — по флагу)
    "f_bake": False,
    "f_deint": "bwdif",
    "f_band": 12,
    "f_side": 4,
    "f_denoise": 6,
    "f_chroma_dn": 10,
    "f_sharpen": 0.5,
    "f_bright": 0.0,
    "f_contrast": 1.0,
    "f_saturation": 1.0,
    "f_scale": "none",         # none | 960x720 | 1440x1080
    "preview_width": 640,
    "record_preview": True,
}

CODECS = {
    "FFV1 (без потерь)":      {"id": "ffv1",  "ext": "mkv", "crf": False},
    "H.264 x264 (почти без потерь)": {"id": "x264", "ext": "mkv", "crf": True},
    "H.265 x265 (компактно)": {"id": "x265",  "ext": "mp4", "crf": True},
    "H.265 NVENC (GPU, быстро)": {"id": "hevc_nvenc", "ext": "mp4", "crf": True},
    "H.264 NVENC (GPU, быстро)": {"id": "h264_nvenc", "ext": "mp4", "crf": True},
    "AV1 SVT (макс. сжатие)": {"id": "svtav1", "ext": "mp4", "crf": True},
    "MPEG-2 (совместимость с DVD)": {"id": "mpeg2", "ext": "mpg", "crf": False},
}
CRF_RANGE = {
    "x264": (10, 28, 16), "x265": (12, 32, 20), "hevc_nvenc": (16, 34, 22),
    "h264_nvenc": (16, 34, 22), "svtav1": (18, 40, 26),
}
AUDIO_CODECS = {
    "FLAC (без потерь)": ("flac", "mkv"),
    "PCM (без потерь)": ("pcm_s16le", "mkv"),
    "AAC 192k": ("aac", "any"),
    "MP3 192k": ("libmp3lame", "any"),
}


CODEC_INFO = {
    "ffv1": "Архив без потерь: ~27 ГБ/час. Ничего не теряется, обрабатывать можно "
            "сколько угодно раз, но места нужно много.",
    "x264": "Рекомендую. CRF 14–16 — визуально без потерь, ~8–10 Мбит/с (4 ГБ/час). "
            "Лучший баланс качества и размера для VHS, чересстрочность сохраняет.",
    "x265": "Вдвое компактнее x264, но на i5-11400F еле тянет реальное время "
            "(~1.3x) — при записи возможны пропуски кадров. Лучше применять "
            "к уже готовому файлу, а не при захвате.",
    "hevc_nvenc": "Кодирует видеокарта — очень быстро и почти не грузит процессор. "
                  "Чересстрочность не сохраняет: либо включайте обработку, либо "
                  "картинка на движении будет «рваться».",
    "h264_nvenc": "Видеокарта, быстро. При том же размере чуть хуже x264. "
                  "Тоже не умеет чересстрочность.",
    "svtav1": "Самый маленький файл (~2.6 Мбит/с). На этом CPU ~5x реального "
              "времени — писать можно, но процессор будет занят.",
    "mpeg2": "Только для старых DVD-плееров: ~9 Мбит/с, большой и неэффективный.",
}

DEINT_INFO = {
    "bwdif": "Рекомендую: учитывает движение, минимум артефактов на шумном VHS.",
    "yadif": "Проще и быстрее, но больше «пилы» на наклонных линиях.",
    "estdif": "Ищет края: резче, но может «дрожать» на зерне.",
    "nnedi": "Нейросеть: самый резкий на статике, но медленный и хуже на движении.",
    "w3fdif": "Аккуратный, но смазывает мелкие детали.",
    "": "Оставить чересстрочность — для DVD и старых телевизоров. На мониторе "
        "будет видна «гребёнка».",
}

# готовый набор под этот компьютер
RECOMMENDED = {
    "codec": "x264", "crf": 16, "f_deint": "bwdif", "f_band": 12, "f_side": 4,
    "f_denoise": 6, "f_chroma_dn": 10, "f_sharpen": 0.5, "f_bright": 0.0,
    "f_contrast": 1.0, "f_saturation": 1.0, "f_scale": "none", "f_bake": False,
    "audio_codec": "FLAC (без потерь)", "monitor": True,
}


def s_fps(settings):
    return 25 if settings.get("standard", "PAL") != "NTSC" else 29.97
