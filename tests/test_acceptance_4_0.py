"""Offline acceptance runner contracts; no actual release is authorized here."""
from copy import deepcopy
import json
import os
import sys
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.acceptance_4_0 import Acceptance, StepFailed, compare_alignment, compare_export, junit_summary, git, worktree, isolated_env


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


    def test_export_comparison_requires_continuous_reference_and_candidate(self):
        good = {"evidence": {"audio": ["pcm"], "video": "video"}, "continuous_added_audio": True}
        bad = {"evidence": {"audio": ["gap"], "video": "video"}, "continuous_added_audio": False}
        baseline = {"scenario": "synthetic", "duration_s": 30, "inputs_unchanged": True,
                    "observations": [good, bad], "median_seconds": 2.}
        candidate = dict(baseline, observations=[good, good], evidence=good["evidence"], median_seconds=1.)
        self.assertEqual(compare_export(baseline, candidate)["discontinuous_baseline_runs"], 1)
        with self.assertRaises(StepFailed):
            compare_export(dict(baseline, observations=[bad]), candidate)
        with self.assertRaises(StepFailed):
            compare_export(baseline, dict(candidate, observations=[good, bad]))
        with self.assertRaises(StepFailed):
            compare_export(dict(baseline, observations=[dict(good, evidence={"different": True})]), candidate)


    def test_subprocess_environment_drops_production_overrides(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {
                "DUBSYNC_SUBTITLE_CACHE_DIR": "/production/cache",
                "DUBSYNC_SUBDL_API_KEY": "private-test-key",
                "DUBSYNC_WHISPER_MODEL": "/production/model",
                "DUBSYNC_NATIVE": "0",
                "XDG_CACHE_HOME": "/production/xdg",
        }):
            root = Path(d)
            env = isolated_env(root, root / "state")
            self.assertEqual(env["DUBSYNC_NATIVE"], "1")
            self.assertEqual(env["DUBSYNC_SUBTITLE_AUTO_SYNC"], "0")
            self.assertEqual(set(k for k in env if k.startswith("DUBSYNC_")),
                             {"DUBSYNC_NATIVE", "DUBSYNC_SUBTITLE_AUTO_SYNC"})
            self.assertEqual(env["XDG_CACHE_HOME"], str(root / "state" / "cache"))
            self.assertEqual(os.environ["DUBSYNC_SUBTITLE_CACHE_DIR"], "/production/cache")
