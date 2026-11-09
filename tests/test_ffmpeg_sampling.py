"""FFmpeg sampling semantics and no-silent-failure regression tests."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import unittest
from itertools import count
from pathlib import Path
from unittest import mock

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

from dubsync.core.ffmpeg_sampling import (SamplingBackendError, benchmark_decoders,
                                         benchmark_decoder_candidates, gpu_decode_candidates,
                                         DecoderCandidate, ffmpeg_sample, vaapi_render_device,
                                         _capture_command)


@unittest.skipUnless(shutil.which("ffmpeg") and cv2 is not None and np is not None,
                     "FFmpeg/OpenCV/NumPy erforderlich")
class FFmpegSamplingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "sample.mkv"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24",
            "-t", "3", "-c:v", "mpeg4", str(self.path),
        ], check=True, timeout=30)

    def test_cpu_frames_are_small_and_monotone(self):
        from dubsync.core.alignment import perceptual_hash
        samples = ffmpeg_sample(str(self.path), 3.0, 0.5, perceptual_hash)
        self.assertGreaterEqual(len(samples), 5)
        self.assertTrue(all(b[0] > a[0] for a, b in zip(samples, samples[1:])))
        self.assertGreater(len({value for _, value in samples}), 2)

    def test_accurate_seek_matches_contiguous_cpu_frames(self):
        from dubsync.core.alignment import perceptual_hash
        full = ffmpeg_sample(str(self.path), 3., .5, perceptual_hash)
        window = ffmpeg_sample(str(self.path), 3., .5, perceptual_hash,
                               start_seconds=1., limit_seconds=2.)
        self.assertEqual(window, [(i * .5, value) for i, (_, value) in enumerate(full[2:])])
        with self.assertRaises(ValueError):
            ffmpeg_sample(str(self.path), 3., .5, perceptual_hash, start_seconds=3.)

    def test_benchmark_cpu_provides_measured_rate(self):
        from dubsync.core.alignment import perceptual_hash
        results = benchmark_decoders(str(self.path), 3.0, 0.5,
                                     perceptual_hash, seconds=2.5)
        self.assertGreaterEqual(results["cpu"]["frames"], 4)
        self.assertGreater(results["cpu"]["samples_per_second"], 0)

    def test_real_cpu_decoder_benchmarks_distributed_windows(self):
        from dubsync.core.alignment import perceptual_hash
        results = benchmark_decoder_candidates(str(self.path), 3., .2, perceptual_hash,
                                               candidates=[], seconds=.8,
                                               probe_starts=(0., 1., 2.))
        self.assertEqual(results["cpu"]["probe_windows"], 3)
        self.assertGreaterEqual(results["cpu"]["frames"], 12)

    def test_native_sampling_preserves_cpu_visual_identity(self):
        from dubsync.core.alignment import perceptual_hash, sample_video
        old = sample_video(str(self.path), 3.0, 0.5, hardware_accel="none")
        new = ffmpeg_sample(str(self.path), 3.0, 0.5, perceptual_hash)
        self.assertGreaterEqual(min(len(old), len(new)), 5)
        # Matching timestamps are compared by position on the fixed sample grid.
        distances = [(old[i].phash ^ new[i][1]).bit_count()
                     for i in range(min(len(old), len(new)))]
        # swscale's gray/area interpolation differs slightly from OpenCV's;
        # the same FFmpeg path is used for both aligned videos in production.
        self.assertGreaterEqual(sum(value <= 24 for value in distances), len(distances) - 1)

    def test_pre_cancellation_starts_no_process(self):
        event = threading.Event()
        event.set()
        with mock.patch("dubsync.core.ffmpeg_sampling.subprocess.Popen") as process:
            with self.assertRaises(SamplingBackendError):
                ffmpeg_sample(str(self.path), 3.0, 0.5, lambda frame: 42,
                              cancel_event=event)
            process.assert_not_called()

    def test_auto_uses_fast_verified_gpu_only(self):
        from dubsync.core.alignment import sample_video
        fast = {"cpu": {"frames": 10, "samples_per_second": 10},
                "cuda": {"frames": 10, "samples_per_second": 30,
                         "frame_match_ratio": 1.0, "median_hamming_to_cpu": 0}}
        with (mock.patch("dubsync.core.alignment.gpu_decode_candidates",
                         return_value=[DecoderCandidate("cuda", "cuda")]),
              mock.patch("dubsync.core.alignment.benchmark_decoder_candidates", return_value=fast),
              mock.patch("dubsync.core.alignment.ffmpeg_sample",
                         return_value=[(index * 0.5, index) for index in range(12)]) as ffmpeg):
            result = sample_video(str(self.path), 60.0, 0.5, hardware_accel="auto")
            self.assertEqual(len(result), 12)
            self.assertEqual(ffmpeg.call_args.kwargs["accel"], "cuda")

    def test_auto_uses_fast_ffmpeg_cpu_when_gpu_is_slower(self):
        from dubsync.core.alignment import sample_video
        slow = {"cpu": {"frames": 10, "samples_per_second": 30},
                "cuda": {"frames": 10, "samples_per_second": 12,
                         "frame_match_ratio": 1.0, "median_hamming_to_cpu": 0}}
        with (mock.patch("dubsync.core.alignment.gpu_decode_candidates",
                         return_value=[DecoderCandidate("cuda", "cuda")]),
              mock.patch("dubsync.core.alignment.benchmark_decoder_candidates", return_value=slow),
              mock.patch("dubsync.core.alignment.ffmpeg_sample",
                         return_value=[(i * .5, i) for i in range(12)]) as ffmpeg):
            self.assertEqual(len(sample_video(str(self.path), 60.0, 0.5,
                                              hardware_accel="auto")), 12)
            self.assertIsNone(ffmpeg.call_args.kwargs["accel"])

    def test_auto_defaults_to_ffmpeg_cpu_without_gpu_candidates(self):
        from dubsync.core.alignment import sample_video
        with (mock.patch("dubsync.core.alignment.gpu_decode_candidates", return_value=[]),
              mock.patch("dubsync.core.alignment.ffmpeg_sample",
                         return_value=[(i * .5, i) for i in range(8)]) as ffmpeg):
            samples = sample_video(str(self.path), 3, .5, hardware_accel="auto")
        self.assertEqual(len(samples), 8)
        self.assertIsNone(ffmpeg.call_args.kwargs["accel"])

    def test_auto_falls_back_to_opencv_when_ffmpeg_cpu_fails(self):
        from dubsync.core.alignment import sample_video
        with (mock.patch("dubsync.core.alignment.gpu_decode_candidates", return_value=[]),
              mock.patch("dubsync.core.alignment.ffmpeg_sample",
                         side_effect=SamplingBackendError("ffmpeg unavailable")) as ffmpeg):
            samples = sample_video(str(self.path), 3., .5, hardware_accel="auto")
        self.assertGreaterEqual(len(samples), 4)
        ffmpeg.assert_called_once()

    def test_auto_cancellation_does_not_retry_or_fallback(self):
        from dubsync.core.alignment import AlignmentError, sample_video
        cancel = threading.Event()

        def interrupted(*_args, **_kwargs):
            cancel.set()
            raise SamplingBackendError("cancelled")

        with (mock.patch("dubsync.core.alignment.gpu_decode_candidates", return_value=[]),
              mock.patch("dubsync.core.alignment.ffmpeg_sample", side_effect=interrupted) as ffmpeg,
              mock.patch("dubsync.core.alignment._open_capture") as opencv):
            with self.assertRaisesRegex(AlignmentError, "abgebrochen"):
                sample_video(str(self.path), 3., .5, hardware_accel="auto",
                             cancel_event=cancel)
        ffmpeg.assert_called_once()
        opencv.assert_not_called()

    def test_auto_chooses_fastest_verified_igpu_on_hybrid_system(self):
        from dubsync.core.alignment import sample_video
        available = [DecoderCandidate("amd-vaapi@renderD129", "vaapi", "/dev/dri/renderD129"),
                     DecoderCandidate("intel-vaapi@renderD128", "vaapi", "/dev/dri/renderD128")]
        speeds = {"cpu": {"frames": 10, "samples_per_second": 10},
                  available[0].key: {"frames": 10, "samples_per_second": 25,
                                     "frame_match_ratio": 1.0, "median_hamming_to_cpu": 0},
                  available[1].key: {"frames": 10, "samples_per_second": 40,
                                     "frame_match_ratio": 1.0, "median_hamming_to_cpu": 0}}
        with (mock.patch("dubsync.core.alignment.gpu_decode_candidates", return_value=available),
              mock.patch("dubsync.core.alignment.is_network_path", return_value=False),
              mock.patch("dubsync.core.alignment.benchmark_decoder_candidates", return_value=speeds) as bench,
              mock.patch("dubsync.core.alignment.ffmpeg_sample",
                         return_value=[(index * 0.5, index) for index in range(12)]) as ffmpeg):
            result = sample_video(str(self.path), 60.0, 0.5, hardware_accel="auto")
        self.assertEqual(len(result), 12)
        self.assertEqual(ffmpeg.call_args.kwargs["device"], "/dev/dri/renderD128")
        self.assertEqual(ffmpeg.call_args.kwargs["accel"], "vaapi")
        self.assertEqual(bench.call_args.kwargs["candidates"], available)

    def test_long_h264_uses_three_windows_only_on_local_files(self):
        from dubsync.core.alignment import _GPU_HINTS, sample_video
        _GPU_HINTS.clear()
        self.addCleanup(_GPU_HINTS.clear)
        candidate = DecoderCandidate("intel", "vaapi", "/dev/dri/renderD128")
        speeds = {"cpu": {"frames": 30, "samples_per_second": 10},
                  "intel": {"frames": 30, "samples_per_second": 20,
                            "frame_match_ratio": 1., "median_hamming_to_cpu": 0,
                            "worst_window_speed_ratio": 1.2}}
        with (mock.patch("dubsync.core.alignment.gpu_decode_candidates", return_value=[candidate]),
              mock.patch("dubsync.core.alignment.is_network_path", return_value=False),
              mock.patch("dubsync.core.alignment.benchmark_decoder_candidates", return_value=speeds) as bench,
              mock.patch("dubsync.core.alignment.ffmpeg_sample",
                         return_value=[(i * .5, i) for i in range(12)])):
            sample_video(str(self.path), 1800., .5, hardware_accel="auto", video_codec="h264")
            self.assertEqual(bench.call_args.kwargs["probe_starts"], (0., 864., 1440.))
            sample_video(str(self.path), 1800., .5, hardware_accel="auto", video_codec="hevc")
            self.assertEqual(len(bench.call_args.kwargs["probe_starts"]), 3)
            sample_video(str(self.path), 1800., .5, hardware_accel="auto", video_codec="vp9")
            self.assertEqual(bench.call_args.kwargs["probe_starts"], (0.,))

        with (mock.patch("dubsync.core.alignment.gpu_decode_candidates", return_value=[candidate]),
              mock.patch("dubsync.core.alignment.is_network_path", return_value=True),
              mock.patch("dubsync.core.alignment.benchmark_decoder_candidates") as bench,
              mock.patch("dubsync.core.alignment.ffmpeg_sample",
                         return_value=[(i * .5, i) for i in range(12)])):
            sample_video(str(self.path), 1800., .5, hardware_accel="auto", video_codec="h264")
            bench.assert_not_called()

    def test_multisegment_benchmark_rejects_corrupt_middle_window(self):
        from dubsync.core.ffmpeg_sampling import choose_verified_decoder
        candidate = DecoderCandidate("cuda", "cuda")
        seen = []

        def sample(*_args, accel=None, start_seconds=0., **_kwargs):
            seen.append((accel, start_seconds))
            hashes = [index + int(start_seconds) for index in range(8)]
            if accel == "cuda" and start_seconds == 480.:
                hashes = [value ^ ((1 << 64) - 1) for value in hashes]
            return [(index * .5, value) for index, value in enumerate(hashes)]

        with (mock.patch("dubsync.core.ffmpeg_sampling.ffmpeg_sample", side_effect=sample),
              mock.patch("dubsync.core.ffmpeg_sampling.time.monotonic", side_effect=count())):
            results = benchmark_decoder_candidates("dummy.mkv", 1000., .5, lambda _: 0,
                                                   candidates=[candidate], seconds=4.,
                                                   probe_starts=(0., 480., 800.))

        self.assertEqual(results["cpu"]["probe_windows"], 3)
        self.assertIn("Bildparität", results["cuda"]["error"])
        self.assertEqual(len(seen), 5)  # No reason to sample a third corrupt GPU window.
        self.assertIsNone(choose_verified_decoder(results, [candidate]))

    def test_multisegment_benchmark_rejects_slow_middle_window(self):
        from dubsync.core.ffmpeg_sampling import choose_verified_decoder
        candidate = DecoderCandidate("intel", "vaapi", "/dev/dri/renderD128")

        def sample(*_args, **_kwargs):
            return [(index * .5, index) for index in range(8)]

        # CPU: 3 x 10s; GPU: 5s, 12s, 5s. Average GPU speed wins,
        # but a sustained slow section must keep automatic decoding on CPU.
        ticks = [0., 10., 10., 20., 20., 30., 30., 35., 35., 47., 47., 52.]
        with (mock.patch("dubsync.core.ffmpeg_sampling.ffmpeg_sample", side_effect=sample),
              mock.patch("dubsync.core.ffmpeg_sampling.time.monotonic", side_effect=ticks)):
            results = benchmark_decoder_candidates("dummy.mkv", 1000., .5, lambda _: 0,
                                                   candidates=[candidate], seconds=4.,
                                                   probe_starts=(0., 480., 800.))

        self.assertGreater(results["intel"]["samples_per_second"],
                           results["cpu"]["samples_per_second"] * 1.15)
        self.assertLess(results["intel"]["worst_window_speed_ratio"], .90)
        self.assertIsNone(choose_verified_decoder(results, [candidate]))

    def test_multisegment_benchmark_accepts_consistently_fast_gpu(self):
        from dubsync.core.ffmpeg_sampling import choose_verified_decoder
        candidate = DecoderCandidate("cuda", "cuda")

        def sample(*_args, **_kwargs):
            return [(index * .5, index) for index in range(8)]

        ticks = [0., 10., 10., 20., 20., 30., 30., 35., 35., 40., 40., 45.]
        with (mock.patch("dubsync.core.ffmpeg_sampling.ffmpeg_sample", side_effect=sample),
              mock.patch("dubsync.core.ffmpeg_sampling.time.monotonic", side_effect=ticks)):
            results = benchmark_decoder_candidates("dummy.mkv", 1000., .5, lambda _: 0,
                                                   candidates=[candidate], seconds=4.,
                                                   probe_starts=(0., 480., 800.))

        self.assertEqual(results["cuda"]["probe_windows"], 3)
        self.assertEqual(results["cuda"]["worst_window_speed_ratio"], 2.)
        self.assertEqual(choose_verified_decoder(results, [candidate]), candidate)

    def test_codec_scoped_gpu_hint_still_rechecks_every_new_file(self):
        from dubsync.core.alignment import sample_video, _GPU_HINTS
        available = [DecoderCandidate("amd", "vaapi", "/dev/dri/renderD129"),
                     DecoderCandidate("intel", "vaapi", "/dev/dri/renderD128")]
        speeds = {"cpu": {"frames": 10, "samples_per_second": 10},
                  "amd": {"frames": 10, "samples_per_second": 23,
                          "frame_match_ratio": 1.0, "median_hamming_to_cpu": 0},
                  "intel": {"frames": 10, "samples_per_second": 34,
                            "frame_match_ratio": 1.0, "median_hamming_to_cpu": 0}}
        _GPU_HINTS.clear()
        try:
            with (mock.patch("dubsync.core.alignment.gpu_decode_candidates", return_value=available),
                  mock.patch("dubsync.core.alignment.is_network_path", return_value=False),
                  mock.patch("dubsync.core.alignment.benchmark_decoder_candidates", return_value=speeds) as bench,
                  mock.patch("dubsync.core.alignment.ffmpeg_sample",
                             return_value=[(index * .5, index) for index in range(12)])):
                sample_video(str(self.path), 60, .5, video_codec="h264_test_hint")
                self.assertEqual(bench.call_args.kwargs["candidates"], available)
                sample_video(str(self.path), 60, .5, video_codec="h264_test_hint")
                self.assertEqual(bench.call_args.kwargs["candidates"], [available[1]])
                sample_video(str(self.path), 60, .5, video_codec="hevc_test_hint")
                self.assertEqual(bench.call_args.kwargs["candidates"], available)
                self.assertEqual(bench.call_count, 3)
        finally:
            _GPU_HINTS.clear()

    def test_failed_preferred_gpu_forces_full_next_comparison(self):
        from dubsync.core.alignment import sample_video, _GPU_HINTS
        available = [DecoderCandidate("amd", "vaapi", "/dev/dri/renderD129"),
                     DecoderCandidate("intel", "vaapi", "/dev/dri/renderD128")]
        good = {"cpu": {"frames": 10, "samples_per_second": 10},
                "amd": {"frames": 10, "samples_per_second": 20,
                        "frame_match_ratio": 1, "median_hamming_to_cpu": 0},
                "intel": {"frames": 10, "samples_per_second": 40,
                          "frame_match_ratio": 1, "median_hamming_to_cpu": 0}}
        bad = {"cpu": {"frames": 10, "samples_per_second": 10},
               "intel": {"error": "driver unavailable"}}
        _GPU_HINTS.clear()
        try:
            with (mock.patch("dubsync.core.alignment.gpu_decode_candidates", return_value=available),
                  mock.patch("dubsync.core.alignment.is_network_path", return_value=False),
                  mock.patch("dubsync.core.alignment.benchmark_decoder_candidates",
                             side_effect=[good, bad, good]) as bench,
                  mock.patch("dubsync.core.alignment.ffmpeg_sample",
                             return_value=[(i * .5, i) for i in range(12)])):
                sample_video(str(self.path), 60, .5, video_codec="test_driver_reset")
                sample_video(str(self.path), 60, .5, video_codec="test_driver_reset")
                sample_video(str(self.path), 60, .5, video_codec="test_driver_reset")
                self.assertEqual(bench.call_args_list[1].kwargs["candidates"], [available[1]])
                self.assertEqual(bench.call_args_list[2].kwargs["candidates"], available)
        finally:
            _GPU_HINTS.clear()

    def test_fastest_gpu_is_rejected_if_frame_content_disagrees(self):
        from dubsync.core.alignment import sample_video
        available = [DecoderCandidate("amd", "vaapi", "/dev/dri/renderD129"),
                     DecoderCandidate("intel", "vaapi", "/dev/dri/renderD128")]
        speeds = {"cpu": {"frames": 10, "samples_per_second": 10},
                  "amd": {"frames": 10, "samples_per_second": 90,
                          "frame_match_ratio": 0.4, "median_hamming_to_cpu": 25},
                  "intel": {"frames": 10, "samples_per_second": 20,
                            "frame_match_ratio": 1.0, "median_hamming_to_cpu": 0}}
        with (mock.patch("dubsync.core.alignment.gpu_decode_candidates", return_value=available),
              mock.patch("dubsync.core.alignment.is_network_path", return_value=False),
              mock.patch("dubsync.core.alignment.benchmark_decoder_candidates", return_value=speeds),
              mock.patch("dubsync.core.alignment.ffmpeg_sample",
                         return_value=[(index * 0.5, index) for index in range(12)]) as ffmpeg):
            sample_video(str(self.path), 60.0, 0.5, hardware_accel="auto")
            self.assertEqual(ffmpeg.call_args.kwargs["device"], "/dev/dri/renderD128")

    def test_no_gpu_is_selected_if_all_content_checks_fail(self):
        from dubsync.core.alignment import sample_video
        available = [DecoderCandidate("amd", "vaapi", "/dev/dri/renderD129"),
                     DecoderCandidate("intel", "vaapi", "/dev/dri/renderD128")]
        invalid = {"cpu": {"frames": 10, "samples_per_second": 10},
                   "amd": {"frames": 10, "samples_per_second": 80,
                           "frame_match_ratio": 0.3, "median_hamming_to_cpu": 25},
                   "intel": {"error": "driver unavailable"}}
        with (mock.patch("dubsync.core.alignment.gpu_decode_candidates", return_value=available),
              mock.patch("dubsync.core.alignment.is_network_path", return_value=False),
              mock.patch("dubsync.core.alignment.benchmark_decoder_candidates", return_value=invalid),
              mock.patch("dubsync.core.alignment.ffmpeg_sample",
                         return_value=[(i * .5, i) for i in range(12)]) as ffmpeg):
            samples = sample_video(str(self.path), 60.0, 0.5, hardware_accel="auto")
            self.assertEqual(len(samples), 12)
            self.assertIsNone(ffmpeg.call_args.kwargs["accel"])

    def test_failed_selected_gpu_retries_ffmpeg_cpu_before_opencv(self):
        from dubsync.core.alignment import sample_video, _GPU_HINTS
        candidate = DecoderCandidate("cuda", "cuda")
        measurements = {"cpu": {"frames": 10, "samples_per_second": 10},
                        "cuda": {"frames": 10, "samples_per_second": 40,
                                 "frame_match_ratio": 1., "median_hamming_to_cpu": 0}}
        _GPU_HINTS.clear()
        try:
            with (mock.patch("dubsync.core.alignment.gpu_decode_candidates", return_value=[candidate]),
                  mock.patch("dubsync.core.alignment.is_network_path", return_value=False),
                  mock.patch("dubsync.core.alignment.benchmark_decoder_candidates", return_value=measurements),
                  mock.patch("dubsync.core.alignment._open_capture") as opencv,
                  mock.patch("dubsync.core.alignment.ffmpeg_sample", side_effect=[
                      SamplingBackendError("GPU decode failed"),
                      [(i * .5, i) for i in range(10)]]) as ffmpeg):
                samples = sample_video(str(self.path), 60., .5, hardware_accel="auto")
            self.assertEqual(len(samples), 10)
            self.assertEqual([call.kwargs["accel"] for call in ffmpeg.call_args_list],
                             ["cuda", None])
            opencv.assert_not_called()
        finally:
            _GPU_HINTS.clear()

    def test_auto_skips_extra_gpu_benchmark_reads_for_nas(self):
        from dubsync.core.alignment import sample_video
        with (mock.patch("dubsync.core.alignment.gpu_decode_candidates",
                         return_value=[DecoderCandidate("intel", "vaapi", "/dev/dri/renderD128")]),
              mock.patch("dubsync.core.alignment.is_network_path", return_value=True),
              mock.patch("dubsync.core.alignment.benchmark_decoder_candidates") as benchmark):
            result = sample_video(str(self.path), 60.0, 0.5, hardware_accel="auto")
        self.assertGreaterEqual(len(result), 4)
        benchmark.assert_not_called()

    def test_explicit_amd_device_flows_to_ffmpeg(self):
        from dubsync.core.alignment import sample_video
        with (mock.patch("dubsync.core.alignment.vaapi_render_device", return_value="/dev/dri/renderD129"),
              mock.patch("dubsync.core.alignment.ffmpeg_sample",
                         return_value=[(index * 0.5, index) for index in range(8)]) as ffmpeg):
            samples = sample_video(str(self.path), 3.0, hardware_accel="amd-vaapi")
            self.assertEqual(len(samples), 8)
            self.assertEqual(ffmpeg.call_args.kwargs["device"], "/dev/dri/renderD129")
            self.assertEqual(ffmpeg.call_args.kwargs["accel"], "vaapi")

    def test_explicit_intel_igpu_device_flows_to_ffmpeg(self):
        from dubsync.core.alignment import sample_video
        with (mock.patch("dubsync.core.alignment.vaapi_render_device", return_value="/dev/dri/renderD128") as node,
              mock.patch("dubsync.core.alignment.ffmpeg_sample",
                         return_value=[(index * 0.5, index) for index in range(8)]) as ffmpeg):
            samples = sample_video(str(self.path), 3.0, hardware_accel="intel-vaapi")
            self.assertEqual(len(samples), 8)
            node.assert_called_once_with("intel")
            self.assertEqual(ffmpeg.call_args.kwargs["device"], "/dev/dri/renderD128")
            self.assertEqual(ffmpeg.call_args.kwargs["accel"], "vaapi")

    def test_parallel_thread_budget_is_passed_to_ffmpeg(self):
        with (mock.patch("dubsync.core.ffmpeg_sampling.shutil.which", return_value="/usr/bin/ffmpeg"),
              mock.patch("dubsync.core.ffmpeg_sampling.ffmpeg_hardware_accels", return_value=frozenset()),
              mock.patch("dubsync.core.ffmpeg_sampling._cpu_count", return_value=8)):
            command = _capture_command("movie.mkv", 1, 10, None, None, decode_threads=3)
        self.assertEqual(command[command.index("-threads") + 1], "3")

    def test_ffmpeg_threads_respect_cgroup_budget_even_when_requested_higher(self):
        with (mock.patch("dubsync.core.ffmpeg_sampling.shutil.which", return_value="/usr/bin/ffmpeg"),
              mock.patch("dubsync.core.ffmpeg_sampling._cpu_count", return_value=2),
              mock.patch("dubsync.core.ffmpeg_sampling.ffmpeg_hardware_accels", return_value=frozenset()),
              mock.patch("cv2.getNumThreads", return_value=32)):
            for requested in (None, 8, 1):
                command = _capture_command("movie.mkv", 1., 10, None, None,
                                           decode_threads=requested)
                self.assertEqual(command[command.index("-threads") + 1],
                                 "1" if requested == 1 else "2")

    def test_native_filter_scales_before_grayscale(self):
        with (mock.patch("dubsync.core.ffmpeg_sampling.shutil.which", return_value="/usr/bin/ffmpeg"),
              mock.patch("dubsync.core.ffmpeg_sampling.ffmpeg_hardware_accels",
                         return_value=frozenset())):
            command = _capture_command("movie.mkv", 1., 20, None, None)
        chain = command[command.index("-vf") + 1]
        self.assertIn("fps=fps=", chain)
        self.assertIn("scale=32:32:flags=area,format=gray", chain)


class AMDDeviceSelectionTests(unittest.TestCase):
    def test_amd_selection_prefers_correct_gpu_on_hybrid_system(self):
        nodes = [Path("/dev/dri/renderD128"), Path("/dev/dri/renderD129")]
        def fake_read_text(path, **_kw):
            return "0x1002" if "renderD129" in str(path) else "0x8086"

        with (mock.patch("dubsync.core.ffmpeg_sampling.sys.platform", "linux"),
              mock.patch.object(Path, "glob", return_value=nodes),
              mock.patch.object(Path, "read_text", fake_read_text),
              mock.patch("dubsync.core.ffmpeg_sampling.os.access", return_value=True)):
            self.assertEqual(vaapi_render_device("amd"), "/dev/dri/renderD129")
            self.assertEqual(vaapi_render_device(), "/dev/dri/renderD129")

    def test_amd_device_is_not_fabricated_if_vendor_is_intel(self):
        with (mock.patch("dubsync.core.ffmpeg_sampling.sys.platform", "linux"),
              mock.patch.object(Path, "glob", return_value=[Path("/dev/dri/renderD128")]),
              mock.patch.object(Path, "read_text", return_value="0x8086"),
              mock.patch("dubsync.core.ffmpeg_sampling.os.access", return_value=True)):
            self.assertIsNone(vaapi_render_device("amd"))

    def test_intel_selects_igpu_even_when_amd_gpu_is_present(self):
        nodes = [Path("/dev/dri/renderD128"), Path("/dev/dri/renderD129")]

        def fake_read_text(path, **_kw):
            return "0x1002" if "renderD129" in str(path) else "0x8086"

        with (mock.patch("dubsync.core.ffmpeg_sampling.sys.platform", "linux"),
              mock.patch.object(Path, "glob", return_value=nodes),
              mock.patch.object(Path, "read_text", fake_read_text),
              mock.patch("dubsync.core.ffmpeg_sampling.os.access", return_value=True)):
            self.assertEqual(vaapi_render_device("intel"), "/dev/dri/renderD128")
            self.assertEqual(vaapi_render_device("amd"), "/dev/dri/renderD129")

    def test_hybrid_discovery_enumerates_distinct_amd_and_intel_adapters(self):
        nodes = [Path("/dev/dri/renderD128"), Path("/dev/dri/renderD129")]

        def vendor(path, **_kw):
            return "0x8086" if "renderD128" in str(path) else "0x1002"

        with (mock.patch("dubsync.core.ffmpeg_sampling.sys.platform", "linux"),
              mock.patch("dubsync.core.ffmpeg_sampling.ffmpeg_hardware_accels",
                         return_value=frozenset({"vaapi"})),
              mock.patch.object(Path, "glob", return_value=nodes),
              mock.patch.object(Path, "read_text", vendor),
              mock.patch("dubsync.core.ffmpeg_sampling.os.access", return_value=True)):
            candidates = gpu_decode_candidates()
        self.assertEqual({candidate.key for candidate in candidates},
                         {"intel-vaapi@renderD128", "amd-vaapi@renderD129"})

    def test_multi_gpu_benchmark_decodes_cpu_once_and_checks_each_gpu(self):
        candidates = [DecoderCandidate("amd", "vaapi", "/dev/dri/renderD129"),
                      DecoderCandidate("intel", "vaapi", "/dev/dri/renderD128")]
        frames = [(float(i), i) for i in range(8)]
        with (mock.patch("dubsync.core.ffmpeg_sampling.ffmpeg_sample", return_value=frames) as sample,
              mock.patch("dubsync.core.ffmpeg_sampling.time.monotonic",
                         side_effect=[1, 2, 3, 3.5, 4, 4.25])):
            benchmark = benchmark_decoder_candidates("movie.mkv", 60, 1, lambda image: 42,
                                                      candidates=candidates)
        self.assertEqual(sample.call_count, 3)
        self.assertEqual(sum(call.kwargs["accel"] is None for call in sample.call_args_list), 1)
        self.assertGreater(benchmark["intel"]["samples_per_second"], benchmark["amd"]["samples_per_second"])
        self.assertEqual(benchmark["intel"]["frame_match_ratio"], 1.0)

    def test_explicit_vaapi_device_is_validated(self):
        with (mock.patch("dubsync.core.ffmpeg_sampling.sys.platform", "linux"),
              mock.patch("dubsync.core.ffmpeg_sampling.shutil.which", return_value="/usr/bin/ffmpeg"),
              mock.patch("dubsync.core.ffmpeg_sampling.ffmpeg_hardware_accels", return_value=frozenset({"vaapi"})),
              mock.patch.object(Path, "glob", return_value=[Path("/dev/dri/renderD129")]),
              mock.patch("dubsync.core.ffmpeg_sampling.os.access", return_value=True)):
            command = _capture_command("movie.mkv", 1, 10, "vaapi", None, "/dev/dri/renderD129")
            self.assertIn("-hwaccel_device", command)
            self.assertEqual(command[command.index("-hwaccel_device") + 1], "/dev/dri/renderD129")


if __name__ == "__main__":
    unittest.main()
