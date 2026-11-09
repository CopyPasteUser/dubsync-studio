"""Read-only candidate explorer: ranking never becomes an export permission."""
from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dubsync.cli import main
from dubsync.core.alignment import AlignmentError
from dubsync.core.episode_explorer import rank_episode_candidates


def _result(*, safe=True, coverage=.8, anchors=30):
    return SimpleNamespace(anchors=[object()] * anchors, visual_distinct_hashes=25,
                           coverage=coverage, inlier_ratio=.9, median_hamming=6.,
                           safe_for_automatic_export=safe)


class EpisodeExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.master = self._video("master.mkv")
        self.first = self._video("first.mkv")
        self.second = self._video("second.mkv")

    def _video(self, name):
        path = Path(self.temp.name) / name
        path.write_bytes(b"placeholder; decoding is mocked")
        return str(path)

    def test_single_verified_candidate_ranks_first_but_cannot_export(self):
        def compare(source, master, **kwargs):
            self.assertEqual(master, self.master)
            self.assertTrue(kwargs["frame_cache_enabled"])
            return _result(coverage=.12) if source == self.first else _result(coverage=.79)

        with (patch("dubsync.core.episode_explorer.align_videos", side_effect=compare),
              patch("dubsync.core.episode_explorer._is_independent_episodic_match",
                    side_effect=lambda item: item.coverage > .7)):
            report = rank_episode_candidates(self.master, [self.first, self.second])
        self.assertEqual(report["verdict"], "single_visual_candidate")
        self.assertEqual(report["suggested_candidate"], 2)
        self.assertEqual([item["status"] for item in report["candidates"]],
                         ["verified", "insufficient"])
        self.assertFalse(report["automatic_reassignment"])
        self.assertFalse(report["automatic_export_allowed"])

    def test_two_verified_candidates_are_explicitly_ambiguous(self):
        with (patch("dubsync.core.episode_explorer.align_videos", return_value=_result()),
              patch("dubsync.core.episode_explorer._is_independent_episodic_match", return_value=True)):
            report = rank_episode_candidates(self.master, [self.first, self.second])
        self.assertEqual(report["verdict"], "multiple_visual_candidates")
        self.assertIsNone(report["suggested_candidate"])

    def test_repeated_intro_or_unreliable_alignment_is_not_verified(self):
        with (patch("dubsync.core.episode_explorer.align_videos", return_value=_result(safe=True)),
              patch("dubsync.core.episode_explorer._is_independent_episodic_match", return_value=False)):
            report = rank_episode_candidates(self.master, [self.first])
        self.assertEqual(report["verdict"], "unverified")
        self.assertEqual(report["candidates"][0]["status"], "insufficient")
        self.assertIsNone(report["suggested_candidate"])

    def test_io_errors_are_not_misreported_as_mismatching_episodes(self):
        with patch("dubsync.core.episode_explorer.align_videos", side_effect=OSError("NAS weg")):
            report = rank_episode_candidates(self.master, [self.first])
        self.assertEqual(report["verdict"], "analysis_incomplete")
        self.assertEqual(report["candidates"][0]["status"], "error")
        with patch("dubsync.core.episode_explorer.align_videos",
                   side_effect=AlignmentError("Zu wenige passende Bildanker gefunden.")):
            report = rank_episode_candidates(self.master, [self.first])
        self.assertEqual(report["candidates"][0]["status"], "no_match")

    def test_duplicate_paths_limits_and_cancellation(self):
        for candidates in ([self.master], [self.first, self.first], [self.first] * 9):
            with self.assertRaises(ValueError):
                rank_episode_candidates(self.master, candidates)
        cancelled = threading.Event()
        cancelled.set()
        with (patch("dubsync.core.episode_explorer.align_videos") as align,
              self.assertRaises(RuntimeError)):
            rank_episode_candidates(self.master, [self.first], cancel_event=cancelled)
        align.assert_not_called()

    def test_cli_json_reports_evidence_with_no_automatic_reassignment(self):
        report = {"master_filename": "master.mkv", "verdict": "single_visual_candidate",
                  "suggested_candidate": 1, "candidates": [],
                  "automatic_reassignment": False, "automatic_export_allowed": False}
        stdout = io.StringIO()
        with (patch("dubsync.core.episode_explorer.rank_episode_candidates", return_value=report),
              patch("sys.stdout", stdout)):
            code = main(["--explore-episode", self.master, self.first, "--json"])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(stdout.getvalue())["automatic_export_allowed"])


if __name__ == "__main__":
    unittest.main()
