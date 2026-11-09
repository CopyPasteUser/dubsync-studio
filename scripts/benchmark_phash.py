"""Reproducible perceptual-hash microbenchmark with an exact scalar oracle.

The timings are not claims about entire video imports or any physical NAS/GPU.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dubsync.core.alignment import perceptual_hash  # noqa: E402
from dubsync.core.native_match import _library  # noqa: E402


def original_phash(frame: np.ndarray) -> int:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    tiny = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    values = cv2.dct(np.float32(tiny))[:8, :8].flatten()[1:]
    median = float(np.median(values))
    result = 0
    for bit, value in enumerate(values):
        if value >= median:
            result |= 1 << bit
    return result


def numpy_phash(frame: np.ndarray) -> int:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    tiny = gray if gray.shape == (32, 32) else cv2.resize(
        gray, (32, 32), interpolation=cv2.INTER_AREA)
    values = cv2.dct(np.float32(tiny))[:8, :8].flatten()[1:]
    median = np.partition(values, 31)[31]
    return int.from_bytes(np.packbits(values >= median, bitorder="little").tobytes(), "little")


def benchmark(frames: int = 2000, repetitions: int = 5) -> dict:
    if not 1 <= frames <= 20_000 or not 1 <= repetitions <= 20:
        raise ValueError("Frames und Wiederholungen müssen positiv und begrenzt sein.")
    rng = np.random.default_rng(20261109)
    # FFmpeg CPU/GPU decode path feeds 32x32 grayscale into this function.
    samples = rng.integers(0, 256, size=(min(frames, 256), 32, 32), dtype=np.uint8)
    timings: dict[str, float] = {}
    expected = [original_phash(frame) for frame in samples]
    cases = (("previous", original_phash), ("numpy_optimized", numpy_phash),
             ("optimized", perceptual_hash))
    for name, method in cases:
        measured = []
        for _ in range(repetitions):
            start = time.perf_counter()
            hashes = [method(samples[i % len(samples)]) for i in range(frames)]
            measured.append(time.perf_counter() - start)
            if any(value != expected[i % len(expected)] for i, value in enumerate(hashes)):
                raise RuntimeError("Optimierter pHash weicht von der vorherigen Berechnung ab.")
        timings[name] = statistics.median(measured)
    library = _library()
    rust = library is not None and hasattr(library, "dubsync_phash_coefficients")
    return {"scenario": "synthetic_32x32_gray", "frames": frames,
            "repetitions": repetitions, "hashes_identical": True,
            "rust_phash_available": rust,
            "previous_seconds": round(timings["previous"], 5),
            "numpy_optimized_seconds": round(timings["numpy_optimized"], 5),
            "optimized_seconds": round(timings["optimized"], 5),
            "speedup": round(timings["previous"] / timings["optimized"], 3),
            "rust_over_numpy_speedup": (round(timings["numpy_optimized"] / timings["optimized"], 3)
                                        if rust else None)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=2000)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = benchmark(args.frames, args.repeat)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{result['frames']} synthetic 32x32 gray frames; identical hashes")
        print(f"Previous: {result['previous_seconds']}s · optimized: "
              f"{result['optimized_seconds']}s · {result['speedup']}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
