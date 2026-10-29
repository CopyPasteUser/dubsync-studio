"""Conservative detection of independently proven timeline sections.

Only monotone visual matches are considered. Unmatched master time becomes
silence at export; missing source scenes are never stretched into other scenes.
"""
from __future__ import annotations

import math

import numpy as np

from .models import AlignmentResult, Anchor, TimelineSegment


def detect_piecewise(
    anchors: list[Anchor], source_hashes: dict[float, int],
    source_path: str, target_path: str, source_duration: float,
    target_duration: float, interval: float, sample_count: int,
    requested_interval: float, max_samples: int, hardware_accel: str,
) -> AlignmentResult | None:
    """Propose several affine sections; return only if all pass the safety gate."""
    good = sorted((a for a in anchors if a.hamming_distance <= 12),
                  key=lambda a: a.source_time)
    if len(good) < 16 or source_duration <= 0 or target_duration <= 0:
        return None
    local_slopes = []
    for left, right in zip(good, good[1:]):
        dx, dy = right.source_time - left.source_time, right.target_time - left.target_time
        if 0 < dx <= max(5.0, interval * 4) and dy > 0:
            local_slopes.append(dy / dx)
    if len(local_slopes) < 12:
        return None
    slope = float(np.median(local_slopes))
    if not 0.90 <= slope <= 1.10:
        return None
    offsets = np.asarray([a.target_time - slope * a.source_time for a in good])
    jump_threshold = max(1.25, interval * 1.5)
    stability = max(0.6, interval * 0.80)
    breaks: list[int] = []
    for i in range(8, len(good) - 7):
        if abs(float(offsets[i] - offsets[i - 1])) < jump_threshold:
            continue
        if len(breaks) >= 4:
            return None
        left = offsets[max(i - 6, (breaks[-1] if breaks else 0)):i]
        right = offsets[i:i + 6]
        if len(left) < 6 or len(right) < 6:
            continue
        left_median, right_median = float(np.median(left)), float(np.median(right))
        if (abs(right_median - left_median) < jump_threshold
                or np.max(np.abs(left - left_median)) > stability
                or np.max(np.abs(right - right_median)) > stability):
            continue
        breaks.append(i)
    if not breaks:
        return None
    groups = [good[start:end] for start, end in
              zip([0, *breaks], [*breaks, len(good)])]
    if any(len(group) < 8 for group in groups):
        return None

    sections: list[TimelineSegment] = []
    matched = 0
    tolerance = max(0.75, interval * 1.20)
    for group in groups:
        x = np.asarray([a.source_time for a in group], dtype=np.float64)
        y = np.asarray([a.target_time for a in group], dtype=np.float64)
        shifted = x - x[0]
        fit_slope, fit_intercept = np.linalg.lstsq(
            np.column_stack((shifted, np.ones(len(shifted)))), y, rcond=None,
        )[0]
        if not 0.90 <= fit_slope <= 1.10:
            return None
        residual = y - (fit_slope * shifted + fit_intercept)
        if np.max(np.abs(residual)) > tolerance:
            return None
        unique = len({source_hashes.get(a.source_time) for a in group
                      if a.source_time in source_hashes})
        if unique < 8:
            return None
        start = max(0.0, float(x[0]) - interval * 0.5)
        end = min(source_duration, float(x[-1]) + interval * 0.5)
        target_start = float(fit_intercept + fit_slope * (start - x[0]))
        target_end = float(fit_intercept + fit_slope * (end - x[0]))
        if target_start < 0:
            start += -target_start / fit_slope
            target_start = 0.0
        if target_end > target_duration:
            end -= (target_end - target_duration) / fit_slope
            target_end = target_duration
        if (not all(math.isfinite(v) for v in (start, end, target_start, target_end))
                or end <= start):
            return None
        sections.append(TimelineSegment(
            source_start=start, source_end=end,
            target_start=target_start, target_end=target_end,
            anchor_count=len(group), median_hamming=float(np.median(
                [a.hamming_distance for a in group])),
            rms_error=float(np.sqrt(np.mean(residual ** 2))), distinct_hashes=unique,
        ))
        matched += len(group)

    total_quality = float(np.median([a.hamming_distance for a in good]))
    coverage = matched / max(1, sample_count)
    confidence = min(1.0, 0.42 * min(1.0, coverage) + 0.38
                     + 0.20 * max(0.0, 1.0 - total_quality / 64.0))
    first = sections[0]
    result = AlignmentResult(
        source_path=source_path, target_path=target_path,
        slope=first.slope,
        offset=first.target_start - first.slope * first.source_start,
        anchors=good, confidence=confidence, inlier_ratio=1.0,
        coverage=coverage, median_hamming=total_quality,
        rms_error=max(s.rms_error for s in sections),
        source_duration=source_duration, target_duration=target_duration,
        sample_interval=interval, requested_interval=requested_interval,
        max_samples=max_samples, hardware_accel=hardware_accel,
        visual_distinct_hashes=len({source_hashes[a.source_time] for a in good
                                    if a.source_time in source_hashes}),
        segments=sections, engine_version="phash-piecewise-1.0",
        warnings=["Abschnittsweise Synchronisation: nicht passende Master-Abschnitte bleiben in der neuen Tonspur stumm."],
    )
    return result if result.safe_for_automatic_export else None
