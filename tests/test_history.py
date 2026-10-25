from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dubsync.core.history import JobHistory, _json


class JobHistoryTests(unittest.TestCase):
    def test_start_finish_and_restart_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "jobs.sqlite3"
            history = JobHistory(path)
            job_id = history.start("export", "Episode S01E01", ["/media/show"], "/media/output")
            history.finish(job_id, "erfolgreich", "Export geprüft", details={"output": "/media/output/test.mkv"})
            recent = JobHistory(path).recent()
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["id"], job_id)
            self.assertEqual(recent[0]["status"], "erfolgreich")
            self.assertEqual(recent[0]["source_roots"], ["/media/show"])
            self.assertEqual(recent[0]["details"]["output"], "/media/output/test.mkv")

    def test_running_job_is_marked_interrupted_after_restart(self):
        with tempfile.TemporaryDirectory() as td:
            history = JobHistory(Path(td) / "jobs.sqlite3")
            job_id = history.start("batch", "Season batch")
            self.assertEqual(history.recover_interrupted(), 1)
            row = history.recent()[0]
            self.assertEqual(row["id"], job_id)
            self.assertEqual(row["status"], "unterbrochen")
            self.assertIsNotNone(row["finished_at"])
            self.assertEqual(history.recover_interrupted(), 0)

    def test_failed_job_keeps_error_details(self):
        with tempfile.TemporaryDirectory() as td:
            history = JobHistory(Path(td) / "jobs.sqlite3")
            job_id = history.start("analysis", "Analyse")
            history.finish(job_id, "fehlgeschlagen", "FFmpeg Fehler", error_text="could not decode")
            self.assertEqual(history.recent()[0]["error_text"], "could not decode")

    def test_history_retention_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            history = JobHistory(Path(td) / "jobs.sqlite3", max_entries=50)
            for index in range(56):
                jid = history.start("scan", f"Scan {index}")
                history.finish(jid, "erfolgreich", "OK")
            self.assertEqual(len(history.recent(200)), 50)


    def test_oversized_details_are_truncated_as_valid_json(self):
        import json
        encoded = _json({"large": "x" * 5000, "keep": "value"}, max_chars=160)
        self.assertLessEqual(len(encoded), 160)
        decoded = json.loads(encoded)
        self.assertTrue(decoded["_truncated"])

        encoded_list = _json(["path-" + str(i) for i in range(500)], max_chars=160)
        self.assertLessEqual(len(encoded_list), 160)
        self.assertIsInstance(json.loads(encoded_list), list)

    def test_recent_orders_same_second_jobs_newest_first(self):
        with tempfile.TemporaryDirectory() as td:
            history = JobHistory(Path(td) / "jobs.sqlite3")
            ids = [history.start("scan", f"Job {n}") for n in range(4)]
            labels = [row["label"] for row in history.recent(4)]
            self.assertEqual(labels, ["Job 3", "Job 2", "Job 1", "Job 0"])

    def test_unknown_status_is_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            history = JobHistory(Path(td) / "jobs.sqlite3")
            jid = history.start("export", "Episode")
            history.finish(jid, "maybe", "Unknown")
            self.assertEqual(history.recent()[0]["status"], "mit Warnungen")


if __name__ == "__main__":
    unittest.main(verbosity=2)
