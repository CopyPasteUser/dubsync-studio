"""The vectorized sequence matcher must preserve the original DP decisions."""
from __future__ import annotations

import random
import unittest

import numpy as np

from dubsync.core.alignment import FrameSample, _sequence_alignment
from dubsync.core.models import Anchor


def scalar_reference(source: list[FrameSample], target: list[FrameSample]) -> list[Anchor]:
    """Small original recurrence retained as an independent correctness oracle."""
    n, m = len(source), len(target)
    gap = 0.205
    costs = np.empty((n + 1, m + 1), dtype=np.float32)
    trace = np.zeros((n + 1, m + 1), dtype=np.uint8)
    costs[:, 0] = np.arange(n + 1, dtype=np.float32) * gap
    costs[0, :] = np.arange(m + 1, dtype=np.float32) * gap
    trace[1:, 0], trace[0, 1:] = 2, 3
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            distance = (source[i - 1].phash ^ target[j - 1].phash).bit_count()
            diagonal = float(costs[i - 1, j - 1]) + distance / 64.0
            above = float(costs[i - 1, j]) + gap
            left = float(costs[i, j - 1]) + gap
            if diagonal <= above and diagonal <= left:
                costs[i, j], trace[i, j] = diagonal, 1
            elif above <= left:
                costs[i, j], trace[i, j] = above, 2
            else:
                costs[i, j], trace[i, j] = left, 3
    anchors = []
    i, j = n, m
    while i > 0 or j > 0:
        direction = int(trace[i, j])
        if i > 0 and j > 0 and direction == 1:
            distance = (source[i - 1].phash ^ target[j - 1].phash).bit_count()
            if distance <= 18:
                anchors.append(Anchor(source[i - 1].time, target[j - 1].time, distance))
            i -= 1
            j -= 1
        elif i > 0 and (j == 0 or direction == 2):
            i -= 1
        else:
            j -= 1
    return list(reversed(anchors))


class VectorizedSequenceTests(unittest.TestCase):
    def test_matches_original_dp_for_cuts_repeats_and_random_noise(self):
        randomizer = random.Random(1549)
        for case in range(50):
            length = randomizer.randrange(0, 70)
            source_hashes = [randomizer.getrandbits(63) for _ in range(length)]
            if case % 4 == 0:
                source_hashes = [value if i % 7 else 0 for i, value in enumerate(source_hashes)]
            target_hashes = source_hashes.copy()
            if case % 3 == 0:
                target_hashes[0:0] = [randomizer.getrandbits(63) for _ in range(case % 12)]
            elif case % 3 == 1:
                at = len(target_hashes) // 2
                target_hashes[at:at] = [randomizer.getrandbits(63) for _ in range(case % 9)]
            else:
                at = len(target_hashes) // 3
                del target_hashes[at:at + case % 11]
            source = [FrameSample(float(i), h) for i, h in enumerate(source_hashes)]
            target = [FrameSample(float(i), h) for i, h in enumerate(target_hashes)]
            with self.subTest(case=case):
                self.assertEqual(_sequence_alignment(source, target), scalar_reference(source, target))


if __name__ == "__main__":
    unittest.main()
