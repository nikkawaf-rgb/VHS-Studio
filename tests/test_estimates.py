# -*- coding: utf-8 -*-
"""Оценка размера файла и форматирование."""
import unittest

from vhs_studio.config import AUDIO_CODECS, DEFAULTS
from vhs_studio.estimates import (estimate_gb_per_hour, estimate_mbps, fmt_gb,
                                  hms, human_size)


def settings(**kw):
    s = dict(DEFAULTS)
    s.update(kw)
    return s


class EstimateTest(unittest.TestCase):
    def test_lower_crf_means_bigger_file(self):
        small = estimate_mbps(settings(codec="x264", crf=14))
        big = estimate_mbps(settings(codec="x264", crf=24))
        self.assertGreater(small, big)

    def test_ffv1_constant_and_huge(self):
        a = estimate_mbps(settings(codec="ffv1", crf=10))
        b = estimate_mbps(settings(codec="ffv1", crf=30))
        self.assertEqual(a, b)
        self.assertGreater(a, 40)

    def test_audio_always_counted(self):
        # звук пишется всегда, поэтому флаг audio_enabled на оценку не влияет
        off = estimate_mbps(settings(codec="x264", audio_enabled=False))
        on = estimate_mbps(settings(codec="x264", audio_enabled=True))
        self.assertEqual(off, on)
        self.assertGreater(off, estimate_mbps(settings(codec="x264",
                                                       audio_codec="AAC 192k")))

    def test_flac_heavier_than_aac(self):
        flac = estimate_mbps(settings(audio_codec="FLAC (без потерь)"))
        aac = estimate_mbps(settings(audio_codec="AAC 192k"))
        self.assertGreater(flac, aac)

    def test_known_values(self):
        # x264 CRF 16 на этой кассете давал ~9.9 Мбит/с вместе со звуком
        mbps = estimate_mbps(settings(codec="x264", crf=16,
                                      audio_codec="FLAC (без потерь)"))
        self.assertAlmostEqual(mbps, 9.9, delta=0.6)

    def test_gb_per_hour_math(self):
        s = settings(codec="x264", crf=16)
        expected = estimate_mbps(s) * 3600 / 8 / 1024
        self.assertAlmostEqual(estimate_gb_per_hour(s), expected, places=6)

    def test_all_codecs_estimate_positive(self):
        for meta in (m for m in DEFAULTS.values() if False):
            pass
        for codec in ("ffv1", "x264", "x265", "hevc_nvenc", "h264_nvenc",
                      "svtav1", "mpeg2"):
            self.assertGreater(estimate_mbps(settings(codec=codec)), 0, codec)


class FormatTest(unittest.TestCase):
    def test_gb(self):
        self.assertEqual(fmt_gb(4.42), "4.4 ГБ")
        self.assertEqual(fmt_gb(0.5), "512 МБ")

    def test_hms(self):
        self.assertEqual(hms(0), "00:00:00")
        self.assertEqual(hms(59), "00:00:59")
        self.assertEqual(hms(3661), "01:01:01")

    def test_human_size(self):
        self.assertEqual(human_size(512), "512.0 Б")
        self.assertEqual(human_size(2048), "2.0 КБ")
        self.assertEqual(human_size(5 * 1024 ** 3), "5.0 ГБ")


class ConfigTest(unittest.TestCase):
    def test_audio_codecs_have_known_keys(self):
        self.assertIn("FLAC (без потерь)", AUDIO_CODECS)
        self.assertIn("AAC 192k", AUDIO_CODECS)


if __name__ == "__main__":
    unittest.main()
