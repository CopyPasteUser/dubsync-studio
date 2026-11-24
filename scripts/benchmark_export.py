"""Measure real multi-track muxing using disposable synthetic media.

Synthetic alignment models exercise the exporter, not episode identity detection.
All export decisions still go through the normal exporter checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stream_hash(ffmpeg: str, path: Path, stream: str, codec_args: list[str]) -> str:
    output = subprocess.check_output([ffmpeg, "-nostdin", "-v", "error", "-i", str(path),
                                      "-map", stream, *codec_args, "-f", "hash", "-hash", "SHA256", "-"], timeout=180)
    value = output.decode("ascii").strip()
    if not value.startswith("SHA256=") or len(value) != 71:
        raise RuntimeError("Missing stream evidence")
    return value


def benchmark(root: Path, duration: int, repeats: int) -> dict:
    from dubsync.core.export import export_multilingual
    from dubsync.core.models import AlignmentResult, Anchor, TrackChoice
    from dubsync.core.probe import probe_media, require_media_tools

    ffmpeg, _ = require_media_tools()
    master = root / "master.mkv"
    base = [ffmpeg, "-nostdin", "-v", "error", "-y"]
    subprocess.run(base + ["-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=12:duration={duration}",
                           "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
                           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(master)],
                   check=True, timeout=120)
    target_duration = probe_media(str(master)).duration
    tracks = []
    inputs = [master]
    for i, offset in enumerate((0.0, -.5, .5)):
        source = root / f"alternate-{i}.wav"
        subprocess.run(base + ["-f", "lavfi", "-i", f"sine=frequency={660+i*110}:sample_rate=44100:duration={duration}", str(source)],
                       check=True, timeout=120)
        inputs.append(source)
        source_duration = probe_media(str(source)).duration
        anchors = [Anchor(t, t + offset, 0) for t in (j * .5 for j in range(duration * 2))
                   if 0 <= t < source_duration and 0 <= t + offset < target_duration]
        result = AlignmentResult(source_path=str(source), target_path=str(master), slope=1., offset=offset,
                                 anchors=anchors, confidence=1., inlier_ratio=1., coverage=1., median_hamming=0.,
                                 rms_error=0., source_duration=source_duration, target_duration=target_duration, sample_interval=.5)
        if not result.safe_for_automatic_export:
            raise RuntimeError("Synthetic fixture did not satisfy exporter preflight")
        tracks.append(TrackChoice(path=str(source), audio_ordinal=0, language="eng", title=f"Fixture {i}", alignment=result))
    before = [file_sha256(path) for path in inputs]
    reference_video = stream_hash(ffmpeg, master, "0:v:0", ["-c:v", "copy"])
    reference = None
    times = []
    sizes = []
    for i in range(repeats):
        output = root / f"out-{i}.mkv"
        started = time.perf_counter()
        export_multilingual(str(master), tracks, str(output))
        times.append(time.perf_counter() - started)
        sizes.append(output.stat().st_size)
        info = probe_media(str(output))
        evidence = {"video": stream_hash(ffmpeg, output, "0:v:0", ["-c:v", "copy"]),
                    "audio": [stream_hash(ffmpeg, output, f"0:a:{j}", ["-c:a", "pcm_s16le"])
                              for j in range(info.audio_streams)],
                    "audio_streams": [{"codec": a.codec_name, "channels": a.channels,
                                       "sample_rate": a.sample_rate, "language": a.language} for a in info.audio]}
        if evidence["video"] != reference_video or len(evidence["audio"]) != 4:
            raise RuntimeError("Master video or track count changed")
        if reference is not None and reference != evidence:
            raise RuntimeError("Repeated exports differ")
        reference = evidence
        output.unlink()
    if before != [file_sha256(path) for path in inputs]:
        raise RuntimeError("Export mutated input media")
    return {"schema_version": 1, "scenario": "synthetic-three-dubs", "duration_s": duration,
            "repetitions": repeats, "wall_seconds": times, "median_seconds": statistics.median(times),
            "output_bytes": sizes, "evidence": reference, "inputs_unchanged": True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--repeat", type=int, choices=range(2, 6), default=3)
    args = parser.parse_args(argv)
    if not 10 <= args.duration <= 120:
        parser.error("Duration must be between 10 and 120 seconds")
    with tempfile.TemporaryDirectory(prefix="dubsync-export-bench-") as directory:
        print(json.dumps(benchmark(Path(directory), args.duration, args.repeat), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
