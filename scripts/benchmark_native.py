"""Compare actual frame-matcher wall time; no media is read or modified."""
from __future__ import annotations

import random
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dubsync.core.alignment import FrameSample, _sequence_alignment  # noqa: E402
from dubsync.core.native_match import _library  # noqa: E402


def main() -> int:
    if _library() is None:
        print("Rust library not installed. Run ./build-native.sh first.", file=sys.stderr)
        return 2
    rng = random.Random(20261109)
    hashes = [rng.getrandbits(64) for _ in range(1200)]
    target_hashes = hashes[:250] + [rng.getrandbits(64) for _ in range(25)] + hashes[250:]
    source = [FrameSample(i * .75, h) for i, h in enumerate(hashes)]
    target = [FrameSample(i * .75, h) for i, h in enumerate(target_hashes)]
    timings = {}
    expected = None
    for backend in ("numpy", "rust"):
        samples = []
        for _ in range(3):
            started = time.perf_counter()
            if backend == "numpy":
                with patch("dubsync.core.alignment.native_pairs", return_value=None):
                    result = _sequence_alignment(source, target)
            else:
                result = _sequence_alignment(source, target)
            samples.append(time.perf_counter() - started)
            if expected is None:
                expected = result
            elif expected != result:
                print("ERROR: Native matching differs from NumPy reference.", file=sys.stderr)
                return 1
        timings[backend] = statistics.median(samples)
    print(f"1200 x 1225 frames · same anchors: {len(expected)}")
    print(f"NumPy: {timings['numpy']:.4f}s · Rust: {timings['rust']:.4f}s · "
          f"factor: {timings['numpy'] / timings['rust']:.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
