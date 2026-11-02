"""GPU recommendations share the real auto-decoder safety gate."""
from __future__ import annotations

import unittest
from unittest import mock

from dubsync.core.ffmpeg_sampling import DecoderCandidate, choose_verified_decoder
from dubsync.gui.decoder_test import summarize_decoder_selftest


class DecoderSelfTestTests(unittest.TestCase):
    def setUp(self):
        self.intel = DecoderCandidate("intel-vaapi@renderD128", "vaapi", "/dev/dri/renderD128")
        self.amd = DecoderCandidate("amd-vaapi@renderD129", "vaapi", "/dev/dri/renderD129")
        self.cpu = {"frames": 10, "samples_per_second": 10.}

    def test_intel_igpu_selected_when_fast_and_frame_equivalent(self):
        bench = {"cpu": self.cpu,
                 self.intel.key: {"frames": 10, "samples_per_second": 17.,
                                  "frame_match_ratio": 1., "median_hamming_to_cpu": 0}}
        self.assertEqual(choose_verified_decoder(bench, [self.intel]), self.intel)
        self.assertIn("Recommended: intel-vaapi", summarize_decoder_selftest(bench, [self.intel]))

    def test_mismatching_or_slow_gpu_cannot_win(self):
        bench = {"cpu": self.cpu,
                 self.intel.key: {"frames": 10, "samples_per_second": 25.,
                                  "frame_match_ratio": .6, "median_hamming_to_cpu": 3},
                 self.amd.key: {"frames": 10, "samples_per_second": 11.,
                                "frame_match_ratio": 1., "median_hamming_to_cpu": 0}}
        self.assertIsNone(choose_verified_decoder(bench, [self.intel, self.amd]))
        self.assertIn("CPU / OpenCV fallback", summarize_decoder_selftest(bench, [self.intel, self.amd]))

    def test_missing_cpu_reference_never_selects_gpu(self):
        bench = {self.intel.key: {"frames": 10, "samples_per_second": 50.,
                                 "frame_match_ratio": 1., "median_hamming_to_cpu": 0}}
        self.assertIsNone(choose_verified_decoder(bench, [self.intel]))

    def test_decoder_dialog_rejects_nas_before_touching_its_metadata(self):
        from PySide6.QtWidgets import QApplication
        from dubsync.gui.decoder_test import DecoderSelfTestDialog
        app = QApplication.instance() or QApplication([])
        dialog = DecoderSelfTestDialog()
        try:
            with mock.patch("dubsync.core.network.is_network_path", return_value=True), \
                 mock.patch("dubsync.gui.decoder_test.Path.is_file",
                            side_effect=AssertionError("must not stat disconnected NFS")):
                dialog.start_test("/disconnected/episode.mkv")
            self.assertIn("NAS", dialog.report.toPlainText())
            self.assertIsNone(dialog.worker)
        finally:
            dialog.close()
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
