"""Offline acceptance runner contracts; no actual release is authorized here."""
from copy import deepcopy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.acceptance_4_0 import Acceptance, StepFailed, compare_alignment, junit_summary


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
