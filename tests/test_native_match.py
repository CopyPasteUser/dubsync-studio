"""Rust/NumPy parity including duplicate frames, cuts, and full-width hashes."""
from __future__ import annotations

import random
import threading
import unittest
from unittest.mock import patch


from dubsync.core.alignment import (AlignmentError, FrameSample, _offset_probe_candidates,
                                   _feature_candidate_pairs, _sequence_alignment)
from dubsync.core.native_match import (_library, native_best_affine,
                                       native_feature_candidates, native_offset_candidates,
                                       native_pairs, native_verify_offset)


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
            self.assertIsNone(native_best_affine([(0, 0, 0, 0., 0.)], 1,
                                                 [(1., 0.)], 0.85))
            self.assertIsNone(native_verify_offset([FrameSample(0., 0)],
                                                    [FrameSample(0., 0)], 0., 0.85))


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


@unittest.skipUnless(_library() is not None and
                     hasattr(_library(), "dubsync_best_affine"),
                     "Optional Rust affine scorer not built")
class RustAffineScoringTests(unittest.TestCase):
    def test_scores_match_numpy_fallback_for_cuts_and_repeated_motifs(self):
        from dubsync.core.alignment import _feature_anchor_fallback
        rng = random.Random(7301)
        for case in range(18):
            source_hashes = [rng.getrandbits(62) for _ in range(72)]
            if case % 3 == 0:
                source_hashes[::7] = [source_hashes[0]] * len(source_hashes[::7])
            source = [FrameSample(float(i), h) for i, h in enumerate(source_hashes)]
            target = [FrameSample(float(i), rng.getrandbits(62)) for i in range(case % 9)]
            for index, hash_ in enumerate(source_hashes):
                if case % 5 == 0 and index in (22, 23, 24):
                    continue
                target.append(FrameSample(len(target) * (1.0 + (case % 3) * .005), hash_))
            with self.subTest(case=case):
                native = _feature_anchor_fallback(source, target, 1.0)
                with patch("dubsync.core.alignment.native_best_affine", return_value=None):
                    reference = _feature_anchor_fallback(source, target, 1.0)
                self.assertEqual(native, reference)

    def test_bad_input_and_empty_hypotheses_are_bounded(self):
        self.assertIsNone(native_best_affine([(0, 0, 0, float("nan"), 0.)],
                                             1, [(1., 0.)], .85))
        self.assertIsNone(native_best_affine([(0, 0, 0, 0., 0.)],
                                             5001, [(1., 0.)], .85))
        self.assertEqual(native_best_affine([], 1, [(1., 0.)], .85), [])

    def test_cancellation_between_hypothesis_chunks(self):
        cancelled = threading.Event()
        cancelled.set()
        self.assertIsNone(native_best_affine([(0, 0, 0, 0., 0.)],
                                             1, [(1., 0.)] * 500, .85, cancelled))


@unittest.skipUnless(_library() is not None and
                     hasattr(_library(), "dubsync_verify_offset"),
                     "Optional Rust long-offset verifier not built")
class RustLongOffsetVerificationTests(unittest.TestCase):
    def test_matches_scalar_window_rules_with_intros_drift_and_duplicates(self):
        from bisect import bisect_left
        rng = random.Random(7107)
        for case in range(70):
            hashes = [rng.getrandbits(64) for _ in range(90)]
            if case % 4 == 0:
                hashes[::5] = [hashes[1]] * len(hashes[::5])
            source = [FrameSample(i * .75, h) for i, h in enumerate(hashes)]
            target = [FrameSample(i * .75, rng.getrandbits(64)) for i in range(case % 11)]
            prefix_len = len(target)
            target.extend(FrameSample((prefix_len + i) * .75, hash_)
                          for i, hash_ in enumerate(hashes))
            shift, tolerance = case % 11 * .75, 0.85
            native = native_verify_offset(source, target, shift, tolerance)
            expected = []
            last_target = -1
            target_times = [item.time for item in target]
            for i, item in enumerate(source):
                predicted = item.time + shift
                j = bisect_left(target_times, predicted)
                matches = []
                for ti in range(max(last_target + 1, j - 2), min(len(target), j + 3)):
                    phase = abs(target[ti].time - predicted)
                    distance = (item.phash ^ target[ti].phash).bit_count()
                    if phase <= tolerance and distance <= 18:
                        matches.append((distance, phase, ti))
                if matches:
                    distance, _, ti = min(matches)
                    expected.append((i, ti, distance))
                    last_target = ti
            with self.subTest(case=case):
                self.assertEqual(native, expected)

    def test_invalid_inputs_do_not_crash_bridge(self):
        sample = [FrameSample(0.0, 0)]
        self.assertIsNone(native_verify_offset([FrameSample(0., -1)], sample, 0., .85))
        self.assertIsNone(native_verify_offset(sample, sample, float("nan"), .85))


if __name__ == "__main__":
    unittest.main()
