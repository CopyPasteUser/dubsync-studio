"""Read-only planning for bounded, reference-aligned listening windows."""
from __future__ import annotations

import math
from typing import Any, Mapping


def plan_audition_window(
    track: Mapping[str, Any], target_time: float, requested_seconds: float,
) -> tuple[float, float, float] | None:
    """Return (source start, source seconds, target/source rate), or no coverage.

    A verified cut has its own local slope. Never cross its boundary or a
    source boundary, including when the global time correction is non-unit.
    """
    try:
        target = float(target_time)
        requested = float(requested_seconds)
        source_end = float(track.get("duration", 0))
        ratio = 1.0 if track.get("master") else float(track.get("scale", 1))
        offset = 0.0 if track.get("master") else float(track.get("offset", 0))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(number) for number in (target, requested, source_end, ratio, offset)):
        return None
    if target < 0 or requested < 0.3 or source_end <= 0 or not 0.5 <= ratio <= 2.0:
        return None

    segments = track.get("segments") or []
    if segments and not track.get("master"):
        piece = next((part for part in segments
                      if part.enabled and part.target_start <= target < part.target_end), None)
        if piece is None or piece.slope <= 0:
            return None
        ratio = piece.slope
        source_start = piece.source_time_for_target(target)
        if source_start is None:
            return None
        seconds = min(requested, (piece.target_end - target) / ratio,
                      piece.source_end - source_start)
    else:
        source_start = (target - offset) / ratio
        seconds = requested
    if not math.isfinite(source_start) or not math.isfinite(seconds):
        return None
    seconds = min(seconds, source_end - source_start, 60.0)
    if source_start < 0 or seconds < 0.3:
        return None
    return source_start, seconds, ratio
