"""Raw frame transport tests, including real FFmpeg pipe output."""
from __future__ import annotations

import io
import shutil
import subprocess
import unittest

from dubsync.core.frame_transport import BATCH_FRAMES, FRAME_SIZE, frame_batches


class FragmentedStream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def read1(self, size):
        chunk = next(self.chunks, b"")
        assert len(chunk) <= size
        return chunk


class FrameTransportTests(unittest.TestCase):
    def test_complete_batches_preserve_bytes_and_order(self):
        data = b"".join(bytes([i]) * FRAME_SIZE for i in range(31))
        batches = list(frame_batches(io.BytesIO(data)))
        self.assertEqual(b"".join(batches), data)
        self.assertEqual(len(batches), 4)
        self.assertTrue(all(len(b) <= BATCH_FRAMES * FRAME_SIZE for b in batches))

    def test_fragmented_reads_reassemble_without_loss(self):
        data = bytes(range(256)) * 16
        chunks = [data[:17], data[17:1025], data[1025:3073], data[3073:]]
        batches = list(frame_batches(FragmentedStream(chunks)))
        self.assertEqual(b"".join(batches), data)
        self.assertTrue(all(len(b) % FRAME_SIZE == 0 for b in batches))

    def test_partial_tail_is_visible_to_caller(self):
        self.assertEqual(list(frame_batches(io.BytesIO(b"x" * (FRAME_SIZE + 7)))),
                         [b"x" * FRAME_SIZE, b"x" * 7])

    def test_empty_stream(self):
        self.assertEqual(list(frame_batches(io.BytesIO())), [])

    def test_stream_without_read1(self):
        class ReadOnly:
            def __init__(self):
                self.stream = io.BytesIO(b"a" * FRAME_SIZE)

            def read(self, size):
                return self.stream.read(size)

        self.assertEqual(list(frame_batches(ReadOnly())), [b"a" * FRAME_SIZE])

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_real_ffmpeg_pipe_exact_bytes(self):
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                   "-i", "testsrc2=size=32x32:rate=24", "-frames:v", "97",
                   "-vf", "format=gray", "-filter_threads", "1", "-threads", "1",
                   "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
        expected = subprocess.check_output(command, timeout=20)
        self.assertEqual(len(expected), 97 * FRAME_SIZE)
        # Use the same buffering as the production sampler. The fixture is
        # bounded; communicate handles process lifetime and timeout.
        with subprocess.Popen(command, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,
                              bufsize=FRAME_SIZE * 4) as process:
            try:
                batches = list(frame_batches(process.stdout))
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        self.assertEqual(b"".join(batches), expected)
        self.assertTrue(all(len(b) % FRAME_SIZE == 0 for b in batches))


if __name__ == "__main__":
    unittest.main()
