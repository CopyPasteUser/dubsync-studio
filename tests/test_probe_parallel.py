from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from dubsync.core.models import MediaInfo
from dubsync.core.probe import MediaToolError, probe_many


class ParallelProbeTests(unittest.TestCase):
    def test_probe_many_keeps_input_order_while_probing_in_parallel(self):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_probe(path):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03 if path.endswith("a.mkv") else 0.01)
            with lock:
                active -= 1
            return MediaInfo(path, 10.0, "matroska", 100, [], 1, 0, 0)

        progress = []
        with patch("dubsync.core.probe.probe_media", side_effect=fake_probe):
            result = probe_many(["a.mkv", "b.mkv", "c.mkv"], max_workers=3,
                                progress=lambda done, total, name: progress.append((done, total, name)))
        self.assertEqual([item.path for item in result], ["a.mkv", "b.mkv", "c.mkv"])
        self.assertGreater(max_active, 1)
        self.assertEqual(len(progress), 3)

    def test_probe_many_fails_with_filename_when_a_probe_fails(self):
        def fake_probe(path):
            if path == "bad.mkv":
                raise MediaToolError("not a media file")
            return MediaInfo(path, 10.0, "matroska", 100, [], 1, 0, 0)
        with patch("dubsync.core.probe.probe_media", side_effect=fake_probe):
            with self.assertRaisesRegex(MediaToolError, "bad.mkv"):
                probe_many(["ok.mkv", "bad.mkv"], max_workers=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
