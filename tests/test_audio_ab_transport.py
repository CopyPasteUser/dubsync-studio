"""Bounded A/B transport and cancellation without touching media sources."""
from __future__ import annotations

import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from dubsync.gui.main_window import MainWindow, SyncPreviewDialog


def _tracks():
    return [{"label": "Deutsch", "path": "/tmp/de.mkv", "stream_index": 1,
             "duration": 180., "offset": 5., "scale": 1., "master": False},
            {"label": "Master", "path": "/tmp/master.mkv", "stream_index": 1,
             "duration": 180., "offset": 0., "scale": 1., "master": True}]


class AudioABTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dispose(self, widget):
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_a_and_b_share_reference_time_but_choose_distinct_audio_sources(self):
        dialog = SyncPreviewDialog(_tracks(), 180.)
        self.addCleanup(self._dispose, dialog)
        requested = []
        dialog.preview_requested.connect(requested.append)
        dialog.position_combo.setCurrentIndex(3)
        dialog.time_spin.setValue(65.)
        dialog.master_button.click()
        dialog.source_button.click()
        self.assertEqual([item["path"] for item in requested],
                         ["/tmp/master.mkv", "/tmp/de.mkv"])
        self.assertEqual([item["target_time"] for item in requested], [65., 65.])
        self.assertTrue(all(item["sample_duration"] == 12. for item in requested))

    def test_recent_clip_cache_is_bounded_and_cleaned_on_close(self):
        dialog = SyncPreviewDialog(_tracks(), 180.)
        self.addCleanup(self._dispose, dialog)
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(dialog.transport, "play_clip", return_value=True),
              patch.object(dialog.transport, "release")):
            for index in range(5):
                dialog._pending_key = (0, float(index))
                path = Path(directory) / f"{index}.wav"
                path.write_bytes(b"test sample")
                self.assertTrue(dialog.preview_ready(str(path)))
            self.assertEqual(len(dialog._cache), 4)
            self.assertFalse((Path(directory) / "0.wav").exists())
            dialog.close()
            self.assertFalse(list(Path(directory).glob("*.wav")))

    def test_long_audition_request_is_explicit_and_transport_shows_minutes(self):
        dialog = SyncPreviewDialog(_tracks(), 180.)
        self.addCleanup(self._dispose, dialog)
        requests = []
        dialog.preview_requested.connect(requests.append)
        dialog.position_combo.setCurrentIndex(3)
        dialog.time_spin.setValue(90.)
        dialog.duration_combo.setCurrentIndex(2)
        dialog.source_button.click()
        self.assertEqual(requests[-1]["sample_duration"], 60.)
        dialog.transport._duration_changed(60_000)
        dialog.transport._position_changed(30_000)
        self.assertEqual(dialog.transport.position_label.text(), "00:30 / 01:00")

    def test_preview_ffmpeg_process_is_killed_after_cancel(self):
        window = MainWindow()
        self.addCleanup(self._dispose, window)
        captured = {}

        def capture_task(_label, task, _success):
            captured["task"] = task

        class StalledFFmpeg:
            def __init__(self, event):
                self.event = event
                self.killed = False

            def communicate(self, timeout=None):
                if timeout is not None:
                    self.event.set()
                    raise subprocess.TimeoutExpired("ffmpeg", timeout)
                return b"", b""

            def poll(self):
                return None if not self.killed else -9

            def kill(self):
                self.killed = True

        event = threading.Event()
        process = StalledFFmpeg(event)
        window._run_task = capture_task
        request = {"path": "/tmp/master.mkv", "stream_index": 1,
                   "target_time": 20., "scale": 1., "offset": 0.,
                   "duration": 180., "master": True}
        with (patch("dubsync.gui.main_window.require_media_tools", return_value=("ffmpeg", "ffprobe")),
              patch("dubsync.gui.main_window.subprocess.Popen", return_value=process) as launch):
            window._generate_preview_clip(request)
            with self.assertRaisesRegex(RuntimeError, "abgebrochen"):
                captured["task"](lambda *_: None, event)
        self.assertTrue(process.killed)
        self.assertIn("-protocol_whitelist", launch.call_args.args[0])

    def test_main_window_cleans_private_audio_previews_on_close(self):
        window = MainWindow()
        directory = window._preview_directory
        (directory / "test.wav").write_bytes(b"only a test preview")
        self.assertTrue(directory.is_dir())
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
