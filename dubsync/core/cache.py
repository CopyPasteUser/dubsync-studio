"""Bounded SQLite-backed alignment cache with safe legacy-JSON migration.

The former implementation rewrote one large JSON document for every alignment
result. For a full series this creates quadratic disk I/O. SQLite stores each
result independently, supports bounded LRU-style retention, and keeps the cache
private to the current user on POSIX systems.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .models import AlignmentResult
from .fingerprint import quick_fingerprint

CACHE_SCHEMA_VERSION = 1
MAX_CACHE_ENTRIES = 1200
MAX_CACHE_FILE_BYTES = 64 * 1024 * 1024
MEMORY_CACHE_ENTRIES = 48
MAX_LEGACY_JSON_BYTES = 64 * 1024 * 1024


def default_cache_path() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "dubsync-studio"
    return root / "alignments.sqlite3"


class AlignmentCache:
    def __init__(self, path: Path | None = None):
        using_default = path is None
        self.path = Path(path or default_cache_path())
        self._memory: OrderedDict[str, AlignmentResult] = OrderedDict()
        self._persistent_available = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            legacy_path = self.path.with_name("alignments.json") if using_default else None
            if self.path.exists() and not self._is_sqlite_file():
                self._migrate_legacy_json()
            elif legacy_path is not None and legacy_path.is_file() and not self.path.exists():
                self._initialize_store()
                migrated = self._import_legacy_json(legacy_path)
                if migrated:
                    try:
                        legacy_path.unlink()
                    except OSError:
                        pass
                else:
                    # Keep an unreadable legacy file for troubleshooting instead of
                    # deleting it, while continuing with an empty SQLite cache.
                    try:
                        backup = legacy_path.with_name(legacy_path.name + f".unreadable-{int(time.time())}")
                        os.replace(legacy_path, backup)
                    except OSError:
                        pass
            self._initialize_store()
            self._persistent_available = True
        except (OSError, sqlite3.Error, ValueError, TypeError):
            # Cache failure must never make media analysis unavailable.
            self._persistent_available = False

    def _is_sqlite_file(self) -> bool:
        try:
            with self.path.open("rb") as stream:
                return stream.read(16) == b"SQLite format 3\x00"
        except OSError:
            return False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=4.0)
        connection.execute("PRAGMA busy_timeout=4000")
        return connection

    @contextmanager
    def _db(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_store(self) -> None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("""CREATE TABLE IF NOT EXISTS alignments (
                cache_key TEXT PRIMARY KEY,
                result_json TEXT NOT NULL,
                accessed_at REAL NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS alignments_access_idx ON alignments(accessed_at)")
            db.execute("PRAGMA user_version=1")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _import_legacy_json(self, source_path: Path) -> bool:
        try:
            if source_path.stat().st_size > MAX_LEGACY_JSON_BYTES:
                return False
            value = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, RecursionError):
            return False
        if not isinstance(value, dict):
            return False
        now = time.time()
        rows: list[tuple[str, str, float]] = []
        for key, raw in list(value.items())[-MAX_CACHE_ENTRIES:]:
            if not isinstance(key, str) or not isinstance(raw, dict):
                continue
            try:
                result = AlignmentResult.from_dict(raw)
                encoded = json.dumps(result.to_dict(), ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False)
            except (KeyError, TypeError, ValueError):
                continue
            rows.append((key, encoded, now))
        try:
            with self._db() as db:
                db.executemany(
                    "INSERT OR REPLACE INTO alignments(cache_key,result_json,accessed_at) VALUES(?,?,?)",
                    rows,
                )
                self._prune(db)
            return True
        except (OSError, sqlite3.Error):
            return False

    def _migrate_legacy_json(self) -> None:
        """Convert the previous JSON cache atomically; retain a backup if invalid."""
        if not self.path.is_file():
            return
        legacy: dict[str, Any] = {}
        valid_legacy = False
        try:
            if self.path.stat().st_size <= MAX_LEGACY_JSON_BYTES:
                parsed = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    legacy = parsed
                    valid_legacy = True
        except (OSError, UnicodeError, ValueError, RecursionError):
            valid_legacy = False

        if not valid_legacy:
            # A cache is disposable, but retain a corrupt legacy file for support.
            try:
                suffix = f".corrupt-{int(time.time())}"
                os.replace(self.path, self.path.with_name(self.path.name + suffix))
            except OSError:
                return

        temporary: str | None = None
        fd = -1
        try:
            fd, temporary = tempfile.mkstemp(prefix=".alignments-migrate-", dir=self.path.parent)
            os.close(fd)
            fd = -1
            # Create the SQLite schema in a separate file before replacing the legacy cache.
            connection = sqlite3.connect(temporary, timeout=4.0)
            try:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=NORMAL")
                connection.execute("""CREATE TABLE alignments (
                    cache_key TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    accessed_at REAL NOT NULL
                )""")
                connection.execute("CREATE INDEX alignments_access_idx ON alignments(accessed_at)")
                connection.execute("PRAGMA user_version=1")
                now = time.time()
                migrated: list[tuple[str, str, float]] = []
                if valid_legacy:
                    for key, value in list(legacy.items())[-MAX_CACHE_ENTRIES:]:
                        if not isinstance(key, str) or not isinstance(value, dict):
                            continue
                        try:
                            result = AlignmentResult.from_dict(value)
                            payload = json.dumps(result.to_dict(), ensure_ascii=False,
                                                 separators=(",", ":"), allow_nan=False)
                        except (KeyError, TypeError, ValueError):
                            continue
                        migrated.append((key, payload, now))
                connection.executemany(
                    "INSERT OR REPLACE INTO alignments(cache_key,result_json,accessed_at) VALUES(?,?,?)",
                    migrated,
                )
                connection.commit()
            finally:
                connection.close()
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            temporary = None
        except (OSError, sqlite3.Error):
            # Preserve malformed/oversized legacy state and continue in memory only.
            return
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    @staticmethod
    def _sig(path: str) -> dict[str, Any]:
        stat = Path(path).stat()
        # Size and mtime are cheap but can be preserved by some copy/restore tools.
        # A cached small-window content fingerprint adds identity without decoding
        # video frames or reading the complete file, including on NAS sources.
        return {
            "path": str(Path(path).resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sample_fingerprint": quick_fingerprint(path),
        }

    def key(self, source: str, target: str, interval: float,
            max_samples: int = 1800, hardware_accel: str = "auto") -> str:
        import hashlib
        value = {
            "source": self._sig(source), "target": self._sig(target),
            "interval": float(interval), "max_samples": int(max_samples),
            "hardware_accel": hardware_accel, "version": 4,
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def _remember(self, cache_key: str, result: AlignmentResult) -> None:
        self._memory[cache_key] = result
        self._memory.move_to_end(cache_key)
        while len(self._memory) > MEMORY_CACHE_ENTRIES:
            self._memory.popitem(last=False)

    def get(self, source: str, target: str, interval: float = 0.5,
            max_samples: int = 1800, hardware_accel: str = "auto") -> AlignmentResult | None:
        try:
            cache_key = self.key(source, target, interval, max_samples, hardware_accel)
        except (OSError, ValueError, TypeError):
            return None
        cached = self._memory.get(cache_key)
        if cached is not None:
            self._memory.move_to_end(cache_key)
            return cached
        if not self._persistent_available or not self.path.exists():
            return None
        try:
            with self._db() as db:
                row = db.execute(
                    "SELECT result_json FROM alignments WHERE cache_key=?", (cache_key,)
                ).fetchone()
                if row is None:
                    return None
                payload = json.loads(row[0])
                result = AlignmentResult.from_dict(payload)
                db.execute(
                    "UPDATE alignments SET accessed_at=? WHERE cache_key=?",
                    (time.time(), cache_key),
                )
            self._remember(cache_key, result)
            return result
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError):
            # A malformed row is a cache miss, never a failed analysis.
            try:
                with self._db() as db:
                    db.execute("DELETE FROM alignments WHERE cache_key=?", (cache_key,))
            except (OSError, sqlite3.Error):
                pass
            return None

    def _prune(self, db: sqlite3.Connection) -> None:
        count = int(db.execute("SELECT COUNT(*) FROM alignments").fetchone()[0])
        excess = count - MAX_CACHE_ENTRIES
        if excess > 0:
            db.execute(
                "DELETE FROM alignments WHERE cache_key IN "
                "(SELECT cache_key FROM alignments ORDER BY accessed_at ASC, rowid ASC LIMIT ?)",
                (excess,),
            )

    def put(self, result: AlignmentResult) -> None:
        try:
            cache_key = self.key(
                result.source_path, result.target_path, result.requested_interval,
                result.max_samples, result.hardware_accel,
            )
            payload = json.dumps(result.to_dict(), ensure_ascii=False,
                                 separators=(",", ":"), allow_nan=False)
        except (OSError, TypeError, ValueError):
            return
        self._remember(cache_key, result)
        if not self._persistent_available:
            return
        try:
            with self._db() as db:
                db.execute(
                    "INSERT OR REPLACE INTO alignments(cache_key,result_json,accessed_at) VALUES(?,?,?)",
                    (cache_key, payload, time.time()),
                )
                self._prune(db)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            if self.path.stat().st_size > MAX_CACHE_FILE_BYTES:
                # Rare compaction path: keep only the most recently used half and
                # reclaim freed pages. Normal inserts remain O(one result), not a
                # rewrite of the entire cache file.
                with self._db() as db:
                    db.execute(
                        "DELETE FROM alignments WHERE cache_key IN "
                        "(SELECT cache_key FROM alignments ORDER BY accessed_at ASC, rowid ASC LIMIT ?)",
                        (max(1, MAX_CACHE_ENTRIES // 2),),
                    )
                with self._db() as db:
                    db.execute("VACUUM")
        except (OSError, sqlite3.Error):
            # Cache persistence is opportunistic; it must not fail the current job.
            self._persistent_available = False

    def clear(self) -> None:
        self._memory.clear()
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                Path(str(self.path) + suffix).unlink(missing_ok=True)
            except OSError:
                pass
        self._persistent_available = False

    @property
    def size(self) -> int:
        if not self._persistent_available or not self.path.exists():
            return len(self._memory)
        try:
            with self._db() as db:
                return int(db.execute("SELECT COUNT(*) FROM alignments").fetchone()[0])
        except (OSError, sqlite3.Error):
            return len(self._memory)
