"""The optimized frame hash must exactly match the original bit layout."""
from __future__ import annotations

import unittest

import cv2
import numpy as np

from dubsync.core.alignment import perceptual_hash


def scalar_phash(frame: np.ndarray) -> int:
    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame
    tiny = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    values = cv2.dct(np.float32(tiny))[:8, :8].flatten()[1:]
    median = float(np.median(values))
    result = 0
    for index, value in enumerate(values):
        if value >= median:
            result |= 1 << index
    return result


class PhashParityTests(unittest.TestCase):
    def test_exact_parity_for_raw_ffmpeg_and_opencv_frames(self):
        rng = np.random.default_rng(513)
        for shape in ((32, 32), (40, 55), (180, 320), (32, 32, 3), (72, 128, 3)):
            for case in range(28):
                frame = rng.integers(0, 256, size=shape, dtype=np.uint8)
                with self.subTest(shape=shape, case=case):
                    self.assertEqual(perceptual_hash(frame), scalar_phash(frame))

    def test_constant_and_tied_coefficients_keep_original_bit_pattern(self):
        for level in (0, 32, 128, 255):
            frame = np.full((32, 32), level, dtype=np.uint8)
            self.assertEqual(perceptual_hash(frame), scalar_phash(frame))
        frame = np.tile(np.arange(32, dtype=np.uint8), (32, 1))
        self.assertEqual(perceptual_hash(frame), scalar_phash(frame))


if __name__ == "__main__":
    unittest.main()
