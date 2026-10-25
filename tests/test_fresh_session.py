"""Regression coverage for the explicit fresh-launch contract (no GUI needed)."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

from dubsync.core.session import persistent_preferences, PREFERENCE_KEYS

GUI = Path(__file__).resolve().parents[1] / "dubsync" / "gui" / "main_window.py"


class FreshSessionTests(unittest.TestCase):
    def test_only_real_preferences_survive(self):
        old = {
            "roots": ["/mnt/older"],
            "root_languages": {"/mnt/older": {"language": "eng"}},
            "track_overrides": {"S01E01": {"offset": 33}},
            "master_overrides": {"S01E01": "/tmp/old.mkv"},
            "last_project_path": "/tmp/old.dubsync",
            "skip_existing": True,
            "performance": {"max_samples": 1200},
            "high_contrast": True,
            "output_directory": "/mnt/output",
            "organize_output": False,
        }
        result = persistent_preferences(old)
        self.assertEqual(set(result), PREFERENCE_KEYS)
        self.assertEqual(result["output_directory"], "/mnt/output")
        self.assertFalse(result["organize_output"])
        self.assertEqual(result["performance"], {"max_samples": 1200})
        self.assertTrue(result["high_contrast"])
        self.assertIn("roots", old, "Must not destructively modify input")

    def test_empty_or_invalid_settings(self):
        self.assertEqual(persistent_preferences(None), {})
        self.assertEqual(persistent_preferences(["invalid"]), {})
        self.assertEqual(persistent_preferences({"roots": ["/old"]}), {})

    def test_different_runs_do_not_restore_previous_session(self):
        saved_158 = {"roots": ["/nas/s01"], "master_overrides": {"x": "y"}, "output_directory": "/out"}
        first_launch = persistent_preferences(saved_158)
        self.assertEqual(first_launch, {"output_directory": "/out"})
        # Session-only additions must not enter the next startup state.
        active_session = dict(first_launch, roots=["/new/s02"], track_overrides={"x": 2})
        self.assertEqual(persistent_preferences(active_session), first_launch)

    def test_gui_startup_and_save_wiring(self):
        content = GUI.read_text(encoding="utf-8")
        ast.parse(content, filename=str(GUI))
        self.assertIn("self.settings = persistent_preferences(load_settings())", content)
        self.assertIn("self.roots: list[str] = []", content)
        self.assertIn("self.root_languages: dict[str, dict[str, str]] = {}", content)
        self.assertIn("self.track_overrides: dict[str, dict[str, Any]] = {}", content)
        self.assertIn("self.master_overrides: dict[str, str] = {}", content)
        self.assertIn('self.cache = AlignmentCache(Path(self._session_cache_dir.name) / "alignments.sqlite3")', content)
        self.assertIn("self._session_cache_dir.cleanup()", content)
        save_region = content.split("    def _save_settings(self):", 1)[1].split("    def _output_options_changed", 1)[0]
        self.assertIn("self.settings = persistent_preferences(self.settings)", save_region)
        self.assertNotIn('self.settings["roots"]', save_region)
        self.assertNotIn('self.settings["track_overrides"]', save_region)
        self.assertNotIn('self.settings["master_overrides"]', save_region)


if __name__ == "__main__":
    unittest.main()
