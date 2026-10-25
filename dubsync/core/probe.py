from __future__ import annotations

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .models import MediaInfo, StreamInfo

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".mov", ".avi", ".webm", ".mpg", ".mpeg", ".ts", ".m2ts", ".wmv", ".flv"}


class MediaToolError(RuntimeError):
    pass


def find_tool(name: str) -> str | None:
    return shutil.which(name)


def require_media_tools() -> tuple[str, str]:
    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    missing = [name for name, value in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not value]
    if missing:
        raise MediaToolError("Benötigte Programme fehlen: " + ", ".join(missing))
    return ffmpeg, ffprobe  # type: ignore[return-value]


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
        if parsed == parsed and abs(parsed) != float("inf"):
            return parsed
    except (TypeError, ValueError):
        pass
    return None


def _fps(stream: dict[str, Any]) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = stream.get(key)
        if isinstance(value, str) and "/" in value:
            num, den = value.split("/", 1)
            try:
                return float(num) / float(den) if float(den) else None
            except (ValueError, ZeroDivisionError):
                pass
        parsed = _float(value)
        if parsed and parsed > 0:
            return parsed
    return None


def probe_media(path: str | os.PathLike[str], timeout: int = 45) -> MediaInfo:
    _, ffprobe = require_media_tools()
    file_path = Path(path).expanduser().resolve()
    if not file_path.is_file():
        raise MediaToolError(f"Datei nicht gefunden: {file_path}")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(file_path)],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaToolError(f"FFprobe-Zeitlimit für {file_path.name}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()[-1200:]
        raise MediaToolError(f"FFprobe konnte {file_path.name} nicht lesen: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaToolError(f"FFprobe lieferte ungültige JSON-Daten für {file_path.name}") from exc

    fmt = payload.get("format") or {}
    duration = _float(fmt.get("duration")) or 0.0
    streams: list[StreamInfo] = []
    for item in payload.get("streams", []):
        tags = item.get("tags") or {}
        disposition = item.get("disposition") or {}
        streams.append(StreamInfo(
            index=int(item.get("index", -1)),
            codec_type=str(item.get("codec_type", "unknown")),
            codec_name=str(item.get("codec_name", "unknown")),
            language=str(tags.get("language", "und")).lower(),
            title=str(tags.get("title", "")),
            duration=_float(item.get("duration")),
            channels=int(item["channels"]) if item.get("channels") is not None else None,
            sample_rate=int(item["sample_rate"]) if item.get("sample_rate") else None,
            width=int(item["width"]) if item.get("width") else None,
            height=int(item["height"]) if item.get("height") else None,
            fps=_fps(item) if item.get("codec_type") == "video" else None,
            disposition={str(k): int(v) for k, v in disposition.items() if isinstance(v, (int, bool))},
            raw=item,
        ))
    videos = sum(stream.codec_type == "video" for stream in streams)
    audios = sum(stream.codec_type == "audio" for stream in streams)
    subtitles = sum(stream.codec_type == "subtitle" for stream in streams)
    return MediaInfo(
        path=str(file_path), duration=duration,
        format_name=str(fmt.get("format_name", "unknown")),
        size_bytes=int(fmt.get("size") or file_path.stat().st_size),
        streams=streams, video_streams=videos,
        audio_streams=audios, subtitle_streams=subtitles,
    )


def probe_many(paths: list[str], max_workers: int = 4, progress: Callable[[int, int, str], None] | None = None, cancel_event=None) -> list[MediaInfo]:
    """Probe files concurrently, while returning results in the caller's input order.

    This speeds up multi-language episodes and NAS libraries without launching an
    unbounded number of FFprobe processes. The caller should use fewer workers for
    network-mounted paths. A failed item fails the operation with its path in the
    error message rather than silently dropping a source.
    """
    ordered = list(dict.fromkeys(str(Path(path).expanduser()) for path in paths))
    if not ordered:
        return []
    workers = max(1, min(8, int(max_workers), len(ordered)))
    results: list[MediaInfo | None] = [None] * len(ordered)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dubsync-probe") as pool:
        futures = {pool.submit(probe_media, path): (index, path) for index, path in enumerate(ordered)}
        completed = 0
        try:
            for future in as_completed(futures):
                if cancel_event is not None and cancel_event.is_set():
                    for pending in futures:
                        pending.cancel()
                    raise MediaToolError("Medienanalyse abgebrochen.")
                index, path = futures[future]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    for pending in futures:
                        pending.cancel()
                    if isinstance(exc, MediaToolError):
                        raise MediaToolError(f"{Path(path).name}: {exc}") from exc
                    raise MediaToolError(f"{Path(path).name}: FFprobe fehlgeschlagen: {exc}") from exc
                completed += 1
                if progress:
                    progress(completed, len(ordered), Path(path).name)
        finally:
            # ThreadPoolExecutor waits only for active probes, each of which has a
            # bounded FFprobe timeout. The GUI remains responsive in its worker.
            pass
    return [item for item in results if item is not None]


def choose_default_audio(info: MediaInfo, preferred_language: str | None = None) -> int:
    tracks = info.audio
    if not tracks:
        raise MediaToolError(f"Keine Audiospur gefunden: {Path(info.path).name}")
    if preferred_language:
        token = preferred_language.lower()
        for i, track in enumerate(tracks):
            if track.language == token or track.language.startswith(token):
                return i
    for i, track in enumerate(tracks):
        if track.disposition.get("default", 0):
            return i
    return 0


def describe_media(info: MediaInfo) -> str:
    mins, secs = divmod(max(0, int(info.duration)), 60)
    video = info.video[0] if info.video else None
    resolution = f"{video.width}×{video.height}" if video and video.width and video.height else "ohne Video"
    return f"{mins:02d}:{secs:02d} · {resolution} · {info.audio_streams} Audio · {info.subtitle_streams} Untertitel"
