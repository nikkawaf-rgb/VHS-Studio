# -*- coding: utf-8 -*-
"""Сборка командной строки ffmpeg: фильтры, кодеки, контейнеры."""
from __future__ import annotations

from .config import AUDIO_CODECS, CODECS, PIN_COMPOSITE

def preview_size(width):
    """Размер кадра предпросмотра: 4:3 с квадратными пикселями.

    Важно задавать ОБА размера явно: если оставить -2, ffmpeg посчитает высоту
    от обрезанного кадра, и она разойдётся с тем, что ждёт Python — картинка
    начнёт «ехать» снизу вверх.
    """
    w = int(width)
    h = int(round(w * 3.0 / 4.0 / 2.0)) * 2
    return w, h


def build_filters(s, for_preview, preview_w=640):
    """Строка -vf из настроек."""
    p = []
    band = int(s.get("f_band") or 0)
    if band > 0:
        p.append("crop=iw:ih-%d:0:0" % band)
    side = int(s.get("f_side") or 0)
    if side > 0:
        p.append("crop=iw-%d:ih:%d:0" % (side * 2, side))

    deint = s.get("f_deint", "bwdif")
    if deint == "bwdif":
        p.append("bwdif=mode=send_frame:parity=tff:deint=all")
    elif deint == "yadif":
        p.append("yadif=mode=send_frame:parity=tff:deint=all")
    elif deint == "nnedi":
        p.append("nnedi=weights=nnedi3_weights.bin:field=tf:deint=all")
    elif deint == "w3fdif":
        p.append("w3fdif=filter=complex:deint=all")
    elif deint == "estdif":
        p.append("estdif=mode=frame:parity=tff:deint=all")

    dn = float(s.get("f_denoise") or 0)
    cdn = float(s.get("f_chroma_dn") or 0)
    if dn > 0 or cdn > 0:
        p.append("hqdn3d=%g:%g:%g:%g" % (dn, cdn, dn, cdn))

    sh = float(s.get("f_sharpen") or 0)
    if sh > 0:
        p.append("unsharp=5:5:%g:5:5:0" % sh)

    br = float(s.get("f_bright") or 0)
    ct = float(s.get("f_contrast") or 1)
    sa = float(s.get("f_saturation") or 1)
    if abs(br) > 1e-6 or abs(ct - 1) > 1e-6 or abs(sa - 1) > 1e-6:
        p.append("eq=brightness=%.3f:contrast=%.3f:saturation=%.3f" % (br, ct, sa))

    sc = s.get("f_scale", "none")
    if sc != "none":
        p.append("scale=%s:flags=lanczos" % sc)
        p.append("setsar=1")
    else:
        # после обрезки краёв сохраняем настоящие пропорции 4:3
        w = max(2, int(s.get("width", 720)) - 2 * side)
        h = max(2, int(s.get("height", 576)) - band)
        p.append("setsar=%.6f" % ((4.0 / 3.0) * h / w))

    if for_preview:
        pw, ph = preview_size(preview_w)
        p.append("scale=%d:%d:flags=bilinear" % (pw, ph))
        p.append("setsar=1")
    return ",".join(p)


def codec_args(s, interlaced=True):
    cid = s["codec"]
    crf = int(s.get("crf") or 20)
    if cid == "ffv1":
        return ["-c:v", "ffv1", "-level", "3", "-g", "1", "-slices", "4", "-threads", "8"]
    if cid == "x264":
        a = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
             "-x264-params", ("tff=1:keyint=50" if interlaced else "keyint=50"),
             "-pix_fmt", "yuv420p"]
        return a
    if cid == "x265":
        return ["-c:v", "libx265", "-preset", "fast", "-crf", str(crf),
                "-x265-params", "log-level=error:aq-mode=3", "-pix_fmt", "yuv420p",
                "-tag:v", "hvc1"]
    if cid == "hevc_nvenc":
        return ["-c:v", "hevc_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                "-cq", str(crf), "-b:v", "0", "-spatial_aq", "1",
                "-pix_fmt", "yuv420p", "-tag:v", "hvc1"]
    if cid == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                "-cq", str(crf), "-b:v", "0", "-spatial_aq", "1", "-pix_fmt", "yuv420p"]
    if cid == "svtav1":
        return ["-c:v", "libsvtav1", "-preset", "8", "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if cid == "mpeg2":
        fl = "+ilme+ildct" if interlaced else "+ildct"
        return ["-c:v", "mpeg2video", "-b:v", "9M", "-maxrate", "9.8M",
                "-bufsize", "1835008", "-flags", fl, "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-crf", str(crf)]


def audio_args(s):
    name, _ = AUDIO_CODECS.get(s.get("audio_codec", "FLAC (без потерь)"),
                               ("flac", "mkv"))
    if name == "flac":
        return ["-c:a", "flac", "-compression_level", "5"]
    if name == "pcm_s16le":
        return ["-c:a", "pcm_s16le"]
    if name == "aac":
        return ["-c:a", "aac", "-b:a", "%dk" % int(s.get("audio_bitrate", 192))]
    return ["-c:a", "libmp3lame", "-b:a", "%dk" % int(s.get("audio_bitrate", 192))]


def output_ext(s):
    meta = CODECS.get(next((k for k, v in CODECS.items() if v["id"] == s["codec"]),
                           "H.264 x264 (почти без потерь)"))
    ext = meta["ext"]
    acodec, aext = AUDIO_CODECS.get(s.get("audio_codec", "FLAC (без потерь)"),
                                    ("flac", "mkv"))
    if ext == "mp4" and aext == "mkv":
        ext = "mkv"
    return ext


def input_spec(s):
    v = "video=" + s["video_device"]
    if s.get("audio_enabled") and s.get("audio_device"):
        v += ":audio=" + s["audio_device"]
    return v


def dshow_args(s):
    a = ["-f", "dshow", "-rtbufsize", "1024M"]
    if int(s.get("input_pin", PIN_COMPOSITE)) >= 0:
        a += ["-crossbar_video_input_pin_number", str(int(s["input_pin"]))]
    a += ["-video_size", "%dx%d" % (int(s["width"]), int(s["height"])),
          "-framerate", str(s["fps"]),
          "-pixel_format", "uyvy422"]
    a += ["-i", input_spec(s)]
    return a
