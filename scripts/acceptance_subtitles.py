"""Reproducible, source-safe FFmpeg acceptance of toggleable subtitle exports.

Creates only synthetic media in a private temporary directory. This measures
the real mux path, not subtitle provider or speech-recognition accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dubsync.core.export import export_multilingual  # noqa: E402
from dubsync.core.models import AlignmentResult, Anchor, TrackChoice  # noqa: E402
from dubsync.core.probe import probe_media  # noqa: E402
from dubsync.core.subtitles import SubtitleOptions  # noqa: E402


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=120,
                            check=False, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(f"Media QA command failed ({command[0]}): {result.stderr[-800:]}")
    return result.stdout


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _video_streamhash(path: Path) -> str:
    output = _run(["ffmpeg", "-hide_banner", "-v", "error", "-i", str(path),
                   "-map", "0:v:0", "-c:v", "copy", "-f", "streamhash",
                   "-hash", "SHA256", "-"])
    result = re.search(r"SHA256=([0-9a-fA-F]{64})", output)
    if result is None:
        raise RuntimeError("FFmpeg streamhash did not return a SHA256 digest")
    return result.group(1).lower()


def accept_synthetic() -> dict:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and FFprobe are required")
    with tempfile.TemporaryDirectory(prefix="dubsync-subtitle-qa-") as temporary:
        root = Path(temporary)
        master = root / "Episode.S01E02.mkv"
        alternate = root / "alternative.wav"
        existing = root / "original.srt"
        sidecar = root / "Episode.S01E02.de.srt"
        with_subs = root / "soft-subtitles.mkv"
        without_subs = root / "without-extra-subs.mkv"
        existing.write_text("1\n00:00:00,600 --> 00:00:01,600\nOriginal\n\n",
                            encoding="utf-8")
        sidecar.write_text("1\n00:00:01,200 --> 00:00:02,600\nSchaltbarer Text\n\n",
                           encoding="utf-8")
        _run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
              "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12:duration=4",
              "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
              "-i", str(existing), "-map", "0:v:0", "-map", "1:a:0",
              "-map", "2:s:0", "-c:v", "mpeg4", "-c:a", "aac",
              "-c:s", "srt", str(master)])
        _run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
              "-f", "lavfi", "-i", "sine=frequency=660:duration=4", str(alternate)])
        original_digests = {p.name: _digest(p) for p in (master, alternate, existing, sidecar)}
        alignment = AlignmentResult(
            source_path=str(alternate), target_path=str(master), slope=1.0, offset=0.0,
            anchors=[Anchor(i * 0.25, i * 0.25, 0) for i in range(16)],
            confidence=1.0, inlier_ratio=1.0, coverage=1.0, median_hamming=0,
            rms_error=0.0, source_duration=4.0, target_duration=4.0, sample_interval=0.25,
        )
        if not alignment.safe_for_automatic_export:
            raise RuntimeError("Synthetic alignment fixture does not meet export safety")
        track = TrackChoice(path=str(alternate), audio_ordinal=0,
                            language="eng", alignment=alignment)
        timings = {}
        for output, mode in ((without_subs, "off"), (with_subs, "sidecar")):
            start = time.perf_counter()
            export_multilingual(str(master), [track], str(output),
                                subtitle_options=SubtitleOptions(mode=mode, language="deu"))
            timings[mode] = round(time.perf_counter() - start, 4)
        before, off, on = (probe_media(str(path)) for path in
                           (master, without_subs, with_subs))
        master_streamhash = _video_streamhash(master)
        copied_video = (master_streamhash == _video_streamhash(with_subs)
                        == _video_streamhash(without_subs))
        extras = [s for s in on.streams if s.codec_type == "subtitle"]
        originals_untouched = all(_digest(root / filename) == digest
                                  for filename, digest in original_digests.items())
        passed = (copied_video and originals_untouched
                  and off.subtitle_streams == before.subtitle_streams
                  and on.subtitle_streams == before.subtitle_streams + 1
                  and off.audio_streams == on.audio_streams == before.audio_streams + 1
                  and len(extras) == 2 and extras[-1].codec_name == "subrip"
                  and extras[-1].language == "deu"
                  and extras[-1].disposition.get("default") == 0)
        return {
            "scenario": "synthetic_ffmpeg_mux",
            "passed": bool(passed),
            "master_video_sha256": master_streamhash,
            "video_bitstream_copied": copied_video,
            "original_files_unchanged": originals_untouched,
            "subtitle_streams": {"source": before.subtitle_streams,
                                 "disabled": off.subtitle_streams,
                                 "enabled": on.subtitle_streams},
            "additional_subtitle_default": bool(extras[-1].disposition.get("default"))
            if extras else None,
            "mux_wall_seconds": timings,
            "limitations": ["synthetic media only", "no live subtitle provider",
                            "no ASR or speech-based timing quality benchmark"],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable QA results")
    args = parser.parse_args()
    try:
        report = accept_synthetic()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Subtitle QA failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Synthetic subtitle export: " + ("PASS" if report["passed"] else "FAIL"))
        print("Video stream copied:", report["video_bitstream_copied"])
        print("Source files preserved:", report["original_files_unchanged"])
        print("Mux times (seconds):", report["mux_wall_seconds"])
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
