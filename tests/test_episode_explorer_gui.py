"""Qt offscreen smoke: candidate review is accessible and strictly informative."""
from __future__ import annotations

import unittest

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QPushButton

from dubsync.core.models import EpisodeGroup, MediaInfo, StreamInfo
from dubsync.gui.episode_explorer import EpisodeExplorerDialog
from dubsync.gui.workbench import StudioReviewPanel


class EpisodeExplorerGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dispose(self, widget):
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_dialog_shows_quality_and_cannot_approve_or_export(self):
        report = {
            "master_filename": "Bildmaster.mkv", "verdict": "single_visual_candidate",
            "candidates": [{
                "filename": "Dub-S01E02.mkv", "candidate": 2,
                "status": "verified", "error": None,
                "visual_proof": {"anchors": 42, "coverage": .8, "median_hamming": 5.6},
            }],
        }
        dialog = EpisodeExplorerDialog(report)
        self.addCleanup(self._dispose, dialog)
        self.assertEqual(dialog.results.rowCount(), 1)
        self.assertIn("Bildinhalt bestätigt", dialog.results.item(0, 1).text())
        self.assertIn("80.0%", dialog.results.item(0, 3).text())
        self.assertFalse(any(button.text().lower().startswith(("export", "zuordnen"))
                             for button in dialog.findChildren(QPushButton)))

    def test_candidate_button_only_when_master_metadata_loaded(self):
        panel = StudioReviewPanel()
        self.addCleanup(self._dispose, panel)
        group = EpisodeGroup("test|s01e001", "Test S01E01", ["/tmp/master.mkv"])
        panel.show_group(group, "/tmp/master.mkv", {}, {})
        self.assertFalse(panel.candidate_button.isEnabled())
        media = MediaInfo("/tmp/master.mkv", 60.0, "matroska", 10, [
            StreamInfo(0, "video", "h264"),
        ], 1, 0, 0)
        panel.show_group(group, "/tmp/master.mkv", {media.path: media}, {})
        self.assertTrue(panel.candidate_button.isEnabled())
        panel.reset()
        self.assertFalse(panel.candidate_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
