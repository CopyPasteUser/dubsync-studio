"""Deterministic concurrency tests without requiring a NAS, GPU or real video."""
from __future__ import annotations

import threading
import time
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dubsync.core.alignment import AlignmentError, FrameSample, _sample_video_pair
from dubsync.core.alignment_diagnostics import AlignmentDiagnostic


class ParallelSamplingTests(unittest.TestCase):
    def setUp(self):
        self.specs = {"logical_cpus": 12, "ram_gib": 24.0}
        self.diag = AlignmentDiagnostic("source.mkv", "master.mkv", 0.5, 1200, "ffmpeg")

    def _run(self, fake, *, workers=2, effective=2):
        with (patch("dubsync.core.alignment.system_specs", return_value=self.specs),
              patch("dubsync.core.alignment.sampling_worker_policy",
                    return_value=(effective, "Test local")),
              patch("dubsync.core.alignment._sample_with_cache", side_effect=fake)):
            return _sample_video_pair("source.mkv", "master.mkv", 30, 30, 0.5,
                                      None, None, "ffmpeg", self.diag, False, workers)

    def test_two_local_workers_run_concurrently_without_swapping_sides(self):
        barrier = threading.Barrier(2)
        threads = []

        def fake(path, *_args, **_kwargs):
            threads.append(threading.current_thread().name)
            barrier.wait(timeout=3)
            time.sleep(0.03)
            identity = 10 if path.startswith("source") else 20
            return [FrameSample(float(i), identity) for i in range(4)]

        source, master = self._run(fake)
        self.assertEqual(source[0].phash, 10)
        self.assertEqual(master[0].phash, 20)
        self.assertTrue(all(name.startswith("dubsync-sample") for name in threads))
        runtime = self.diag.payload["stages"]["sample_runtime"]
        self.assertEqual(runtime["effective_workers"], 2)
        self.assertLess(runtime["wall_seconds"], runtime["source_seconds"] + runtime["master_seconds"])

    def test_sequential_mode_keeps_order(self):
        visited = []

        def fake(path, *_args, **_kwargs):
            visited.append(path)
            return [FrameSample(float(i), 10) for i in range(4)]

        self._run(fake, workers=1, effective=1)
        self.assertEqual(visited, ["source.mkv", "master.mkv"])
        self.assertEqual(self.diag.payload["stages"]["sample_runtime"]["effective_workers"], 1)

    def test_worker_failure_cancels_sibling(self):
        barrier = threading.Barrier(2)
        sibling_stopped = threading.Event()

        def fake(path, duration, interval, report, side, cancel, *_args):
            barrier.wait(timeout=3)
            if path.startswith("source"):
                raise AlignmentError("broken source")
            until = time.monotonic() + 2
            while time.monotonic() < until and not cancel.is_set():
                time.sleep(0.005)
            if cancel.is_set():
                sibling_stopped.set()
            raise AlignmentError("sibling stopped")

        with self.assertRaises(AlignmentError):
            self._run(fake)
        self.assertTrue(sibling_stopped.is_set())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg nötig")
class LocalMediaParallelIntegrationTests(unittest.TestCase):
    def test_real_ffmpeg_pair_matches_and_preserves_safety_gates(self):
        from dubsync.core.alignment import align_videos

        with tempfile.TemporaryDirectory(prefix="dubsync-parallel-media-") as temp:
            source = Path(temp) / "source.mkv"
            master = Path(temp) / "master.mkv"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24",
                            "-t", "8", "-c:v", "mpeg4", str(source)],
                           check=True, timeout=45)
            shutil.copyfile(source, master)
            reports = []
            with (patch("dubsync.core.alignment.system_specs", return_value={
                "logical_cpus": 12, "ram_gib": 24.0}),
                  patch("dubsync.core.alignment.sampling_worker_policy",
                        return_value=(2, "Local synthetic test"))):
                result = align_videos(str(source), str(master), interval=0.5,
                                      max_samples=200, hardware_accel="ffmpeg",
                                      frame_cache_enabled=False, sampling_workers=2,
                                      diagnostic_sink=reports.append)
            self.assertTrue(result.safe_for_automatic_export)
            stage = reports[0]["stages"]
            self.assertEqual(stage["sample_runtime"]["effective_workers"], 2)
            self.assertTrue(stage["pipeline_profile"]["complete"])
            self.assertGreater(stage["pipeline_profile"]["total_wall_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
