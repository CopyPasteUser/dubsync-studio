"""Pure timeline projection contracts; no Qt or media access required."""
from types import SimpleNamespace as NS
import unittest

from dubsync.gui.timeline_index import TimelineIndex


def anchor(source, target, distance=0):
    return NS(source_time=source, target_time=target, hamming_distance=distance)


def alignment(anchors, segments=(), mapping=lambda t: t + 10):
    return NS(anchors=anchors, segments=segments, sample_interval=.5,
              source_duration=100, target_time=mapping)


class TimelineIndexTests(unittest.TestCase):
    def test_empty(self):
        index = TimelineIndex()
        self.assertEqual(index.visible_anchors(0, 100), ())
        self.assertEqual(index.segments, ())

    def test_filters_and_inclusive_sorted_bounds(self):
        index = TimelineIndex(alignment([
            anchor(20, 30), anchor(0, 10), anchor(10, 20), anchor(10, 20),
            anchor(-1, 9), anchor(101, 111), anchor(8, 50), anchor(5, 15, 16),
            anchor(4, float('nan')),
        ]))
        self.assertEqual(index.visible_anchors(10, 20), (10, 20, 20))
        self.assertEqual(index.visible_anchors(21, 29), ())
        self.assertEqual(index.visible_anchors(30, 30), (30,))

    def test_disabled_and_unmapped_segments_do_not_draw_anchors(self):
        segments = [NS(enabled=True, source_start=0, source_end=10,
                       target_start=10, target_end=20),
                    NS(enabled=False, source_start=20, source_end=30,
                       target_start=30, target_end=40)]
        index = TimelineIndex(alignment([anchor(5, 15), anchor(15, 25), anchor(25, 35)], segments))
        self.assertEqual(index.segments, ((10, 20),))
        self.assertEqual(index.visible_anchors(0, 100), (15,))

    def test_repeated_viewport_queries_do_not_recompute_mapping(self):
        calls = []
        def mapping(t):
            calls.append(t)
            return t + 10
        index = TimelineIndex(alignment([anchor(t, t + 10) for t in range(100)], mapping=mapping))
        self.assertEqual(len(calls), 100)
        for start in range(100):
            index.visible_anchors(start, start + 8)
        self.assertEqual(len(calls), 100)

    def test_rebuild_reflects_changed_mapping(self):
        data = alignment([anchor(1, 11)])
        self.assertEqual(TimelineIndex(data).anchor_times, (11,))
        data.target_time = lambda t: t + 20
        self.assertEqual(TimelineIndex(data).anchor_times, ())


if __name__ == '__main__':
    unittest.main()
