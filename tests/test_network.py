from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dubsync.core.network import parse_mount_table, network_mount_for_path, is_network_path
from dubsync.core.fingerprint import quick_fingerprint, fingerprint_many


class NetworkMountTests(unittest.TestCase):
    def test_mount_table_detects_cifs_nfs_and_decodes_escaped_spaces(self):
        rows = parse_mount_table(
            "//nas/Medien /mnt/Serien\\040Archiv cifs rw,relatime 0 0\n"
            "server:/tv /media/tv nfs4 rw,relatime 0 0\n"
            "/dev/sda1 / ext4 rw,relatime 0 0\n"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["filesystem"], "cifs")
        self.assertEqual(rows[0]["mountpoint"], "/mnt/Serien Archiv")
        self.assertEqual(rows[1]["filesystem"], "nfs4")

    def test_deepest_mount_for_path_wins(self):
        mounts = [
            {"source": "nas", "mountpoint": "/mnt", "filesystem": "cifs"},
            {"source": "nested", "mountpoint": "/mnt/series", "filesystem": "nfs"},
        ]
        self.assertEqual(network_mount_for_path("/mnt/series/S01E01.mkv", mounts)["source"], "nested")
        self.assertIsNone(network_mount_for_path("/home/user/video.mkv", mounts))

    def test_unc_paths_are_network_even_without_linux_mount_table(self):
        self.assertTrue(is_network_path(r"\\server\share\episode.mkv"))
        self.assertTrue(is_network_path("//server/share/episode.mkv"))


class QuickFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_stable_cached_and_invalidated_by_file_signature(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cache = base / "fingerprints.sqlite3"
            path = base / "movie.mkv"
            path.write_bytes(b"A" * 200_000 + b"B" * 100_000 + b"C" * 200_000)
            first = quick_fingerprint(path, cache_path=cache)
            self.assertEqual(first, quick_fingerprint(path, cache_path=cache))
            path.write_bytes(b"Z" * 200_000 + b"B" * 100_000 + b"C" * 200_000)
            second = quick_fingerprint(path, cache_path=cache)
            self.assertNotEqual(first, second)
            self.assertTrue(cache.exists())

    def test_same_size_edit_with_restored_mtime_refreshes_the_fingerprint(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "media.mkv"
            path.write_bytes(b"A" * 200_000)
            first_stat = path.stat()
            cache = Path(td) / "fp.sqlite3"
            before = quick_fingerprint(path, cache_path=cache)
            path.write_bytes(b"B" * 200_000)
            os.utime(path, ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns))
            after = quick_fingerprint(path, cache_path=cache)
            self.assertNotEqual(before, after)

    def test_fingerprint_many_preserves_input_keys_and_reports_progress(self):
        with tempfile.TemporaryDirectory() as td:
            paths = []
            for name in ("a.mkv", "b.mkv", "c.mkv"):
                path = Path(td) / name
                path.write_bytes(name.encode("utf-8") * 5000)
                paths.append(str(path))
            progress = []
            with_cache = Path(td) / "cache.sqlite3"
            result = fingerprint_many(paths, max_workers=2,
                                      progress=lambda done, total, name: progress.append((done, total, name)),
                                      cache_path=with_cache)
            self.assertEqual(set(result), set(paths))
            self.assertEqual(len(progress), 3)
            self.assertEqual(progress[-1][0], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
