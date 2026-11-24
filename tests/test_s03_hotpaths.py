"""Verify compact snapshots and the zero-delay FFmpeg fast path."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from dubsync.core.audio_filters import delay_filter
from dubsync.core.batch_snapshot import snapshot_json


class SnapshotTests(unittest.TestCase):
    def test_round_trip_retains_resume_and_unicode(self):
        payload = {"schema_version": 1, "episodes": [{"reason": "geprüft",
                   "resume_proof": {"source_id": "abc", "recipe": "xyz"},
                   "attempts": [{"metrics": {"slope": 1.0}, "warnings": []}]}]}
        compact = snapshot_json(payload)
        self.assertEqual(json.loads(compact), payload)
        self.assertLess(len(compact), len(json.dumps(payload, indent=2, ensure_ascii=False)))
        self.assertTrue(compact.endswith("\n"))

    def test_nonfinite_rejected(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(ValueError):
                snapshot_json({"value": value})


class AudioFilterTests(unittest.TestCase):
    def test_exact_zero_only(self):
        self.assertEqual(delay_filter(0), "")
        self.assertEqual(delay_filter(1), "adelay=1:all=1,")
        self.assertEqual(delay_filter(250), "adelay=250:all=1,")
        with self.assertRaises(ValueError):
            delay_filter(-1)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_real_pcm_parity(self):
        for rate in (44100, 48000):
            for channels in (1, 2):
                for tempo in (0.98, 1.0, 1.02):
                    with self.subTest(rate=rate, channels=channels, tempo=tempo):
                        prefix = (f"aformat=channel_layouts={'mono' if channels == 1 else 'stereo'},"
                                  f"aresample=48000,atrim=start=0.15,asetpts=PTS-STARTPTS,atempo={tempo},")
                        suffix = "apad,atrim=duration=4"
                        command = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                                   f"sine=frequency=440:sample_rate={rate}:duration=4"]
                        def pcm(graph):
                            return subprocess.check_output(command + ["-af", graph,
                                                           "-f", "s16le", "-"], timeout=20)
                        before = pcm(prefix + "adelay=0:all=1," + suffix)
                        after = pcm(prefix + delay_filter(0) + suffix)
                        self.assertTrue(before)
                        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
