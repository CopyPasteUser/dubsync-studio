"""Offline UI and safety checks for the 2.0 review and queue controls."""
from __future__ import annotations

import os
import unittest
import tempfile
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from dubsync.gui.queue_room import QueueControlRoom
    from dubsync.gui.workbench import (AlignmentTimeline, FrameReviewWorker,
                                       StudioReviewPanel, review_position)
    from dubsync.core.models import AlignmentResult, Anchor, EpisodeGroup, MediaInfo, StreamInfo, TimelineSegment
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "Requires native Qt GUI dependencies")
class ReviewWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def result(*, segments=None, manual=False):
        return AlignmentResult(
            source_path="/not-mounted/source.mkv", target_path="/not-mounted/master.mkv",
            slope=1.0, offset=10.0,
            anchors=[Anchor(x, x + 10, 2) for x in (2, 5, 15, 25, 35, 45, 55, 65, 75, 85)],
            confidence=.96, inlier_ratio=.93, coverage=.91, median_hamming=3,
            rms_error=.18, source_duration=90, target_duration=110,
            sample_interval=1.0, segments=segments or [], manual_segments=manual,
        )

    @staticmethod
    def media(path: str, duration: float = 110) -> MediaInfo:
        return MediaInfo(path=path, duration=duration, format_name="matroska",
                         size_bytes=1000, streams=[StreamInfo(0, "video", codec_name="h264")],
                         video_streams=1, audio_streams=0, subtitle_streams=0)

    def test_unmatched_segment_never_shows_guessed_frame(self):
        pair = self.result(segments=[
            TimelineSegment(0, 20, 10, 30, 4, 3, .1, 4),
            TimelineSegment(40, 70, 60, 90, 5, 3, .1, 4),
        ])
        self.assertEqual(review_position(pair, 45, 90)[0], None)
        self.assertIn("stumm", review_position(pair, 45, 90)[1])
        self.assertEqual(review_position(pair, 15, 90)[0], 5)
        self.assertEqual(review_position(None, 50, 90)[0], 50)
        self.assertIn("nominell", review_position(None, 50, 90)[1].lower())
        self.assertIsNone(review_position(None, 100, 90)[0])

    def test_review_does_not_access_media_until_explicit_click(self):
        panel = StudioReviewPanel()
        try:
            group = EpisodeGroup("episode", "Episode S01E01", ["/not-mounted/master.mkv", "/not-mounted/source.mkv"])
            with mock.patch("dubsync.gui.workbench._extract_frame") as frame:
                panel.show_group(group, group.files[0],
                                 {p: self.media(p) for p in group.files}, {})
                panel.timeline.set_position(25, emit=True)
                self.app.processEvents()
                self.assertEqual(panel.source_combo.count(), 1)
                self.assertEqual(panel.review_tabs.count(), 2)
                self.assertTrue(panel.compare_button.isEnabled())
                panel.set_processing_enabled(False)
                self.assertFalse(panel.compare_button.isEnabled())
                panel.set_processing_enabled(True)
                self.assertTrue(panel.compare_button.isEnabled())
                frame.assert_not_called()
        finally:
            panel.close()
            panel.deleteLater()

    def test_timeline_clamps_cursor_and_zoom_and_supports_keyboard(self):
        timeline = AlignmentTimeline()
        try:
            timeline.set_data(100, self.result())
            timeline.set_position(95)
            timeline.set_zoom(99)
            self.assertEqual(timeline.zoom, 16)
            timeline.show()
            timeline.setFocus()
            QTest.keyClick(timeline, Qt.Key.Key_Right)
            self.assertEqual(timeline.position, 100)
            QTest.keyClick(timeline, Qt.Key.Key_Home)
            self.assertEqual(timeline.position, 0)
            timeline.set_zoom(0.5)
            self.assertEqual(timeline.zoom, 1)
        finally:
            timeline.close()
            timeline.deleteLater()

    def test_refresh_keeps_review_cursor_and_zoom_when_metrics_change(self):
        panel = StudioReviewPanel()
        try:
            group = EpisodeGroup("episode", "E01", ["/offline/master.mkv", "/offline/source.mkv"])
            media = {p: self.media(p) for p in group.files}
            original = self.result()
            panel.show_group(group, group.files[0], media,
                             {(group.files[0], group.files[1]): original})
            panel.timeline.set_position(36, emit=True)
            panel.timeline.set_zoom(4)
            panel.refresh({(group.files[0], group.files[1]): self.result(manual=True)})
            self.assertEqual(panel.timeline.position, 36)
            self.assertEqual(panel.timeline.zoom, 4)
            self.assertIn("PRÜFUNG ERFORDERLICH", panel.quality_label.text())
        finally:
            panel.close()
            panel.deleteLater()

    def test_native_three_panes_keep_existing_editor_and_batch_actions(self):
        from dubsync.gui.main_window import MainWindow
        from PySide6.QtWidgets import QSplitter
        with tempfile.TemporaryDirectory(prefix="dubsync-2-test-") as directory:
            with mock.patch.dict(os.environ, {
                "XDG_CONFIG_HOME": directory, "XDG_STATE_HOME": directory,
                "XDG_CACHE_HOME": directory,
            }):
                window = MainWindow()
                try:
                    window.show()
                    self.app.processEvents()
                    group = EpisodeGroup("demo", "DEMO · S01E01", ["/offline/master.mkv", "/offline/source.mkv"])
                    window.selected_group = group
                    window.groups = [group]
                    window.media_cache = {p: self.media(p) for p in group.files}
                    window._render_group(group)
                    splitters = window.findChildren(QSplitter)
                    self.assertTrue(any(splitter.count() == 3 for splitter in splitters))
                    self.assertEqual(window.review_panel.source_combo.count(), 1)
                    self.assertIn(group.files[0], window.source_rows)
                    window._navigate_to(1)
                    self.assertIsNotNone(window.queue_room)
                    window._navigate_to(0)
                    window._focus_episode_search()
                    self.assertTrue(window.episode_search.hasFocus() or
                                    window.focusWidget() is window.episode_search)
                finally:
                    window.project_dirty = False
                    window.close()
                    window.deleteLater()

    def test_frame_worker_emits_pair_or_unmatched_master_without_writing(self):
        frames = []
        worker = FrameReviewWorker("master", "source", 5, None)
        worker.ready.connect(frames.append)
        with mock.patch("dubsync.gui.workbench._extract_frame", return_value=b"JPEG") as mock_frame:
            worker.run()
        self.assertEqual(len(frames), 1)
        self.assertIsNone(frames[0].source)
        mock_frame.assert_called_once()


@unittest.skipUnless(HAS_QT, "Requires native Qt GUI dependencies")
class QueueRoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_queue_never_autostarts_and_requires_explicit_selection(self):
        room = QueueControlRoom()
        try:
            starts, retries, drains = [], [], []
            room.startRequested.connect(starts.append)
            room.retryRequested.connect(retries.append)
            room.drainRequested.connect(lambda: drains.append(True))
            entries = [
                {"id": "a", "label": "Folge A", "state": "interrupted", "journal_path": "/tmp/report", "progress_current": 1, "progress_total": 2},
                {"id": "b", "label": "Folge B", "state": "completed", "journal_path": "/tmp/report", "progress_current": 2, "progress_total": 2},
            ]
            room.set_entries(entries)
            self.assertEqual(starts, [])
            self.assertTrue(room.start_button.isEnabled())
            room.start_button.click()
            self.assertEqual(starts, ["a"])
            room.retry_button.click()
            self.assertEqual(retries, ["a"])
            room.table.selectRow(1)
            self.assertFalse(room.start_button.isEnabled())
            self.assertTrue(room.retry_button.isEnabled())
            room.set_entries(entries, busy=True)
            self.assertFalse(room.retry_button.isEnabled())
            room.set_entries([dict(entries[0], state="queued", journal_path=None)])
            self.assertTrue(room.drain_button.isEnabled())
            room.drain_button.click()
            self.assertEqual(drains, [True])
        finally:
            room.close()
            room.deleteLater()


if __name__ == "__main__":
    unittest.main()
