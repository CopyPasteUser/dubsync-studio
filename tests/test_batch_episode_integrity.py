"""Regression tests for real South Park filenames and batch cancellation."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dubsync.core.batch import (batch_export_groups, batch_step_message,
                                episode_identity_errors, output_path_for_group)
from dubsync.core.models import EpisodeGroup, MediaInfo, StreamInfo
from dubsync.core.scanner import episode_key, scan_directories


class EpisodeIdentityTests(unittest.TestCase):
    def test_e004_never_becomes_e001(self):
        a = episode_key('/media/A/SouthparkS01E04_SSS.mp4')
        b = episode_key('/media/B/South Park S01E004.mkv')
        c = episode_key('/media/B/South Park S01E001.mkv')
        self.assertEqual(a[0], b[0])
        self.assertNotEqual(b[0], c[0])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a_root, b_root = root/'first', root/'second'
            a_root.mkdir(); b_root.mkdir()
            (a_root/'SouthparkS01E04_SSS.mp4').touch()
            (b_root/'South Park S01E004.mkv').touch()
            (b_root/'South Park S01E001.mkv').touch()
            groups = scan_directories([str(a_root), str(b_root)])
            self.assertEqual(len(groups), 2)
            e4, = (group for group in groups if group.episode_number == 4)
            e1, = (group for group in groups if group.episode_number == 1)
            self.assertEqual(len(e4.files), 2)
            self.assertEqual(len(e1.files), 1)
            self.assertEqual(e4.episode_number, 4)
            self.assertTrue(output_path_for_group(str(root),e4).name.endswith('S01E04.multilingual.mkv'))
            self.assertIn('Episode S01E04', batch_step_message(1,13,e4,'Abgleich'))
            self.assertIn('Stapelposition 1/13', batch_step_message(1,13,e4,'Abgleich'))

    def test_mislabelled_group_is_rejected_without_video_access(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = ['South Park S01E001.mkv', 'South Park S01E004.mkv']
            paths = [str(root/name) for name in names]
            for idx, path in enumerate(paths):
                Path(path).write_bytes(f'different file {idx}'.encode())
            invalid = EpisodeGroup('southpark|s01e004', 'South Park · S01E04', paths,
                                   'South Park', 1, 4)
            issues = episode_identity_errors(invalid)
            self.assertEqual(len(issues), 1)
            self.assertIn('S01E01', issues[0])
            with patch('dubsync.core.batch.probe_media', side_effect=AssertionError('MUST NOT probe')):
                report = batch_export_groups([invalid], str(root/'output'))
            self.assertEqual(report['exported'], [])
            self.assertTrue(any('Episodenzuordnung' in issue for issue in report['failed']))

    def test_inconsistent_group_key_and_metadata_are_rejected(self):
        group = EpisodeGroup('southpark|s01e004','Show · S01E04', ['Show.S01E004.mkv'],
                             'Show', 1, 1)
        self.assertTrue(episode_identity_errors(group))


class DiagnosticContextTests(unittest.TestCase):
    def test_batch_diagnostic_identifies_position_separately_from_episode(self):
        from dubsync.core.alignment import align_videos
        from dubsync.core.alignment_diagnostics import (AlignmentDiagnostic,
                                                       summarize_diagnostic)
        snapshots = []
        def save(snapshot):
            snapshots.append(snapshot.payload)
            return None
        with patch('dubsync.core.alignment._align_videos_impl',
                   side_effect=RuntimeError('simulated decoder failure')), \
             patch.object(AlignmentDiagnostic, 'save', save):
            with self.assertRaisesRegex(RuntimeError, 'simulated decoder failure'):
                align_videos('a.mkv', 'b.mkv', batch_context={
                    'position': 1, 'total': 13, 'season': 1, 'episode': 4,
                })
        self.assertEqual(len(snapshots), 1)
        context = snapshots[0]['stages']['batch_context']
        self.assertEqual((context['position'],context['episode']), (1,4))
        summary = summarize_diagnostic(snapshots[0])
        self.assertIn('Stapelposition: 1/13', summary)
        self.assertIn('Episodennummer: S01E04', summary)


class CancellationTests(unittest.TestCase):
    def test_mid_episode_cancel_is_not_reported_as_failed_or_unsafe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            filenames = ('Show.S01E004.source.mkv', 'Show.S01E004.master.mkv',
                         'Show.S01E005.source.mkv', 'Show.S01E005.master.mkv')
            for i, name in enumerate(filenames):
                (root/name).write_bytes(f'file {i}'.encode())
            e4paths = [str(root/filenames[i]) for i in (0,1)]
            e5paths = [str(root/filenames[i]) for i in (2,3)]
            groups = [EpisodeGroup('show|s01e004','Show · S01E04',e4paths,'Show',1,4),
                      EpisodeGroup('show|s01e005','Show · S01E05',e5paths,'Show',1,5)]
            def make_info(path):
                size = 500 if 'master' in path else 900
                return MediaInfo(path,100.0,'matroska',size,
                    [StreamInfo(index=0,codec_type='video',width=400,height=300),
                     StreamInfo(index=1,codec_type='audio')],1,1,0)
            event = threading.Event()
            invoked = []
            def cancel_alignment(*args, **kwargs):
                invoked.append((args,kwargs))
                event.set()
                raise RuntimeError('Videoanalyse abgebrochen.')
            def report_progress(*args):
                # Mirrors TaskWorker.report: a late progress callback can also
                # observe cancellation; this must not overwrite the partial report.
                if event.is_set():
                    raise RuntimeError('Aufgabe abgebrochen')
            with patch('dubsync.core.batch.probe_media',side_effect=make_info), \
                 patch('dubsync.core.batch.align_videos',side_effect=cancel_alignment), \
                 patch('dubsync.core.batch.shutil.disk_usage',return_value=type('Usage',(),{'free':2**40})()), \
                 patch('dubsync.core.batch.export_multilingual',side_effect=AssertionError('MUST NOT export')):
                result = batch_export_groups(groups,str(root/'output'),cancel_event=event,progress=report_progress)
            self.assertEqual(len(invoked),1)
            self.assertEqual(result['exported'],[])
            self.assertEqual(result['failed'],[])
            self.assertEqual(len(result['cancelled']),1)
            self.assertIn('E04',result['cancelled'][0])
            self.assertTrue(any('S01E05' in value for value in result['skipped']))
            self.assertTrue(any('kein Synchronisationsurteil' in value for value in result['skipped']))


if __name__ == '__main__':
    unittest.main()
