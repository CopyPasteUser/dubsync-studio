"""Compare actual frame-matcher wall time; no media is read or modified."""
from __future__ import annotations

import random
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dubsync.core.alignment import (FrameSample, _feature_candidate_pairs,  # noqa: E402
                                   _sequence_alignment)
from dubsync.core.native_match import _library  # noqa: E402


def main() -> int:
    lib = _library()
    if lib is None or not hasattr(lib, "dubsync_feature_candidates"):
        print("Rust frame and feature matcher not installed. Run ./build-native.sh first.",
              file=sys.stderr)
        return 2
    rng = random.Random(20261109)
    hashes = [rng.getrandbits(64) for _ in range(1200)]
    target_hashes = hashes[:250] + [rng.getrandbits(64) for _ in range(25)] + hashes[250:]
    source = [FrameSample(i * .75, h) for i, h in enumerate(hashes)]
    target = [FrameSample(i * .75, h) for i, h in enumerate(target_hashes)]
    for label, method, symbol in (("monotone DP", _sequence_alignment, "native_pairs"),
                                  ("feature candidates", _feature_candidate_pairs,
                                   "native_feature_candidates")):
        timings = {}
        expected = None
        for backend in ("numpy", "rust"):
            samples = []
            for _ in range(3):
                started = time.perf_counter()
                if backend == "numpy":
                    with patch(f"dubsync.core.alignment.{symbol}", return_value=None):
                        result = method(source, target)
                else:
                    result = method(source, target)
                samples.append(time.perf_counter() - started)
                if expected is None:
                    expected = result
                elif expected != result:
                    print(f"ERROR: {label} differs from NumPy reference.", file=sys.stderr)
                    return 1
            timings[backend] = statistics.median(samples)
        print(f"{label}: 1200 x 1225 frames · matches: {len(expected)}")
        print(f"  NumPy: {timings['numpy']:.4f}s · Rust: {timings['rust']:.4f}s · "
              f"factor: {timings['numpy'] / timings['rust']:.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
