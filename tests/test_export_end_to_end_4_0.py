"""Production exporter checks with synthetic owned media, no network or subtitles."""
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import threading
import unittest

from dubsync.core.export import ExportError, export_multilingual
from dubsync.core.models import AlignmentResult, Anchor, TrackChoice, TimelineSegment
from dubsync.core.probe import probe_media
from scripts.benchmark_export import benchmark, file_sha256, packet_timing_evidence, continuous_added_audio


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


    def test_off_mode_does_not_load_subtitle_backend(self):
        # Fresh interpreter checks the actual import graph, not a cached module.
        subprocess.run([sys.executable, '-c',
            'import sys; from dubsync.core.export import export_multilingual; '
            'assert "dubsync.core.subtitles" not in sys.modules'], check=True, timeout=30)

    def test_post_commit_callback_error_does_not_report_failed_export(self):
        def report(percent, _message):
            if percent == 100:
                raise RuntimeError('late cancellation notification')
        self.assertEqual(export_multilingual(str(self.master), [self.track], str(self.output), progress=report),
                         str(self.output))
        self.assertEqual(probe_media(str(self.output)).audio_streams, 2)


    def test_delayed_sections_keep_continuous_clock_and_unmatched_silence(self):
        import array
        for segments, quiet, audible in (
            ([TimelineSegment(0, 7, 1, 8, 14, 0, 0, 14)], (0.25, 8.5), (2., 6.)),
            ([TimelineSegment(0, 3, 1, 4, 10, 0, 0, 10),
              TimelineSegment(4, 7, 6, 9, 10, 0, 0, 10)], (0.25, 5., 9.5), (2., 7.)),
        ):
            with self.subTest(sections=len(segments)):
                self.result.segments = segments
                self.result.manual_segments = True
                export_multilingual(str(self.master), [self.track], str(self.output),
                                    allow_low_confidence=True, overwrite=True)
                timing = packet_timing_evidence('ffprobe', self.output)
                self.assertTrue(continuous_added_audio(timing, self.result.target_duration, [2]), timing)
                raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(self.output),
                    '-map', '0:a:1', '-ac', '1', '-ar', '48000', '-f', 's16le', '-'], timeout=20)
                samples = array.array('h')
                samples.frombytes(raw)
                if sys.byteorder != 'little':
                    samples.byteswap()
                def rms(at):
                    start = round(at * 48000)
                    values = samples[start:start + 9600]
                    self.assertEqual(len(values), 9600)
                    return (sum(x*x for x in values) / len(values)) ** .5
                for at in quiet:
                    self.assertLess(rms(at), 30)
                for at in audible:
                    self.assertGreater(rms(at), 100)
