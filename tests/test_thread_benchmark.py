"""Bounded CPU thread tuning never weakens fingerprint correctness."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dubsync.cli import main
from dubsync.core.ffmpeg_sampling import SamplingBackendError
from dubsync.core.thread_benchmark import compare_cpu_decode_threads


class CPUThreadBenchmarkTests(unittest.TestCase):
    @staticmethod
    def _run_fake(costs, *, mismatch=None, failures=None, logical=4):
        ticks = [0.]
        mismatch = mismatch or set()
        failures = failures or set()

        def decode(*_args, decode_threads=None, **_kwargs):
            if decode_threads in failures:
                raise SamplingBackendError("broken")
            ticks[0] += costs[decode_threads]
            return [(float(i), (i if decode_threads not in mismatch else i ^ 255))
                    for i in range(8)]

        with (patch("dubsync.core.thread_benchmark.ffmpeg_sample", side_effect=decode),
              patch("dubsync.core.thread_benchmark.time.perf_counter",
                    side_effect=lambda: ticks[0])):
            return compare_cpu_decode_threads("movie.mkv", 30., seconds=8., interval=1.,
                                              repetitions=3, logical_cpus=logical)

    def test_suggests_faster_threads_only_with_exact_hash_parity(self):
        result = self._run_fake({None: 1., 1: .5, 2: .8, 4: 1.1})
        self.assertEqual(result["recommended_threads"], 1)
        self.assertEqual(result["measured_speedup"], 2.)
        self.assertEqual(result["frames_per_run"], 8)
        self.assertTrue(all(row["exact_frame_parity"] for row in result["runs"]))
        self.assertFalse(result["automatic_setting_change"])

    def test_faster_decoder_with_different_frames_is_rejected(self):
        result = self._run_fake({None: 1., 1: .3, 2: .95, 4: 1.1}, mismatch={1})
        self.assertIsNone(result["recommended_threads"])
        self.assertFalse(next(row for row in result["runs"] if row["threads"] == 1)["exact_frame_parity"])

    def test_unusable_default_decoder_is_a_visible_error(self):
        with self.assertRaisesRegex(RuntimeError, "Standard-FFmpeg"):
            self._run_fake({None: 1., 1: .5, 2: .8, 4: 1.1}, failures={None})

    def test_invalid_sampling_budget_rejected(self):
        with self.assertRaises(ValueError):
            compare_cpu_decode_threads("movie.mkv", 2., seconds=3., interval=1.)

    def test_thread_candidates_respect_container_cpu_budget(self):
        samples = [(float(i), i) for i in range(8)]
        with (patch("dubsync.core.thread_benchmark.system_specs",
                    return_value={"logical_cpus": 2}),
              patch("dubsync.core.thread_benchmark.ffmpeg_sample", return_value=samples)):
            report = compare_cpu_decode_threads("movie.mkv", 30., seconds=8.,
                                                interval=1., repetitions=1)
        self.assertEqual([run["threads"] for run in report["runs"]], [None, 1, 2])

    def test_long_video_checks_three_separated_windows_and_reverses_order(self):
        ticks = [0.]
        calls = []

        def sample(*_args, decode_threads=None, start_seconds=0., **_kwargs):
            calls.append((decode_threads, start_seconds))
            ticks[0] += 1. if decode_threads is None else .5
            return [(float(index), index + int(start_seconds)) for index in range(8)]

        with (patch("dubsync.core.thread_benchmark.ffmpeg_sample", side_effect=sample),
              patch("dubsync.core.thread_benchmark.time.perf_counter",
                    side_effect=lambda: ticks[0])):
            report = compare_cpu_decode_threads("movie.mkv", 1800., seconds=8.,
                                                interval=1., repetitions=2, logical_cpus=1)
        self.assertEqual([item["start_seconds"] for item in report["windows"]],
                         [0., 896., 1792.])
        self.assertEqual(report["tested_seconds"], 24.)
        self.assertEqual(report["frames_per_run"], 24)
        self.assertEqual(report["recommended_threads"], 1)
        self.assertEqual(calls[:3], [(None, 0.), (None, 896.), (None, 1792.)])
        self.assertEqual(calls[6:9], [(1, 1792.), (1, 896.), (1, 0.)])

    def test_slow_late_window_rejects_fast_aggregate(self):
        ticks = [0.]

        def sample(*_args, decode_threads=None, start_seconds=0., **_kwargs):
            costs = {None: 1., 1: 1.4 if start_seconds > 1000 else .2}
            ticks[0] += costs[decode_threads]
            return [(float(index), index + int(start_seconds)) for index in range(8)]

        with (patch("dubsync.core.thread_benchmark.ffmpeg_sample", side_effect=sample),
              patch("dubsync.core.thread_benchmark.time.perf_counter",
                    side_effect=lambda: ticks[0])):
            report = compare_cpu_decode_threads("movie.mkv", 1800., seconds=8.,
                                                interval=1., repetitions=3, logical_cpus=1)
        thread = next(row for row in report["runs"] if row["threads"] == 1)
        self.assertTrue(thread["exact_frame_parity"])
        self.assertGreater(thread["speedup_vs_default"], 1.15)
        self.assertLess(thread["worst_window_speedup"], .9)
        self.assertGreater(report["measured_speedup"], 1.15)
        self.assertIsNone(report["recommended_threads"])

    def test_late_window_frame_mismatch_fails_closed(self):
        ticks = [0.]

        def sample(*_args, decode_threads=None, start_seconds=0., **_kwargs):
            ticks[0] += .5 if decode_threads == 1 else 1.
            corrupt = decode_threads == 1 and start_seconds > 1000
            return [(float(index), index ^ 42 if corrupt else index)
                    for index in range(8)]

        with (patch("dubsync.core.thread_benchmark.ffmpeg_sample", side_effect=sample),
              patch("dubsync.core.thread_benchmark.time.perf_counter",
                    side_effect=lambda: ticks[0])):
            report = compare_cpu_decode_threads("movie.mkv", 1800., seconds=8.,
                                                interval=1., repetitions=2, logical_cpus=1)
        self.assertFalse(next(row for row in report["runs"] if row["threads"] == 1)
                         ["exact_frame_parity"])
        self.assertIsNone(report["recommended_threads"])

    def test_cli_json_shows_measurements_without_media_path_or_settings_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "test.mkv"
            file.write_bytes(b"mock")
            expected = self._run_fake({None: 1., 1: .5, 2: .8, 4: 1.1})
            stdout = io.StringIO()
            with (patch("dubsync.core.network.is_network_path", return_value=False),
                  patch("dubsync.core.probe.probe_media", return_value=SimpleNamespace(duration=30.)),
                  patch("dubsync.core.thread_benchmark.compare_cpu_decode_threads",
                        return_value=expected) as bench,
                  patch("sys.stdout", stdout)):
                code = main(["--benchmark-threads", str(file), "--json"])
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(stdout.getvalue())["automatic_setting_change"])
            self.assertNotIn(tmp, stdout.getvalue())
            self.assertEqual(bench.call_args.args[0], str(file))

    def test_cli_refuses_repeated_nas_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "test.mkv"
            file.write_bytes(b"mock")
            with (patch("dubsync.core.network.is_network_path", return_value=True),
                  patch("dubsync.core.probe.probe_media") as probe,
                  patch("sys.stderr", io.StringIO())):
                self.assertEqual(main(["--benchmark-threads", str(file)]), 2)
            probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
