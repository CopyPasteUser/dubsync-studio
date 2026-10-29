"""Sample-cache resilience, invalidation, isolation, and integration contracts."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from dubsync.core.alignment import (AlignmentError, FrameSample,
                                    _sample_with_cache)
from dubsync.core.frame_cache import FrameSampleCache, source_identity


class FrameCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "S01E02.mkv"
        self.file.write_bytes(b"a" * 190000 + b"b" * 190000 + b"c" * 190000)
        self.db = self.root / "cache" / "video-samples.sqlite3"
        self.samples = [(float(i), 0x8000000000000000 + i) for i in range(12)]

    def test_cache_persists_and_is_keyed_by_mode_and_interval(self):
        fingerprint = source_identity(str(self.file))
        one = FrameSampleCache(self.db)
        self.assertTrue(one.put(fingerprint, 12, 1.0, self.samples, "none"))
        two = FrameSampleCache(self.db)
        self.assertEqual(two.get(fingerprint, 12, 1.0, "none"), self.samples)
        self.assertIsNone(two.get(fingerprint, 12, 0.5, "none"))
        self.assertIsNone(two.get(fingerprint, 12, 1.0, "vaapi"))
        self.assertEqual(two.stats()["entries"], 1)
        if os.name == "posix":
            self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)

    def test_identity_changes_when_content_changes_even_with_same_mtime_and_size(self):
        first = source_identity(str(self.file))
        previous = self.file.stat()
        with self.file.open("r+b") as handle:
            handle.seek(210000)
            handle.write(b"CHANGED")
        os.utime(self.file, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.assertNotEqual(source_identity(str(self.file)), first)

    def test_other_episode_path_does_not_share_samples(self):
        duplicate = self.root / "S01E03.mkv"
        duplicate.write_bytes(self.file.read_bytes())
        original = source_identity(str(self.file))
        other = source_identity(str(duplicate))
        self.assertNotEqual(original, other)
        cache = FrameSampleCache(self.db)
        self.assertTrue(cache.put(original, 12, 1, self.samples))
        self.assertIsNone(cache.get(other, 12, 1))

    def test_truncated_decode_is_never_persisted(self):
        identity = source_identity(str(self.file))
        cache = FrameSampleCache(self.db)
        self.assertFalse(cache.put(identity, 60, 1, self.samples))
        self.assertEqual(cache.stats()["entries"], 0)

    def test_corrupt_entry_becomes_cache_miss(self):
        identity = source_identity(str(self.file))
        cache = FrameSampleCache(self.db)
        self.assertTrue(cache.put(identity, 12, 1.0, self.samples))
        key = cache._key(identity, 12, 1.0, "auto")
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE samples SET payload=? WHERE key=?", (b"not-zlib-data", key))
        self.assertIsNone(FrameSampleCache(self.db).get(identity, 12, 1.0))
        self.assertEqual(cache.stats()["entries"], 0)

    def test_alignment_cache_hit_skips_decode_not_alignment(self):
        cache = FrameSampleCache(self.db)
        generated = [FrameSample(stamp, value) for stamp, value in self.samples]
        with mock.patch("dubsync.core.alignment.sample_video", return_value=generated) as decode:
            first = _sample_with_cache(str(self.file), 12, 1.0, None, "Quellvideo",
                                       None, "none", None, cache)
            self.assertEqual(first, generated)
            second_cache = FrameSampleCache(self.db)
            second = _sample_with_cache(str(self.file), 12, 1.0, None, "Quellvideo",
                                        None, "none", None, second_cache)
            self.assertEqual(second, generated)
            decode.assert_called_once()

    def test_cancellation_stops_cached_read(self):
        identity = source_identity(str(self.file))
        cache = FrameSampleCache(self.db)
        cache.put(identity, 12, 1.0, self.samples, "none")
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(AlignmentError, "abgebrochen"):
            _sample_with_cache(str(self.file), 12, 1.0, None, "Quellvideo",
                               cancelled, "none", None, cache)

    def test_changed_source_during_decode_is_rejected(self):
        cache = FrameSampleCache(self.db)
        generated = [FrameSample(stamp, value) for stamp, value in self.samples]

        def change_content(*_args, **_kwargs):
            with self.file.open("r+b") as handle:
                handle.seek(200000)
                handle.write(b"new-content")
            return generated

        with mock.patch("dubsync.core.alignment.sample_video", side_effect=change_content):
            with self.assertRaisesRegex(AlignmentError, "verändert"):
                _sample_with_cache(str(self.file), 12, 1.0, None, "Referenzvideo",
                                   None, "none", None, cache)


if __name__ == "__main__":
    unittest.main()
