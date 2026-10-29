# -*- coding: utf-8 -*-
"""Проверки сборки аргументов ffmpeg — без камеры и без окна."""
import unittest

from vhs_studio.commands import (audio_args, build_filters, codec_args,
                                 dshow_args, input_spec, output_ext,
                                 preview_size)
from vhs_studio.config import DEFAULTS


def settings(**kw):
    s = dict(DEFAULTS)
    s["video_device"] = "Camera"
    s["audio_device"] = "Sound"
    s.update(kw)
    return s


class PreviewSizeTest(unittest.TestCase):
    def test_always_4_3_and_even(self):
        for w in (320, 640, 1026, 1100, 1280):
            pw, ph = preview_size(w)
            self.assertEqual(pw, w)
            self.assertEqual(ph % 2, 0)
            self.assertLessEqual(abs(ph - w * 3 / 4), 1)

    def test_no_negative(self):
        pw, ph = preview_size(640)
        self.assertGreater(pw, 0)
        self.assertGreater(ph, 0)


class BuildFiltersTest(unittest.TestCase):
    def test_empty_when_everything_off(self):
        s = settings(f_band=0, f_side=0, f_deint="", f_denoise=0,
                     f_chroma_dn=0, f_sharpen=0)
        vf = build_filters(s, False)
        self.assertNotIn("crop", vf)
        self.assertNotIn("bwdif", vf)
        self.assertNotIn("hqdn3d", vf)
        self.assertNotIn("unsharp", vf)

    def test_crop_order(self):
        s = settings(f_band=12, f_side=4, f_deint="", f_denoise=0,
                     f_chroma_dn=0, f_sharpen=0)
        vf = build_filters(s, False)
        self.assertIn("crop=iw:ih-12:0:0", vf)
        self.assertIn("crop=iw-8:ih:4:0", vf)
        self.assertLess(vf.index("ih-12"), vf.index("iw-8"))

    def test_deinterlacers(self):
        for name, token in (("bwdif", "bwdif="), ("yadif", "yadif="),
                            ("nnedi", "nnedi="), ("w3fdif", "w3fdif="),
                            ("estdif", "estdif=")):
            vf = build_filters(settings(f_deint=name), False)
            self.assertIn(token, vf, name)

    def test_tff_parity(self):
        vf = build_filters(settings(f_deint="bwdif"), False)
        self.assertIn("parity=tff", vf)
        self.assertIn("deint=all", vf)

    def test_denoise_uses_both_strengths(self):
        s = settings(f_deint="", f_denoise=3, f_chroma_dn=7, f_sharpen=0)
        self.assertIn("hqdn3d=3:7:3:7", build_filters(s, False))

    def test_eq_only_when_changed(self):
        base = settings(f_deint="", f_denoise=0, f_chroma_dn=0, f_sharpen=0)
        self.assertNotIn("eq=", build_filters(base, False))
        tuned = dict(base, f_bright=0.1)
        self.assertIn("eq=", build_filters(tuned, False))

    def test_scale_and_sar(self):
        s = settings(f_deint="", f_denoise=0, f_chroma_dn=0, f_sharpen=0,
                     f_scale="1440x1080")
        vf = build_filters(s, False)
        self.assertIn("scale=1440x1080", vf)
        self.assertIn("setsar=1", vf)

    def test_sar_keeps_43_after_crop(self):
        s = settings(f_deint="", f_denoise=0, f_chroma_dn=0, f_sharpen=0,
                     f_band=12, f_side=4, width=720, height=576)
        vf = build_filters(s, False)
        # 712x564 -> (4/3)*564/712
        self.assertIn("setsar=1.056180", vf)

    def test_preview_gets_explicit_size(self):
        s = settings(f_deint="")
        vf = build_filters(s, True, 640)
        self.assertIn("scale=640:480", vf)
        self.assertNotIn("-2", vf.split("scale=")[-1].split(",")[0])


class CodecArgsTest(unittest.TestCase):
    def test_x264_tff_only_when_interlaced(self):
        s = settings(codec="x264", crf=16)
        self.assertIn("tff=1:keyint=50", " ".join(codec_args(s, True)))
        self.assertNotIn("tff=1", " ".join(codec_args(s, False)))

    def test_crf_passed(self):
        self.assertIn("22", codec_args(settings(codec="x265", crf=22), True))

    def test_ffv1_lossless_flags(self):
        args = codec_args(settings(codec="ffv1"), True)
        self.assertIn("ffv1", args)
        self.assertIn("3", args)          # level 3

    def test_nvenc_uses_cq(self):
        args = codec_args(settings(codec="hevc_nvenc", crf=24), True)
        self.assertIn("-cq", args)
        self.assertIn("24", args)

    def test_mpeg2_interlaced_flags(self):
        self.assertIn("+ilme+ildct", codec_args(settings(codec="mpeg2"), True))
        self.assertIn("+ildct", codec_args(settings(codec="mpeg2"), False))


class OutputTest(unittest.TestCase):
    def test_extension_matches_codec(self):
        self.assertEqual(output_ext(settings(codec="ffv1",
                                             audio_codec="FLAC (без потерь)")), "mkv")
        self.assertEqual(output_ext(settings(codec="x265",
                                             audio_codec="FLAC (без потерь)")), "mkv")
        self.assertEqual(output_ext(settings(codec="x265",
                                             audio_codec="AAC 192k")), "mp4")
        self.assertEqual(output_ext(settings(codec="mpeg2")), "mpg")

    def test_audio_args(self):
        self.assertEqual(audio_args(settings(audio_codec="PCM (без потерь)"))[:2],
                         ["-c:a", "pcm_s16le"])
        self.assertIn("flac", audio_args(settings(audio_codec="FLAC (без потерь)")))


class DshowArgsTest(unittest.TestCase):
    def test_always_uyvy(self):
        # YUYV у этого драйвера отдаёт зелёную картинку
        self.assertIn("uyvy422", dshow_args(settings()))

    def test_crossbar_pin(self):
        self.assertIn("1", dshow_args(settings(input_pin=1)))
        self.assertNotIn("-crossbar_video_input_pin_number", dshow_args(settings(input_pin=-1)))

    def test_video_and_audio_in_one_graph(self):
        self.assertIn("video=Camera:audio=Sound", input_spec(settings()))

    def test_ntsc_size(self):
        self.assertIn("720x480", dshow_args(settings(width=720, height=480, fps=29.97)))


if __name__ == "__main__":
    unittest.main()
