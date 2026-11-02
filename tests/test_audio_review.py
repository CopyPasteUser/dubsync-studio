"""Bounded local waveform extraction and opt-in UI behavior."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dubsync.gui.audio_review import WaveformExcerpt, WaveformWorker, extract_waveform


class AudioReviewTests(unittest.TestCase):
    def test_refuses_unbounded_requests_without_opening_media(self):
        with mock.patch("dubsync.gui.audio_review.subprocess.Popen") as process:
            with self.assertRaises(ValueError):
                extract_waveform("missing", 0, cancelled=threading.Event(), window_seconds=100)
            with self.assertRaises(ValueError):
                extract_waveform("missing", float("nan"), cancelled=threading.Event())
            process.assert_not_called()

    def test_cancelled_does_not_read_media(self):
        cancelled = threading.Event()
        cancelled.set()
        with mock.patch("dubsync.gui.audio_review.subprocess.Popen") as process:
            with self.assertRaisesRegex(RuntimeError, "abgebrochen"):
                extract_waveform("missing", 4, cancelled=cancelled)
            process.assert_not_called()

    def test_unmapped_section_reads_only_master(self):
        worker = WaveformWorker("master", "source", 4, None)
        result = []
        worker.ready.connect(result.append)
        with mock.patch("dubsync.gui.audio_review.extract_waveform",
                        return_value=WaveformExcerpt(0, (.2, .4))) as extractor:
            worker.run()
        self.assertEqual(result, [(WaveformExcerpt(0, (.2, .4)), None)])
        extractor.assert_called_once()

    @unittest.skipUnless(shutil.which("ffmpeg"), "Needs FFmpeg")
    def test_real_local_wav_bounded_peaks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sine.wav"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                            str(path)], check=True, timeout=15)
            excerpt = extract_waveform(str(path), 1.5, cancelled=threading.Event())
            self.assertGreater(len(excerpt.peaks), 30)
            self.assertLessEqual(len(excerpt.peaks), 128)
            self.assertGreater(max(excerpt.peaks), .05)
            self.assertTrue(all(0 <= peak <= 1 for peak in excerpt.peaks))


if __name__ == "__main__":
    unittest.main()
