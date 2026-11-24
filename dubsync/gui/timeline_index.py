"""Read-only projection of alignment anchors for timeline rendering.

This is display data only and must never be used to authorize an export.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from math import isfinite


class TimelineIndex:
    """Rebuilt when the review receives new alignment data, not on paint."""

    def __init__(self, alignment=None):
        self.anchor_times: tuple[float, ...] = ()
        self.segments: tuple[tuple[float, float], ...] = ()
        if alignment is None:
            return
        active = tuple(segment for segment in alignment.segments if segment.enabled)
        self.segments = tuple((s.target_start, s.target_end) for s in active)
        tolerance = max(.75, alignment.sample_interval * 1.2)
        times = []
        for anchor in alignment.anchors:
            if anchor.hamming_distance > 15:
                continue
            if (not 0 <= anchor.source_time <= alignment.source_duration or
                    not isfinite(anchor.target_time) or
                    abs(anchor.target_time - alignment.target_time(anchor.source_time)) > tolerance):
                continue
            if alignment.segments and not any(
                s.source_start <= anchor.source_time <= s.source_end and
                s.target_start <= anchor.target_time <= s.target_end for s in active
            ):
                continue
            times.append(anchor.target_time)
        self.anchor_times = tuple(sorted(times))

    def visible_anchors(self, start: float, end: float) -> tuple[float, ...]:
        """Return measured anchor positions inside inclusive viewport bounds."""
        lo = bisect_left(self.anchor_times, start)
        hi = bisect_right(self.anchor_times, end)
        return self.anchor_times[lo:hi]
