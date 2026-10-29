"""Validated, bounded cache for decoded video *samples*, never alignment decisions.

Every lookup re-reads small, distributed windows of the source file. This avoids
trusting a cached file fingerprint that was itself keyed only by metadata and
keeps episode matching and export safety checks fresh on each run.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
import zlib
from collections import OrderedDict
from pathlib import Path


SAMPLE_FORMAT_VERSION = 1
MAX_ENTRIES = 400
MAX_CACHE_BYTES = 128 * 1024 * 1024
MAX_SAMPLES = 6000
MEMORY_ENTRIES = 12
WINDOW_BYTES = 16384
WINDOW_COUNT = 11


def default_frame_cache_path() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return root / "dubsync-studio" / "video-samples.sqlite3"


def source_identity(path: str) -> str:
    """Fresh stat + distributed content evidence, including post-read race check.

    This is a *sampled* signature, not a full cryptographic hash of the video.
    A source that changes while its identity is read cannot enter the cache.
    """
    file = Path(path).expanduser().resolve(strict=True)
    before = file.stat()
    if not file.is_file() or before.st_size <= 0:
        raise OSError("Keine lesbare reguläre Videodatei")
    digest = hashlib.blake2b(digest_size=32)
    digest.update(os.fsencode(file))
    digest.update(str((before.st_size, before.st_mtime_ns, before.st_ctime_ns,
                       before.st_ino, before.st_dev)).encode("ascii"))
    max_offset = max(0, before.st_size - WINDOW_BYTES)
    positions = sorted({(max_offset * index) // (WINDOW_COUNT - 1)
                        for index in range(WINDOW_COUNT)})
    with file.open("rb", buffering=0) as handle:
        for offset in positions:
            handle.seek(offset)
            payload = handle.read(WINDOW_BYTES)
            digest.update(offset.to_bytes(8, "little"))
            digest.update(len(payload).to_bytes(4, "little"))
            digest.update(payload)
    after = file.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino,
            before.st_dev) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns,
                               after.st_ino, after.st_dev):
        raise OSError("Videodatei hat sich während der Identitätsprüfung geändert")
    return digest.hexdigest()


class FrameSampleCache:
    """Independent cache of pHashes; a hit never bypasses an alignment check."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else default_frame_cache_path()
        self._memory: OrderedDict[str, list[tuple[float, int]]] = OrderedDict()
        self._enabled = False
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name == "posix":
                os.chmod(self.path.parent, 0o700)
            with self._db() as db:
                db.execute("""CREATE TABLE IF NOT EXISTS samples (
                    key TEXT PRIMARY KEY, payload BLOB NOT NULL, accessed_at REAL NOT NULL
                )""")
                db.execute("CREATE INDEX IF NOT EXISTS sample_access_idx ON samples(accessed_at)")
            if os.name == "posix":
                os.chmod(self.path, 0o600)
            self._enabled = True
        except (OSError, sqlite3.Error):
            # Cache must never prevent decoding or export.
            self._enabled = False

    def _db(self):
        from contextlib import contextmanager

        @contextmanager
        def connect():
            connection = sqlite3.connect(self.path, timeout=2.0)
            try:
                with connection:
                    yield connection
            finally:
                connection.close()
        return connect()

    @staticmethod
    def _key(identity: str, duration: float, interval: float, mode: str) -> str:
        value = (SAMPLE_FORMAT_VERSION, identity, round(duration, 6), round(interval, 9), mode)
        return hashlib.sha256(repr(value).encode("ascii")).hexdigest()

    def get(self, identity: str, duration: float, interval: float,
            mode: str = "auto") -> list[tuple[float, int]] | None:
        if not self._enabled:
            return None
        key = self._key(identity, duration, interval, mode)
        if key in self._memory:
            self._memory.move_to_end(key)
            return list(self._memory[key])
        try:
            with self._db() as db:
                row = db.execute("SELECT payload FROM samples WHERE key=?", (key,)).fetchone()
                if row is None:
                    return None
                if len(row[0]) > 250000:
                    raise ValueError("Sample-Cache-Eintrag zu groß")
                decoder = zlib.decompressobj()
                raw = decoder.decompress(row[0], MAX_SAMPLES * 50)
                if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                    raise ValueError("Unvollständiger oder übergroßer Sample-Eintrag")
                pairs = json.loads(raw)
                if not isinstance(pairs, list) or not 4 <= len(pairs) <= MAX_SAMPLES:
                    raise ValueError("Ungültige Anzahl gecachter Samples")
                samples: list[tuple[float, int]] = []
                last = -1.0
                for item in pairs:
                    if not isinstance(item, list) or len(item) != 2:
                        raise ValueError("Ungültige Sample-Struktur")
                    stamp, value = item
                    if (type(stamp) not in (int, float) or not math.isfinite(stamp) or
                            stamp <= last or stamp < 0 or stamp > duration + interval * 2 or
                            type(value) is not int or not 0 <= value < 2**64):
                        raise ValueError("Beschädigter Sample-Eintrag")
                    samples.append((float(stamp), value))
                    last = stamp
                if last < max(0.0, duration - interval * 2):
                    raise ValueError("Sample-Cache deckt das Dateiende nicht ausreichend ab")
                db.execute("UPDATE samples SET accessed_at=? WHERE key=?", (time.time(), key))
            self._remember(key, samples)
            return list(samples)
        except (OSError, sqlite3.Error, ValueError, TypeError, zlib.error, MemoryError):
            try:
                with self._db() as db:
                    db.execute("DELETE FROM samples WHERE key=?", (key,))
            except (OSError, sqlite3.Error):
                pass
            return None

    def _remember(self, key: str, samples: list[tuple[float, int]]) -> None:
        self._memory[key] = list(samples)
        self._memory.move_to_end(key)
        while len(self._memory) > MEMORY_ENTRIES:
            self._memory.popitem(last=False)

    def put(self, identity: str, duration: float, interval: float,
            samples: list[tuple[float, int]], mode: str = "auto") -> bool:
        if not self._enabled or not 4 <= len(samples) <= MAX_SAMPLES:
            return False
        if (not samples or not math.isfinite(samples[-1][0]) or
                samples[-1][0] < max(0.0, duration - interval * 2)):
            # Do not persist an interrupted/truncated but technically readable
            # decode. A later run should retry that media from scratch.
            return False
        key = self._key(identity, duration, interval, mode)
        try:
            payload = zlib.compress(json.dumps(samples, separators=(",", ":"),
                                               allow_nan=False).encode("ascii"), level=3)
            if len(payload) > 250000:
                return False
            with self._db() as db:
                db.execute("INSERT OR REPLACE INTO samples(key,payload,accessed_at) VALUES(?,?,?)",
                           (key, payload, time.time()))
                db.execute("DELETE FROM samples WHERE key IN "
                           "(SELECT key FROM samples ORDER BY accessed_at DESC LIMIT -1 OFFSET ?)",
                           (MAX_ENTRIES,))
            self._remember(key, samples)
            if self.path.stat().st_size > MAX_CACHE_BYTES:
                with self._db() as db:
                    db.execute("DELETE FROM samples WHERE key IN "
                               "(SELECT key FROM samples ORDER BY accessed_at ASC LIMIT ?)",
                               (MAX_ENTRIES // 2,))
                with self._db() as db:
                    db.execute("VACUUM")
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return False

    def stats(self) -> dict[str, int | bool]:
        if not self._enabled:
            return {"available": False, "entries": 0, "bytes": 0}
        try:
            with self._db() as db:
                entries = db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            return {"available": True, "entries": int(entries),
                    "bytes": self.path.stat().st_size}
        except (OSError, sqlite3.Error):
            return {"available": False, "entries": 0, "bytes": 0}
