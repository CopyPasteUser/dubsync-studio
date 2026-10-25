from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from dubsync.core.project import (
    PROJECT_FORMAT, PROJECT_SCHEMA_VERSION, ProjectFileError,
    load_project_file, normalize_project_payload, save_project_file,
)


class ProjectFileTests(unittest.TestCase):
    def payload(self, root: str) -> dict:
        return {
            "roots": [root, root],
            "root_languages": {root: {"language": "deu", "title": "Deutsch"}},
            "output_directory": str(Path(root) / "output"),
            "organize_output": True,
            "skip_existing": True,
            "performance": {
                "preset": "system", "cpu_threads": 3,
                "max_samples": 1200, "cache_enabled": True,
            },
            "track_overrides": {
                str(Path(root) / "episode.mkv"): {
                    "audio_ordinal": 1, "language": "eng", "title": "English",
                    "include": True, "manual_alignment": True,
                    "offset": 1.25, "scale": 1.002,
                }
            },
            "master_overrides": {"test-series|s01e001": str(Path(root) / "master.mkv")},
        }

    def test_roundtrip_uses_atomic_project_file_and_deduplicates_roots(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "sources"
            root.mkdir()
            path = save_project_file(Path(td) / "Example", self.payload(str(root)))
            self.assertEqual(path.suffix, ".dubsync")
            loaded = load_project_file(path)
            self.assertEqual(loaded["project"]["roots"], [str(root)])
            self.assertEqual(loaded["project"]["root_languages"][str(root)]["title"], "Deutsch")
            self.assertEqual(loaded["project"]["performance"]["cpu_threads"], 3)
            track_override = loaded["project"]["track_overrides"][str(Path(root) / "episode.mkv")]
            self.assertEqual(track_override["audio_ordinal"], 1)
            self.assertEqual(track_override["title"], "English")
            self.assertTrue(track_override["manual_alignment"])
            self.assertAlmostEqual(track_override["offset"], 1.25)
            self.assertEqual(loaded["project"]["master_overrides"]["test-series|s01e001"], str(Path(root) / "master.mkv"))
            self.assertEqual(loaded["missing_roots"], [])
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(list(Path(td).glob("*.tmp")))

    def test_missing_source_roots_are_reported_not_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            missing = str(Path(td) / "not-created")
            path = save_project_file(Path(td) / "Missing", self.payload(missing))
            loaded = load_project_file(path)
            self.assertEqual(loaded["missing_roots"], [missing])

    def test_invalid_project_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "future.dubsync"
            path.write_text(json.dumps({
                "format": PROJECT_FORMAT,
                "schema_version": PROJECT_SCHEMA_VERSION + 1,
                "project": {},
            }), encoding="utf-8")
            with self.assertRaises(ProjectFileError):
                load_project_file(path)


    def test_automatic_alignment_values_are_not_saved_as_manual_overrides(self):
        payload = self.payload("/tmp/media")
        payload["track_overrides"]["/tmp/media/episode.mkv"]["manual_alignment"] = False
        normalized = normalize_project_payload(payload)
        override = normalized["track_overrides"]["/tmp/media/episode.mkv"]
        self.assertFalse(override["manual_alignment"])
        self.assertNotIn("offset", override)
        self.assertNotIn("scale", override)

    def test_boolean_schema_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.dubsync"
            path.write_text(json.dumps({
                "format": PROJECT_FORMAT,
                "schema_version": True,
                "project": {},
            }), encoding="utf-8")
            with self.assertRaises(ProjectFileError):
                load_project_file(path)

    def test_wrong_format_and_malformed_json_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "broken.dubsync"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ProjectFileError):
                load_project_file(path)
            path.write_text(json.dumps({"format": "something-else", "schema_version": 1, "project": {}}), encoding="utf-8")
            with self.assertRaises(ProjectFileError):
                load_project_file(path)

    def test_payload_validation_clamps_structure_and_rejects_nul_path(self):
        with tempfile.TemporaryDirectory() as td:
            normalized = normalize_project_payload(self.payload(td))
            self.assertEqual(normalized["organize_output"], True)
            with self.assertRaises(ProjectFileError):
                normalize_project_payload({"roots": ["/tmp\x00bad"]})
            with self.assertRaises(ProjectFileError):
                normalize_project_payload({"roots": ["/tmp"] * 1001})

    def test_invalid_track_override_numbers_are_ignored(self):
        payload = self.payload("/tmp/media")
        payload["track_overrides"] = {
            "/tmp/media/episode.mkv": {
                "audio_ordinal": 9999, "offset": float("nan"),
                "scale": 9.0, "title": "A" * 400,
            }
        }
        normalized = normalize_project_payload(payload)
        override = normalized["track_overrides"]["/tmp/media/episode.mkv"]
        self.assertNotIn("audio_ordinal", override)
        self.assertNotIn("offset", override)
        self.assertNotIn("scale", override)
        self.assertNotIn("title", override)

    def test_atomic_save_can_replace_same_project_without_temp_remnants(self):
        with tempfile.TemporaryDirectory() as td:
            path = save_project_file(Path(td) / "Example.dubsync", self.payload(td))
            old = path.read_text(encoding="utf-8")
            save_project_file(path, {**self.payload(td), "skip_existing": False})
            new = path.read_text(encoding="utf-8")
            self.assertNotEqual(old, new)
            self.assertFalse(list(Path(td).glob(".*.tmp")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
