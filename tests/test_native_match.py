"""Rust/NumPy parity including duplicate frames, cuts, and full-width hashes."""
from __future__ import annotations

import random
import threading
import unittest
from unittest.mock import patch

from dubsync.core.alignment import AlignmentError, FrameSample, _sequence_alignment
from dubsync.core.native_match import _library, native_pairs


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


if __name__ == "__main__":
    unittest.main()
