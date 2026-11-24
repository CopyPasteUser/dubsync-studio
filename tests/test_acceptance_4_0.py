"""Offline acceptance runner contracts; no actual release is authorized here."""
from copy import deepcopy
import json
import sys
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.acceptance_4_0 import Acceptance, StepFailed, compare_alignment, junit_summary, git, worktree


class AcceptanceTests(unittest.TestCase):
    def test_success_and_failed_step_persist_result(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            acceptance = Acceptance(root)
            report = acceptance.step('ok', [sys.executable, '-c', 'print("hello")'], root, timeout=5)
            self.assertEqual(report.read_text().strip(), 'hello')
            with self.assertRaises(StepFailed):
                acceptance.step('bad', [sys.executable, '-c', 'raise SystemExit(7)'], root, timeout=5)
            payload = json.loads((root / 'manifest.json').read_text())
            self.assertEqual([r['status'] for r in payload['steps']], ['passed', 'failed'])
            self.assertFalse(payload['release_authorized'])

    def test_parity_gate(self):
        report = {'result': {'recipe': {'decoder': 'ffmpeg'}, 'scenario': 'same',
                  'native_matcher_available': True, 'evidence_identical': True,
                  'evidence_sha256': 'a' * 64, 'summary': {mode: {'wall_seconds': {'median': 1.0}}
                                                           for mode in ('cold', 'warm')}}}
        self.assertEqual(compare_alignment(report, deepcopy(report))['cold']['after_seconds'], 1.0)
        for field, value in (('evidence_sha256', 'b' * 64), ('scenario', 'shifted'),
                             ('evidence_identical', False), ('native_matcher_available', False)):
            changed = deepcopy(report)
            changed['result'][field] = value
            with self.assertRaises(StepFailed):
                compare_alignment(report, changed)

    def test_junit_retains_skips(self):
        with tempfile.TemporaryDirectory() as d:
            xml = Path(d) / 'tests.xml'
            xml.write_text('<testsuites><testsuite><testcase classname="x" name="ok"/>'
                           '<testcase classname="x" name="skip"><skipped message="reason"/></testcase>'
                           '<testcase classname="x" name="bad"><failure/></testcase></testsuite></testsuites>')
            self.assertEqual(junit_summary(xml), {'total': 3, 'failed': 1,
                'skipped': [{'name': 'x.skip', 'reason': 'reason'}]})


    @unittest.skipUnless(shutil.which("git"), "Git required")
    def test_detached_worktree_is_removed_and_original_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "source").write_text("original")
            subprocess.run(["git", "-C", str(root), "add", "source"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=QA", "-c", "user.email=qa@example.invalid",
                            "commit", "-qm", "fixture [skip ci]"], check=True)
            revision = git(root, "rev-parse", "HEAD")
            destination = Path(d) / "isolated"
            with worktree(root, revision, destination) as checkout:
                self.assertEqual(git(checkout, "rev-parse", "HEAD"), revision)
                (checkout / "source").write_text("modified fixture")
            self.assertFalse(destination.exists())
            self.assertEqual((root / "source").read_text(), "original")
            self.assertEqual(git(root, "status", "--porcelain"), "")

    def test_timed_out_step_is_failed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            acceptance = Acceptance(root)
            with self.assertRaises(subprocess.TimeoutExpired):
                acceptance.step("timeout", [sys.executable, "-c", "import time; time.sleep(30)"], root, timeout=1)
            payload = json.loads((root / "manifest.json").read_text())
            self.assertEqual(payload["steps"][0]["status"], "failed")
            self.assertFalse(payload["release_authorized"])
