"""Production exporter checks with synthetic owned media, no network or subtitles."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

from dubsync.core.export import ExportError, export_multilingual
from dubsync.core.models import AlignmentResult, Anchor, TrackChoice
from dubsync.core.probe import probe_media
from scripts.benchmark_export import benchmark, file_sha256


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Media tools required')
class ExportEndToEndTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.master = self.root / 'master.mkv'
        self.source = self.root / 'source.wav'
        self.output = self.root / 'output.mkv'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=160x90:rate=12:duration=10', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=10', '-c:v', 'mpeg4', '-threads', '1',
                        '-c:a', 'aac', '-shortest', str(self.master)], check=True, timeout=30)
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=frequency=660:duration=10', str(self.source)], check=True, timeout=30)
        self.result = AlignmentResult(str(self.source), str(self.master), 1., 0.,
            [Anchor(i * .5, i * .5, 0) for i in range(20)], 1., 1., 1., 0., 0.,
            probe_media(str(self.source)).duration, probe_media(str(self.master)).duration, .5)
        self.assertTrue(self.result.safe_for_automatic_export)
        self.track = TrackChoice(str(self.source), 0, 'eng', 'Fixture', True, self.result)
        self.input_hashes = [file_sha256(p) for p in (self.master, self.source)]

    def tearDown(self):
        self.assertEqual(self.input_hashes, [file_sha256(p) for p in (self.master, self.source)])
        self.assertFalse(list(self.root.glob('.*.dubsync-*.mkv')))

    def test_other_source_alignment_rejected_even_with_manual_confirmation(self):
        self.result.source_path = str(self.root / 'wrong.wav')
        with self.assertRaisesRegex(ExportError, 'ausgewählten Quelle'):
            export_multilingual(str(self.master), [self.track], str(self.output), allow_low_confidence=True)
        self.assertFalse(self.output.exists())

    def test_cancel_during_verification_preserves_existing_output(self):
        self.output.write_bytes(b'old-output')
        stop = threading.Event()
        def report(_percent, message):
            if message.startswith('Prüfe exportierte'):
                stop.set()
        with self.assertRaisesRegex(ExportError, 'abgebrochen'):
            export_multilingual(str(self.master), [self.track], str(self.output),
                                overwrite=True, progress=report, cancel_event=stop)
        self.assertEqual(self.output.read_bytes(), b'old-output')

    def test_concurrent_destination_not_overwritten_after_mux(self):
        def report(_percent, message):
            if message.startswith('Prüfe exportierte'):
                self.output.write_bytes(b'concurrent-writer')
        with self.assertRaisesRegex(ExportError, 'existiert inzwischen'):
            export_multilingual(str(self.master), [self.track], str(self.output), progress=report)
        self.assertEqual(self.output.read_bytes(), b'concurrent-writer')

    def test_three_dubs_remain_repeatable_and_continuous(self):
        root = self.root / 'benchmark'
        root.mkdir()
        report = benchmark(root, 30, 3)
        self.assertTrue(report['inputs_unchanged'])
        self.assertTrue(all(row['continuous_added_audio'] for row in report['observations']))
        self.assertEqual(len(report['evidence']['audio']), 4)
        self.assertLessEqual(report['evidence']['packet_timing']['4']['max_gap_s'], .004)
