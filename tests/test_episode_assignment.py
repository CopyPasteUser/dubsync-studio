"""Manual episode assignment is explicit, local and never bypasses safety."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dubsync.core.episode_assignment import approved_candidate_path, apply_candidate_assignments
from dubsync.core.models import EpisodeGroup
from dubsync.core.project import load_project_file, save_project_file, ProjectFileError


class AssignmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.master = self.root / "Show.S01E02.master.mkv"
        self.candidate = self.root / "Show.S01E03.de.mkv"
        for path in (self.master, self.candidate):
            path.write_bytes(path.name.encode())
        stat = self.candidate.stat()
        self.report = {
            "master_path": str(self.master), "automatic_reassignment": False,
            "automatic_export_allowed": False,
            "candidates": [{"candidate": 1, "path": str(self.candidate),
                            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                            "status": "verified", "visual_proof": {
                                "safe_for_automatic_export": True, "independent_episode_proof": True,
                            }}],
        }

    def approved(self):
        return approved_candidate_path(self.report, 1, master=str(self.master),
                                       inspected_paths=[str(self.candidate)])

    def test_human_approval_requires_exact_verified_candidate_and_unchanged_file(self):
        self.assertEqual(self.approved(), str(self.candidate))
        for field, value in (("status", "insufficient"), ("path", str(self.master))):
            old = self.report["candidates"][0][field]
            self.report["candidates"][0][field] = value
            with self.assertRaises(ValueError):
                self.approved()
            self.report["candidates"][0][field] = old
        self.report["candidates"][0]["visual_proof"]["independent_episode_proof"] = False
        with self.assertRaises(ValueError):
            self.approved()
        self.report["candidates"][0]["visual_proof"]["independent_episode_proof"] = True
        self.candidate.write_bytes(b"new content")
        with self.assertRaisesRegex(ValueError, "verändert"):
            self.approved()

    def test_assignment_moves_source_without_duplicate_owner_and_keeps_empty_group(self):
        groups = [EpisodeGroup("show|s01e002", "Episode 2", [str(self.master)]),
                  EpisodeGroup("show|s01e003", "Episode 3", [str(self.candidate)])]
        moved = apply_candidate_assignments(groups, {str(self.candidate): "show|s01e002"})
        self.assertEqual(moved[0].files, sorted([str(self.candidate), str(self.master)]))
        self.assertEqual(moved[1].files, [])
        self.assertEqual(groups[1].files, [str(self.candidate)])  # Original scan remains immutable.
        restored = apply_candidate_assignments(moved, {str(self.candidate): "show|s01e002"})
        self.assertEqual(restored[0].files, moved[0].files)
        self.assertEqual(restored[1].files, [])

    def test_project_roundtrip_validates_manual_assignments(self):
        target = self.root / "project.dubsync"
        save_project_file(target, {"roots": [str(self.root)],
                                   "manual_candidate_assignments": {
                                       str(self.candidate): "show|s01e002"}})
        self.assertEqual(load_project_file(target)["project"]["manual_candidate_assignments"],
                         {str(self.candidate): "show|s01e002"})
        with self.assertRaises(ProjectFileError):
            save_project_file(target, {"manual_candidate_assignments": {"bad\x00path": "show"}})


if __name__ == "__main__":
    unittest.main()
