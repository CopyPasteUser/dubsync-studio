"""Rust/NumPy parity including duplicate frames, cuts, and full-width hashes."""
from __future__ import annotations

import random
import threading
import unittest
from unittest.mock import patch


from dubsync.core.alignment import (AlignmentError, FrameSample, _offset_probe_candidates,
                                   _feature_candidate_pairs, _sequence_alignment)
from dubsync.core.native_match import (_library, native_feature_candidates,
                                       native_offset_candidates, native_pairs)


def scalar_candidates(source, target):
    matches = []
    for si, src in enumerate(source):
        distances = sorted(((src.phash ^ tgt.phash).bit_count(), ti)
                           for ti, tgt in enumerate(target))
        for distance, ti in [row for row in distances if row[0] <= 16][:2]:
            matches.append((si, ti, distance, src.time, target[ti].time))
    return matches


class NativeBridgeCompatibilityTests(unittest.TestCase):
    def test_old_rust_binary_without_optional_candidate_symbol_falls_back(self):
        with patch("dubsync.core.native_match._library", return_value=object()):
            self.assertIsNone(native_feature_candidates([0], [0]))
            self.assertIsNone(native_offset_candidates([0], [0], [True]))


@unittest.skipUnless(_library() is not None, "Optional Rust library not built")
class RustMatchingTests(unittest.TestCase):
    def test_native_pairs_agree_with_numpy_for_mixed_edits(self):
        rng = random.Random(418)
        for case in range(120):
            n = rng.randrange(0, 125)
            hashes = [rng.getrandbits(64) for _ in range(n)]
            if case % 4 == 0:
                hashes[::7] = [0] * len(hashes[::7])
            target_hashes = hashes.copy()
            offset = len(target_hashes) // 2
            if case % 3 == 0:
                target_hashes[offset:offset] = [rng.getrandbits(64) for _ in range(case % 15)]
            elif case % 3 == 1:
                del target_hashes[offset:offset + case % 12]
            elif target_hashes:
                target_hashes[offset] ^= 1 << (case % 64)
            sources = [FrameSample(i * 0.75, h) for i, h in enumerate(hashes)]
            targets = [FrameSample(i * 0.75, h) for i, h in enumerate(target_hashes)]
            with self.subTest(case=case):
                native = _sequence_alignment(sources, targets)
                with patch("dubsync.core.alignment.native_pairs", return_value=None):
                    numpy_result = _sequence_alignment(sources, targets)
                self.assertEqual(native, numpy_result)

    def test_native_rejects_invalid_hash_and_preserves_cancellation(self):
        self.assertIsNone(native_pairs([-1], [0]))
        self.assertEqual(native_pairs([], [0]), [])
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(AlignmentError):
            _sequence_alignment([FrameSample(0., 0)], [FrameSample(0., 0)], cancel_event=cancelled)


@unittest.skipUnless(_library() is not None and
                     hasattr(_library(), "dubsync_feature_candidates"),
                     "Optional Rust feature candidate extension not built")
class RustFeatureCandidateTests(unittest.TestCase):
    def test_rust_matches_reference_with_repeats_sparse_matches_and_full_width_hashes(self):
        rng = random.Random(40191)
        for case in range(75):
            src_hashes = [rng.getrandbits(64) for _ in range(rng.randrange(0, 55))]
            if src_hashes and case % 3 == 0:
                src_hashes[::5] = [0] * len(src_hashes[::5])
            tgt_hashes = src_hashes.copy()
            if case % 2 == 0:
                tgt_hashes = [rng.getrandbits(64) for _ in range(10)] + tgt_hashes
            if case % 3 == 0:
                tgt_hashes.extend([src_hashes[0]] * 3 if src_hashes else [])
            source = [FrameSample(i * .75, h) for i, h in enumerate(src_hashes)]
            target = [FrameSample(i * .9, h) for i, h in enumerate(tgt_hashes)]
            with self.subTest(case=case):
                self.assertEqual(_feature_candidate_pairs(source, target),
                                 scalar_candidates(source, target))
                self.assertEqual(native_feature_candidates(src_hashes, tgt_hashes),
                                 [(si, ti, dist) for si, ti, dist, *_ in
                                  scalar_candidates(source, target)])

    def test_invalid_inputs_rejected_without_disabling_old_engine(self):
        self.assertIsNone(native_feature_candidates([-1], [0]))
        self.assertIsNone(native_feature_candidates([2**64], [0]))
        self.assertIsNone(native_feature_candidates([0] * 5001, [0]))
        self.assertEqual(native_feature_candidates([], [0]), [])
        self.assertIsNotNone(native_pairs([0], [0]))


@unittest.skipUnless(_library() is not None and
                     hasattr(_library(), "dubsync_offset_candidates"),
                     "Optional Rust offset candidate extension not built")
class RustOffsetCandidateTests(unittest.TestCase):
    def test_rust_matches_scalar_with_duplicate_hashes_and_filtered_targets(self):
        rng = random.Random(4320)
        for case in range(90):
            hashes = [rng.getrandbits(64) for _ in range(rng.randrange(0, 55))]
            if hashes and case % 4 == 0:
                hashes[::5] = [hashes[0]] * len(hashes[::5])
            target = hashes + [rng.getrandbits(64) for _ in range(14)]
            if case % 3 == 0:
                target.extend(hashes[:5])
            permitted = [i % 7 != (case % 7) for i in range(len(target))]
            with self.subTest(case=case):
                native = native_offset_candidates(hashes, target, permitted)
                with patch("dubsync.core.alignment.native_offset_candidates", return_value=None):
                    scalar = _offset_probe_candidates(hashes, target, permitted)
                self.assertEqual(native, scalar)
                self.assertEqual(_offset_probe_candidates(hashes, target, permitted), scalar)

    def test_invalid_offset_candidate_arguments(self):
        self.assertIsNone(native_offset_candidates([-1], [0], [True]))
        self.assertIsNone(native_offset_candidates([0], [0], []))
        self.assertIsNone(native_offset_candidates([0], [0], [2]))
        self.assertIsNone(native_offset_candidates([0] * 5001, [0], [True]))
        self.assertEqual(native_offset_candidates([], [0], [True]), [])


if __name__ == "__main__":
    unittest.main()
