"""Exercise the production sampler through real compressed input and pipe faults."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from dubsync.core.ffmpeg_sampling import (FRAME_SIZE, DecoderCandidate,
    SamplingBackendError, _capture_command, benchmark_decoder_candidates, ffmpeg_sample)


def fingerprint(frame):
    return int.from_bytes(hashlib.blake2b(frame.tobytes(), digest_size=8).digest(), 'little')


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
class SamplerIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'fixture.mkv'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=160x90:rate=12:duration=5', '-c:v', 'mpeg4',
                        '-threads', '1', str(self.path)], check=True, timeout=30)

    def test_compressed_media_exact_raw_frame_order(self):
        command = _capture_command(str(self.path), .25, 22, None, None, decode_threads=1)
        raw = subprocess.check_output(command, timeout=30)
        self.assertEqual(len(raw) % FRAME_SIZE, 0)
        expected = [(round(i * .25, 6), int.from_bytes(hashlib.blake2b(raw[start:start+FRAME_SIZE],
                    digest_size=8).digest(), 'little')) for i, start in enumerate(range(0, len(raw), FRAME_SIZE))]
        reports = []
        actual = ffmpeg_sample(str(self.path), 5., .25, fingerprint,
                              decode_threads=1, progress=lambda *args: reports.append(args))
        self.assertEqual(actual, expected)
        self.assertEqual(reports[-1][1:], (len(expected), len(expected)))

    def test_cancellation_between_fingerprints(self):
        stop = threading.Event()
        calls = []
        def cancel_after_first(frame):
            calls.append(1)
            stop.set()
            return fingerprint(frame)
        with self.assertRaisesRegex(SamplingBackendError, 'abgebrochen'):
            ffmpeg_sample(str(self.path), 5., .25, cancel_after_first,
                          decode_threads=1, cancel_event=stop)
        self.assertEqual(calls, [1])

    def test_truncated_pipe_is_rejected(self):
        command = [sys.executable, '-c', f'import sys; sys.stdout.buffer.write(b"x" * {FRAME_SIZE*5+7})']
        with patch('dubsync.core.ffmpeg_sampling._capture_command', return_value=command):
            with self.assertRaisesRegex(SamplingBackendError, 'unvollständigen'):
                ffmpeg_sample(str(self.path), 5., .25, fingerprint, decode_threads=1)

    def test_qualification_reuses_thread_budget(self):
        frames = [(i * .5, i) for i in range(8)]
        with patch('dubsync.core.ffmpeg_sampling.ffmpeg_sample', return_value=frames) as sample:
            benchmark_decoder_candidates(str(self.path), 5., .5, fingerprint,
                candidates=[DecoderCandidate('gpu', 'cuda')], seconds=4., decode_threads=2)
        self.assertEqual(len(sample.call_args_list), 2)
        self.assertTrue(all(call.kwargs['decode_threads'] == 2 for call in sample.call_args_list))
