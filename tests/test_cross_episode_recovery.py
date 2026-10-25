"""Content-verified mutual swaps may be recovered, but intro-only matches never are."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dubsync.core.batch import (batch_export_groups, _is_independent_episodic_match,
                                _suspected_content_mismatch)
from dubsync.core.models import AlignmentResult, Anchor, EpisodeGroup, MediaInfo, StreamInfo


class CrossEpisodeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.groups = []
        for n in (2, 3):
            source = self.root / 'dub' / f'South Park S01E{n:03d}.mkv'
            master = self.root / 'picture' / f'South Park S01E{n:03d}.mkv'
            for path in (source, master):
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(f'{path.parent.name}-{n}'.encode())
            self.groups.append(EpisodeGroup(f'southpark|s01e{n:03d}', f'South Park · S01E{n:02d}',
                                            [str(source), str(master)], 'South Park', 1, n))
        self.streams = [StreamInfo(index=0, codec_type='video', width=720, height=404, fps=24),
                        StreamInfo(index=1, codec_type='audio', language='deu')]

    def probe(self, path):
        return MediaInfo(path, 1327.0 if 'dub' in Path(path).parts else 1438.0,
                         'matroska', 200 if 'dub' in Path(path).parts else 100,
                         self.streams, 1, 1, 0)

    def align_result(self, source, target, *, safe):
        duration = 1327.0
        anchors = ([Anchor(duration * i / 60, duration * i / 60 + 111.0, 5)
                    for i in range(1, 60)] if safe else
                   [Anchor(i * 1.5, i * 1.5 + 111.0, 5) for i in range(18)] +
                   [Anchor(1300.0 + i, 1411.0 + i, 5) for i in range(18)])
        return AlignmentResult(source, target, 1., 111., anchors,
                               .97 if safe else .50, .96,
                               .87 if safe else .025, 5., .1,
                               1327.0, 1438.0, .75,
                               visual_distinct_hashes=100)

    def test_only_two_direction_verified_swap_gets_exported(self):
        exported = []
        def align(source, target, **kwargs):
            src_episode = Path(source).stem.split('E')[1][:3]
            dst_episode = Path(target).stem.split('E')[1][:3]
            safe = src_episode != dst_episode
            kwargs['diagnostic_sink']({'decision': {'status': 'safe' if safe else 'unsafe'},
                                       'stages': {'content_probe': {'mid_episode_matched': safe}}})
            return self.align_result(source, target, safe=safe)
        def export(master, tracks, output, **kwargs):
            exported.append((master, tracks[0].path, output))
            return output
        with patch('dubsync.core.batch.probe_media', side_effect=self.probe), \
             patch('dubsync.core.batch.align_videos', side_effect=align), \
             patch('dubsync.core.batch.export_multilingual', side_effect=export), \
             patch('dubsync.core.batch.shutil.disk_usage',
                   return_value=type('D', (), {'free': 2**40})()):
            result = batch_export_groups(self.groups, str(self.root / 'out'),
                                         alignment_options={'interval': .75, 'max_samples': 1200},
                                         journal_directory=self.root / 'reports')
        report = json.loads(Path(result['batch_report_path']).read_text())
        self.assertEqual([r['status'] for r in report['episodes']], ['exported', 'exported'])
        self.assertEqual(len(exported), 2)
        self.assertEqual(len(result['exported']), 2)
        self.assertEqual(len(result['skipped']), 0)
        for master, source, output in exported:
            self.assertNotEqual(Path(master).stem, Path(source).stem)
            self.assertIn('S01E' + Path(master).stem.split('E')[1][:3].lstrip('0').zfill(2), output)
        self.assertTrue(all(any(a.get('cross_episode_verified') for a in r['attempts'])
                            for r in report['episodes']))
        self.assertIn('confirmed_episode_swap',
                      [e['event'] for e in report['episodes'][0]['events']])
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_no_export_if_only_one_cross_match_is_safe(self):
        def align(source, target, **kwargs):
            safe = 'E002' in source and 'E003' in target
            return self.align_result(source, target, safe=safe)
        with patch('dubsync.core.batch.probe_media', side_effect=self.probe), \
             patch('dubsync.core.batch.align_videos', side_effect=align), \
             patch('dubsync.core.batch.export_multilingual',
                   side_effect=AssertionError('unsafe must not be exported')), \
             patch('dubsync.core.batch.shutil.disk_usage',
                   return_value=type('D', (), {'free': 2**40})()):
            result = batch_export_groups(self.groups, str(self.root / 'out'),
                                         alignment_options={'interval': .75, 'max_samples': 1200},
                                         journal_directory=self.root / 'reports')
        self.assertEqual(len(result['skipped']), 2)
        self.assertEqual(result['exported'], [])

    def test_common_opening_and_ending_is_not_cross_episode_identity(self):
        source = '/tmp/example.source.mkv'
        target = '/tmp/example.master.mkv'
        result = self.align_result(source, target, safe=False)
        self.assertTrue(_suspected_content_mismatch(result))
        self.assertFalse(_is_independent_episodic_match(result))
        fake_safe = self.align_result(source, target, safe=True)
        # Simulate an overly generous raw coverage, still only edge anchors.
        fake_safe.anchors = result.anchors
        self.assertFalse(_is_independent_episodic_match(fake_safe))

if __name__ == '__main__':
    unittest.main()
