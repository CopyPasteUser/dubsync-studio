"""Unsafe episodes receive an evidence-based retry before being skipped."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dubsync.core.batch import batch_export_groups
from dubsync.core.models import AlignmentResult, Anchor, EpisodeGroup, MediaInfo, StreamInfo


class UnsafeEpisodeRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'South Park S01E002.source.mkv'
        self.master = self.root / 'South Park S01E002.master.mkv'
        self.source.write_bytes(b'different source')
        self.master.write_bytes(b'different master')
        self.group = EpisodeGroup('south-park|s01e002', 'South Park · S01E02',
                                  [str(self.source), str(self.master)], 'South Park', 1, 2)
        self.video = StreamInfo(index=0, codec_type='video', width=720, height=404, fps=24.0)
        self.audio = StreamInfo(index=1, codec_type='audio', language='deu')

    def info(self, path):
        if path == str(self.master):
            return MediaInfo(path, 1438.209, 'matroska', 100, [self.video, self.audio], 1, 1, 0)
        return MediaInfo(path, 1327.117, 'matroska', 200, [self.video, self.audio], 1, 1, 0)

    def result(self, safe):
        # A claimed-safe mapping must also have evidence near the episode end.
        anchors = [Anchor(i * 19.0, i * 19.0 + 110.0, 4) for i in range(70)]
        return AlignmentResult(
            source_path=str(self.source), target_path=str(self.master),
            slope=1.0, offset=110.0, anchors=anchors,
            confidence=.95 if safe else .3, inlier_ratio=.98,
            coverage=.88 if safe else .2, median_hamming=4.0, rms_error=0.12,
            source_duration=1327.117, target_duration=1438.209,
            sample_interval=0.7, requested_interval=.75, max_samples=2400,
            visual_distinct_hashes=50,
        )

    def test_e02_retries_and_exports_only_after_safe_second_pass(self):
        budgets = []
        def align(*args, **kwargs):
            budgets.append(kwargs['max_samples'])
            return self.result(safe=(len(budgets) == 2))
        with patch('dubsync.core.batch.probe_media', side_effect=self.info), \
             patch('dubsync.core.batch.align_videos', side_effect=align), \
             patch('dubsync.core.batch.shutil.disk_usage', return_value=type('U', (), {'free': 2**40})()), \
             patch('dubsync.core.batch.export_multilingual', return_value='successful.mkv'):
            report = batch_export_groups([self.group], str(self.root / 'out'),
                                         alignment_options={'interval': .75, 'max_samples': 1200})
        self.assertEqual(budgets, [1200, 2400])
        self.assertEqual(report['exported'], ['successful.mkv'])
        self.assertEqual(report['skipped'], [])

    def test_e02_stays_skipped_if_retry_is_unsafe(self):
        budgets = []
        def align(*args, **kwargs):
            budgets.append(kwargs['max_samples'])
            return self.result(safe=False)
        with patch('dubsync.core.batch.probe_media', side_effect=self.info), \
             patch('dubsync.core.batch.align_videos', side_effect=align), \
             patch('dubsync.core.batch.shutil.disk_usage', return_value=type('U', (), {'free': 2**40})()), \
             patch('dubsync.core.batch.export_multilingual', side_effect=AssertionError('must not export')):
            report = batch_export_groups([self.group], str(self.root / 'out'),
                                         alignment_options={'interval': .75, 'max_samples': 1200})
        self.assertEqual(budgets, [1200, 2400])
        self.assertEqual(report['exported'], [])
        self.assertIn('Abdeckung', report['skipped'][0])
        self.assertIn('E02', report['skipped'][0])

    def test_existing_output_is_skipped_with_explicit_reason(self):
        from dubsync.core.batch import output_path_for_group
        dest = output_path_for_group(str(self.root / 'out'), self.group)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b'previous export')
        with patch('dubsync.core.batch.probe_media', side_effect=AssertionError('must not probe')):
            report = batch_export_groups([self.group], str(self.root / 'out'), skip_existing=True)
        self.assertIn('Ausgabe existiert bereits', report['skipped'][0])
        self.assertIn('E02', report['skipped'][0])

if __name__ == '__main__':
    unittest.main()
