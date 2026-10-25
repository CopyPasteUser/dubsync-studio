from __future__ import annotations

import unittest

from dubsync.cli import VERSION, doctor_report


class CliTests(unittest.TestCase):
    def test_cli_version_matches_release_version(self):
        self.assertEqual(VERSION, "1.5.14")

    def test_doctor_report_has_expected_sections_and_boolean_dependencies(self):
        report = doctor_report()
        self.assertEqual(report["application_version"], VERSION)
        self.assertTrue(report["python"])
        self.assertTrue(report["platform"])
        self.assertIn("PySide6", report["dependencies"])
        self.assertTrue(all(isinstance(value, bool) for value in report["dependencies"].values()))
        self.assertIn("ffmpeg", report["media_tools"])
        self.assertIn("ffprobe", report["media_tools"])
        self.assertIn("log", report["paths"])
        self.assertIsInstance(report["warnings"], list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
