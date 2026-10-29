# -*- coding: utf-8 -*-
"""Оценка размера файла и форматирование чисел."""
from __future__ import annotations

from .config import AUDIO_CODECS

def estimate_mbps(s):
    """Оценка битрейта записи (Мбит/с) для текущих настроек.

    Ориентиры взяты из измерений на этой кассете и в среднем по VHS,
    поэтому цифра приблизительная — реальная зависит от «шумности» записи.
    """
    cid = s.get("codec")
    crf = float(s.get("crf") or 20)
    if cid == "ffv1":
        video = 60.0
    elif cid == "x264":
        video = 9.0 * 2.0 ** ((16.0 - crf) / 6.0)
    elif cid == "x265":
        video = 4.2 * 2.0 ** ((20.0 - crf) / 6.0)
    elif cid == "h264_nvenc":
        video = 3.2 * 2.0 ** ((22.0 - crf) / 6.0)
    elif cid == "hevc_nvenc":
        video = 2.6 * 2.0 ** ((22.0 - crf) / 6.0)
    elif cid == "svtav1":
        video = 3.0 * 2.0 ** ((26.0 - crf) / 6.0)
    elif cid == "mpeg2":
        video = 9.0
    else:
        video = 9.0

    acodec, _ = AUDIO_CODECS.get(s.get("audio_codec", ""), ("flac", "mkv"))
    if acodec == "flac":
        audio = 0.9
    elif acodec == "pcm_s16le":
        audio = 1.536
    else:
        audio = int(s.get("audio_bitrate", 192)) / 1000.0
    return video + audio


def estimate_gb_per_hour(s):
    return estimate_mbps(s) * 3600.0 / 8.0 / 1024.0


def fmt_gb(gb):
    if gb < 1.0:
        return "%.0f МБ" % (gb * 1024.0)
    return "%.1f ГБ" % gb



def human_size(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f ТБ" % n


def hms(sec):
    sec = int(sec)
    return "%02d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)
