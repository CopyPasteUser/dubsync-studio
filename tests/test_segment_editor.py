"""Native Qt regressions for manual timeline editing and safety states."""
from __future__ import annotations

import os
import unittest

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog
except ImportError:
    QApplication = None

from dubsync.core.models import AlignmentResult, TimelineSegment


def _result() -> AlignmentResult:
    return AlignmentResult(
        source_path="/tmp/source.mkv", target_path="/tmp/master.mkv",
        slope=1, offset=0, anchors=[], confidence=0.96, inlier_ratio=0.96,
        coverage=0.98, median_hamming=3.0, rms_error=0.03,
        source_duration=20, target_duration=20, sample_interval=0.5,
    )


@unittest.skipUnless(QApplication is not None, "PySide6 erforderlich")
class SegmentEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def test_split_and_disable_produces_silent_interval(self):
        from dubsync.gui.segment_editor import SegmentEditorDialog
        dialog = SegmentEditorDialog(_result())
        self.assertEqual(dialog.table.rowCount(), 1)
        dialog.table.selectRow(0)
        dialog._split_selected()
        self.assertEqual(dialog.table.rowCount(), 2)
        dialog.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
        dialog._accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(dialog.segments[0].enabled)
        self.assertFalse(dialog.segments[1].enabled)

    def test_overlap_is_rejected_before_apply(self):
        from dubsync.gui.segment_editor import SegmentEditorDialog
        dialog = SegmentEditorDialog(_result())
        dialog._split_selected()
        dialog.table.cellWidget(1, 3).setValue(1.0)
        self.assertFalse(dialog.apply_button.isEnabled())

    def test_manual_sections_are_never_automatically_safe(self):
        from dataclasses import replace
        original = _result()
        edited = replace(original, manual_segments=True, segments=[
            TimelineSegment(0, 10, 0, 10, 0, 99, 99, 0)
        ])
        self.assertFalse(edited.safe_for_automatic_export)
        self.assertIsNone(edited.source_time(15.0))

    def test_undo_redo_reverses_splits_and_spinner_changes(self):
        from dubsync.gui.segment_editor import SegmentEditorDialog
        dialog = SegmentEditorDialog(_result())
        dialog._split_selected()
        self.assertEqual(dialog.table.rowCount(), 2)
        dialog._undo()
        self.assertEqual(dialog.table.rowCount(), 1)
        dialog._redo()
        self.assertEqual(dialog.table.rowCount(), 2)
        dialog.table.cellWidget(1, 3).setValue(10.5)
        self.assertAlmostEqual(dialog._read_segments()[1].target_start, 10.5)
        dialog._undo()
        self.assertAlmostEqual(dialog._read_segments()[1].target_start, 10.0)

    def test_selection_inspector_and_discard_confirmation(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        from dubsync.gui.segment_editor import SegmentEditorDialog
        dialog = SegmentEditorDialog(_result())
        dialog.show()
        self.assertIn("Masterdauer", dialog.selection_label.text())
        dialog._split_selected()
        self.assertEqual(dialog.timeline.selected_index, 1)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            dialog.reject()
        self.assertTrue(dialog.isVisible())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog.reject()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)


if __name__ == "__main__":
    unittest.main()
