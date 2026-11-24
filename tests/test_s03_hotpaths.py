"""Verify compact snapshots and the zero-delay FFmpeg fast path."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
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
        self.assertEqual(delay_filter(1), "adelay=1:all=1,asetpts=N/SR/TB,")
        self.assertEqual(delay_filter(250), "adelay=250:all=1,asetpts=N/SR/TB,")
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


    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_real_aac_decode_parity_including_surround(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "track.mkv"
            for rate in (44100, 48000):
                for layout in ("mono", "stereo", "5.1", "7.1"):
                    for tempo in (.98, 1., 1.02):
                        with self.subTest(rate=rate, layout=layout, tempo=tempo):
                            prefix = (f"aformat=channel_layouts={layout},aresample=48000,"
                                      f"atrim=start=0.15,asetpts=PTS-STARTPTS,atempo={tempo},")
                            evidence = []
                            for delay in ("adelay=0:all=1,", delay_filter(0)):
                                subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y",
                                    "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}:duration=4",
                                    "-af", prefix + delay + "apad,atrim=duration=4",
                                    "-c:a", "aac", "-b:a", "128k", str(output)], check=True, timeout=20)
                                evidence.append(subprocess.check_output(["ffmpeg", "-v", "error", "-nostdin",
                                    "-i", str(output), "-map", "0:a:0", "-c:a", "pcm_s16le",
                                    "-f", "hash", "-hash", "SHA256", "-"], timeout=20))
                            self.assertEqual(evidence[0], evidence[1])


    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_positive_delay_keeps_exact_silence_and_sample_count(self):
        pcm = subprocess.check_output(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=48000:duration=2", "-af",
            delay_filter(500) + "apad,atrim=duration=2", "-f", "s16le", "-"], timeout=20)
        self.assertEqual(len(pcm), 2 * 48000 * 2)
        self.assertEqual(pcm[:24000 * 2], bytes(24000 * 2))
        self.assertTrue(any(pcm[24000 * 2:]))


if __name__ == "__main__":
    unittest.main()
