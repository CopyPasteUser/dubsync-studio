"""Piecewise cuts require independent visual proof and accurate exported silence."""
from __future__ import annotations

import random
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from dubsync.core.alignment import FrameSample, _sequence_alignment, align_videos
from dubsync.core.batch import _manual_alignment
from dubsync.core.export import export_multilingual
from dubsync.core.models import AlignmentResult, Anchor, MediaInfo, StreamInfo, TrackChoice
from dubsync.core.piecewise import detect_piecewise
from dubsync.core.probe import probe_media


def candidate(anchors, source, target_duration, interval=1.0):
    return detect_piecewise(
        anchors, {f.time: f.phash for f in source},
        "source.mkv", "master.mkv", len(source), target_duration,
        interval, min(len(source), int(target_duration / interval)),
        interval, 1200, "none",
    )


class CutDetectionTests(unittest.TestCase):
    def setUp(self):
        rng = random.Random(123401)
        self.frames = [FrameSample(float(i), rng.getrandbits(63)) for i in range(300)]
        self.unrelated = [FrameSample(float(i), rng.getrandbits(63)) for i in range(300)]

    def test_extra_master_scene_becomes_a_silent_gap(self):
        target = self.frames[:120] + [FrameSample(float(120 + i), f.phash)
                                            for i, f in enumerate(self.unrelated[:20])]
        target += [FrameSample(float(i + 20), f.phash) for i, f in enumerate(self.frames[120:], 120)]
        matched = _sequence_alignment(self.frames, target)
        result = candidate(matched, self.frames, 320)
        self.assertIsNotNone(result)
        self.assertTrue(result.safe_for_automatic_export)
        self.assertEqual(len(result.segments), 2)
        self.assertAlmostEqual(result.segments[1].target_start - result.segments[0].target_end,
                               20, delta=1)
        self.assertAlmostEqual(result.target_time(200), 220, delta=0.5)
        self.assertIsNone(result.source_time(130))
        self.assertAlmostEqual(result.source_time(220), 200, delta=0.5)
        self.assertEqual(AlignmentResult.from_dict(result.to_dict()), result)
        manual = _manual_alignment(result, {"manual_alignment": True,
                                            "offset": 0.5, "scale": 1.0})
        self.assertEqual(manual.segments, [])
        self.assertFalse(manual.safe_for_automatic_export)

    def test_integrated_analyzer_selects_verified_piecewise_mapping(self):
        target = self.frames[:120] + [FrameSample(float(120 + i), f.phash)
                                            for i, f in enumerate(self.unrelated[:20])]
        target += [FrameSample(float(i + 20), f.phash) for i, f in enumerate(self.frames[120:], 120)]
        def info(path, duration):
            video = StreamInfo(0, "video", codec_name="h264", width=640, height=360, fps=25.)
            return MediaInfo(path, duration, "matroska", 10000, [video], 1, 0, 0)
        observed = []
        with patch("dubsync.core.alignment.sample_video", side_effect=[self.frames, target]), \
             patch("dubsync.core.alignment._feature_anchor_fallback",
                   side_effect=AssertionError("Piecewise must run before expensive fallback")), \
             patch("dubsync.core.alignment.AlignmentDiagnostic.save", return_value=None):
            result = align_videos("source.mkv", "master.mkv", interval=1.0,
                                  source_info=info("source.mkv", 300),
                                  target_info=info("master.mkv", 320),
                                  diagnostic_sink=observed.append)
        self.assertTrue(result.safe_for_automatic_export)
        self.assertEqual(len(result.segments), 2)
        self.assertEqual(observed[-1]["decision"]["method"], "piecewise_verified")

    def test_missing_master_scene_skips_source_audio(self):
        target = self.frames[:120] + [FrameSample(float(i - 20), f.phash)
                                     for i, f in enumerate(self.frames[140:], 140)]
        result = candidate(_sequence_alignment(self.frames, target), self.frames, 280)
        self.assertIsNotNone(result)
        self.assertTrue(result.safe_for_automatic_export)
        self.assertGreater(result.segments[1].source_start - result.segments[0].source_end, 19)
        self.assertAlmostEqual(result.target_time(200), 180, delta=0.5)

    def test_two_cuts_produce_three_distinct_audio_sections(self):
        anchors = ([Anchor(float(i), float(i), 0) for i in range(100)]
                   + [Anchor(float(i), float(i + 20), 0) for i in range(100, 200)]
                   + [Anchor(float(i), float(i + 10), 0) for i in range(210, 300)])
        result = candidate(anchors, self.frames, 310)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.segments), 3)
        self.assertTrue(result.safe_for_automatic_export)
        self.assertAlmostEqual(result.target_time(250), 260, delta=0.5)

    def test_more_than_five_cuts_cannot_auto_export(self):
        rng = random.Random(927)
        source = [FrameSample(float(i), rng.getrandbits(63)) for i in range(600)]
        anchors = [Anchor(float(i), float(i + 10 * (i // 100)), 0)
                   for i in range(600)]
        self.assertIsNone(candidate(anchors, source, 650))

    def test_shared_intro_and_credits_do_not_certify_different_episode(self):
        anchors = ([Anchor(float(i), float(i), 0) for i in range(60)]
                   + [Anchor(float(i), float(i + 20), 0) for i in range(440, 500)])
        extra = self.frames + [FrameSample(float(i), i + 8) for i in range(300, 500)]
        self.assertIsNone(candidate(anchors, extra, 520))

    def test_single_affine_drift_is_not_called_a_cut(self):
        anchors = [Anchor(float(i), float(i) * 1.02, 1) for i in range(300)]
        self.assertIsNone(candidate(anchors, self.frames, 306))

    def test_low_visual_diversity_never_enables_piecewise(self):
        repetitive = [FrameSample(f.time, int(f.time) % 4) for f in self.frames]
        anchors = ([Anchor(float(i), float(i), 0) for i in range(150)]
                   + [Anchor(float(i), float(i + 20), 0) for i in range(150, 300)])
        self.assertIsNone(candidate(anchors, repetitive, 320))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg/FFprobe erforderlich")
class PiecewiseExportTests(unittest.TestCase):
    def test_real_audio_is_silent_in_unmatched_master_scene(self):
        with tempfile.TemporaryDirectory(prefix="dubsync-piecewise-") as directory:
            root = Path(directory)
            master, source, output = (root / name for name in
                                      ("master.mkv", "source.wav", "output.mkv"))
            subprocess.run([
                "ffmpeg", "-hide_banner", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12:duration=14",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=14",
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "mpeg4", "-c:a", "aac",
                "-shortest", str(master),
            ], check=True, capture_output=True, timeout=60)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-v", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=12",
                str(source),
            ], check=True, capture_output=True, timeout=60)
            visual = [FrameSample(i * 0.5, i + 1000) for i in range(24)]
            anchors = [Anchor(f.time, f.time + (2 if f.time >= 6 else 0), 0)
                       for f in visual]
            result = detect_piecewise(anchors, {f.time: f.phash for f in visual},
                                      str(source), str(master), 12.0, 14.0,
                                      0.5, 24, 0.5, 1200, "none")
            self.assertIsNotNone(result)
            self.assertTrue(result.safe_for_automatic_export)
            export_multilingual(str(master), [TrackChoice(
                path=str(source), audio_ordinal=0, alignment=result,
            )], str(output))
            checked = probe_media(str(output))
            self.assertEqual(checked.video[0].codec_name, "mpeg4")
            self.assertEqual(len(checked.audio), 2)
            audio = subprocess.run([
                "ffmpeg", "-hide_banner", "-v", "error", "-i", str(output),
                "-map", "0:a:1", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1",
            ], check=True, capture_output=True, timeout=60).stdout
            samples = np.frombuffer(audio, dtype="<f4")
            def rms(at):
                values = samples[int(at * 48000):int((at + .25) * 48000)]
                return float(np.sqrt(np.mean(values ** 2)))
            self.assertGreater(rms(3.0), 0.005)
            self.assertLess(rms(7.0), 0.0003)
            self.assertGreater(rms(10.0), 0.005,
                               [round(rms(float(i)), 5) for i in range(14)])

    def test_removed_source_scene_does_not_leak_into_master_audio(self):
        with tempfile.TemporaryDirectory(prefix="dubsync-piecewise-cut-") as directory:
            root = Path(directory)
            master, source, output = (root / name for name in
                                      ("master.mkv", "source.wav", "output.mkv"))
            subprocess.run([
                "ffmpeg", "-hide_banner", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12:duration=10",
                "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=10",
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "mpeg4", "-c:a", "aac",
                "-shortest", str(master),
            ], check=True, capture_output=True, timeout=60)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-v", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
                "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=4",
                "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]",
                "-map", "[a]", str(source),
            ], check=True, capture_output=True, timeout=60)
            source_times = [i * .25 for i in range(24)] + [8 + i * .25 for i in range(16)]
            anchors = [Anchor(t, t if t < 6 else t - 2, 0) for t in source_times]
            result = detect_piecewise(anchors, {t: i + 1000 for i, t in enumerate(source_times)},
                                      str(source), str(master), 12, 10, .25, 40, .25,
                                      1200, "none")
            self.assertIsNotNone(result)
            self.assertTrue(result.safe_for_automatic_export)
            export_multilingual(str(master), [TrackChoice(
                path=str(source), audio_ordinal=0, alignment=result,
            )], str(output))
            encoded = subprocess.run([
                "ffmpeg", "-hide_banner", "-v", "error", "-i", str(output),
                "-map", "0:a:1", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1",
            ], check=True, capture_output=True, timeout=60).stdout
            samples = np.frombuffer(encoded, dtype="<f4")
            def dominant_frequency(at):
                segment = samples[int(at * 48000):int((at + 0.5) * 48000)]
                spectrum = np.abs(np.fft.rfft(segment))
                return float(np.fft.rfftfreq(len(segment), d=1/48000)[np.argmax(spectrum)])
            self.assertAlmostEqual(dominant_frequency(3), 440, delta=6)
            self.assertAlmostEqual(dominant_frequency(8), 660, delta=6)


if __name__ == "__main__":
    unittest.main()
