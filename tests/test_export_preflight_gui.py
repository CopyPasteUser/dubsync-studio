"""Single export preflight blocks obvious unsafe cases and is keyboard reachable."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from dubsync.core.export_preflight import inspect_single_export, PreflightLine
from dubsync.core.models import TrackChoice
from dubsync.gui.export_preflight import ExportPreflightDialog


class ExportPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.master = self.root / "master.mkv"
        self.source = self.root / "dub.mkv"
        self.master.write_bytes(b"master")
        self.source.write_bytes(b"audio")
        self.alignment = SimpleNamespace(source_path=str(self.source), target_path=str(self.master),
                                         safe_for_automatic_export=True, coverage=.1)

    def _lines(self, **kwargs):
        return inspect_single_export(str(self.master),
                                     [TrackChoice(str(self.source), 0, alignment=self.alignment)],
                                     str(self.root / "result.mkv"), **kwargs)

    def test_nominal_ready_but_not_a_certification(self):
        self.assertFalse([line for line in self._lines() if line.status == "blocked"])
        self.assertIn("Bildanalyse", self._lines()[-1].detail)

    def test_source_overwrite_is_blocked_even_when_overwrite_flag_true(self):
        lines = inspect_single_export(str(self.master),
                                      [TrackChoice(str(self.source), 0, alignment=self.alignment)],
                                      str(self.master), overwrite=True)
        self.assertTrue(any(line.status == "blocked" and line.title == "Ausgabe" for line in lines))

    def test_manual_candidate_requires_strong_fresh_independent_proof(self):
        lines = self._lines(manually_assigned_paths={str(self.source)})
        self.assertTrue(any(line.status == "blocked" and "unabhängiger" in line.detail for line in lines))

    def test_missing_source_and_wrong_master_are_blocked(self):
        self.alignment.target_path = str(self.source)
        self.assertTrue(any(line.status == "blocked" for line in self._lines()))
        self.source.unlink()
        self.assertIn("fehlt", self._lines()[-1].detail)

    def test_dialog_disables_export_when_blocked(self):
        blocked = ExportPreflightDialog([PreflightLine("Quelle", "blocked", "Fehlt")], "out.mkv")
        ready = ExportPreflightDialog([PreflightLine("Quelle", "ready", "Geprüft")], "out.mkv")
        self.addCleanup(self._dispose, blocked)
        self.addCleanup(self._dispose, ready)
        self.assertFalse(blocked.start_button.isEnabled())
        self.assertTrue(ready.start_button.isEnabled())
        self.assertEqual(blocked.findChild(type(blocked.start_button), "primaryButton"),
                         blocked.start_button)

    def _dispose(self, widget):
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
