"""Qt offscreen smoke: candidate review is accessible and strictly informative."""
from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QPushButton

from dubsync.core.models import EpisodeGroup, MediaInfo, StreamInfo
from dubsync.gui.episode_explorer import EpisodeExplorerDialog
from dubsync.gui.workbench import StudioReviewPanel
from dubsync.gui.main_window import MainWindow


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

    def test_assignment_needs_both_verified_visual_evidence_and_explicit_checkbox(self):
        report = {
            "master_filename": "Bildmaster.mkv", "verdict": "single_visual_candidate",
            "candidates": [
                {"candidate": 1, "filename": "Episode-3.mkv", "status": "insufficient",
                 "error": None, "visual_proof": {"anchors": 4}},
                {"candidate": 2, "filename": "Episode-2.mkv", "status": "verified",
                 "error": None, "visual_proof": {"anchors": 55, "coverage": .9,
                                                "independent_episode_proof": True,
                                                "safe_for_automatic_export": True}},
            ],
        }
        dialog = EpisodeExplorerDialog(report, allow_assignment=True)
        self.addCleanup(self._dispose, dialog)
        self.assertFalse(dialog.assign_button.isEnabled())
        dialog.results.selectRow(0)
        dialog.approval_checkbox.setChecked(True)
        self.assertFalse(dialog.assign_button.isEnabled())
        dialog.results.selectRow(1)
        self.assertTrue(dialog.assign_button.isEnabled())
        dialog.assign_button.click()
        self.assertEqual(dialog.selected_candidate, 2)
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)

    def test_approved_candidate_moves_into_current_editor_without_automatic_export(self):
        window = MainWindow()
        self.addCleanup(self._dispose, window)
        with tempfile.TemporaryDirectory() as tmp:
            master = str(Path(tmp) / "Show.S01E002.master.mkv")
            dub = str(Path(tmp) / "Show.S01E003.dub.mkv")
            Path(master).write_bytes(b"master")
            Path(dub).write_bytes(b"dub")
            window.groups = [EpisodeGroup("show|s01e002", "Episode 2", [master]),
                             EpisodeGroup("show|s01e003", "Episode 3", [dub])]
            window.selected_group = window.groups[0]
            window.selected_master = master
            window.episode_table.setRowCount(2)
            with patch.object(window, "_on_episode_selected") as refresh:
                window._adopt_episode_candidate(dub, master)
            refresh.assert_called_once()
            self.assertEqual(window.groups[0].files, sorted([master, dub]))
            self.assertEqual(window.groups[1].files, [])
            self.assertEqual(window.manual_candidate_assignments[dub], "show|s01e002")
            self.assertEqual(window.master_overrides["show|s01e002"], master)
            with patch("dubsync.gui.main_window.QMessageBox.information") as notice:
                window.export_batch()
            self.assertIn("Keine Stapelgruppen", notice.call_args.args[1])
            window.selected_group = window.groups[0]
            window.selected_master = dub
            with patch("dubsync.gui.main_window.QMessageBox.warning") as warning:
                window.export_selected()
            self.assertIn("Bildmaster", warning.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
