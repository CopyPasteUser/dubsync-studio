"""Measure real FFmpeg decode + visual alignment before/after pHash optimization.

Uses a temporary synthetic test clip by default. Pass SOURCE MASTER to measure
owned media without changing either input or persisting alignment diagnostics.
The same selected Rust matching backend (if installed) is used for both variants.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dubsync.core.alignment import align_videos  # noqa: E402
from dubsync.core.native_match import _library  # noqa: E402
from scripts.benchmark_phash import original_phash  # noqa: E402


def _synthetic_video(path: Path, duration: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg ist für den synthetischen Benchmark erforderlich.")
    subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=8",
                    "-t", str(duration), "-c:v", "mpeg4", "-q:v", "8",
                    "-an", str(path)], check=True, timeout=max(30, duration * 2))


def _alignment(path1: str, path2: str, interval: float, max_samples: int,
               baseline: bool):
    with ExitStack() as stack:
        stack.enter_context(patch("dubsync.core.alignment.AlignmentDiagnostic.save",
                                  return_value=None))
        if baseline:
            stack.enter_context(patch("dubsync.core.alignment.perceptual_hash", original_phash))
        start = time.perf_counter()
        result = align_videos(path1, path2, interval=interval,
                              max_samples=max_samples, hardware_accel="ffmpeg",
                              frame_cache_enabled=False, sampling_workers=1)
        elapsed = time.perf_counter() - start
    return elapsed, result


def benchmark(path1: str, path2: str, *, interval: float, max_samples: int,
              repetitions: int) -> dict:
    if not 0.5 <= interval <= 15 or not 200 <= max_samples <= 5000:
        raise ValueError("Ungültiges Sampling-Profil.")
    if not 1 <= repetitions <= 8:
        raise ValueError("Wiederholungen müssen zwischen 1 und 8 liegen.")
    for path in (path1, path2):
        if not Path(path).is_file():
            raise ValueError("Die beiden Eingabemedien müssen existierende Dateien sein.")

    _alignment(path1, path2, interval, max_samples, baseline=False)  # warm-up
    measured: dict[str, list[float]] = {"previous": [], "optimized": []}
    reference = None
    for repeat in range(repetitions):
        # Alternate order to reduce OS cache / thermal bias.
        sequence = ("previous", "optimized") if repeat % 2 == 0 else ("optimized", "previous")
        for name in sequence:
            elapsed, result = _alignment(path1, path2, interval, max_samples,
                                         baseline=name == "previous")
            evidence = (result.safe_for_automatic_export, result.anchors, result.slope,
                        result.offset, result.confidence)
            if reference is None:
                reference = evidence
            elif evidence != reference:
                raise RuntimeError("Alignment-Beweis weicht zwischen den Varianten ab.")
            measured[name].append(elapsed)
    before = statistics.median(measured["previous"])
    after = statistics.median(measured["optimized"])
    native = _library()
    return {"scenario": "ffmpeg_decode_and_alignment", "repetitions": repetitions,
            "interval_s": interval, "max_samples": max_samples,
            "native_rust_available": native is not None,
            "same_alignment_evidence": True,
            "safe_for_automatic_export": bool(reference[0]) if reference else False,
            "previous_seconds": round(before, 5), "optimized_seconds": round(after, 5),
            "speedup": round(before / after, 3)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?")
    parser.add_argument("master", nargs="?")
    parser.add_argument("--duration", type=int, default=90,
                        help="synthetic test clip length when no media files are given")
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--max-samples", type=int, default=1800)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if bool(args.source) != bool(args.master):
        parser.error("SOURCE und MASTER müssen gemeinsam angegeben werden.")
    if args.duration < 15 or args.duration > 240:
        parser.error("--duration muss zwischen 15 und 240 Sekunden liegen.")
    if args.source:
        report = benchmark(args.source, args.master, interval=args.interval,
                           max_samples=args.max_samples, repetitions=args.repeat)
        report["media"] = "supplied"
    else:
        with tempfile.TemporaryDirectory(prefix="dubsync-performance-") as temporary:
            source = Path(temporary) / "source.mkv"
            master = Path(temporary) / "master.mkv"
            _synthetic_video(source, args.duration)
            shutil.copyfile(source, master)
            report = benchmark(str(source), str(master), interval=args.interval,
                               max_samples=args.max_samples, repetitions=args.repeat)
        report["media"] = "temporary_synthetic"
        report["synthetic_duration_s"] = args.duration
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"Pipeline ({report['media']}): {report['previous_seconds']}s -> "
              f"{report['optimized_seconds']}s, {report['speedup']}x faster")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
