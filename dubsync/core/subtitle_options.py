"""Lightweight subtitle configuration, independent of acquisition backends."""
from dataclasses import dataclass


class SubtitleError(RuntimeError):
    pass


@dataclass(frozen=True)
class SubtitleOptions:
    mode: str = "off"  # off, auto, existing, file, sidecar, archive, embedded, download, subdl, url, generate
    language: str = "auto"  # Speech language; not an automatic translation request.
    file_path: str = ""
    whisper_model: str = "small"
