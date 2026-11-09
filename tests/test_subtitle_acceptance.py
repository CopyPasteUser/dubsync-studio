"""Run the actual FFmpeg soft-subtitle QA without fetching private media."""
from __future__ import annotations

import shutil
import unittest

from scripts.acceptance_subtitles import accept_synthetic


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg/FFprobe required for real-container acceptance")
class SubtitleAcceptanceTests(unittest.TestCase):
    def test_toggleable_stream_preserves_bitstream_and_originals(self):
        report = accept_synthetic()
        self.assertTrue(report["passed"], report)
        self.assertTrue(report["video_bitstream_copied"])
        self.assertTrue(report["original_files_unchanged"])
        self.assertEqual(report["subtitle_streams"],
                         {"source": 1, "disabled": 1, "enabled": 2})
        self.assertFalse(report["additional_subtitle_default"])
        self.assertGreater(report["mux_wall_seconds"]["off"], 0)
        self.assertGreater(report["mux_wall_seconds"]["sidecar"], 0)


if __name__ == "__main__":
    unittest.main()
