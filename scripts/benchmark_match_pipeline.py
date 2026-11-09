"""Compare Rust and Python matching on identical decoded FFmpeg video.

No exports or persistent diagnostics are written. Synthetic media lives in a
temporary directory; supplied media is read-only. Results are measurements of
this host, not predictions for another CPU, GPU or NAS.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import asdict
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
from scripts.benchmark_pipeline import _synthetic_video  # noqa: E402


_MATCHER_HOOKS = ("native_pairs", "native_feature_candidates", "native_offset_candidates",
                  "native_best_affine", "prepare_native_offset_verifier")
_REQUIRED_SYMBOLS = ("dubsync_match", "dubsync_feature_candidates", "dubsync_offset_candidates",
                     "dubsync_best_affine", "dubsync_verify_offset")


def _synthetic_shift(source: Path, master: Path, duration: int) -> None:
    """Create a separate intro followed by the same frames; force shifted matching."""
    _synthetic_video(source, duration)
    ffmpeg = shutil.which("ffmpeg")
    subprocess.run([
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "lavfi", "-t", "32", "-i", "testsrc=size=320x180:rate=8",
        "-i", str(source), "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "mpeg4", "-q:v", "8", "-an", str(master),
    ], check=True, timeout=max(45, duration * 3))


def _alignment(source: str, master: str, interval: float, max_samples: int,
               native: bool):
    reports = []

    def capture(diagnostic):
        reports.append(diagnostic.payload)

    with ExitStack() as stack:
        stack.enter_context(patch("dubsync.core.alignment.AlignmentDiagnostic.save", capture))
        if not native:
            for hook in _MATCHER_HOOKS:
                stack.enter_context(patch(f"dubsync.core.alignment.{hook}", return_value=None))
        start = time.perf_counter()
        result = align_videos(source, master, interval=interval, max_samples=max_samples,
                              hardware_accel="ffmpeg", frame_cache_enabled=False,
                              sampling_workers=1)
        elapsed = time.perf_counter() - start

    if len(reports) != 1:
        raise RuntimeError("Das Laufzeitprofil fehlt.")
    report = reports[0]
    method = next((event["method"] for event in report["events"]
                   if event.get("event") == "method_selected"), "unknown")
    evidence = (asdict(result), result.safe_for_automatic_export, method)
    return elapsed, report["stages"]["pipeline_profile"], evidence


def benchmark(source: str, master: str, *, interval: float = .5,
              max_samples: int = 1800, repetitions: int = 3) -> dict:
    if not .5 <= interval <= 15 or not 200 <= max_samples <= 5000:
        raise ValueError("Ungültiges Sampling-Profil.")
    if not 1 <= repetitions <= 8:
        raise ValueError("Wiederholungen müssen zwischen 1 und 8 liegen.")
    if not all(Path(path).is_file() for path in (source, master)):
        raise ValueError("Beide Eingabemedien müssen existierende Dateien sein.")
    lib = _library()
    if lib is None or any(not hasattr(lib, symbol) for symbol in _REQUIRED_SYMBOLS):
        raise RuntimeError("Alle fünf Rust-Matcher müssen gebaut sein (./build-native.sh).")

    _alignment(source, master, interval, max_samples, native=True)  # OS and decoder warm-up
    measured = {"python": {"total": [], "matching": [], "sampling": []},
                "rust": {"total": [], "matching": [], "sampling": []}}
    reference = None
    for repeat in range(repetitions):
        for name in (("python", "rust") if repeat % 2 == 0 else ("rust", "python")):
            elapsed, profile, evidence = _alignment(source, master, interval, max_samples,
                                                     native=name == "rust")
            if reference is None:
                reference = evidence
            elif evidence != reference:
                raise RuntimeError("Rust und Python liefern unterschiedliche Alignment-Beweise.")
            measured[name]["total"].append(elapsed)
            measured[name]["matching"].append(profile["matching_wall_seconds"])
            measured[name]["sampling"].append(profile["sampling_wall_seconds"])

    medians = {name: {stage: statistics.median(values) for stage, values in times.items()}
               for name, times in measured.items()}
    return {"scenario": "ffmpeg_decode_and_alignment",
            "repetitions": repetitions, "interval_s": interval,
            "max_samples": max_samples, "evidence_identical": True,
            "method": reference[2], "safe_for_automatic_export": reference[1],
            "rust_matcher_exercised": reference[2] not in ("same_timeline", "unknown"),
            "python_seconds": round(medians["python"]["total"], 5),
            "rust_seconds": round(medians["rust"]["total"], 5),
            "pipeline_speedup": round(medians["python"]["total"] / medians["rust"]["total"], 3),
            "python_matching_seconds": round(medians["python"]["matching"], 5),
            "rust_matching_seconds": round(medians["rust"]["matching"], 5),
            "matching_speedup": round(medians["python"]["matching"] /
                                      max(medians["rust"]["matching"], .000001), 3),
            "python_sampling_seconds": round(medians["python"]["sampling"], 5),
            "rust_sampling_seconds": round(medians["rust"]["sampling"], 5)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?")
    parser.add_argument("master", nargs="?")
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--interval", type=float, default=.5)
    parser.add_argument("--max-samples", type=int, default=1800)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if bool(args.source) != bool(args.master):
        parser.error("SOURCE und MASTER müssen gemeinsam angegeben werden.")
    if not args.source and not 30 <= args.duration <= 240:
        parser.error("--duration muss zwischen 30 und 240 Sekunden liegen.")
    if args.source:
        result = benchmark(args.source, args.master, interval=args.interval,
                           max_samples=args.max_samples, repetitions=args.repeat)
        result["media"] = "supplied"
    else:
        with tempfile.TemporaryDirectory(prefix="dubsync-rust-pipeline-") as directory:
            source = Path(directory) / "source.mkv"
            master = Path(directory) / "master.mkv"
            _synthetic_shift(source, master, args.duration)
            result = benchmark(str(source), str(master), interval=args.interval,
                               max_samples=args.max_samples, repetitions=args.repeat)
        result["media"] = "temporary_synthetic"
        result["synthetic_duration_s"] = args.duration
        result["intro_offset_s"] = 32
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"Matching: {result['python_matching_seconds']}s -> "
              f"{result['rust_matching_seconds']}s ({result['matching_speedup']}x); "
              f"whole pipeline: {result['python_seconds']}s -> "
              f"{result['rust_seconds']}s ({result['pipeline_speedup']}x)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
