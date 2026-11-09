"""Keep end-to-end Rust timing honest: parity before speed claims."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import benchmark_match_pipeline as bench


class MatcherPipelineBenchmarkTests(unittest.TestCase):
    def test_reject_missing_media_or_invalid_repeat(self):
        with self.assertRaises(ValueError):
            bench.benchmark("not-here.mkv", "absent.mkv")
        with self.assertRaises(ValueError):
            bench.benchmark("not-here.mkv", "absent.mkv", repetitions=0)

    def test_all_matching_paths_and_exact_evidence_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "input.mkv"
            media.touch()
            with patch.object(bench, "_library", return_value=object()):
                with self.assertRaisesRegex(RuntimeError, "fünf Rust-Matcher"):
                    bench.benchmark(str(media), str(media))

            fake_lib = SimpleNamespace(**{name: object() for name in bench._REQUIRED_SYMBOLS})

            def fake_alignment(_source, _master, _interval, _max_samples, native):
                profile = {"matching_wall_seconds": .1 if native else 1.,
                           "sampling_wall_seconds": 2.}
                return (2.5 if native else 4.), profile, ("anchors", native, "sequence")

            with patch.object(bench, "_library", return_value=fake_lib), \
                    patch.object(bench, "_alignment", side_effect=fake_alignment):
                with self.assertRaisesRegex(RuntimeError, "unterschiedliche Alignment-Beweise"):
                    bench.benchmark(str(media), str(media), repetitions=2)

    def test_report_distinguishes_matching_from_whole_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "input.mkv"
            media.touch()
            fake_lib = SimpleNamespace(**{name: object() for name in bench._REQUIRED_SYMBOLS})

            def fake_alignment(_source, _master, _interval, _max_samples, native):
                profile = {"matching_wall_seconds": .1 if native else 1.,
                           "sampling_wall_seconds": 2.}
                return (2.5 if native else 4.), profile, ("anchors", True, "sequence")

            with patch.object(bench, "_library", return_value=fake_lib), \
                    patch.object(bench, "_alignment", side_effect=fake_alignment):
                report = bench.benchmark(str(media), str(media), repetitions=2)
            self.assertTrue(report["evidence_identical"])
            self.assertEqual(report["matching_speedup"], 10.)
            self.assertEqual(report["pipeline_speedup"], 1.6)
            self.assertTrue(report["rust_matcher_exercised"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_synthetic_long_intro_is_a_real_shifted_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            source, master = (Path(directory) / name for name in ("source.mkv", "master.mkv"))
            bench._synthetic_shift(source, master, 60)
            elapsed, profile, (result, safe, method) = bench._alignment(
                str(source), str(master), .5, 1800, native=False)
            self.assertGreater(elapsed, 0)
            self.assertGreater(profile["sampling_wall_seconds"], 0)
            self.assertTrue(safe)
            self.assertEqual(method, "long_offset_verified")
            self.assertAlmostEqual(result["offset"], 32., places=2)


if __name__ == "__main__":
    unittest.main()
