from __future__ import annotations

import unittest
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dubsync.cli import VERSION, doctor_report, main
from dubsync.core.ffmpeg_sampling import DecoderCandidate


class CliTests(unittest.TestCase):
    def test_cli_version_matches_release_version(self):
        self.assertEqual(VERSION, "2.0.0b2")

    def test_doctor_report_has_expected_sections_and_boolean_dependencies(self):
        report = doctor_report()
        self.assertEqual(report["application_version"], VERSION)
        self.assertTrue(report["python"])
        self.assertTrue(report["platform"])
        self.assertIn("PySide6", report["dependencies"])
        self.assertTrue(all(isinstance(value, bool) for value in report["dependencies"].values()))
        self.assertIn("ffmpeg", report["media_tools"])
        self.assertIn("ffprobe", report["media_tools"])
        self.assertIn("log", report["paths"])
        self.assertIsInstance(report["warnings"], list)

    def test_alignment_profile_runs_read_only_and_returns_json(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.mkv"
            master = Path(temp) / "master.mkv"
            source.write_bytes(b"source")
            master.write_bytes(b"master")
            captured = io.StringIO()

            def capture(*_args, **kwargs):
                self.assertEqual(kwargs["frame_cache_enabled"], False)
                self.assertEqual(kwargs["sampling_workers"], 2)
                self.assertEqual(kwargs["hardware_accel"], "intel-vaapi")
                kwargs["diagnostic_sink"]({"decision": {"status": "unsafe"},
                                           "stages": {"pipeline_profile": {"total_wall_seconds": 1.25}}})

            with (patch("dubsync.core.alignment.align_videos", side_effect=capture),
                  patch("sys.stdout", captured)):
                exit_code = main(["--profile-alignment", str(source), str(master),
                                  "--profile-workers", "2", "--profile-decoder", "intel-vaapi", "--json"])
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(captured.getvalue())["decision"]["status"], "unsafe")

    def test_auto_benchmark_reports_all_gpu_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.mkv"
            source.write_bytes(b"source")
            candidates = [DecoderCandidate("amd@D129", "vaapi", "/dev/dri/renderD129"),
                          DecoderCandidate("intel@D128", "vaapi", "/dev/dri/renderD128")]
            measured = {"cpu": {"frames": 10}, "amd@D129": {"frames": 10},
                        "intel@D128": {"frames": 10}}
            captured = io.StringIO()
            with (patch("dubsync.core.probe.probe_media", return_value=SimpleNamespace(duration=60)),
                  patch("dubsync.core.ffmpeg_sampling.gpu_decode_candidates", return_value=candidates),
                  patch("dubsync.core.ffmpeg_sampling.benchmark_decoder_candidates",
                        return_value=measured) as benchmark,
                  patch("sys.stdout", captured)):
                exit_code = main(["--benchmark-decode", str(source), "--benchmark-gpu", "auto", "--json"])
            self.assertEqual(exit_code, 0)
            self.assertEqual(benchmark.call_args.kwargs["candidates"], candidates)
            self.assertEqual(set(json.loads(captured.getvalue())["measurements"]), set(measured))


if __name__ == "__main__":
    unittest.main(verbosity=2)
