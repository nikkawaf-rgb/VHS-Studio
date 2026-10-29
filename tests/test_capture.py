# -*- coding: utf-8 -*-
"""Проверки захвата без камеры: используем тестовый источник ffmpeg (lavfi)."""
import os
import time
import unittest

from vhs_studio.capture import AudioMonitor, Preview
from vhs_studio.config import DEFAULTS
from vhs_studio.device import FFMPEG, FFPLAY


def settings(**kw):
    s = dict(DEFAULTS)
    s.update(kw)
    return s


def lavfi_command(width=320, height=240, seconds=6):
    """Подменяет команду захвата на синтетический источник — камера не нужна."""
    return [FFMPEG, "-hide_banner", "-loglevel", "quiet", "-nostats",
            "-f", "lavfi", "-i", "testsrc=size=%dx%d:rate=25" % (width, height),
            "-t", str(seconds),
            "-vf", "scale=%d:%d,format=rgb24" % (width, height),
            "-f", "rawvideo", "pipe:1"]


@unittest.skipIf(not FFMPEG, "нет ffmpeg")
class PreviewStopTest(unittest.TestCase):
    def test_stop_does_not_report_error(self):
        """Штатная остановка не должна выглядеть как авария.

        Раньше поток наблюдения успевал увидеть смерть процесса раньше,
        чем выставлялся флаг stopping, и приложение гасило кнопку записи.
        """
        errors = []
        frames = []
        p = Preview(settings(), lambda *a: frames.append(1), lambda *a: None,
                    errors.append)
        p.command = lambda: lavfi_command()
        self.assertTrue(p.start())
        time.sleep(0.8)
        self.assertGreater(len(frames), 0, "кадры должны идти")
        p.stop()
        time.sleep(0.6)
        self.assertEqual(errors, [], "штатная остановка не должна давать ошибку")

    def test_no_frames_after_stop(self):
        frames = []
        p = Preview(settings(), lambda *a: frames.append(1), lambda *a: None,
                    lambda m: None)
        p.command = lambda: lavfi_command()
        p.start()
        time.sleep(0.5)
        p.stop()
        time.sleep(0.4)
        count = len(frames)
        time.sleep(0.4)
        self.assertEqual(len(frames), count, "после stop кадры не должны приходить")


@unittest.skipIf(not FFMPEG, "нет ffmpeg")
class PreviewGeometryTest(unittest.TestCase):
    def test_frames_have_declared_size(self):
        """Размер кадра в потоке должен совпадать с ожидаемым, иначе картинка «едет»."""
        sizes = []
        p = Preview(settings(preview_width=640), lambda d, w, h: sizes.append((len(d), w, h)),
                    lambda *a: None, lambda m: None)
        p.command = lambda: lavfi_command(640, 480)
        p.start()
        time.sleep(0.8)
        p.stop()
        self.assertTrue(sizes)
        for n, w, h in sizes:
            self.assertEqual(n, p.pw * p.ph * 3)
            self.assertEqual((w, h), (p.pw, p.ph))


class AudioMonitorTest(unittest.TestCase):
    def test_feed_without_start_is_safe(self):
        m = AudioMonitor()
        m.feed(b"\x00\x00" * 100)      # выключен — просто игнорируется
        self.assertIsNone(m.proc)

    def test_queue_overflow_does_not_raise(self):
        m = AudioMonitor()
        m.enabled = True
        for _ in range(200):
            m.feed(b"\x00\x00" * 1024)  # очередь переполнится — не должно падать


if __name__ == "__main__":
    unittest.main()
