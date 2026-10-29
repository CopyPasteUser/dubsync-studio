"""A journal is not proof unless the current sources and output still match."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dubsync.core.batch import batch_export_groups, output_path_for_group
from dubsync.core.models import AlignmentResult, Anchor, EpisodeGroup, MediaInfo, StreamInfo


class VerifiedBatchResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.groups = []
        self.video = StreamInfo(0, "video", "mpeg4", width=320, height=240)
        self.audio = StreamInfo(1, "audio", "aac", language="eng")
        for episode in (1, 2):
            source = self.root / f"Show S01E{episode:02d} source.mkv"
            master = self.root / f"Show S01E{episode:02d} master.mkv"
            source.write_bytes((f"source-{episode}-" * 10000).encode())
            master.write_bytes((f"master-{episode}-" * 10000).encode())
            self.groups.append(EpisodeGroup(f"show|s01e{episode:02d}",
                                            f"Show · S01E{episode:02d}",
                                            [str(source), str(master)], "Show", 1, episode))

    def info(self, path):
        master = "master" in Path(path).name
        return MediaInfo(str(path), 60, "matroska", 800 if master else 1000,
                         [self.video, self.audio], 1, 1, 0)

    def align(self, source, target, **kwargs):
        return AlignmentResult(
            source_path=source, target_path=target, slope=1, offset=0,
            anchors=[Anchor(float(i), float(i), 1) for i in range(60)],
            confidence=.97, inlier_ratio=.99, coverage=.98,
            median_hamming=1, rms_error=.05, source_duration=60,
            target_duration=60, sample_interval=.5, visual_distinct_hashes=30,
        )

    def export(self, master, tracks, destination, **kwargs):
        output = Path(destination)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes((f"finished:{Path(master).name}:" * 3000).encode())
        return str(output)

    def run_batch(self, **kwargs):
        with (patch("dubsync.core.batch.probe_media", side_effect=self.info),
              patch("dubsync.core.batch.align_videos", side_effect=self.align) as align,
              patch("dubsync.core.batch.export_multilingual", side_effect=self.export) as export,
              patch("dubsync.core.batch.shutil.disk_usage",
                    return_value=type("Disk", (), {"free": 2**40})())):
            result = batch_export_groups(self.groups, str(self.root / "out"),
                                         journal_directory=self.root / "reports", **kwargs)
        return result, align.call_count, export.call_count

    def test_verified_completed_run_never_realigns_or_reexports(self):
        first, aligns, exports = self.run_batch()
        self.assertEqual((aligns, exports), (2, 2))
        second, aligns, exports = self.run_batch(
            resume_from_journal=first["batch_report_path"], skip_existing=False,
        )
        self.assertEqual((aligns, exports), (0, 0))
        self.assertEqual(len(second["skipped"]), 2)
        self.assertTrue(all("verifiziert" in message for message in second["skipped"]))

    def test_changed_source_is_not_considered_completed(self):
        first, _, _ = self.run_batch()
        source = Path(self.groups[0].files[0])
        source.write_bytes(b"new source contents")
        second, aligns, exports = self.run_batch(
            resume_from_journal=first["batch_report_path"], skip_existing=True,
        )
        self.assertEqual((aligns, exports), (0, 0))
        self.assertIn("Ausgabe existiert bereits", second["skipped"][0])
        self.assertIn("verifiziert", second["skipped"][1])

    def test_changed_output_is_not_considered_completed(self):
        first, _, _ = self.run_batch()
        output = output_path_for_group(str(self.root / "out"), self.groups[0])
        output.write_bytes(b"broken replacement")
        second, _, _ = self.run_batch(
            resume_from_journal=first["batch_report_path"], skip_existing=True,
        )
        self.assertIn("Ausgabe existiert bereits", second["skipped"][0])
        self.assertIn("verifiziert", second["skipped"][1])

    def test_changed_recipe_refuses_batch_before_writes(self):
        first, _, _ = self.run_batch()
        with self.assertRaisesRegex(ValueError, "passt nicht"):
            self.run_batch(resume_from_journal=first["batch_report_path"],
                           alignment_options={"audio_bitrate_kbps": 256})


if __name__ == "__main__":
    unittest.main()
