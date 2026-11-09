"""Compare actual frame-matcher wall time; no media is read or modified."""
from __future__ import annotations

import random
import statistics
import sys
import time
from bisect import bisect_left
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dubsync.core.alignment import (FrameSample, _feature_anchor_fallback,  # noqa: E402
                                   _feature_candidate_pairs,
                                   _offset_probe_candidates, _sequence_alignment)
from dubsync.core.native_match import _library, prepare_native_offset_verifier  # noqa: E402


def _scalar_verify_offset(source, target, shift, tolerance):
    times = [sample.time for sample in target]
    result = []
    previous = -1
    for si, src in enumerate(source):
        estimate = src.time + shift
        j = bisect_left(times, estimate)
        matches = []
        for ti in range(max(previous + 1, j - 2), min(len(target), j + 3)):
            phase = abs(target[ti].time - estimate)
            if phase <= tolerance:
                distance = (src.phash ^ target[ti].phash).bit_count()
                if distance <= 18:
                    matches.append((distance, phase, ti))
        if matches:
            dist, _, ti = min(matches)
            result.append((si, ti, dist))
            previous = ti
    return result


def _measure(label, reference, accelerated):
    timings = {}
    expected = None
    for backend, method in (("numpy", reference), ("rust", accelerated)):
        samples = []
        for _ in range(3):
            started = time.perf_counter()
            result = method()
            samples.append(time.perf_counter() - started)
            if expected is None:
                expected = result
            elif expected != result:
                print(f"ERROR: {label} differs from Python reference.", file=sys.stderr)
                return False
        timings[backend] = statistics.median(samples)
    print(f"{label}: Python/NumPy: {timings['numpy']:.4f}s · Rust: "
          f"{timings['rust']:.4f}s · factor: "
          f"{timings['numpy'] / timings['rust']:.2f}x")
    return True


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
    if hasattr(lib, "dubsync_best_affine"):
        short_hashes = hashes[:120]
        intro = [rng.getrandbits(64) for _ in range(17)]
        short_source = [FrameSample(i * .75, h) for i, h in enumerate(short_hashes)]
        short_target = [FrameSample(i * .75, h) for i, h in
                        enumerate(intro + short_hashes)]

        def fallback_reference():
            with patch("dubsync.core.alignment.native_best_affine", return_value=None):
                return _feature_anchor_fallback(short_source, short_target, .75)

        if not _measure("affine hypotheses (120 frames)", fallback_reference,
                        lambda: _feature_anchor_fallback(short_source, short_target, .75)):
            return 1
        # A slight, monotonically increasing timing drift produces thousands of
        # distinct plausible models. That is where hypothesis scoring can be a
        # real bottleneck; an exact-offset example above tests its FFI floor.
        drift_hashes = hashes[:240]
        drift_source = [FrameSample(i * .75, h) for i, h in enumerate(drift_hashes)]
        drift_intro = [FrameSample(i * .75, rng.getrandbits(64)) for i in range(17)]
        drift_target = drift_intro + [
            FrameSample(12.75 + i * .75 * 1.012 + rng.uniform(-.08, .08), h)
            for i, h in enumerate(drift_hashes)
        ]

        def drift_reference():
            with patch("dubsync.core.alignment.native_best_affine", return_value=None):
                return _feature_anchor_fallback(drift_source, drift_target, .75)

        if not _measure("affine drift hypotheses (240 frames)", drift_reference,
                        lambda: _feature_anchor_fallback(drift_source, drift_target, .75)):
            return 1
    if hasattr(lib, "dubsync_verify_offset"):
        offsets = [offset * .75 for offset in (10, 15, 25, 30, 40, 45, 50, 55, 60, 65, 70, 75)]
        verifier = prepare_native_offset_verifier(source, target)
        if verifier is None:
            print("ERROR: Rust offset verifier unavailable.", file=sys.stderr)
            return 1
        if not _measure("12 long-offset verifications (1200 frames)",
                        lambda: [_scalar_verify_offset(source, target, offset, .85)
                                 for offset in offsets],
                        lambda: [verifier(offset, .85) for offset in offsets]):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
