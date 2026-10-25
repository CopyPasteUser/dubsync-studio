from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(HAS_PYSIDE, "PySide6 fehlt in diesem Test-Interpreter; GUI-Smoke-Test wird in CI mit Qt ausgeführt.")
class QtGuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._config_home = tempfile.TemporaryDirectory(prefix="dubsync-gui-config-")
        cls._cache_home = tempfile.TemporaryDirectory(prefix="dubsync-gui-cache-")
        cls._state_home = tempfile.TemporaryDirectory(prefix="dubsync-gui-state-")
        os.environ["XDG_CONFIG_HOME"] = cls._config_home.name
        os.environ["XDG_CACHE_HOME"] = cls._cache_home.name
        os.environ["XDG_STATE_HOME"] = cls._state_home.name
        from PySide6.QtWidgets import QApplication
        from dubsync.gui.main_window import MainWindow, SourceRow
        cls.QApplication = QApplication
        cls.MainWindow = MainWindow
        cls.SourceRow = SourceRow
        cls.app = QApplication.instance() or QApplication(["dubsync-smoke-test"])

    @classmethod
    def tearDownClass(cls):
        cls._config_home.cleanup()
        cls._cache_home.cleanup()
        cls._state_home.cleanup()

    def test_main_window_constructs_with_logo_nas_and_preview_controls(self):
        window = self.MainWindow()
        try:
            window.show()
            self.app.processEvents()
            self.assertIn("DubSync Studio", window.windowTitle())
            self.assertFalse(window.windowIcon().isNull())
            self.assertTrue(hasattr(window, "network_button"))
            self.assertTrue(window.root_list.acceptDrops())
            self.assertTrue(window.acceptDrops())
            self.assertTrue(hasattr(window, "_handle_dropped_paths"))
            self.assertTrue(hasattr(window, "_source_context_menu"))
            self.assertTrue(hasattr(window, "preview_button"))
            self.assertFalse(window.settings.get("high_contrast", False))
            settings_menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "Einstellungen")
            self.assertTrue(any(action.text() == "Hoher Kontrast" for action in settings_menu.actions()))
        finally:
            window.close()
            self.app.processEvents()

    def test_manual_alignment_undo_and_redo_round_trip(self):
        from dubsync.core.models import MediaInfo, StreamInfo
        audio = StreamInfo(index=1, codec_type="audio", language="eng", title="English", channels=2)
        video = StreamInfo(index=0, codec_type="video", codec_name="h264", width=320, height=180, fps=24.0)
        info = MediaInfo("/tmp/undo-redo-test.mkv", 120.0, "matroska", 1024, [video, audio], 1, 1, 0)
        row = self.SourceRow(info.path, info, is_master=False)
        try:
            row.offset_spin.setValue(1.25)
            row.scale_spin.setValue(1.005)
            row._commit_alignment_edit()
            row.undo_alignment()
            self.assertAlmostEqual(row.offset_spin.value(), 0.0)
            self.assertAlmostEqual(row.scale_spin.value(), 1.0)
            row.redo_alignment()
            self.assertAlmostEqual(row.offset_spin.value(), 1.25)
            self.assertAlmostEqual(row.scale_spin.value(), 1.005)
        finally:
            row.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
