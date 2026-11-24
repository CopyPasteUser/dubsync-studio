"""Real subprocess and filesystem contracts for export publication."""
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from dubsync.core.export_process import MuxCancelled, promote_output, run_mux


class MuxProcessTests(unittest.TestCase):
    def test_progress_and_exit_code(self):
        with tempfile.TemporaryFile() as log:
            seen = []
            rc = run_mux([sys.executable, '-c', 'print("out_time_us=10"); raise SystemExit(7)'],
                         log, seen.append)
        self.assertEqual(rc, 7)
        self.assertEqual(seen, ['out_time_us=10\n'])

    def test_cancel_silent_and_partial_line(self):
        for prefix in ('', 'import sys; sys.stdout.write("partial"); sys.stdout.flush(); '):
            with self.subTest(prefix=prefix), tempfile.TemporaryFile() as log:
                cancel = threading.Event()
                timer = threading.Timer(.3, cancel.set)
                started = time.monotonic()
                timer.start()
                try:
                    with self.assertRaises(MuxCancelled):
                        run_mux([sys.executable, '-c', prefix + 'import time; time.sleep(30)'],
                                log, lambda _: None, cancel)
                finally:
                    timer.cancel()
                    timer.join()
                self.assertLess(time.monotonic() - started, 5)

    def test_pre_cancel_does_not_launch(self):
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryFile() as log, patch('dubsync.core.export_process.subprocess.Popen') as popen:
            with self.assertRaises(MuxCancelled):
                run_mux(['unused'], log, lambda _: None, cancel)
            popen.assert_not_called()

    def test_callback_error_stops_child(self):
        with tempfile.TemporaryFile() as log:
            started = time.monotonic()
            def fail(_):
                raise ValueError('callback failed')
            with self.assertRaisesRegex(ValueError, 'callback failed'):
                run_mux([sys.executable, '-u', '-c', 'import time; print("ready"); time.sleep(30)'], log, fail)
            self.assertLess(time.monotonic() - started, 5)


    def test_cancel_after_stdout_closed(self):
        with tempfile.TemporaryFile() as log:
            cancel = threading.Event()
            timer = threading.Timer(.3, cancel.set)
            timer.start()
            started = time.monotonic()
            try:
                with self.assertRaises(MuxCancelled):
                    run_mux([sys.executable, '-c', 'import os,time; os.close(1); time.sleep(30)'],
                            log, lambda _: None, cancel)
            finally:
                timer.cancel()
                timer.join()
            self.assertLess(time.monotonic() - started, 5)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Media tools required')
    def test_real_ffmpeg_mux_and_verified_publication(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryFile() as log:
            temp, output = Path(d) / 'temp.mkv', Path(d) / 'out.mkv'
            messages = []
            command = ['ffmpeg', '-nostdin', '-v', 'error', '-progress', 'pipe:1',
                       '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=12:duration=2',
                       '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                       '-c:v', 'mpeg4', '-threads', '1', '-c:a', 'aac', str(temp)]
            self.assertEqual(run_mux(command, log, messages.append), 0)
            self.assertIn('progress=end\n', messages)
            streams = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                'stream=codec_type', '-of', 'csv=p=0', str(temp)], timeout=15).decode().splitlines()
            self.assertEqual(streams, ['video', 'audio'])
            payload = temp.read_bytes()
            promote_output(temp, output, overwrite=False)
            self.assertEqual(output.read_bytes(), payload)


class PublishTests(unittest.TestCase):
    def test_existing_output_never_replaced_without_permission(self):
        with tempfile.TemporaryDirectory() as d:
            old, new = Path(d) / 'out.mkv', Path(d) / 'temp.mkv'
            old.write_bytes(b'old')
            new.write_bytes(b'new')
            with self.assertRaises(FileExistsError):
                promote_output(new, old, overwrite=False)
            self.assertEqual(old.read_bytes(), b'old')
            self.assertEqual(new.read_bytes(), b'new')
            promote_output(new, old, overwrite=True)
            self.assertEqual(old.read_bytes(), b'new')
            self.assertFalse(new.exists())

    def test_two_publishers_only_one_wins(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / 'out.mkv'
            inputs = [Path(d) / f'{i}.mkv' for i in range(2)]
            for i, item in enumerate(inputs):
                item.write_bytes(bytes([i]))
            barrier = threading.Barrier(2)
            results = []
            def publish(i):
                barrier.wait(timeout=2)
                try:
                    promote_output(inputs[i], output, overwrite=False)
                    results.append(('won', i))
                except FileExistsError:
                    results.append(('lost', i))
            threads = [threading.Thread(target=publish, args=(i,)) for i in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            self.assertEqual(sorted(status for status, _ in results), ['lost', 'won'])
            winner = next(i for status, i in results if status == 'won')
            self.assertEqual(output.read_bytes(), bytes([winner]))

    def test_hardlink_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            source, output = Path(d) / 'temp.mkv', Path(d) / 'out.mkv'
            source.write_bytes(b'new')
            with patch('dubsync.core.export_process.ctypes.CDLL', return_value=object()):
                promote_output(source, output, overwrite=False)
                self.assertEqual(output.read_bytes(), b'new')
                with self.assertRaises(FileExistsError):
                    promote_output(source, output, overwrite=False)
