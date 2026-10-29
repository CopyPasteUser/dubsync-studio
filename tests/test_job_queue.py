"""1.6 queue persistence, fail-closed retry and resource budget regression tests."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dubsync.core.batch_diagnostics import BatchDiagnostic
from dubsync.core.job_queue import BatchJobQueue, QueueError, adaptive_resource_options
from dubsync.core.models import AlignmentResult, Anchor, EpisodeGroup, MediaInfo, StreamInfo


class QueueTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.queue = BatchJobQueue(self.root / "private" / "jobs.sqlite3")
        self.groups = [
            EpisodeGroup(f"series|s01e{n:02}", f"Series S01E{n:02}",
                         [str(self.root / f"S01E{n:02}-de.mkv"),
                          str(self.root / f"S01E{n:02}-en.mkv")], "Series", 1, n)
            for n in (1, 2)
        ]

    def enqueue(self, **kwargs):
        return self.queue.enqueue(self.groups, str(self.root / "output"),
                                  options={"roots": [str(self.root)],
                                           "alignment_options": {"sampling_workers": 2,
                                                                 "audio_bitrate_kbps": 160}}, **kwargs)

    def test_persists_private_snapshot_across_process_lifetimes(self):
        job_id = self.enqueue()
        second = BatchJobQueue(self.queue.path)
        self.assertEqual(second.get(job_id)["state"], "queued")
        self.assertEqual(second.get(job_id)["spec"]["groups"][1]["episode_number"], 2)
        self.assertEqual(second.recent()[0]["id"], job_id)
        if os.name == "posix":
            self.assertEqual(self.queue.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.queue.path.parent.stat().st_mode & 0o777, 0o700)

    def test_recovers_interrupted_and_paused_without_auto_running(self):
        first = self.enqueue()
        second = self.enqueue()
        self.queue._claim(first)
        self.queue._claim(second)
        self.queue.pause(second, True)
        fresh = BatchJobQueue(self.queue.path)
        self.assertEqual(fresh.recover_interrupted(), 0, "Eine zweite Instanz darf aktive Jobs nicht stehlen")
        self.queue._update(first, owner_pid=99999999)
        self.queue._update(second, owner_pid=99999999)
        self.assertEqual(fresh.recover_interrupted(), 2)
        self.assertEqual(fresh.get(first)["state"], "interrupted")
        self.assertEqual(fresh.get(second)["state"], "interrupted")
        self.assertEqual(fresh.recover_interrupted(), 0)

    def test_atomic_claim_and_no_duplicate_run(self):
        job_id = self.enqueue()
        self.queue._claim(job_id)
        with self.assertRaises(QueueError):
            BatchJobQueue(self.queue.path)._claim(job_id)

    def test_journal_is_persisted_before_batch_processing_and_resume_is_safe(self):
        job_id = self.enqueue()
        calls = []

        def fake_batch(groups, directory, **kwargs):
            calls.append(kwargs)
            kwargs["journal_created"](str(self.root / f"journal-{len(calls)}.json"))
            self.assertEqual(self.queue.get(job_id)["journal_path"],
                             str(self.root / f"journal-{len(calls)}.json"))
            kwargs["progress"](1, 2, "Episode 1 fertig")
            return {"exported": ["one.mkv"], "skipped": [], "failed": [],
                    "cancelled": [], "warnings": []}

        with patch("dubsync.core.job_queue.batch_export_groups", side_effect=fake_batch):
            first = self.queue.run(job_id)
            self.assertEqual(first["exported"], ["one.mkv"])
            self.assertEqual(self.queue.get(job_id)["state"], "completed")
            with self.assertRaises(QueueError):
                self.queue.run(job_id)
            self.queue._update(job_id, state="interrupted")
            self.queue.run(job_id)

        self.assertIsNone(calls[0]["resume_from_journal"])
        self.assertEqual(calls[1]["resume_from_journal"], str(self.root / "journal-1.json"))
        self.assertFalse(calls[1]["overwrite_existing"])
        self.assertTrue(calls[1]["skip_existing"])
        self.assertEqual(calls[0]["alignment_options"], calls[1]["alignment_options"])

    def test_selected_manual_resume_keeps_recipe(self):
        journal = str(self.root / "manual.json")
        job_id = self.enqueue(initial_resume_journal=journal)
        seen = []

        def fake_batch(groups, directory, **kwargs):
            seen.append(kwargs)
            return {"exported": [], "skipped": ["Episode 1: verifiziert"],
                    "failed": [], "cancelled": [], "warnings": []}

        with patch("dubsync.core.job_queue.batch_export_groups", side_effect=fake_batch):
            self.queue.run(job_id)
        self.assertEqual(seen[0]["resume_from_journal"], journal)
        self.assertEqual(seen[0]["alignment_options"]["sampling_workers"], 2)
        self.assertEqual(self.queue.get(job_id)["state"], "completed")

    def test_failed_export_stays_inspectable_and_runnable(self):
        job_id = self.enqueue()
        with patch("dubsync.core.job_queue.batch_export_groups", side_effect=RuntimeError("probe crashed")):
            with self.assertRaisesRegex(RuntimeError, "probe crashed"):
                self.queue.run(job_id)
        self.assertEqual(self.queue.get(job_id)["state"], "failed")
        self.assertIn("probe crashed", self.queue.get(job_id)["error_text"])

    def test_retry_problematic_creates_subset_without_overwrite(self):
        job_id = self.enqueue()
        journal = BatchDiagnostic(self.groups, alignment_options={},
                                  skip_existing=True, overwrite_existing=False,
                                  organize_by_series=True, directory=self.root / "reports")
        journal.conclude(1, "exported", "OK")
        journal.conclude(2, "failed", "Kein sicherer Abgleich")
        self.queue._update(job_id, state="needs_review", journal_path=str(journal.path))
        retry = self.queue.retry_problematic(job_id)
        spec = self.queue.get(retry)["spec"]
        self.assertEqual(len(spec["groups"]), 1)
        self.assertEqual(spec["groups"][0]["episode_number"], 2)
        self.assertTrue(spec["options"]["skip_existing"])
        self.assertFalse(spec["options"]["overwrite_existing"])

    def test_rejects_journal_with_different_source_mapping(self):
        job_id = self.enqueue()
        journal = BatchDiagnostic(self.groups, alignment_options={},
                                  skip_existing=True, overwrite_existing=False,
                                  organize_by_series=True, directory=self.root / "reports")
        payload = json.loads(journal.path.read_text(encoding="utf-8"))
        payload["episodes"][0]["files"] = payload["episodes"][1]["files"]
        journal.path.write_text(json.dumps(payload), encoding="utf-8")
        self.queue._update(job_id, state="needs_review", journal_path=str(journal.path))
        with self.assertRaisesRegex(QueueError, "Quellzuordnung"):
            self.queue.retry_problematic(job_id)


class AdaptiveResourcesTests(unittest.TestCase):
    def test_adapts_concurrency_not_sync_accuracy(self):
        requested = {"sampling_workers": 2, "max_samples": 1800,
                     "interval": .5, "hardware_accel": "auto"}
        hardware = {"logical_cpus": 8, "ram_gib": 16}
        fast, _ = adaptive_resource_options(requested, specs=hardware,
                                           load_average=.5, available_ram_gib=12)
        busy, reason = adaptive_resource_options(requested, specs=hardware,
                                                 load_average=7.5, available_ram_gib=12)
        low_ram, _ = adaptive_resource_options(requested, specs=hardware,
                                              load_average=.5, available_ram_gib=2)
        self.assertEqual([fast["sampling_workers"], busy["sampling_workers"],
                          low_ram["sampling_workers"]], [2, 1, 1])
        self.assertIn("CPU", reason)
        self.assertEqual(busy["max_samples"], fast["max_samples"])
        self.assertEqual(busy["hardware_accel"], requested["hardware_accel"])


class RestartIntegrationTests(unittest.TestCase):
    """Real batch journal and proof handling, mocked only at media-tool boundary."""

    def test_restart_resumes_only_proven_completed_episode(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        groups = []
        for episode in (1, 2):
            source = root / f"Show S01E{episode:02} source.mkv"
            master = root / f"Show S01E{episode:02} master.mkv"
            source.write_bytes((f"source-{episode}-" * 10000).encode())
            master.write_bytes((f"master-{episode}-" * 10000).encode())
            groups.append(EpisodeGroup(f"show|s01e{episode:02}", f"Show S01E{episode:02}",
                                       [str(source), str(master)], "Show", 1, episode))
        queue_path = root / "private" / "jobs.sqlite3"
        queue = BatchJobQueue(queue_path)
        job_id = queue.enqueue(groups, str(root / "out"), options={
            "journal_directory": str(root / "reports"), "skip_existing": False,
            "overwrite_existing": False, "alignment_options": {"sampling_workers": 1},
        })
        video = StreamInfo(0, "video", "mpeg4", width=320, height=240)
        audio = StreamInfo(1, "audio", "aac", language="eng")

        def info(path):
            master = "master" in Path(path).name
            return MediaInfo(str(path), 60, "matroska", 800 if master else 1000,
                             [video, audio], 1, 1, 0)

        def align(source, target, **kwargs):
            return AlignmentResult(source_path=source, target_path=target,
                                   slope=1, offset=0,
                                   anchors=[Anchor(float(i), float(i), 1) for i in range(60)],
                                   confidence=.97, inlier_ratio=.99, coverage=.98,
                                   median_hamming=1, rms_error=.05,
                                   source_duration=60, target_duration=60,
                                   sample_interval=.5, visual_distinct_hashes=30)

        exports = []
        stop = threading.Event()

        def export(master, tracks, destination, **kwargs):
            output = Path(destination)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes((f"finished:{Path(master).name}:" * 3000).encode())
            exports.append(str(output))
            if len(exports) == 1:
                stop.set()
            return str(output)

        with (patch("dubsync.core.batch.probe_media", side_effect=info),
              patch("dubsync.core.batch.align_videos", side_effect=align) as align_mock,
              patch("dubsync.core.batch.export_multilingual", side_effect=export),
              patch("dubsync.core.batch.shutil.disk_usage",
                    return_value=type("Disk", (), {"free": 2**40})())):
            first = queue.run(job_id, cancel_event=stop)
            self.assertEqual(queue.get(job_id)["state"], "cancelled")
            self.assertTrue(first.get("batch_report_path"))
            self.assertEqual(len(exports), 1)

            # The app has restarted; it knows only the saved SQLite job and
            # journal. Existing output remains byte-for-byte untouched.
            initial_output = Path(exports[0]).read_bytes()
            fresh = BatchJobQueue(queue_path)
            self.assertEqual(fresh.recover_interrupted(), 0)
            completed = fresh.run(job_id, cancel_event=threading.Event())
            self.assertEqual(align_mock.call_count, 2)
            self.assertEqual(len(exports), 2)
            self.assertEqual(Path(exports[0]).read_bytes(), initial_output)
            self.assertEqual(len(completed["exported"]), 1)
            self.assertTrue(any("verifiziert" in text for text in completed["skipped"]))
            self.assertEqual(fresh.get(job_id)["state"], "completed")

            # A *second* interruption must retain proof from the first run,
            # even though episode one was only 'skipped' on the second run.
            fresh._update(job_id, state="interrupted")
            third = BatchJobQueue(queue_path).run(job_id, cancel_event=threading.Event())
            self.assertEqual(len(exports), 2)
            self.assertEqual(align_mock.call_count, 2)
            self.assertEqual(len(third["skipped"]), 2)
            self.assertTrue(all("verifiziert" in text for text in third["skipped"]))


if __name__ == "__main__":
    unittest.main()
