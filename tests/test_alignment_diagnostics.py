"""No GUI required: diagnostics, rejected analysis, recovery candidate selection."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from dubsync.core.alignment import (
    AlignmentError, FrameSample, _same_timeline_result, align_videos,
)
from dubsync.core.alignment_diagnostics import AlignmentDiagnostic, diagnostics_directory, latest_diagnostic
from dubsync.core.models import Anchor, MediaInfo, StreamInfo


def media(path: str, duration: float = 54.0) -> MediaInfo:
    return MediaInfo(path=path, duration=duration, format_name="matroska",
                     size_bytes=2345678, streams=[StreamInfo(index=0, codec_type="video",
                                                              codec_name="h264", width=640,
                                                              height=480, fps=24.0)],
                     video_streams=1, audio_streams=0, subtitle_streams=0)


class AlignmentDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.env = patch.dict("os.environ", {"XDG_STATE_HOME": self.folder.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.source = str(Path(self.folder.name) / "Language-DE-E02.mkv")
        self.target = str(Path(self.folder.name) / "Language-EN-E02.mkv")

    def test_report_has_thresholds_metrics_and_no_full_paths(self):
        diag = AlignmentDiagnostic(self.source, self.target, 1.0, 1200, "none")
        diag.metadata(media(self.source), media(self.target))
        anchors = [Anchor(float(i), float(i + (0 if i < 12 else 8)), 3)
                   for i in range(25)]
        from dubsync.core.alignment import _evaluate_anchors
        result = _evaluate_anchors(anchors, self.source, self.target, media(self.source),
                                   media(self.target), 1.0, 1.0, 1200, "none", 30)
        diag.complete(result, "sequence")
        path = diag.save()
        self.assertIsNotNone(path)
        self.assertEqual(path.parent, diagnostics_directory())
        data = json.loads(path.read_text())
        self.assertIn(data["decision"]["status"], ("unsafe", "safe"))
        self.assertIn("confidence", data["decision"]["conditions"])
        self.assertIn("unverified_middle_ratio", data["decision"]["conditions"])
        self.assertEqual(data["requested"]["max_samples"], 1200)
        self.assertEqual(data["files"]["source"]["filename"], "Language-DE-E02.mkv")
        self.assertNotIn(self.folder.name, path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(latest_diagnostic(), path)

    def test_long_unverified_middle_is_explained_and_blocks_auto_export(self):
        from dubsync.core.alignment import _evaluate_anchors
        times = list(range(300)) + list(range(700, 1000))
        anchors = [Anchor(float(i), float(i), 0) for i in times]
        result = _evaluate_anchors(anchors, self.source, self.target,
                                   media(self.source, 1000), media(self.target, 1000),
                                   1.0, 1.0, 1200, "none", 1000, visual_distinct_hashes=600)
        self.assertGreater(result.confidence, 0.62)
        self.assertEqual(result.coverage, 0.6)
        self.assertFalse(result.safe_for_automatic_export)
        self.assertTrue(any("ungeprüfter Abschnitt" in warning for warning in result.warnings))
        diagnostic = AlignmentDiagnostic(self.source, self.target, 1.0, 1200, "none")
        diagnostic.complete(result, "sequence")
        self.assertIn("unverified_middle_ratio", diagnostic.payload["decision"]["failed_conditions"])

    def test_failure_report_does_not_expose_full_media_paths(self):
        diagnostic = AlignmentDiagnostic(self.source, self.target, 1.0, 1200, "none")
        try:
            raise RuntimeError(f"Decoder konnte {self.source} nicht öffnen")
        except RuntimeError as exc:
            diagnostic.failure(exc)
        path = diagnostic.save()
        self.assertIsNotNone(path)
        data = json.loads(path.read_text())
        self.assertEqual(data["decision"]["status"], "error")
        self.assertIn("<source file>", data["decision"]["traceback"])
        self.assertNotIn(self.source, data["decision"]["traceback"])

    def test_same_timeline_rejects_identical_blank_frames(self):
        source = [FrameSample(float(i), 0) for i in range(30)]
        target = [FrameSample(float(i), 0) for i in range(30)]
        diag = AlignmentDiagnostic(self.source, self.target, 1.0, 1200, "none")
        result = _same_timeline_result(self.source, self.target, media(self.source, 30),
                                       media(self.target, 30), source, target, 1.0, 1200, "none", diag)
        self.assertIsNone(result)
        self.assertEqual(diag.payload["stages"]["same_timeline"]["reason"], "insufficient_visual_diversity")

    def test_identical_static_videos_do_not_pass_automatic_export(self):
        frames = [FrameSample(float(i), 0) for i in range(30)]
        with patch("dubsync.core.alignment.sample_video", side_effect=[frames, frames]):
            result = align_videos(self.source, self.target, interval=1.0,
                                  max_samples=1200, hardware_accel="none",
                                  source_info=media(self.source, 30),
                                  target_info=media(self.target, 30))
        self.assertFalse(result.safe_for_automatic_export)
        self.assertEqual(result.visual_distinct_hashes, 1)
        self.assertTrue(any("Standbilder" in warning for warning in result.warnings))
        report = json.loads(latest_diagnostic().read_text())
        self.assertIn("visual_distinct_hashes", report["decision"]["failed_conditions"])

    def test_bad_sequence_with_high_coverage_also_triggers_fallback(self):
        rng = np.random.default_rng(56)
        hashes = [int(rng.integers(0, 2**63, dtype=np.int64)) for _ in range(48)]
        source_samples = [FrameSample(float(i), h) for i, h in enumerate(hashes)]
        target_samples = [FrameSample(float(i + 4), h) for i, h in enumerate(hashes)]
        # The global matcher covers much of the episode, but has a timeline cut.
        # Previously high coverage would prevent the fallback from being tried.
        bad_sequence = [Anchor(float(i), float(i + 4 + (0 if i < 24 else 11)), 2)
                        for i in range(45)]
        source_info = media(self.source, 48)
        target_info = media(self.target, 55)
        with patch("dubsync.core.alignment.sample_video", side_effect=[source_samples, target_samples]), \
             patch("dubsync.core.alignment._sequence_alignment", return_value=bad_sequence):
            result = align_videos(self.source, self.target, interval=1.0,
                                  max_samples=1200, hardware_accel="none",
                                  source_info=source_info, target_info=target_info)
        self.assertTrue(result.safe_for_automatic_export)
        self.assertAlmostEqual(result.offset, 4.0, places=1)
        self.assertAlmostEqual(result.slope, 1.0, places=1)
        path = latest_diagnostic()
        report = json.loads(path.read_text())
        self.assertEqual(report["decision"]["method"], "feature_fallback")
        self.assertEqual(report["decision"]["status"], "safe")
        self.assertTrue(report["stages"]["fallback_decision"]["fallback_started"])
        self.assertEqual(report["stages"]["candidate_comparison"]["sequence"]["safe"], False)

    def test_analysis_error_is_persisted_and_reraised(self):
        with patch("dubsync.core.alignment.probe_media", side_effect=AlignmentError("Probe kaputt")):
            with self.assertRaisesRegex(AlignmentError, "Probe kaputt"):
                align_videos(self.source, self.target, interval=1.0)
        report = json.loads(latest_diagnostic().read_text())
        self.assertEqual(report["decision"]["status"], "error")
        self.assertIn("Probe kaputt", report["decision"]["traceback"])

    def test_diagnostic_write_failure_does_not_hide_analysis_error(self):
        with patch("dubsync.core.alignment_diagnostics.diagnostics_directory", return_value=Path("/proc/no_dubsync_dir")), \
             patch("dubsync.core.alignment.probe_media", side_effect=AlignmentError("Analysefehler")):
            with self.assertRaisesRegex(AlignmentError, "Analysefehler"):
                align_videos(self.source, self.target, interval=1.0)


if __name__ == "__main__":
    unittest.main()
