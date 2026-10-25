from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np

from dubsync.core.alignment import align_videos, perceptual_hash, FrameSample, _feature_anchor_fallback, _same_timeline_result
from dubsync.core.batch import (batch_export_groups, output_path_for_group, choose_smallest_video, estimate_output_size_bytes)
from dubsync.core.cache import AlignmentCache
from dubsync.core.export import export_multilingual
from dubsync.core.models import Anchor, AlignmentResult, EpisodeGroup, TrackChoice, MediaInfo, StreamInfo
from dubsync.core.languages import default_track_title, language_display_name, normalize_language_code, resolve_track_label
from dubsync.core.probe import probe_media
from dubsync.core.scanner import episode_key, infer_language, scan_directories


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


class ScannerTests(unittest.TestCase):
    def test_compact_southpark_filename_matches_spaced_episode_name(self):
        compact, compact_title = episode_key("/media/Staffel_1/SouthparkS01E01_SSS.mp4")
        spaced, spaced_title = episode_key("/media/Season 01/South Park S01E001.mkv")
        self.assertEqual(compact, spaced)
        self.assertIn("S01E01", compact_title)
        self.assertIn("S01E01", spaced_title)

    def test_two_separate_language_folders_group_by_episode_across_roots(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            first = base / "Staffel_1"
            second = base / "Season 01"
            first.mkdir()
            second.mkdir()
            for episode in range(1, 14):
                (first / f"SouthparkS01E{episode:02d}_SSS.mp4").touch()
                (second / f"South Park S01E{episode:03d}.mkv").touch()
            groups = scan_directories([str(first), str(second)])
            self.assertEqual(len(groups), 13)
            self.assertTrue(all(group.count == 2 for group in groups))
            self.assertTrue(all("S01E" in group.display_name for group in groups))

    def test_language_versions_group_as_same_episode(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            de = base / "Deutsch"
            en = base / "English"
            de.mkdir()
            en.mkdir()
            (de / "Arcane.S01E02.German.1080p.WEB-DL.mkv").touch()
            (en / "Arcane.S01E02.English.720p.WEB-DL.mkv").touch()
            groups = scan_directories([str(base)])
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].count, 2)
            self.assertIn("S01E02", groups[0].display_name)

    def test_episode_files_in_season_subfolders_group_together(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "Example Show"
            (base / "Deutsch" / "Staffel 1").mkdir(parents=True)
            (base / "English" / "Season 1").mkdir(parents=True)
            (base / "Deutsch" / "Staffel 1" / "S01E01.mkv").touch()
            (base / "English" / "Season 1" / "S01E01.mkv").touch()
            groups = scan_directories([str(base)])
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].count, 2)

    def test_scan_ignores_own_exports_and_hidden_temporary_files(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "Show.S01E01.German.mkv").touch()
            (base / "Show.S01E01.English.mkv").touch()
            (base / "Show.S01E01.multilingual.mkv").touch()
            (base / ".Show.S01E01.dubsync-deadbeef.mkv").touch()
            groups = scan_directories([str(base)])
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].count, 2)

    def test_language_inference_supports_more_than_german_and_english(self):
        self.assertEqual(infer_language("/media/Português/Show.S01E01.mkv"), "por")
        self.assertEqual(infer_language("/media/Polski/Show.S01E02.mkv"), "pol")
        self.assertEqual(infer_language("/media/日本語/Show.S01E03.mkv"), "jpn")

    def test_episode_key_ignores_language_and_quality(self):
        first, _ = episode_key("/media/de/Bluey.S02E03.German.1080p.mkv")
        second, _ = episode_key("/media/en/Bluey.S02E03.English.WEB-DL.720p.mkv")
        self.assertEqual(first, second)

    def test_group_carries_series_season_and_episode_for_output_layout(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "Staffel_1").mkdir()
            (base / "Season 01").mkdir()
            (base / "Staffel_1" / "SouthparkS01E01_SSS.mp4").touch()
            (base / "Season 01" / "South Park S01E001.mkv").touch()
            groups = scan_directories([str(base / "Staffel_1"), str(base / "Season 01")])
            self.assertEqual(len(groups), 1)
            group = groups[0]
            self.assertEqual(group.series_name, "South Park")
            self.assertEqual(group.season_number, 1)
            self.assertEqual(group.episode_number, 1)
            target = output_path_for_group(str(base / "Output"), group)
            self.assertEqual(target.parent.name, "Staffel 01")
            self.assertEqual(target.parent.parent.name, "South Park")
            self.assertEqual(target.name, "South Park - S01E01.multilingual.mkv")


class LanguageLabelTests(unittest.TestCase):
    def test_language_codes_and_human_titles(self):
        self.assertEqual(normalize_language_code("Deutsch"), "deu")
        self.assertEqual(normalize_language_code("English"), "eng")
        self.assertEqual(language_display_name("eng"), "English")
        self.assertEqual(default_track_title("deu", "Stereo", "/tmp/Show.mkv"), "Deutsch")

    def test_root_language_rule_overrides_ambiguous_folder_name(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            folder = base / "Staffel_1"
            folder.mkdir()
            media = folder / "SouthparkS01E01_SSS.mp4"
            media.touch()
            lang, title = resolve_track_label(
                str(media), roots=[str(folder)],
                root_languages={str(folder): {"language": "deu", "title": "Deutsch"}},
                inferred_language="und",
            )
            self.assertEqual(lang, "deu")
            self.assertEqual(title, "Deutsch")


class CompactMasterTests(unittest.TestCase):
    def test_smallest_video_is_selected_even_if_another_has_higher_resolution(self):
        from dubsync.core.models import MediaInfo, StreamInfo
        small_path = "/tmp/compact-master-small.mkv"
        large_path = "/tmp/compact-master-large.mkv"
        small = MediaInfo(
            small_path, 600.0, "matroska", 120_000_000,
            [StreamInfo(index=0, codec_type="video", width=640, height=360),
             StreamInfo(index=1, codec_type="audio")], 1, 1, 0,
        )
        large = MediaInfo(
            large_path, 600.0, "matroska", 900_000_000,
            [StreamInfo(index=0, codec_type="video", width=1920, height=1080),
             StreamInfo(index=1, codec_type="audio")], 1, 1, 0,
        )
        self.assertEqual(choose_smallest_video([large, small]).path, small_path)

    def test_output_size_estimate_includes_only_extra_audio_budget(self):
        base = 500 * 1024 * 1024
        estimated = estimate_output_size_bytes(base, 3600, 2, 128)
        self.assertGreater(estimated, base)
        self.assertAlmostEqual(estimated - base - 1024 * 1024, 2 * 3600 * 128_000 / 8 * 1.08, delta=2)
        self.assertGreater(estimate_output_size_bytes(base, 3600, 2, 192), estimated)


class SameTimelineAlignmentTests(unittest.TestCase):
    def test_same_video_timeline_with_different_audio_uses_zero_offset(self):
        import random
        rng = random.Random(42)
        hashes = [rng.getrandbits(63) for _ in range(40)]
        source = [FrameSample(float(i), value) for i, value in enumerate(hashes)]
        target = [FrameSample(float(i), value) for i, value in enumerate(hashes)]
        source_info = MediaInfo("/tmp/source.mkv", 40.0, "matroska", 1000, [], 1, 1, 0)
        target_info = MediaInfo("/tmp/target.mkv", 40.0, "matroska", 1000, [], 1, 1, 0)
        result = _same_timeline_result(
            source_info.path, target_info.path, source_info, target_info,
            source, target, 1.0, 1200, "auto",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.offset, 0.0)
        self.assertEqual(result.slope, 1.0)
        self.assertTrue(result.safe_for_automatic_export)


class HashTests(unittest.TestCase):
    def test_hash_is_stable_and_shape_sensitive(self):
        a = np.zeros((120, 160, 3), dtype=np.uint8)
        a[10:60, 10:80] = (255, 255, 255)
        b = a.copy()
        c = np.zeros((120, 160, 3), dtype=np.uint8)
        c[65:115, 80:150] = (255, 255, 255)
        self.assertEqual(perceptual_hash(a), perceptual_hash(b))
        self.assertNotEqual(perceptual_hash(a), perceptual_hash(c))


@unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg/FFprobe nicht installiert")


class BatchOverrideTests(unittest.TestCase):
    def test_batch_respects_manual_master_track_selection_labels_and_timing(self):
        from dubsync.core.batch import batch_export_groups

        with tempfile.TemporaryDirectory(prefix="dubsync-batch-overrides-") as td:
            base = Path(td)
            smallest = str(base / "Smallest.mkv")
            chosen_master = str(base / "ChosenMaster.mkv")
            selected_dub = str(base / "English source.mkv")

            def media(path, size, audio_count=1):
                streams = [StreamInfo(index=0, codec_type="video", codec_name="mpeg4",
                                      width=320, height=180, fps=24.0)]
                for ordinal in range(audio_count):
                    streams.append(StreamInfo(index=ordinal + 1, codec_type="audio",
                                              codec_name="aac", language="und",
                                              title=f"Audio {ordinal + 1}", channels=2,
                                              sample_rate=48000))
                return MediaInfo(path, 60.0, "matroska", size, streams, 1, audio_count, 0)

            infos = {
                smallest: media(smallest, 1000, 1),
                chosen_master: media(chosen_master, 1500, 1),
                selected_dub: media(selected_dub, 2000, 2),
            }
            group = EpisodeGroup(
                key="testshow|s01e01", display_name="Test Show · S01E01",
                files=[smallest, chosen_master, selected_dub],
                series_name="Test Show", season_number=1, episode_number=1,
            )
            anchors = [Anchor(float(i * 5), float(i * 5) + 0.25, 1) for i in range(10)]
            aligned = AlignmentResult(
                source_path=selected_dub, target_path=chosen_master,
                slope=1.0, offset=0.0, anchors=anchors,
                confidence=0.95, inlier_ratio=1.0, coverage=1.0,
                median_hamming=1.0, rms_error=0.0,
                source_duration=60.0, target_duration=60.0,
                sample_interval=0.5, requested_interval=0.5,
                max_samples=1200, hardware_accel="none",
            )

            class NoCache:
                def get(self, *args, **kwargs):
                    return None
                def put(self, result):
                    return None

            output_dir = base / "output"
            root_overrides = {
                smallest: {"include": False},
                selected_dub: {
                    "audio_ordinal": 1,
                    "language": "eng",
                    "title": "English Selected",
                    "include": True,
                    "manual_alignment": True,
                    "offset": 0.25,
                    "scale": 1.0,
                },
            }
            expected_output = output_path_for_group(str(output_dir), group)
            with patch("dubsync.core.batch.align_videos", return_value=aligned) as align_mock, \
                 patch("dubsync.core.batch.export_multilingual", return_value=str(expected_output)) as export_mock:
                report = batch_export_groups(
                    [group], str(output_dir), cache=NoCache(),
                    media_cache=infos,
                    track_overrides=root_overrides,
                    master_overrides={group.key: chosen_master},
                    alignment_options={"interval": 0.5, "max_samples": 1200,
                                       "hardware_accel": "none", "audio_bitrate_kbps": 128},
                )

            self.assertEqual(report["exported"], [str(expected_output)])
            self.assertEqual(report["failed"], [])
            align_mock.assert_called_once()
            call_args = export_mock.call_args
            self.assertEqual(call_args.args[0], chosen_master)
            tracks = call_args.args[1]
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0].path, selected_dub)
            self.assertEqual(tracks[0].audio_ordinal, 1)
            self.assertEqual(tracks[0].language, "eng")
            self.assertEqual(tracks[0].title, "English Selected")
            self.assertAlmostEqual(tracks[0].alignment.offset, 0.25)
            self.assertAlmostEqual(tracks[0].alignment.slope, 1.0)

    def test_manual_alignment_helper_ignores_non_manual_spin_values(self):
        from dubsync.core.batch import _manual_alignment
        anchors = [Anchor(float(i), float(i) + 0.5, 1) for i in range(10)]
        original = AlignmentResult(
            source_path="source.mkv", target_path="master.mkv", slope=1.0, offset=0.5,
            anchors=anchors, confidence=0.95, inlier_ratio=1.0, coverage=1.0,
            median_hamming=1.0, rms_error=0.0, source_duration=10.0,
            target_duration=12.0, sample_interval=0.5, requested_interval=0.5,
            max_samples=1200, hardware_accel="none",
        )
        result = _manual_alignment(original, {"manual_alignment": False, "offset": 0.0, "scale": 1.0})
        self.assertEqual(result.offset, 0.5)
        self.assertEqual(result.slope, 1.0)


class MediaIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dubsync-tests-")
        self.base = Path(self.tmp.name)
        self.master = self.base / "Master.mkv"
        self.alternate = self.base / "Alternative.mkv"

    def tearDown(self):
        self.tmp.cleanup()

    def _run_ffmpeg(self, args):
        result = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def _make_reference(self):
        self._run_ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=12:duration=14",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=14",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "mpeg4", "-q:v", "4",
            "-c:a", "aac", "-shortest", str(self.master),
        ])

    def _make_alternate(self):
        self._run_ffmpeg([
            "-ss", "2", "-i", str(self.master), "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "mpeg4", "-q:v", "4", "-c:a", "aac", str(self.alternate),
        ])

    def test_probe_finds_video_and_audio_streams(self):
        self._make_reference()
        info = probe_media(str(self.master))
        self.assertEqual(info.video_streams, 1)
        self.assertGreaterEqual(info.audio_streams, 1)
        self.assertGreater(info.duration, 10)

    def test_fallback_alignment_recovers_shared_frames_after_different_intro(self):
        # A cartoon version may have a different opening, which can confuse the
        # global sequence matcher. The fallback should find the common timeline.
        rng = np.random.default_rng(2026)
        hashes = [int(rng.integers(0, 2**63, dtype=np.int64)) for _ in range(48)]
        source = [FrameSample(float(i), value) for i, value in enumerate(hashes)]
        target = [FrameSample(float(i), int(rng.integers(0, 2**63, dtype=np.int64))) for i in range(4)]
        target.extend(FrameSample(float(i + 4), value) for i, value in enumerate(hashes))
        anchors = _feature_anchor_fallback(source, target, 1.0)
        self.assertGreaterEqual(len(anchors), 40)
        self.assertAlmostEqual(float(np.median([a.target_time - a.source_time for a in anchors])), 4.0, delta=0.1)

    def test_fallback_recovers_shared_frames_with_mild_linear_drift(self):
        rng = np.random.default_rng(20261025)
        hashes = [int(rng.integers(0, 2**63, dtype=np.int64)) for _ in range(48)]
        source = [FrameSample(float(i), value) for i, value in enumerate(hashes)]
        target = [FrameSample(float(i), int(rng.integers(0, 2**63, dtype=np.int64))) for i in range(4)]
        target.extend(FrameSample(4.0 + 1.02 * i, value) for i, value in enumerate(hashes))
        anchors = _feature_anchor_fallback(source, target, 1.0)
        self.assertGreaterEqual(len(anchors), 40)
        fitted = np.polyfit([a.source_time for a in anchors], [a.target_time for a in anchors], 1)
        self.assertAlmostEqual(float(fitted[0]), 1.02, delta=0.002)
        self.assertAlmostEqual(float(fitted[1]), 4.0, delta=0.1)

    def test_video_alignment_detects_trimmed_two_second_intro(self):
        self._make_reference()
        self._make_alternate()
        result = align_videos(str(self.alternate), str(self.master), interval=1.0)
        self.assertGreaterEqual(len(result.anchors), 8)
        self.assertAlmostEqual(result.offset, 2.0, delta=0.5)
        self.assertAlmostEqual(result.slope, 1.0, delta=0.02)
        self.assertTrue(result.safe_for_automatic_export, result.warnings)

    def test_alignment_reuses_preprobed_metadata(self):
        self._make_reference()
        self._make_alternate()
        source_info = probe_media(str(self.alternate))
        target_info = probe_media(str(self.master))
        with patch("dubsync.core.alignment.probe_media", side_effect=AssertionError("duplicate FFprobe call")):
            result = align_videos(
                str(self.alternate), str(self.master), interval=1.0,
                source_info=source_info, target_info=target_info,
            )
        self.assertAlmostEqual(result.offset, 2.0, delta=0.5)

    def test_batch_exports_confident_episode_and_preserves_inputs(self):
        self._make_reference()
        self._make_alternate()
        original_master = self.master.stat().st_size
        original_alternate = self.alternate.stat().st_size
        output_dir = self.base / "BatchOutput"
        german_dir = self.base / "Deutsch"
        english_dir = self.base / "English"
        german_dir.mkdir()
        english_dir.mkdir()
        german_copy = german_dir / "Test Show S01E01 German.mkv"
        english_copy = english_dir / "Test Show S01E01 English.mkv"
        shutil.copy2(self.master, german_copy)
        shutil.copy2(self.alternate, english_copy)
        original_german = german_copy.stat().st_size
        original_english = english_copy.stat().st_size
        group = EpisodeGroup(
            key="testshow|s01e001", display_name="Test Show · S01E01",
            files=[str(german_copy), str(english_copy)],
            series_name="Test Show", season_number=1, episode_number=1,
        )
        report = batch_export_groups(
            [group], str(output_dir),
            cache=AlignmentCache(self.base / "alignment-cache.json"),
            roots=[str(german_dir), str(english_dir)],
            root_languages={
                str(german_dir): {"language": "deu", "title": "Deutsch"},
                str(english_dir): {"language": "eng", "title": "English"},
            },
            organize_by_series=True,
        )
        self.assertEqual(len(report["exported"]), 1, report)
        self.assertFalse(report["failed"], report["failed"])
        exported = Path(report["exported"][0])
        self.assertEqual(exported.parent.name, "Staffel 01")
        self.assertEqual(exported.parent.parent.name, "Test Show")
        self.assertEqual(exported.name, "Test Show - S01E01.multilingual.mkv")
        self.assertTrue(exported.is_file())
        exported_info = probe_media(str(exported))
        # The smaller trimmed English source is automatically selected as picture master.
        self.assertEqual(exported_info.audio[0].language, "eng")
        self.assertEqual(exported_info.audio[0].title, "English")
        self.assertEqual(exported_info.audio[1].language, "deu")
        self.assertEqual(exported_info.audio[1].title, "Deutsch")
        self.assertLessEqual(exported_info.duration, probe_media(str(german_copy)).duration)
        self.assertEqual(german_copy.stat().st_size, original_german)
        self.assertEqual(english_copy.stat().st_size, original_english)
        self.assertEqual(self.master.stat().st_size, original_master)
        self.assertEqual(self.alternate.stat().st_size, original_alternate)

    def test_batch_checks_free_space_before_expensive_alignment(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from dubsync.core.batch import batch_export_groups

        master = str(self.base / "small-master.mkv")
        alternate = str(self.base / "larger-alternate.mkv")
        video = StreamInfo(index=0, codec_type="video", width=640, height=360, fps=24.0)
        audio = StreamInfo(index=1, codec_type="audio", language="eng", channels=2)
        media = {
            master: MediaInfo(master, 600.0, "matroska", 80_000_000, [video, audio], 1, 1, 0),
            alternate: MediaInfo(alternate, 600.0, "matroska", 140_000_000, [video, audio], 1, 1, 0),
        }
        group = EpisodeGroup(
            key="testshow|s01e01", display_name="Test Show · S01E01",
            files=[master, alternate], series_name="Test Show", season_number=1, episode_number=1,
        )
        usage = SimpleNamespace(total=1000, used=995, free=5)
        with patch("dubsync.core.batch.probe_media", side_effect=lambda path: media[path]), \
             patch("dubsync.core.batch.shutil.disk_usage", return_value=usage), \
             patch("dubsync.core.batch.align_videos", side_effect=AssertionError("alignment should not run")):
            report = batch_export_groups([group], str(self.base / "low-space-out"))
        self.assertEqual(report["exported"], [])
        self.assertTrue(any("zu wenig freier Speicher" in item for item in report["failed"]))

    def test_batch_rejects_duplicate_output_destinations_before_any_probe(self):
        output_dir = self.base / "CollisionOutput"
        files_a = [str(self.base / "a-master.mkv"), str(self.base / "a-alt.mkv")]
        files_b = [str(self.base / "b-master.mkv"), str(self.base / "b-alt.mkv")]
        first = EpisodeGroup(
            key="series-a|s01e01", display_name="Show A · S01E01",
            files=files_a, series_name="Same Show", season_number=1, episode_number=1,
        )
        second = EpisodeGroup(
            key="series-b|s01e01", display_name="Show B · S01E01",
            files=files_b, series_name="Same Show", season_number=1, episode_number=1,
        )
        with patch("dubsync.core.batch.probe_media", side_effect=AssertionError("probe must not run")):
            report = batch_export_groups([first, second], str(output_dir))
        self.assertEqual(report["exported"], [])
        self.assertEqual(len(report["failed"]), 1)
        self.assertIn("Zielkonflikt", report["failed"][0])

    def test_batch_rejects_same_source_assigned_to_multiple_episode_groups(self):
        shared = str(self.base / "same-source.mkv")
        first = EpisodeGroup(
            key="testshow|s01e05", display_name="Test Show · S01E05",
            files=[shared, str(self.base / "e05-alt.mkv")],
            series_name="Test Show", season_number=1, episode_number=5,
        )
        second = EpisodeGroup(
            key="testshow|s01e06", display_name="Test Show · S01E06",
            files=[shared, str(self.base / "e06-alt.mkv")],
            series_name="Test Show", season_number=1, episode_number=6,
        )
        with patch("dubsync.core.batch.probe_media", side_effect=AssertionError("probe must not run")):
            report = batch_export_groups([first, second], str(self.base / "duplicate-source-out"))
        self.assertEqual(report["exported"], [])
        self.assertTrue(any("Quelldatei mehreren Episoden zugeordnet" in item for item in report["failed"]))

    def test_batch_rejects_identical_content_under_different_episode_filenames(self):
        first_file = self.base / "Show.S01E05.German.mkv"
        second_file = self.base / "Show.S01E06.German.mkv"
        first_alt = self.base / "Show.S01E05.English.mkv"
        second_alt = self.base / "Show.S01E06.English.mkv"
        payload = (b"same episode payload" * 8000)
        for path in (first_file, second_file):
            path.write_bytes(payload)
        first_alt.write_bytes(b"episode five english" * 8000)
        second_alt.write_bytes(b"episode six english" * 8000)
        first = EpisodeGroup(
            key="testshow|s01e05", display_name="Test Show · S01E05",
            files=[str(first_file), str(first_alt)], series_name="Test Show", season_number=1, episode_number=5,
        )
        second = EpisodeGroup(
            key="testshow|s01e06", display_name="Test Show · S01E06",
            files=[str(second_file), str(second_alt)], series_name="Test Show", season_number=1, episode_number=6,
        )
        with patch("dubsync.core.batch.probe_media", side_effect=AssertionError("probe must not run")):
            report = batch_export_groups([first, second], str(self.base / "duplicate-content-out"))
        self.assertEqual(report["exported"], [])
        self.assertTrue(any("Identischer Videoinhalt" in item for item in report["failed"]))

    def test_batch_skips_existing_output_before_media_probe_or_alignment(self):
        output_dir = self.base / "SkipOutput"
        group = EpisodeGroup(
            key="testshow|s01e01", display_name="Test Show · S01E01",
            files=[str(self.base / "not-yet-probed-a.mkv"), str(self.base / "not-yet-probed-b.mkv")],
            series_name="Test Show", season_number=1, episode_number=1,
        )
        existing = output_path_for_group(str(output_dir), group)
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"already done")
        with patch("dubsync.core.batch.probe_media", side_effect=AssertionError("probe must not run")), \
             patch("dubsync.core.batch.align_videos", side_effect=AssertionError("alignment must not run")):
            report = batch_export_groups([group], str(output_dir), skip_existing=True)
        self.assertEqual(report["exported"], [])
        self.assertEqual(report["failed"], [])
        self.assertEqual(len(report["skipped"]), 1)
        self.assertIn("fertige Ausgabe übersprungen", report["skipped"][0])

    def test_video_alignment_detects_uniform_three_percent_drift(self):
        self._run_ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=25",
            "-f", "lavfi", "-i", "sine=frequency=510:sample_rate=48000:duration=25",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "mpeg4", "-q:v", "4",
            "-c:a", "aac", "-shortest", str(self.master),
        ])
        self._run_ffmpeg([
            "-i", str(self.master), "-vf", "setpts=PTS/1.03", "-fps_mode", "vfr",
            "-map", "0:v:0", "-map", "0:a:0", "-c:v", "mpeg4", "-q:v", "4",
            "-c:a", "aac", str(self.alternate),
        ])
        result = align_videos(str(self.alternate), str(self.master), interval=0.5)
        self.assertGreaterEqual(len(result.anchors), 20)
        self.assertAlmostEqual(result.slope, 1.03, delta=0.02)
        self.assertAlmostEqual(result.offset, 0.0, delta=0.5)
        self.assertTrue(result.safe_for_automatic_export, result.warnings)

    def test_mux_creates_additional_audio_and_verifies_output(self):
        self._make_reference()
        self._make_alternate()
        result = align_videos(str(self.alternate), str(self.master), interval=1.0)
        output = self.base / "output.mkv"
        choice = TrackChoice(
            path=str(self.alternate), audio_ordinal=0, language="eng",
            title="Test English", include=True, alignment=result,
        )
        created = export_multilingual(
            str(self.master), [choice], str(output), allow_low_confidence=True,
            master_audio_metadata={0: ("deu", "Deutsch")},
            audio_bitrate_kbps=96,
        )
        self.assertEqual(created, str(output))
        info = probe_media(created)
        self.assertEqual(info.video_streams, 1)
        self.assertGreaterEqual(info.audio_streams, 2)
        self.assertGreaterEqual(info.duration, 12)
        self.assertEqual(info.audio[0].language, "deu")
        self.assertEqual(info.audio[0].title, "Deutsch")
        self.assertEqual(info.audio[1].language, "eng")
        self.assertEqual(info.audio[1].title, "Test English")
        bit_rate = info.audio[1].raw.get("bit_rate")
        if bit_rate:
            self.assertLessEqual(int(bit_rate), 150_000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
