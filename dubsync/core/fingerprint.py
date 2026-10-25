"""Cached sampled file fingerprints that avoid full-file decoding or hashing.

The signature reads only a few small windows near the beginning, middle and end of
an input. It is a fast identity hint for caches, not a cryptographic full-file hash.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


def default_fingerprint_cache_path() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return root / "dubsync-studio" / "fingerprints.sqlite3"


_DB_LOCK = threading.RLock()
_MEMORY: dict[tuple[str, int, int, int], str] = {}
_MEMORY_LIMIT = 4096


@contextmanager
def _db_connection(path: Path):
    connection = sqlite3.connect(path, timeout=2.0)
    try:
        connection.execute("PRAGMA busy_timeout=2000")
        with connection:
            yield connection
    finally:
        connection.close()


def _signature(path: str | os.PathLike[str]) -> tuple[str, int, int, int, int]:
    file_path = Path(path).expanduser()
    stat = file_path.stat()
    return (os.path.abspath(os.fspath(file_path)), int(stat.st_size), int(stat.st_mtime_ns),
            int(getattr(stat, "st_ino", 0)), int(getattr(stat, "st_ctime_ns", 0)))


def quick_fingerprint(path: str | os.PathLike[str], sample_bytes: int = 32768,
                       cache_path: str | os.PathLike[str] | None = None) -> str:
    """Return a cached BLAKE2 fingerprint sampled from up to three file windows.

    Only about 96 KiB is read for a large file by default. A stat signature and
    SQLite cache make subsequent checks cheap, including on mounted NAS shares.
    """
    sample_bytes = max(4096, min(262144, int(sample_bytes)))
    sig = _signature(path)
    cached = _MEMORY.get((*sig, sample_bytes))
    if cached:
        return cached
    cache_file = Path(cache_path) if cache_path else default_fingerprint_cache_path()
    cache_key = (*sig, sample_bytes)

    try:
        with _DB_LOCK:
            cache_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(cache_file.parent, 0o700)
            except OSError:
                pass
            with _db_connection(cache_file) as db:
                db.execute("CREATE TABLE IF NOT EXISTS fingerprints (path TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, inode INTEGER NOT NULL, ctime_ns INTEGER NOT NULL DEFAULT 0, sample_bytes INTEGER NOT NULL, digest TEXT NOT NULL, accessed_at REAL NOT NULL, PRIMARY KEY(path,size,mtime_ns,inode,ctime_ns,sample_bytes))")
                # Migrate the pre-release schema in place when this helper is upgraded.
                columns = {row[1] for row in db.execute("PRAGMA table_info(fingerprints)")}
                if "ctime_ns" not in columns:
                    db.execute("ALTER TABLE fingerprints ADD COLUMN ctime_ns INTEGER NOT NULL DEFAULT 0")
                row = db.execute("SELECT digest FROM fingerprints WHERE path=? AND size=? AND mtime_ns=? AND inode=? AND ctime_ns=? AND sample_bytes=?", cache_key).fetchone()
                if row:
                    digest = str(row[0])
                    db.execute("UPDATE fingerprints SET accessed_at=? WHERE path=? AND size=? AND mtime_ns=? AND inode=? AND ctime_ns=? AND sample_bytes=?", (time.time(), *cache_key))
                    _MEMORY[cache_key] = digest
                    return digest
    except (OSError, sqlite3.Error):
        # A failed cache remains a cache miss; fingerprinting still works in-memory.
        pass

    absolute, size, _mtime, _inode, _ctime = sig
    digest = hashlib.blake2b(digest_size=20)
    digest.update(f"{size}:{sample_bytes}".encode("ascii"))
    offsets = sorted({0, max(0, (size // 2) - (sample_bytes // 2)), max(0, size - sample_bytes)})
    with open(absolute, "rb", buffering=0) as stream:
        for offset in offsets:
            stream.seek(offset)
            data = stream.read(sample_bytes)
            digest.update(offset.to_bytes(8, "little", signed=False))
            digest.update(len(data).to_bytes(4, "little", signed=False))
            digest.update(data)
    value = digest.hexdigest()
    _MEMORY[cache_key] = value
    if len(_MEMORY) > _MEMORY_LIMIT:
        # Dict insertion order is stable on supported Python versions.
        for old_key in list(_MEMORY)[: len(_MEMORY) - _MEMORY_LIMIT]:
            _MEMORY.pop(old_key, None)
    try:
        with _DB_LOCK, _db_connection(cache_file) as db:
            db.execute("CREATE TABLE IF NOT EXISTS fingerprints (path TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, inode INTEGER NOT NULL, ctime_ns INTEGER NOT NULL DEFAULT 0, sample_bytes INTEGER NOT NULL, digest TEXT NOT NULL, accessed_at REAL NOT NULL, PRIMARY KEY(path,size,mtime_ns,inode,ctime_ns,sample_bytes))")
            columns = {row[1] for row in db.execute("PRAGMA table_info(fingerprints)")}
            if "ctime_ns" not in columns:
                db.execute("ALTER TABLE fingerprints ADD COLUMN ctime_ns INTEGER NOT NULL DEFAULT 0")
            db.execute("INSERT OR REPLACE INTO fingerprints(path,size,mtime_ns,inode,ctime_ns,sample_bytes,digest,accessed_at) VALUES(?,?,?,?,?,?,?,?)", (*cache_key, value, time.time()))
            db.execute("DELETE FROM fingerprints WHERE rowid IN (SELECT rowid FROM fingerprints ORDER BY accessed_at DESC LIMIT -1 OFFSET 12000)")
        try:
            os.chmod(cache_file, 0o600)
        except OSError:
            pass
    except (OSError, sqlite3.Error):
        pass
    return value


def fingerprint_many(paths: list[str], max_workers: int = 4, progress=None, cancel_event=None,
                     cache_path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Calculate sampled file signatures concurrently with bounded worker count."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    ordered = list(dict.fromkeys(str(Path(p).expanduser()) for p in paths))
    results: dict[str, str] = {}
    if not ordered:
        return results
    workers = max(1, min(int(max_workers), len(ordered), 8))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dubsync-fp") as pool:
        futures = {pool.submit(quick_fingerprint, path, 32768, cache_path): path for path in ordered}
        for done, future in enumerate(as_completed(futures), 1):
            if cancel_event is not None and cancel_event.is_set():
                for queued in futures:
                    queued.cancel()
                raise RuntimeError("Fingerprint-Scan abgebrochen.")
            path = futures[future]
            results[path] = future.result()
            if progress:
                progress(done, len(ordered), Path(path).name)
    return results
