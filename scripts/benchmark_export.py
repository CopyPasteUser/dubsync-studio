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


def packet_timing_evidence(ffprobe: str, path: Path) -> dict:
    """Keep each stream's packet order; container interleaving is not evidence."""
    output = subprocess.check_output([ffprobe, "-v", "error", "-show_packets",
        "-show_entries", "packet=stream_index,pts_time,dts_time,duration_time",
        "-of", "json", str(path)], timeout=180)
    packets = json.loads(output)["packets"]
    streams = {}
    for packet in packets:
        index = int(packet["stream_index"])
        streams.setdefault(index, []).append(packet)
    if not streams:
        raise RuntimeError("Missing packet timing evidence")
    result = {}
    for index, sequence in sorted(streams.items()):
        try:
            stamps = [(float(p["pts_time"]), float(p["duration_time"])) for p in sequence]
        except (KeyError, ValueError):
            stamps = []
        result[str(index)] = {
            "sha256": hashlib.sha256(json.dumps(sequence, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(),
            "count": len(sequence),
            "first_s": round(stamps[0][0], 6) if stamps else None,
            "end_s": round(max(t + d for t, d in stamps), 6) if stamps else None,
            "max_gap_s": round(max((max(0., b[0] - a[0] - a[1])
                                    for a, b in zip(stamps, stamps[1:])), default=0.), 6) if stamps else None,
        }
    return result


def packet_timing_sha256(ffprobe: str, path: Path) -> str:
    return hashlib.sha256(json.dumps(packet_timing_evidence(ffprobe, path),
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def continuous_added_audio(timing: dict, duration: float, indexes: list[int]) -> bool:
    for index in indexes:
        stream = timing.get(str(index), {})
        if (stream.get("first_s") is None or stream.get("end_s") is None or
                stream.get("max_gap_s") is None or stream["first_s"] > .05 or
                stream["end_s"] < duration - .05 or stream["max_gap_s"] > .004):
            return False
    return True


def benchmark(root: Path, duration: int, repeats: int, *, observe_baseline: bool = False) -> dict:
    from dubsync.core.export import export_multilingual
    from dubsync.core.models import AlignmentResult, Anchor, TrackChoice
    from dubsync.core.probe import probe_media, require_media_tools

    ffmpeg, ffprobe = require_media_tools()
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
    observations = []
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
                    "packet_timing": packet_timing_evidence(ffprobe, output),
                    "duration_s": info.duration,
                    "audio_streams": [{"codec": a.codec_name, "channels": a.channels,
                                       "sample_rate": a.sample_rate, "language": a.language} for a in info.audio]}
        if evidence["video"] != reference_video or len(evidence["audio"]) != 4:
            raise RuntimeError("Master video or track count changed")
        continuous = continuous_added_audio(evidence["packet_timing"], target_duration,
                                          [a.index for a in info.audio[1:]])
        observations.append({"evidence": evidence, "continuous_added_audio": continuous})
        if not observe_baseline:
            if not continuous:
                raise RuntimeError("Generated audio has a packet timing discontinuity")
            if reference is not None and reference != evidence:
                raise RuntimeError("Repeated exports differ")
        reference = evidence
        output.unlink()
    if before != [file_sha256(path) for path in inputs]:
        raise RuntimeError("Export mutated input media")
    return {"schema_version": 1, "scenario": "synthetic-three-dubs", "duration_s": duration,
            "repetitions": repeats, "wall_seconds": times, "median_seconds": statistics.median(times),
            "output_bytes": sizes, "evidence": reference, "observations": observations,
            "inputs_unchanged": True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--repeat", type=int, choices=range(2, 6), default=3)
    parser.add_argument("--observe-baseline", action="store_true",
                        help="Record every original result, including known timestamp discontinuities")
    args = parser.parse_args(argv)
    if not 10 <= args.duration <= 120:
        parser.error("Duration must be between 10 and 120 seconds")
    with tempfile.TemporaryDirectory(prefix="dubsync-export-bench-") as directory:
        print(json.dumps(benchmark(Path(directory), args.duration, args.repeat,
                                   observe_baseline=args.observe_baseline), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
