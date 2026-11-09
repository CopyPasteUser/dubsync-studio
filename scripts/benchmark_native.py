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
                                   _offset_probe_candidates, _sequence_alignment)
from dubsync.core.native_match import _library  # noqa: E402


def main() -> int:
    lib = _library()
    if (lib is None or not hasattr(lib, "dubsync_feature_candidates")
            or not hasattr(lib, "dubsync_offset_candidates")):
        print("All three Rust matchers are needed. Run ./build-native.sh first.",
              file=sys.stderr)
        return 2
    rng = random.Random(20261109)
    hashes = [rng.getrandbits(64) for _ in range(1200)]
    target_hashes = hashes[:250] + [rng.getrandbits(64) for _ in range(25)] + hashes[250:]
    source = [FrameSample(i * .75, h) for i, h in enumerate(hashes)]
    target = [FrameSample(i * .75, h) for i, h in enumerate(target_hashes)]
    probe_hashes = hashes[:110]
    mask = [i % 17 != 0 for i in range(len(target_hashes))]
    cases = (("monotone DP", _sequence_alignment, "native_pairs", (source, target)),
             ("feature candidates", _feature_candidate_pairs,
              "native_feature_candidates", (source, target)),
             ("long-offset probes", _offset_probe_candidates,
              "native_offset_candidates", (probe_hashes, target_hashes, mask)))
    for label, method, symbol, arguments in cases:
        timings = {}
        expected = None
        for backend in ("numpy", "rust"):
            samples = []
            for _ in range(3):
                started = time.perf_counter()
                if backend == "numpy":
                    with patch(f"dubsync.core.alignment.{symbol}", return_value=None):
                        result = method(*arguments)
                else:
                    result = method(*arguments)
                samples.append(time.perf_counter() - started)
                if expected is None:
                    expected = result
                elif expected != result:
                    print(f"ERROR: {label} differs from NumPy reference.", file=sys.stderr)
                    return 1
            timings[backend] = statistics.median(samples)
        print(f"{label}: {len(arguments[0])} x {len(arguments[1])} frames · matches: {len(expected)}")
        print(f"  NumPy: {timings['numpy']:.4f}s · Rust: {timings['rust']:.4f}s · "
              f"factor: {timings['numpy'] / timings['rust']:.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
