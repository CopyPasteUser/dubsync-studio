"""Real FFmpeg verification helpers for cross-revision export acceptance."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile
import unittest

from scripts.benchmark_export import file_sha256, packet_timing_sha256, stream_hash


class ExportBenchmarkEvidenceTests(unittest.TestCase):
    def test_file_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test'
            path.write_bytes(b'original')
            self.assertEqual(file_sha256(path), hashlib.sha256(b'original').hexdigest())

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Media tools required')
    def test_copy_keeps_media_hash_and_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = [Path(directory) / name for name in ('source.mkv', 'out.mkv')]
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=160x90:rate=12:duration=2', '-c:v', 'mpeg4',
                            '-threads', '1', str(source)], check=True, timeout=30)
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source),
                            '-map', '0:v:0', '-c', 'copy', str(output)], check=True, timeout=30)
            self.assertEqual(stream_hash('ffmpeg', source, '0:v:0', ['-c:v', 'copy']),
                             stream_hash('ffmpeg', output, '0:v:0', ['-c:v', 'copy']))
            self.assertEqual(packet_timing_sha256('ffprobe', source), packet_timing_sha256('ffprobe', output))
