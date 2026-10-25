from pathlib import Path
import tempfile
import unittest

from dubsync.core.drop import classify_dropped_paths


class DropClassificationTests(unittest.TestCase):
    def test_video_files_add_parent_roots_and_deduplicate(self):
        result = classify_dropped_paths([
            "/media/series/S01E01.de.mkv",
            "/media/series/S01E01.en.mp4",
            "/media/other/S01E02.mkv",
        ])
        self.assertEqual(result["roots"], ["/media/series", "/media/other"])
        self.assertEqual(result["projects"], [])

    def test_dubsync_project_is_classified_without_filesystem_access(self):
        result = classify_dropped_paths(["/tmp/show.dubsync"])
        self.assertEqual(result["projects"], ["/tmp/show.dubsync"])
        self.assertEqual(result["roots"], [])

    def test_folder_roots_are_normalized_without_stat_calls(self):
        with tempfile.TemporaryDirectory() as td:
            result = classify_dropped_paths([td, td + "/."])
            self.assertEqual(result["roots"], [str(Path(td).resolve())])

    def test_empty_and_nul_entries_are_ignored(self):
        result = classify_dropped_paths(["", "\x00bad", "  "])
        self.assertEqual(result, {"roots": [], "projects": [], "ignored": []})


if __name__ == "__main__":
    unittest.main(verbosity=2)
