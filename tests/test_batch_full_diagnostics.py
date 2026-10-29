"""The full run must include E02 even if a later E03 attempt gets cancelled."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dubsync.core.batch import batch_export_groups, output_path_for_group
from dubsync.core.batch_diagnostics import summarize_batch
from dubsync.core.models import AlignmentResult, Anchor, EpisodeGroup, MediaInfo, StreamInfo


class FullBatchDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.groups = []
        self.video = StreamInfo(index=0, codec_type='video', width=720, height=404, fps=24.0)
        self.audio = StreamInfo(index=1, codec_type='audio', language='eng')
        for number in range(1, 5):
            src = self.base / f'South Park S01E{number:03d}.large.mkv'
            small = self.base / f'South Park S01E{number:03d}.small.mkv'
            src.write_bytes(f'src-{number}'.encode())
            small.write_bytes(f'master-{number}'.encode())
            self.groups.append(EpisodeGroup(
                f'south-park|s01e{number:03d}', f'South Park · S01E{number:02d}',
                [str(src), str(small)], 'South Park', 1, number,
            ))

    def info(self, path):
        name = Path(path).name
        small = '.small.' in name
        return MediaInfo(path, 1438.209 if small else 1327.117, 'matroska',
                         100 if small else 200, [self.video, self.audio], 1, 1, 0)

    def alignment(self, source, master, safe):
        # Cover the full common episode; a dense first half alone is not safe.
        anchors = [Anchor(i * 22.0, i * 22.0 + 110.0, 2) for i in range(60)]
        return AlignmentResult(
            source_path=source, target_path=master, slope=1.0, offset=110.0,
            anchors=anchors, confidence=.95 if safe else .2, inlier_ratio=.98,
            coverage=.9 if safe else .2, median_hamming=3.0, rms_error=.2,
            source_duration=1327.117, target_duration=1438.209,
            sample_interval=.75, requested_interval=.75, max_samples=1200,
            visual_distinct_hashes=50,
        )

    def test_completed_stapel_stores_e02_skip_and_both_alignment_attempts(self):
        def align(source, master, **kwargs):
            safe = 'E002' not in Path(source).name
            kwargs['diagnostic_sink']({
                'analysis_id': f'analysis-{Path(source).stem}-{kwargs["max_samples"]}',
                'stages': {'sample_source': {'samples_collected': kwargs['max_samples']}},
                'decision': {'status': 'safe' if safe else 'unsafe',
                             'failed_conditions': [] if safe else ['coverage']},
            })
            return self.alignment(source, master, safe)
        with patch('dubsync.core.batch.probe_media', side_effect=self.info), \
             patch('dubsync.core.batch.align_videos', side_effect=align), \
             patch('dubsync.core.batch.export_multilingual', side_effect=lambda m, t, o, **kw: o), \
             patch('dubsync.core.batch.shutil.disk_usage', return_value=type('U', (), {'free': 2**40})()):
            result = batch_export_groups(self.groups[:2], str(self.base / 'out'),
                                         journal_directory=self.base / 'reports',
                                         alignment_options={'interval': .75, 'max_samples': 1200})
        data = json.loads(Path(result['batch_report_path']).read_text(encoding='utf-8'))
        self.assertEqual(data['status'], 'completed')
        self.assertEqual([e['status'] for e in data['episodes']], ['exported', 'skipped'])
        e02 = data['episodes'][1]
        self.assertEqual(e02['episode'], 'S01E02')
        self.assertEqual([a['max_samples'] for a in e02['attempts']], [1200, 2400])
        self.assertEqual([a['status'] for a in e02['attempts']], ['unsafe', 'unsafe'])
        self.assertEqual(e02['attempts'][0]['alignment']['decision']['failed_conditions'], ['coverage'])
        self.assertIn('unsichere Synchronisation', e02['reason'])
        self.assertIn('S01E02', summarize_batch(data))

    def test_e03_cancel_does_not_erase_e02_and_e04_is_not_started(self):
        cancel = threading.Event()
        def align(source, master, **kwargs):
            if 'E003' in Path(source).name:
                cancel.set()
                kwargs['diagnostic_sink']({'decision': {'status': 'cancelled'},
                                           'stages': {'sample_source': {'samples_collected': 329}}})
                raise RuntimeError('Videoanalyse abgebrochen.')
            safe = 'E002' not in Path(source).name
            kwargs['diagnostic_sink']({'decision': {'status': 'safe' if safe else 'unsafe'},
                                       'stages': {'sample_source': {'samples_collected': 1200}}})
            return self.alignment(source, master, safe)
        with patch('dubsync.core.batch.probe_media', side_effect=self.info), \
             patch('dubsync.core.batch.align_videos', side_effect=align), \
             patch('dubsync.core.batch.export_multilingual', side_effect=lambda m, t, o, **kw: o), \
             patch('dubsync.core.batch.shutil.disk_usage', return_value=type('U', (), {'free': 2**40})()):
            result = batch_export_groups(self.groups, str(self.base / 'out'),
                                         journal_directory=self.base / 'reports',
                                         cancel_event=cancel,
                                         alignment_options={'interval': .75, 'max_samples': 1200})
        data = json.loads(Path(result['batch_report_path']).read_text(encoding='utf-8'))
        self.assertEqual(data['status'], 'cancelled')
        self.assertEqual([e['status'] for e in data['episodes']],
                         ['exported', 'skipped', 'cancelled', 'pending'])
        self.assertEqual(len(data['episodes'][1]['attempts']), 2)
        self.assertEqual(data['episodes'][2]['attempts'][0]['alignment']['stages']['sample_source']['samples_collected'], 329)
        self.assertEqual(data['summary']['not_started'], 1)
        self.assertEqual(data['summary']['skipped'], 1)

    def test_cli_can_copy_entire_stapel_run_as_one_json(self):
        import contextlib
        import io
        import os
        from dubsync.cli import main
        group = self.groups[1]
        previous = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(self.base / "state")
        try:
            target = output_path_for_group(str(self.base / "out"), group)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"preexisting")
            batch_export_groups([group], str(self.base / "out"), skip_existing=True)
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                exit_code = main(["--latest-batch", "--json"])
            self.assertEqual(exit_code, 0)
            report = json.loads(capture.getvalue())
            self.assertEqual(report["episodes"][0]["episode"], "S01E02")
            self.assertEqual(report["episodes"][0]["status"], "skipped")
            self.assertIn("reason", report["episodes"][0])
        finally:
            if previous is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = previous

    def test_output_already_exists_is_explained_even_without_alignment(self):
        group = self.groups[1]
        target = output_path_for_group(str(self.base / 'out'), group)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'finished')
        result = batch_export_groups([group], str(self.base / 'out'),
                                     journal_directory=self.base / 'reports', skip_existing=True)
        data = json.loads(Path(result['batch_report_path']).read_text(encoding='utf-8'))
        episode = data['episodes'][0]
        self.assertEqual(episode['status'], 'skipped')
        self.assertEqual(episode['attempts'], [])
        self.assertIn('Ausgabe existiert bereits', episode['reason'])
        self.assertNotIn(str(self.base), json.dumps(data))


if __name__ == '__main__':
    unittest.main()
