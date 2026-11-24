"""Baseline harness contracts. Timing thresholds belong to real-device QA."""
from dataclasses import dataclass
import os
import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts.performance_baseline import (
    alignment_suite, evidence, isolate, parse_args, summarize,
)


class BaselineTests(unittest.TestCase):
    def test_summary_retains_raw_samples(self):
        self.assertEqual(summarize([3., 1., 2.]),
                         {"samples": [3., 1., 2.], "median": 2., "min": 1., "max": 3.})
        with self.assertRaises(ValueError):
            summarize([])

    def test_invalid_settings_rejected(self):
        for args in (["only-one"], ["--repeat", "0"], ["--interval", "nan"],
                     ["--suite", "gui", "a", "b"], ["--idle-seconds", "31"]):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                parse_args(args)

    def test_private_xdg_state(self):
        with patch.dict(os.environ, {}, clear=True):
            isolate(Path("/private/benchmark"))
            for name in ("CONFIG", "CACHE", "STATE", "DATA"):
                self.assertEqual(os.environ[f"XDG_{name}_HOME"],
                                 f"/private/benchmark/{name.lower()}")

    def test_evidence_excludes_paths_but_preserves_safety(self):
        @dataclass
        class Result:
            source_path: str = "/private/source"
            target_path: str = "/private/master"
            offset: float = 1.
            safe_for_automatic_export: bool = False

        self.assertEqual(evidence(Result()), {"offset": 1., "safe_for_automatic_export": False})

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Real FFmpeg tools required")
    def test_cold_warm_real_pipeline(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            root = Path(directory)
            isolate(root / "initial")
            from scripts.benchmark_pipeline import _synthetic_video
            source, master = root / "source.mkv", root / "master.mkv"
            _synthetic_video(source, 30)
            shutil.copyfile(source, master)
            before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (source, master)]
            report = alignment_suite(root, parse_args([str(source), str(master), "--repeat", "1"]))
            self.assertTrue(report["evidence_identical"])
            self.assertRegex(report["evidence_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(report["runs"]["cold"][0]["safe_for_automatic_export"])
            for side in ("source", "master"):
                self.assertEqual(report["runs"]["warm"][0]["sampling"][side]["status"], "cache_hit")
            self.assertGreater(report["summary"]["cold"]["wall_seconds"]["median"], 0)
            self.assertEqual(before, [hashlib.sha256(path.read_bytes()).hexdigest()
                                      for path in (source, master)])


if __name__ == "__main__":
    unittest.main()
