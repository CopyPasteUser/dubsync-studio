"""Exercise the real SQLite and memory sample cache independently of the aligner."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from dubsync.core.frame_cache import FrameSampleCache, source_identity, MEMORY_ENTRIES


class EpisodeSampleCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / 'samples.sqlite3'
        self.cache = FrameSampleCache(self.db)
        self.samples = [(float(i), i) for i in range(12)]

    def test_memory_hit_avoids_database_and_returns_copy(self):
        self.assertTrue(self.cache.put('master', 12, 1, self.samples, 'ffmpeg'))
        with patch.object(self.cache, '_db', side_effect=AssertionError('unexpected disk read')):
            found = self.cache.get('master', 12, 1, 'ffmpeg')
            found.clear()
            self.assertEqual(self.cache.get('master', 12, 1, 'ffmpeg'), self.samples)
        self.assertEqual(FrameSampleCache(self.db).get('master', 12, 1, 'ffmpeg'), self.samples)

    def test_reject_corrupt_put_before_memory_or_disk_admission(self):
        self.assertFalse(self.cache.put('master', 12, 1, self.samples + [(11., 9)]))
        self.assertIsNone(self.cache.get('master', 12, 1))
        self.assertEqual(self.cache.stats()['entries'], 0)

    def test_memory_bound_and_recipe_separation(self):
        for i in range(MEMORY_ENTRIES + 3):
            self.assertTrue(self.cache.put(str(i), 12, 1, self.samples))
        self.assertEqual(len(self.cache._memory), MEMORY_ENTRIES)
        self.assertIsNone(self.cache.get('1', 12, .5))
        self.assertIsNone(self.cache.get('1', 12, 1, 'cuda'))
        self.assertEqual(self.cache.get('1', 12, 1), self.samples)
        self.assertEqual(len(self.cache._memory), MEMORY_ENTRIES)

    def test_identity_change_does_not_hit_memory(self):
        path = self.root / 'master.mkv'
        path.write_bytes(b'a' * 400000)
        identity = source_identity(str(path))
        self.assertTrue(self.cache.put(identity, 12, 1, self.samples))
        before = path.stat()
        with path.open('r+b') as handle:
            handle.seek(0)
            handle.write(b'changed')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        changed = source_identity(str(path))
        self.assertNotEqual(identity, changed)
        self.assertIsNone(self.cache.get(changed, 12, 1))
