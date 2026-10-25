from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dubsync.core.models import AlignmentResult, Anchor, EpisodeGroup, MediaInfo, StreamInfo
from dubsync.core.cache import AlignmentCache
from dubsync.core.performance import (
    default_performance_settings,
    normalize_performance_settings,
    settings_for_preset,
    supported_hardware_modes,
    apply_cpu_thread_limit, system_specs, recommended_profile,
)


class PerformanceSettingTests(unittest.TestCase):
    def test_default_profile_is_system_adaptive_and_cache_disabled_for_fresh_exports(self):
        settings = default_performance_settings()
        specs = system_specs()
        self.assertEqual(settings["preset"], "system")
        self.assertEqual(settings["max_samples"] >= 200, True)
        self.assertEqual(settings["auto_system_profile"], True)
        self.assertFalse(settings["cache_enabled"])
        self.assertGreaterEqual(settings["cpu_threads"], 1)
        self.assertLessEqual(settings["cpu_threads"], specs["logical_cpus"])
        self.assertEqual(settings["audio_bitrate_kbps"], 128)
        self.assertIn(recommended_profile(specs), {"fast", "balanced", "quality"})

    def test_system_recommendation_adapts_to_low_memory_small_cpu(self):
        from unittest.mock import patch
        with patch("dubsync.core.performance.system_specs", return_value={
            "logical_cpus": 4, "physical_cores": 2, "ram_bytes": 4 * 1024**3,
            "ram_gib": 4.0, "gpu_vendors": [],
        }), patch("dubsync.core.performance._hardware_decoder_available", return_value=False):
            settings = default_performance_settings()
        self.assertEqual(settings["preset"], "system")
        self.assertEqual(settings["max_samples"], 400)
        self.assertEqual(settings["sample_interval"], 1.25)
        self.assertEqual(settings["cpu_threads"], 1)
        self.assertEqual(settings["hardware_accel"], "none")

    def test_invalid_performance_options_are_clamped(self):
        settings = normalize_performance_settings({
            "preset": "not-a-preset",
            "cpu_threads": 99999,
            "sample_interval": -2,
            "max_samples": 999999,
            "hardware_accel": "imaginary-gpu",
            "audio_bitrate_kbps": 9999,
            "cache_enabled": False,
        })
        self.assertEqual(settings["preset"], "balanced")
        self.assertLessEqual(settings["cpu_threads"], max(1, os.cpu_count() or 2))
        self.assertEqual(settings["sample_interval"], 0.5)
        self.assertEqual(settings["max_samples"], 4000)
        self.assertEqual(settings["hardware_accel"], "auto")
        self.assertEqual(settings["audio_bitrate_kbps"], 320)
        self.assertFalse(settings["cache_enabled"])

    def test_presets_trade_sample_budget_for_runtime(self):
        fast = settings_for_preset("fast")
        balanced = settings_for_preset("balanced")
        quality = settings_for_preset("quality")
        self.assertLess(fast["max_samples"], balanced["max_samples"])
        self.assertLess(balanced["max_samples"], quality["max_samples"])
        custom = normalize_performance_settings({"preset": "custom"})
        self.assertEqual(custom["preset"], "custom")

    def test_hardware_modes_always_keep_safe_fallbacks(self):
        modes = supported_hardware_modes()
        self.assertEqual(modes[:2], ["auto", "none"])
        self.assertEqual(len(modes), len(set(modes)))

    def test_cpu_thread_limit_is_applied_to_opencv(self):
        import cv2
        with patch.object(cv2, "setNumThreads") as set_threads:
            effective = apply_cpu_thread_limit(2)
        self.assertEqual(effective, 2)
        set_threads.assert_called_once_with(2)


class AlignmentCacheProfileTests(unittest.TestCase):
    def test_cache_keys_are_separated_by_analysis_profile(self):
        with tempfile.TemporaryDirectory(prefix="dubsync-cache-profile-") as td:
            root = Path(td)
            source = root / "source.mkv"
            target = root / "target.mkv"
            source.write_bytes(b"source video test")
            target.write_bytes(b"target video test")
            result = AlignmentResult(
                source_path=str(source), target_path=str(target),
                slope=1.0, offset=1.25,
                anchors=[Anchor(0.0, 1.25, 2), Anchor(5.0, 6.25, 3)],
                confidence=0.9, inlier_ratio=1.0, coverage=1.0,
                median_hamming=2.5, rms_error=0.0,
                source_duration=10.0, target_duration=12.0,
                sample_interval=0.5, requested_interval=0.5,
                max_samples=1400, hardware_accel="auto",
            )
            cache = AlignmentCache(root / "cache.sqlite3")
            cache.put(result)
            self.assertIsNotNone(cache.get(str(source), str(target), 0.5, 1400, "auto"))
            self.assertIsNone(cache.get(str(source), str(target), 0.5, 700, "auto"))
            self.assertIsNone(cache.get(str(source), str(target), 0.5, 1400, "none"))
            self.assertEqual(cache.size, 1)


    def test_cache_is_sqlite_private_and_persists_across_instances(self):
        with tempfile.TemporaryDirectory(prefix="dubsync-sqlite-cache-") as td:
            root = Path(td) / "private-cache"
            root.mkdir()
            source = root / "source.mkv"
            target = root / "target.mkv"
            source.write_bytes(b"source test media")
            target.write_bytes(b"target test media")
            path = root / "alignments.sqlite3"
            result = AlignmentResult(
                source_path=str(source), target_path=str(target),
                slope=1.0, offset=0.5,
                anchors=[Anchor(float(i), float(i) + 0.5, 1) for i in range(10)],
                confidence=0.9, inlier_ratio=1.0, coverage=1.0,
                median_hamming=1.0, rms_error=0.0, source_duration=10.0,
                target_duration=12.0, sample_interval=0.5,
                requested_interval=0.5, max_samples=1200, hardware_accel="none",
            )
            first = AlignmentCache(path)
            first.put(result)
            self.assertTrue(path.read_bytes().startswith(b"SQLite format 3\x00"))
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            second = AlignmentCache(path)
            loaded = second.get(str(source), str(target), 0.5, 1200, "none")
            self.assertIsNotNone(loaded)
            self.assertAlmostEqual(loaded.offset, 0.5)
            self.assertEqual(second.size, 1)

    def test_legacy_json_cache_is_migrated_to_sqlite(self):
        with tempfile.TemporaryDirectory(prefix="dubsync-legacy-cache-") as td:
            root = Path(td)
            source = root / "source.mkv"
            target = root / "target.mkv"
            source.write_bytes(b"source test media")
            target.write_bytes(b"target test media")
            path = root / "alignments.json"
            probe = AlignmentCache(root / "key-probe.sqlite3")
            result = AlignmentResult(
                source_path=str(source), target_path=str(target),
                slope=1.0, offset=0.5,
                anchors=[Anchor(float(i), float(i) + 0.5, 1) for i in range(10)],
                confidence=0.9, inlier_ratio=1.0, coverage=1.0,
                median_hamming=1.0, rms_error=0.0, source_duration=10.0,
                target_duration=12.0, sample_interval=0.5,
                requested_interval=0.5, max_samples=1200, hardware_accel="none",
            )
            key = probe.key(str(source), str(target), 0.5, 1200, "none")
            path.write_text(json.dumps({key: result.to_dict()}), encoding="utf-8")
            migrated = AlignmentCache(path)
            self.assertTrue(path.read_bytes().startswith(b"SQLite format 3\x00"))
            loaded = migrated.get(str(source), str(target), 0.5, 1200, "none")
            self.assertIsNotNone(loaded)
            self.assertAlmostEqual(loaded.offset, 0.5)
            probe.clear()

    def test_default_cache_imports_legacy_json_cache(self):
        with tempfile.TemporaryDirectory(prefix="dubsync-default-legacy-cache-") as td:
            root = Path(td)
            cache_dir = root / "dubsync-studio"
            cache_dir.mkdir(mode=0o700)
            source = root / "source.mkv"
            target = root / "target.mkv"
            source.write_bytes(b"source test media")
            target.write_bytes(b"target test media")

            probe = AlignmentCache(root / "key-probe.sqlite3")
            result = AlignmentResult(
                source_path=str(source), target_path=str(target),
                slope=1.0, offset=0.75,
                anchors=[Anchor(float(i), float(i) + 0.75, 1) for i in range(10)],
                confidence=0.9, inlier_ratio=1.0, coverage=1.0,
                median_hamming=1.0, rms_error=0.0, source_duration=10.0,
                target_duration=12.0, sample_interval=0.5,
                requested_interval=0.5, max_samples=1200, hardware_accel="none",
            )
            key = probe.key(str(source), str(target), 0.5, 1200, "none")
            old_json = cache_dir / "alignments.json"
            old_json.write_text(json.dumps({key: result.to_dict()}), encoding="utf-8")

            with patch.dict(os.environ, {"XDG_CACHE_HOME": str(root)}):
                migrated = AlignmentCache()

            self.assertEqual(migrated.path, cache_dir / "alignments.sqlite3")
            self.assertTrue(migrated.path.read_bytes().startswith(b"SQLite format 3\x00"))
            self.assertFalse(old_json.exists())
            loaded = migrated.get(str(source), str(target), 0.5, 1200, "none")
            self.assertIsNotNone(loaded)
            self.assertAlmostEqual(loaded.offset, 0.75)
            probe.clear()

    def test_cache_clear_removes_persisted_file(self):
        with tempfile.TemporaryDirectory(prefix="dubsync-cache-clear-") as td:
            path = Path(td) / "cache.json"
            path.write_text(json.dumps({"old": {"value": 1}}), encoding="utf-8")
            cache = AlignmentCache(path)
            cache.clear()
            self.assertFalse(path.exists())
            self.assertEqual(cache.size, 0)

    def test_batch_cache_can_be_disabled_without_touching_cache(self):
        from unittest.mock import patch
        from dubsync.core.batch import batch_export_groups

        with tempfile.TemporaryDirectory(prefix="dubsync-no-cache-") as td:
            root = Path(td)
            source = str(root / "source.mkv")
            master = str(root / "master.mkv")
            audio_stream = StreamInfo(index=1, codec_type="audio", language="eng", title="English")
            video_stream = StreamInfo(index=0, codec_type="video", width=320, height=180, fps=24.0)
            media = {
                source: MediaInfo(source, 100.0, "matroska", 1000, [video_stream, audio_stream], 1, 1, 0),
                master: MediaInfo(master, 100.0, "matroska", 1000, [video_stream, audio_stream], 1, 1, 0),
            }
            anchors = [Anchor(float(i * 10), float(i * 10), 1) for i in range(10)]
            aligned = AlignmentResult(
                source_path=source, target_path=master, slope=1.0, offset=0.0,
                anchors=anchors, confidence=0.95, inlier_ratio=1.0, coverage=1.0,
                median_hamming=1.0, rms_error=0.0, source_duration=100.0,
                target_duration=100.0, sample_interval=0.5,
                requested_interval=0.5, max_samples=1400, hardware_accel="auto",
            )
            cache = AlignmentCache(root / "cache.json")
            group = EpisodeGroup(
                key="test|s01e01", display_name="Test · S01E01",
                files=[master, source], series_name="Test", season_number=1, episode_number=1,
            )
            with patch("dubsync.core.batch.probe_media", side_effect=lambda path: media[path]), \
                 patch("dubsync.core.batch.AlignmentCache.get", side_effect=AssertionError("cache read must be disabled")), \
                 patch.object(cache, "put", side_effect=AssertionError("cache write must be disabled")), \
                 patch("dubsync.core.batch.align_videos", return_value=aligned), \
                 patch("dubsync.core.batch.export_multilingual", return_value=str(root / "output.mkv")):
                report = batch_export_groups(
                    [group], str(root / "output"), cache=cache,
                    alignment_options={
                        "interval": 0.5, "max_samples": 1400,
                        "hardware_accel": "auto", "cache_enabled": True,  # batch must still ignore stale cache
                    },
                )
            self.assertEqual(report["exported"], [str(root / "output.mkv")])
            self.assertEqual(report["failed"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
