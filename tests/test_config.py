# -*- coding: utf-8 -*-
"""Согласованность констант: чтобы интерфейс и настройки не разъезжались."""
import unittest

from vhs_studio.config import (AUDIO_CODECS, CODECS, CODEC_INFO, CRF_RANGE,
                               DEINT_INFO, DEFAULTS, INPUT_PINS, RECOMMENDED)


class ConstantsTest(unittest.TestCase):
    def test_defaults_have_all_keys(self):
        for key in ("video_device", "audio_device", "codec", "crf", "out_dir",
                    "f_deint", "f_band", "f_side", "f_scale", "audio_gain",
                    "monitor", "record_preview", "tape_name"):
            self.assertIn(key, DEFAULTS, key)

    def test_default_codec_exists(self):
        ids = {m["id"] for m in CODECS.values()}
        self.assertIn(DEFAULTS["codec"], ids)

    def test_every_codec_has_description(self):
        for meta in CODECS.values():
            self.assertIn(meta["id"], CODEC_INFO, meta["id"])

    def test_crf_ranges_cover_defaults(self):
        for codec, (lo, hi, dflt) in CRF_RANGE.items():
            self.assertLessEqual(lo, dflt, codec)
            self.assertLessEqual(dflt, hi, codec)

    def test_deint_descriptions(self):
        for name in ("bwdif", "yadif", "estdif", "nnedi", "w3fdif", ""):
            self.assertIn(name, DEINT_INFO, name or "нет")

    def test_recommended_keys_are_known(self):
        for key in RECOMMENDED:
            self.assertIn(key, DEFAULTS, key)

    def test_recommended_is_consistent(self):
        self.assertEqual(RECOMMENDED["f_deint"], "bwdif")
        self.assertEqual(RECOMMENDED["codec"], "x264")
        ids = {m["id"] for m in CODECS.values()}
        self.assertIn(RECOMMENDED["codec"], ids)
        self.assertIn(RECOMMENDED["audio_codec"], AUDIO_CODECS)

    def test_input_pins_unique(self):
        self.assertEqual(len(INPUT_PINS), len(set(INPUT_PINS)))

    def test_codec_extensions(self):
        for meta in CODECS.values():
            self.assertIn(meta["ext"], ("mkv", "mp4", "mpg"), meta["id"])


if __name__ == "__main__":
    unittest.main()
