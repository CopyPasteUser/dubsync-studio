"""Regression tests for cancellations and the actionable support explanation."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import cv2

from dubsync.core.alignment import AlignmentError, sample_video, align_videos
from dubsync.core.alignment_diagnostics import AlignmentDiagnostic, latest_diagnostic, summarize_diagnostic
from dubsync.core.models import MediaInfo, StreamInfo


class FakeCapture:
    def __init__(self):
        self.frame = 0
        self.closed = False

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return 24.0
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return 1200
        if prop == cv2.CAP_PROP_POS_MSEC:
            return self.frame * 1000.0 / 24.0
        return 0.0

    def grab(self):
        if self.frame >= 1200:
            return False
        self.frame += 1
        return True

    def retrieve(self):
        return True, np.full((32, 32, 3), self.frame % 255, dtype=np.uint8)

    def release(self):
        self.closed = True


class CancellationInsightTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.dir_patch = patch('dubsync.core.alignment_diagnostics.diagnostics_directory', return_value=self.dir)
        self.dir_patch.start()
        self.addCleanup(self.dir_patch.stop)
        self.source = str(self.dir / 'Series S01E004.mkv')
        self.target = str(self.dir / 'Other S01E004.mkv')
        self.source_info = MediaInfo(path=self.source, duration=120.0, size_bytes=400,
                                     format_name='matroska', video_streams=1, audio_streams=0, subtitle_streams=0,
                                     streams=[StreamInfo(index=0, codec_type='video', codec_name='h264', fps=24.0)])
        self.target_info = MediaInfo(path=self.target, duration=230.0, size_bytes=200,
                                     format_name='matroska', video_streams=1, audio_streams=0, subtitle_streams=0,
                                     streams=[StreamInfo(index=0, codec_type='video', codec_name='h264', fps=24.0)])

    def test_partial_sampling_telemetry_survives_cancel(self):
        cap = FakeCapture()
        cancel = threading.Event()
        diag = AlignmentDiagnostic(self.source, self.target, 1.0, 1200, 'none')

        def progress(phase, current, total):
            if current >= 20:
                cancel.set()

        with patch('dubsync.core.alignment._open_capture', return_value=(cap, False)):
            with self.assertRaisesRegex(AlignmentError, 'abgebrochen'):
                sample_video(self.source, 120, 1.0, progress, 'Quellvideo', cancel, 'none', diag)
        self.assertTrue(cap.closed)
        telemetry = diag.payload['stages']['sample_source']
        self.assertEqual(telemetry['status'], 'cancelled')
        self.assertGreaterEqual(telemetry['frames_grabbed'], 400)
        self.assertGreaterEqual(telemetry['samples_collected'], 20)
        self.assertGreater(telemetry['last_decoded_timestamp_s'], 15)
        self.assertGreaterEqual(telemetry['elapsed_s'], 0)
        self.assertNotIn('sample_master', diag.payload['stages'])

    def test_cancel_origin_saved_by_alignment(self):
        cancel = threading.Event()
        cancel.reason = 'user_button'
        cancel.requested_utc = '2026-10-25T14:35:00+01:00'
        def interrupted(*args, **kwargs):
            cancel.set()
            raise AlignmentError('Videoanalyse abgebrochen.')
        with patch('dubsync.core.alignment.sample_video', side_effect=interrupted):
            with self.assertRaises(AlignmentError):
                align_videos(self.source, self.target, source_info=self.source_info,
                             target_info=self.target_info, cancel_event=cancel)
        report = json.loads(latest_diagnostic().read_text())
        self.assertEqual(report['decision']['status'], 'cancelled')
        self.assertEqual(report['decision']['cancellation_origin'], 'user_button')
        self.assertIn('cancellation_requested', [x['event'] for x in report['events']])
        rendered = summarize_diagnostic(report)
        self.assertIn('ABGEBROCHEN', rendered)
        self.assertIn('Schaltfläche', rendered)
        self.assertIn('110.000s', rendered)

    def test_old_cancelled_report_is_not_mislabelled_unsafe(self):
        payload = {'files': {'source': {'filename': 'S01E004.mkv', 'duration_s': 1327.326},
                             'master': {'filename': 'S01E004.mkv', 'duration_s': 1436.375}},
                   'decision': {'status': 'cancelled', 'exception_type': 'AlignmentError'},
                   'stages': {'sampling_plan': {'max_samples': 1200}}}
        explanation = summarize_diagnostic(payload)
        self.assertIn('KEINE BEWERTUNG', explanation)
        self.assertIn('109.049s', explanation)
        self.assertIn('nicht feststellbar', explanation)
        self.assertNotIn('UNSICHERE SYNCHRONISATION', explanation)


if __name__ == '__main__':
    unittest.main()
