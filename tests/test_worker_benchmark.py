"""Deterministic 1-vs-2 decoder benchmark without physical performance claims."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dubsync.cli import main
from dubsync.core.models import Anchor
from dubsync.core.worker_benchmark import compare_sampling_workers


def _alignment(*, target_offset=0., safe=True):
    return SimpleNamespace(anchors=[Anchor(float(i), float(i) + target_offset, 3)
                                    for i in range(40)], safe_for_automatic_export=safe)


class WorkerBenchmarkTests(unittest.TestCase):
    def test_recommends_two_only_when_measurably_faster_and_parity_holds(self):
        requests = []

        def compare(*args, **kwargs):
            requested = kwargs["sampling_workers"]
            requests.append(requested)
            self.assertFalse(kwargs["frame_cache_enabled"])
            kwargs["diagnostic_sink"]({"stages": {"sample_runtime": {
                "effective_workers": requested, "wall_seconds": 2. if requested == 1 else 1.,
            }}})
            return _alignment()

        with patch("dubsync.core.worker_benchmark.align_videos", side_effect=compare):
            report = compare_sampling_workers("source", "master")
        self.assertEqual(requests, [1, 2])
        self.assertTrue(report["visual_anchor_parity"])
        self.assertTrue(report["safety_decision_parity"])
        self.assertEqual(report["sampling_speedup"], 2.)
        self.assertEqual(report["suggested_workers"], 2)
        self.assertFalse(report["automatic_setting_change"])

    def test_nas_policy_fallback_keeps_one_decoder(self):
        def compare(*args, **kwargs):
            kwargs["diagnostic_sink"]({"stages": {"sample_runtime": {
                "effective_workers": 1, "wall_seconds": .8,
            }}})
            return _alignment()

        with patch("dubsync.core.worker_benchmark.align_videos", side_effect=compare):
            report = compare_sampling_workers("source", "master")
        self.assertEqual(report["suggested_workers"], 1)
        self.assertIn("NAS", report["note"])

    def test_different_anchors_are_not_used_for_tuning(self):
        def compare(*args, **kwargs):
            workers = kwargs["sampling_workers"]
            kwargs["diagnostic_sink"]({"stages": {"sample_runtime": {
                "effective_workers": workers, "wall_seconds": 3. if workers == 1 else 1.,
            }}})
            return _alignment(target_offset=float(workers - 1))

        with patch("dubsync.core.worker_benchmark.align_videos", side_effect=compare):
            report = compare_sampling_workers("source", "master")
        self.assertFalse(report["visual_anchor_parity"])
        self.assertEqual(report["suggested_workers"], 1)

    def test_cli_benchmark_json_has_no_source_paths_or_settings_mutations(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, master = Path(temporary) / "source.mkv", Path(temporary) / "master.mkv"
            source.write_bytes(b"sample")
            master.write_bytes(b"sample")
            captured = io.StringIO()
            report = {"runs": [], "visual_anchor_parity": True,
                      "safety_decision_parity": True, "sampling_speedup": None,
                      "suggested_workers": 1, "automatic_setting_change": False,
                      "note": "no timing"}
            with (patch("dubsync.core.worker_benchmark.compare_sampling_workers",
                        return_value=report) as benchmark,
                  patch("sys.stdout", captured)):
                code = main(["--benchmark-workers", str(source), str(master), "--json"])
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(captured.getvalue())["automatic_setting_change"])
            self.assertNotIn(temporary, captured.getvalue())
            self.assertEqual(benchmark.call_args.kwargs["hardware_accel"], "auto")


if __name__ == "__main__":
    unittest.main()
