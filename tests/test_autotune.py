"""Autotune safety, hardware identity and explicit-acceptance regression tests."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dubsync.core.autotune import tune_local_cpu_decoder
from dubsync.core.performance import (cpu_decoder_threads_for_settings,
                                      normalize_performance_settings)


class CpuAutotuneCoreTests(unittest.TestCase):
    def setUp(self):
        self.specs = {"logical_cpus": 8, "cpu_model": "Test CPU"}
        self.valid_report = {
            "recommended_threads": 2,
            "repetitions": 3,
            "runs": [{"threads": 2, "exact_frame_parity": True,
                      "completed_runs": 3, "speedup_vs_default": 1.3,
                      "worst_window_speedup": 1.1}],
        }

    def test_network_mount_is_rejected_before_stat_or_probe(self):
        with (patch("dubsync.core.autotune.is_network_path", return_value=True),
              patch("dubsync.core.autotune.Path.is_file") as stat,
              patch("dubsync.core.autotune.probe_media") as probe):
            with self.assertRaisesRegex(ValueError, "Netzwerk"):
                tune_local_cpu_decoder("/mnt/nas/movie.mkv")
        stat.assert_not_called()
        probe.assert_not_called()

    def test_symlink_into_network_mount_is_rejected_before_stat(self):
        def remote(path):
            return path.startswith("/mnt/nas/")

        with (patch("dubsync.core.autotune.os.path.realpath",
                    return_value="/mnt/nas/video.mkv"),
              patch("dubsync.core.autotune.is_network_path", side_effect=remote),
              patch("dubsync.core.autotune.Path.is_file") as stat):
            with self.assertRaisesRegex(ValueError, "NAS"):
                tune_local_cpu_decoder("/tmp/local-link.mkv")
        stat.assert_not_called()

    def test_eligible_measurement_returns_host_bound_suggestion_without_write(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "sample.mkv"
            video.touch()
            with (patch("dubsync.core.autotune.is_network_path", return_value=False),
                  patch("dubsync.core.autotune.probe_media",
                        return_value=SimpleNamespace(video=[object()], duration=1800.)),
                  patch("dubsync.core.autotune.system_specs", return_value=self.specs),
                  patch("dubsync.core.autotune.compare_cpu_decode_threads",
                        return_value=self.valid_report) as benchmark):
                result = tune_local_cpu_decoder(str(video))
            self.assertEqual(result["tuning"], {
                "cpu_model": "Test CPU", "logical_cpus": 8, "threads": 2})
            self.assertEqual(result["selected_filename"], "sample.mkv")
            self.assertTrue(video.is_file())
            self.assertEqual(benchmark.call_args.kwargs["repetitions"], 3)
            self.assertEqual(benchmark.call_args.kwargs["logical_cpus"], 8)

    def test_mismatch_cannot_be_accepted_even_if_report_claims_recommendation(self):
        bad_report = {**self.valid_report, "runs": [
            {**self.valid_report["runs"][0], "exact_frame_parity": False}]}
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "sample.mkv"
            video.touch()
            with (patch("dubsync.core.autotune.is_network_path", return_value=False),
                  patch("dubsync.core.autotune.probe_media",
                        return_value=SimpleNamespace(video=[object()], duration=1800.)),
                  patch("dubsync.core.autotune.system_specs", return_value=self.specs),
                  patch("dubsync.core.autotune.compare_cpu_decode_threads",
                        return_value=bad_report)):
                result = tune_local_cpu_decoder(str(video))
            self.assertIsNone(result["tuning"])

    def test_cancel_prevents_probe(self):
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "sample.mkv"
            video.touch()
            with (patch("dubsync.core.autotune.is_network_path", return_value=False),
                  patch("dubsync.core.autotune.probe_media") as probe):
                with self.assertRaisesRegex(RuntimeError, "abgebrochen"):
                    tune_local_cpu_decoder(str(video), cancel_event=cancel)
            probe.assert_not_called()


class HostBoundSettingsTests(unittest.TestCase):
    def test_only_explicit_custom_tuning_on_same_cpu_is_used(self):
        specs = {"logical_cpus": 8, "cpu_model": "Test CPU", "physical_cores": 4,
                 "ram_gib": 16., "ram_bytes": 16 * 1024**3, "gpu_vendors": []}
        tuning = {"cpu_model": "Test CPU", "logical_cpus": 8, "threads": 2}
        with patch("dubsync.core.performance.system_specs", return_value=specs):
            chosen = normalize_performance_settings({
                "preset": "custom", "cpu_threads": 2, "cpu_decoder_tuning": tuning})
            self.assertEqual(cpu_decoder_threads_for_settings(chosen, specs), 2)
            self.assertEqual(chosen["cpu_decoder_tuning"], tuning)
            manual = normalize_performance_settings({
                "preset": "custom", "cpu_threads": 3, "cpu_decoder_tuning": tuning})
            self.assertIsNone(cpu_decoder_threads_for_settings(manual, specs))
            self.assertNotIn("cpu_decoder_tuning", manual)
            preset = normalize_performance_settings({
                "preset": "balanced", "cpu_threads": 2, "cpu_decoder_tuning": tuning})
            self.assertNotIn("cpu_decoder_tuning", preset)
            self.assertIsNone(cpu_decoder_threads_for_settings({}, specs))

    def test_host_or_budget_change_revokes_previous_decoder_tuning(self):
        original = {"cpu_threads": 2, "cpu_decoder_tuning": {
            "cpu_model": "Test CPU", "logical_cpus": 8, "threads": 2}}
        self.assertIsNone(cpu_decoder_threads_for_settings(
            original, {"cpu_model": "Other CPU", "logical_cpus": 8}))
        self.assertIsNone(cpu_decoder_threads_for_settings(
            original, {"cpu_model": "Test CPU", "logical_cpus": 4}))


if __name__ == "__main__":
    unittest.main()
