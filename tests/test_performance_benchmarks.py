"""Reproducible speed tests must prove correctness and leave user inputs alone."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.benchmark_phash import benchmark as benchmark_phash
from scripts.benchmark_pipeline import _synthetic_video, benchmark as benchmark_pipeline


class BenchmarkContractTests(unittest.TestCase):
    def test_phash_benchmark_proves_identical_hashes(self):
        report = benchmark_phash(frames=48, repetitions=1)
        self.assertTrue(report["hashes_identical"])
        self.assertGreater(report["previous_seconds"], 0)
        self.assertGreater(report["numpy_optimized_seconds"], 0)

    def test_pipeline_rejects_missing_inputs_and_unbounded_runs(self):
        with self.assertRaises(ValueError):
            benchmark_pipeline("missing.mkv", "other.mkv", interval=.5,
                               max_samples=1800, repetitions=1)
        with self.assertRaises(ValueError):
            benchmark_pipeline("missing.mkv", "other.mkv", interval=.5,
                               max_samples=1800, repetitions=99)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Real FFmpeg/FFprobe needed")
    def test_pipeline_benchmark_compares_matching_evidence_without_source_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mkv"
            target = Path(directory) / "target.mkv"
            _synthetic_video(source, 18)
            shutil.copyfile(source, target)
            before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (source, target)]
            report = benchmark_pipeline(str(source), str(target), interval=.5,
                                        max_samples=200, repetitions=1)
            after = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (source, target)]
            self.assertEqual(before, after)
            self.assertTrue(report["same_alignment_evidence"])
            self.assertTrue(report["safe_for_automatic_export"])
            self.assertGreater(report["previous_seconds"], 0)
            self.assertGreater(report["optimized_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
