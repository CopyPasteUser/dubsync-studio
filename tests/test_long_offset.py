"""Long offset detection must be proven against misleading visual matches."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from dubsync.core.alignment import FrameSample, _long_offset_alignment, align_videos, AlignmentError
from dubsync.core.models import AlignmentResult, Anchor, MediaInfo, StreamInfo


def info(name: str, duration: float) -> MediaInfo:
    video = StreamInfo(0, "video", codec_name="h264", width=1280, height=720, fps=24.)
    audio = StreamInfo(1, "audio", codec_name="aac")
    return MediaInfo(name, duration, "matroska", 50000000, [video, audio], 1, 1, 0)


class LongOffsetTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(1600)

    def frames(self, count, start=0):
        return [FrameSample(float(i + start), int(self.rng.integers(0, 2**63, dtype=np.int64)))
                for i in range(count)]

    def check(self, source, target, allowed=True):
        result = _long_offset_alignment(source, target, "source.mkv", "master.mkv",
                                        info("source.mkv", len(source)),
                                        info("master.mkv", len(target)),
                                        1.0, 1.0, 1200, "none")
        if allowed:
            self.assertIsNotNone(result)
            self.assertTrue(result.safe_for_automatic_export, result.warnings)
        else:
            self.assertIsNone(result)
        return result

    def test_extra_150_second_intro_in_master(self):
        source = self.frames(300)
        target = self.frames(150) + [FrameSample(f.time + 150, f.phash) for f in source]
        result = self.check(source, target)
        self.assertAlmostEqual(result.offset, 150, delta=0.02)
        self.assertAlmostEqual(result.slope, 1, delta=0.001)

    def test_extra_165_second_intro_in_source(self):
        content = self.frames(300)
        source = self.frames(165) + [FrameSample(f.time + 165, f.phash) for f in content]
        result = self.check(source, content)
        self.assertAlmostEqual(result.offset, -165, delta=0.02)

    def test_150_second_extra_intro_with_small_clock_drift_and_reencode_noise(self):
        source = self.frames(300)
        target = self.frames(150)
        for sample in source:
            flips = 0
            for bit in self.rng.choice(63, size=5, replace=False):
                flips |= 1 << int(bit)
            target.append(FrameSample(150 + sample.time * 1.0007, sample.phash ^ flips))
        result = self.check(source, target)
        self.assertAlmostEqual(result.offset, 150, delta=0.6)
        self.assertAlmostEqual(result.slope, 1.0007, delta=0.002)

    def test_180_second_extra_outro_in_master(self):
        source = self.frames(300)
        target = list(source) + [FrameSample(300 + f.time, f.phash) for f in self.frames(180)]
        result = self.check(source, target)
        self.assertAlmostEqual(result.offset, 0, delta=0.05)

    def test_unrelated_episodes_are_rejected(self):
        self.check(self.frames(300), self.frames(450), allowed=False)

    def test_tiny_shared_intro_not_sufficient(self):
        content = self.frames(30)
        source = content + [FrameSample(f.time + 30, f.phash) for f in self.frames(270)]
        target = content + [FrameSample(f.time + 30, f.phash) for f in self.frames(420)]
        self.check(source, target, allowed=False)

    def test_shared_intro_and_credits_cannot_mask_different_middle(self):
        # Different episodes can share substantial opening AND closing footage.
        # Matching both ends is not proof that the dialogue in between aligns.
        source = self.frames(300)
        unrelated_middle = self.frames(180)
        target = (source[:60]
                  + [FrameSample(float(i + 60), frame.phash)
                     for i, frame in enumerate(unrelated_middle)]
                  + source[240:])
        self.check(source, target, allowed=False)

    def test_many_good_anchors_cannot_hide_unverified_middle(self):
        # 60% convincing anchors, perfect affine fit and matching timecodes can
        # still hide an entirely different 400-second middle section.
        times = list(range(0, 300)) + list(range(700, 1000))
        result = AlignmentResult(
            'source.mkv', 'master.mkv', 1.0, 0.0,
            [Anchor(float(t), float(t), 0) for t in times],
            .95, 1.0, .60, 0.0, 0.0, 1000.0, 1000.0, 1.0,
            visual_distinct_hashes=600,
        )
        self.assertFalse(result.safe_for_automatic_export)

    def test_many_good_anchors_cannot_hide_unverified_ending(self):
        times = list(range(700))
        result = AlignmentResult(
            'source.mkv', 'master.mkv', 1.0, 0.0,
            [Anchor(float(t), float(t), 0) for t in times],
            .95, 1.0, .70, 0.0, 0.0, 1000.0, 1000.0, 1.0,
            visual_distinct_hashes=700,
        )
        self.assertFalse(result.safe_for_automatic_export)

    def test_extra_intro_outside_shared_timeline_is_not_unverified_edge(self):
        anchors = [Anchor(float(i), float(i + 150), 0) for i in range(300)]
        result = AlignmentResult('source.mkv', 'master.mkv', 1.0, 150.0,
                                 anchors, .95, 1.0, 1.0, 0.0, 0.0,
                                 300.0, 450.0, 1.0, visual_distinct_hashes=300)
        self.assertTrue(result.safe_for_automatic_export)

    def test_partially_shared_episode_with_different_ending_is_not_approved(self):
        # Only 70% belongs to the same timeline, so a single offset must not
        # silently claim the whole episode is aligned.
        content = self.frames(300)
        target = self.frames(150) + [FrameSample(f.time + 150, f.phash) for f in content[:210]]
        target.extend(FrameSample(float(i + 360), f.phash) for i, f in enumerate(self.frames(90)))
        self.check(content, target, allowed=False)

    def test_match_cannot_be_from_repeated_still_frames(self):
        source = [FrameSample(float(i), i % 3) for i in range(300)]
        target = [FrameSample(float(i), int(self.rng.integers(0, 2**63, dtype=np.int64)))
                  for i in range(150)] + [FrameSample(float(i + 150), i % 3) for i in range(300)]
        self.check(source, target, allowed=False)

    def test_integrated_analysis_tries_shift_before_full_sequence(self):
        source = self.frames(300)
        target = self.frames(150) + [FrameSample(f.time + 150, f.phash) for f in source]
        with patch("dubsync.core.alignment.sample_video", side_effect=[source, target]), \
             patch("dubsync.core.alignment._sequence_alignment", side_effect=AssertionError("shift check missing")), \
             patch("dubsync.core.alignment.AlignmentDiagnostic.save", return_value=None):
            result = align_videos("source.mkv", "master.mkv", interval=1.0,
                                  source_info=info("source.mkv", 300),
                                  target_info=info("master.mkv", 450))
        self.assertTrue(result.safe_for_automatic_export)
        self.assertAlmostEqual(result.offset, 150, delta=0.02)


if __name__ == "__main__":
    unittest.main()
