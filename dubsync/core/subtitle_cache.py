"""Private, bounded reuse of validated online and generated SRT subtitles.

Only expensive provider/ASR results are stored. Sidecars and embedded tracks are
always checked afresh, so replacing a local subtitle immediately takes effect.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path

from .fingerprint import quick_fingerprint


_CACHE_VERSION = 1
_MAX_FILE_BYTES = 5 * 1024 * 1024
_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_MAX_ENTRIES = 256
_ONLINE_TTL_SECONDS = 14 * 24 * 3600
_GENERATED_TTL_SECONDS = 60 * 24 * 3600
_CACHED_STRATEGIES = {"subdl", "subliminal", "generate"}


def default_subtitle_cache_dir() -> Path:
    override = os.environ.get("DUBSYNC_SUBTITLE_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    root = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return root / "dubsync-studio" / "subtitles"


def subtitle_cache_enabled() -> bool:
    return os.environ.get("DUBSYNC_SUBTITLE_CACHE", "1").strip().casefold() not in {
        "0", "no", "false", "off",
    }


class SubtitleCache:
    """A best-effort disk cache; cache failures never block an export."""

    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory is not None else default_subtitle_cache_dir()

    def key(self, master: Path, duration: float, language: str, strategy: str,
            model: str = "small") -> str | None:
        if not subtitle_cache_enabled() or strategy not in _CACHED_STRATEGIES:
            return None
        try:
            source = Path(master)
            meta = source.stat()
            model_path = Path(os.environ.get("DUBSYNC_WHISPER_MODEL", ""))
            # A local GGML model can change independently of the selected size.
            native_model = None
            if strategy == "generate" and model_path.is_file():
                model_stat = model_path.stat()
                native_model = [str(model_path.resolve()), model_stat.st_size,
                                model_stat.st_mtime_ns]
            signature = {
                "schema": _CACHE_VERSION,
                "master": source.name,
                "size": meta.st_size,
                "mtime_ns": meta.st_mtime_ns,
                "fingerprint": quick_fingerprint(source),
                "duration_ms": round(duration * 1000),
                "language": language,
                "strategy": strategy,
                "model": model if strategy == "generate" else None,
                "native_model": native_model,
            }
            serialized = json.dumps(signature, sort_keys=True, ensure_ascii=False)
            return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        except (OSError, ValueError, OverflowError):
            return None

    def _safe_root(self, *, create: bool) -> bool:
        try:
            if create:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.directory.is_dir() or self.directory.is_symlink():
                return False
            # A private directory is required: subtitles can contain sensitive speech.
            os.chmod(self.directory, 0o700)
            return True
        except OSError:
            return False

    def load(self, key: str | None, target: Path, *, duration: float,
             strategy: str) -> bool:
        if not _valid_key(key) or strategy not in _CACHED_STRATEGIES or not self._safe_root(create=False):
            return False
        cached = self.directory / (key + ".srt")
        try:
            mode = cached.lstat().st_mode
            if not stat.S_ISREG(mode):
                return False
            age = time.time() - cached.stat().st_mtime
            ttl = _GENERATED_TTL_SECONDS if strategy == "generate" else _ONLINE_TTL_SECONDS
            if age > ttl or age < -60:
                return False
            # O_NOFOLLOW protects against replacement of the checked path.
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(cached, flags)
            with os.fdopen(fd, "rb") as stream:
                meta = os.fstat(stream.fileno())
                if not stat.S_ISREG(meta.st_mode) or not 12 < meta.st_size <= _MAX_FILE_BYTES:
                    return False
                payload = stream.read(_MAX_FILE_BYTES + 1)
            if len(payload) > _MAX_FILE_BYTES:
                return False
            # Never trust a cached file without rechecking timing and SRT syntax.
            from .subtitles import SubtitleError, _check_timing

            if not _contains_srt_timestamps(payload):
                return False
            target.write_bytes(payload)
            try:
                _check_timing(target, duration)
            except SubtitleError:
                target.unlink(missing_ok=True)
                return False
            return True
        except (OSError, ValueError):
            return False

    def store(self, key: str | None, source: Path) -> None:
        if not _valid_key(key) or not self._safe_root(create=True):
            return
        temporary: Path | None = None
        try:
            size = source.stat().st_size
            if not 12 < size <= _MAX_FILE_BYTES:
                return
            with source.open("rb") as stream:
                content = stream.read(_MAX_FILE_BYTES + 1)
            if not 12 < len(content) <= _MAX_FILE_BYTES or not _contains_srt_timestamps(content):
                return
            fd, name = tempfile.mkstemp(prefix=".caption-", suffix=".tmp", dir=self.directory)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(content)
            os.replace(temporary, self.directory / (key + ".srt"))
            temporary = None
            self._prune()
        except OSError:
            pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _prune(self) -> None:
        """Bound both entry count and disk use, without following symlinks."""
        try:
            records: list[tuple[float, int, Path]] = []
            now = time.time()
            for entry in self.directory.glob("*.srt"):
                if not _valid_key(entry.stem):
                    continue
                metadata = entry.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                if now - metadata.st_mtime > _GENERATED_TTL_SECONDS:
                    entry.unlink(missing_ok=True)
                    continue
                records.append((metadata.st_mtime, metadata.st_size, entry))
            records.sort(key=lambda record: record[0], reverse=True)
            retained_bytes = 0
            for index, (_mtime, size, entry) in enumerate(records):
                if index >= _MAX_ENTRIES or retained_bytes + size > _MAX_TOTAL_BYTES:
                    entry.unlink(missing_ok=True)
                else:
                    retained_bytes += size
        except OSError:
            pass  # Opportunistic housekeeping cannot fail subtitle export.


def _contains_srt_timestamps(content: bytes) -> bool:
    # Validate without decoding arbitrary bytes; detailed duration check is separate.
    return bool(re.search(rb"\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*"
                          rb"\d{2}:\d{2}:\d{2},\d{3}", content[:_MAX_FILE_BYTES]))


def _valid_key(key: str | None) -> bool:
    return bool(key and re.fullmatch(r"[a-f0-9]{64}", key))
