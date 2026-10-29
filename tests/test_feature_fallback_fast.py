"""Optimized feature matching must preserve the scalar alignment decisions."""
from __future__ import annotations

import random
import threading
import unittest

import numpy as np

from dubsync.core.alignment import (AlignmentError, FrameSample,
                                   _feature_anchor_fallback, _feature_candidate_pairs)
from dubsync.core.models import Anchor


def scalar_candidates(source, target):
    result = []
    for si, src in enumerate(source):
        matches = sorted(((src.phash ^ tgt.phash).bit_count(), ti)
                         for ti, tgt in enumerate(target)
                         if (src.phash ^ tgt.phash).bit_count() <= 16)
        for distance, ti in matches[:2]:
            result.append((si, ti, distance, src.time, target[ti].time))
    return result


def scalar_feature_fallback(source, target, interval):
    """Original scalar matcher, retained as a deterministic regression oracle."""
    candidates = scalar_candidates(source, target)
    if len(candidates) < 8:
        return []
    ranked = sorted(candidates, key=lambda item: (item[2], item[0], item[1]))
    pool_size = min(180, len(ranked))
    if len(ranked) > pool_size:
        pool = [ranked[round(i * (len(ranked) - 1) / (pool_size - 1))]
                for i in range(pool_size)]
    else:
        pool = ranked
    tolerance = max(0.85, interval * 1.5)
    hypotheses = {(1.0, item[4] - item[3]) for item in pool}
    min_sep = max(8.0, interval * 4.0)
    for i, a in enumerate(pool):
        for b in pool[i + 1:]:
            dx, dy = b[3] - a[3], b[4] - a[4]
            if dx < min_sep or dy <= 0:
                continue
            slope = dy / dx
            if 0.90 <= slope <= 1.10:
                hypotheses.add((round(slope, 6), round(a[4] - slope * a[3], 3)))
    best = None
    for slope, offset in sorted(hypotheses):
        chosen = {}
        for si, ti, dist, st, tt in candidates:
            residual = abs(tt - (slope * st + offset))
            if residual > tolerance:
                continue
            prior = chosen.get(si)
            if prior is None or (dist, residual) < (prior[0], prior[2]):
                chosen[si] = (dist, ti, residual)
        last_target = -1
        kept = []
        for si, (distance, target_index, residual) in sorted(chosen.items()):
            if target_index > last_target:
                kept.append((si, target_index, distance, residual))
                last_target = target_index
        if not kept:
            continue
        score = (len(kept), -float(np.median([row[2] for row in kept])),
                 -float(np.median([row[3] for row in kept])))
        if best is None or score > best[0]:
            best = (score, slope, offset)
    if best is None:
        return []
    _, slope, offset = best
    chosen = {}
    for si, ti, dist, st, tt in candidates:
        residual = abs(tt - (slope * st + offset))
        if residual > tolerance:
            continue
        prior = chosen.get(si)
        if prior is None or (dist, residual) < (prior[0], prior[2]):
            chosen[si] = (dist, ti, residual)
    result = []
    last_target = -1
    for si, (distance, ti, _residual) in sorted(chosen.items()):
        if ti > last_target:
            result.append(Anchor(source[si].time, target[ti].time, distance))
            last_target = ti
    return result


class FastFeatureFallbackTests(unittest.TestCase):
    def test_vector_candidates_match_scalar_with_repeats_and_near_collisions(self):
        randomizer = random.Random(7012026)
        for case in range(32):
            hashes = [randomizer.getrandbits(63) for _ in range(24)]
            if case % 2 == 0:
                hashes[3::4] = [hashes[0]] * len(hashes[3::4])
            source = [FrameSample(float(i), hash_) for i, hash_ in enumerate(hashes)]
            target_hashes = hashes[case % 6:] + hashes[:case % 6]
            if case % 3 == 0:
                target_hashes = [hashes[0] ^ (1 << i) for i in range(10)] + target_hashes
            target = [FrameSample(float(i), hash_) for i, hash_ in enumerate(target_hashes)]
            with self.subTest(case=case):
                self.assertEqual(_feature_candidate_pairs(source, target),
                                 scalar_candidates(source, target))

    def test_full_fallback_matches_scalar_on_cuts_drift_and_repeated_scenes(self):
        randomizer = random.Random(291029)
        for case in range(12):
            hashes = [randomizer.getrandbits(63) for _ in range(52)]
            if case % 3 == 0:
                for i in range(3, 52, 7):
                    hashes[i] = hashes[1]
            source = [FrameSample(float(i), hash_) for i, hash_ in enumerate(hashes)]
            intro = [FrameSample(float(i), randomizer.getrandbits(63))
                     for i in range(2 + case % 5)]
            retained = [hash_ for i, hash_ in enumerate(hashes)
                        if not (case % 4 == 0 and i in (17, 18, 19))]
            target = intro + [FrameSample(float(len(intro)) + i * (1.0 + case % 3 / 100), hash_)
                              for i, hash_ in enumerate(retained)]
            with self.subTest(case=case):
                self.assertEqual(_feature_anchor_fallback(source, target, 1.0),
                                 scalar_feature_fallback(source, target, 1.0))

    def test_cancelled_candidate_scan_raises(self):
        event = threading.Event()
        event.set()
        samples = [FrameSample(float(i), i) for i in range(8)]
        with self.assertRaises(AlignmentError):
            _feature_candidate_pairs(samples, samples, event)


if __name__ == "__main__":
    unittest.main()
