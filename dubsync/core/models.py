from __future__ import annotations

from dataclasses import dataclass, field, asdict
import math
from pathlib import Path
from typing import Any


MAX_UNVERIFIED_MIDDLE_RATIO = 0.20
MAX_UNVERIFIED_EDGE_RATIO = 0.15


@dataclass(slots=True)
class StreamInfo:
    index: int
    codec_type: str
    codec_name: str = "unknown"
    language: str = "und"
    title: str = ""
    duration: float | None = None
    channels: int | None = None
    sample_rate: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    disposition: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MediaInfo:
    path: str
    duration: float
    format_name: str
    size_bytes: int
    streams: list[StreamInfo]
    video_streams: int
    audio_streams: int
    subtitle_streams: int

    @property
    def audio(self) -> list[StreamInfo]:
        return [stream for stream in self.streams if stream.codec_type == "audio"]

    @property
    def video(self) -> list[StreamInfo]:
        return [stream for stream in self.streams if stream.codec_type == "video"]


@dataclass(slots=True)
class Anchor:
    source_time: float
    target_time: float
    hamming_distance: int


@dataclass(slots=True)
class TimelineSegment:
    """A visually confirmed source interval mapped into the master timeline."""
    source_start: float
    source_end: float
    target_start: float
    target_end: float
    anchor_count: int
    median_hamming: float
    rms_error: float
    distinct_hashes: int
    enabled: bool = True

    @property
    def slope(self) -> float:
        span = self.source_end - self.source_start
        return (self.target_end - self.target_start) / span if span > 0 else 0.0

    def source_time_for_target(self, target_time: float) -> float | None:
        if self.slope <= 0 or not self.target_start <= target_time <= self.target_end:
            return None
        return self.source_start + (target_time - self.target_start) / self.slope


@dataclass(slots=True)
class AlignmentResult:
    source_path: str
    target_path: str
    slope: float
    offset: float
    anchors: list[Anchor]
    confidence: float
    inlier_ratio: float
    coverage: float
    median_hamming: float
    rms_error: float
    source_duration: float
    target_duration: float
    sample_interval: float
    warnings: list[str] = field(default_factory=list)
    engine_version: str = "phash-dtw-0.4"
    requested_interval: float = 0.5
    max_samples: int = 1800
    hardware_accel: str = "auto"
    # Minimum number of distinct visual frame hashes seen on either input.
    # Old tests/manual results default to a sufficiently diverse stream.
    visual_distinct_hashes: int = 10
    segments: list[TimelineSegment] = field(default_factory=list)
    # Edited sections are not independently verified by the visual matcher.
    manual_segments: bool = False

    def target_time(self, source_time: float) -> float:
        for segment in self.segments:
            if segment.enabled and segment.source_start <= source_time <= segment.source_end:
                return segment.target_start + segment.slope * (source_time - segment.source_start)
        return self.slope * source_time + self.offset

    def source_time(self, target_time: float) -> float | None:
        if self.segments:
            for segment in self.segments:
                if not segment.enabled:
                    continue
                source = segment.source_time_for_target(target_time)
                if source is not None:
                    return source
            return None  # The master has an unpaired scene: audio is intentionally silent.
        return (target_time - self.offset) / self.slope if self.slope > 0 else None

    @property
    def mapped_overlap_ratio(self) -> float:
        if self.segments:
            return sum(s.target_end - s.target_start for s in self.segments if s.enabled) / max(self.target_duration, 0.001)
        start = self.offset
        end = self.slope * self.source_duration + self.offset
        overlap = max(0.0, min(self.target_duration, end) - max(0.0, start))
        return overlap / max(self.target_duration, 0.001)

    def _verified_anchor_times(self) -> tuple[float, float, list[float]]:
        """Trusted source timestamps inside the actual shared media interval."""
        if not math.isfinite(self.slope) or self.slope <= 0:
            return 0.0, 0.0, []
        start = max(0.0, -self.offset / self.slope)
        end = min(self.source_duration, (self.target_duration - self.offset) / self.slope)
        span = end - start
        if not math.isfinite(span) or span <= 0:
            return 0.0, 0.0, []
        tolerance = max(0.75, self.sample_interval * 1.20)
        times = sorted(a.source_time for a in self.anchors
                       if start <= a.source_time <= end and a.hamming_distance <= 15
                       and abs(a.target_time - self.target_time(a.source_time)) <= tolerance)
        return start, end, times

    @property
    def max_unverified_middle_ratio(self) -> float:
        """Largest gap between trustworthy anchors in the shared timeline."""
        if self.segments:
            if not all(segment.enabled for segment in self.segments):
                return 1.0
            gaps = [max(0.0, right.target_start - left.target_end)
                    for left, right in zip(self.segments, self.segments[1:])]
            for segment in self.segments:
                times = self._segment_anchor_target_times(segment)
                if len(times) < 2:
                    return 1.0
                gaps.extend(b - a for a, b in zip(times, times[1:]))
            return max(gaps, default=0.0) / max(self.target_duration, 0.001)
        start, end, times = self._verified_anchor_times()
        if len(times) < 2:
            return 1.0
        return max(b - a for a, b in zip(times, times[1:])) / (end - start)

    @property
    def max_unverified_edge_ratio(self) -> float:
        """Unverified opening/ending, excluding a genuinely non-overlapping intro."""
        if self.segments:
            if not all(segment.enabled for segment in self.segments):
                return 1.0
            return max(self.segments[0].target_start,
                       self.target_duration - self.segments[-1].target_end) / max(self.target_duration, 0.001)
        start, end, times = self._verified_anchor_times()
        if len(times) < 2:
            return 1.0
        return max(times[0] - start, end - times[-1]) / (end - start)

    @property
    def safe_for_automatic_export(self) -> bool:
        if self.manual_segments:
            return False
        if self.segments:
            return self._safe_piecewise_export()
        return (
            self.confidence >= 0.62
            and self.inlier_ratio >= 0.65
            and self.coverage >= 0.55
            and self.median_hamming <= 15
            and self.rms_error <= max(0.75, self.sample_interval * 1.20)
            and 0.90 <= self.slope <= 1.10
            and self.mapped_overlap_ratio >= 0.55
            and len(self.anchors) >= 8
            and self.visual_distinct_hashes >= 8
            and self.max_unverified_middle_ratio <= MAX_UNVERIFIED_MIDDLE_RATIO
            and self.max_unverified_edge_ratio <= MAX_UNVERIFIED_EDGE_RATIO
        )

    def _segment_anchor_target_times(self, segment: TimelineSegment) -> list[float]:
        tolerance = max(0.75, self.sample_interval * 1.20)
        return sorted(a.target_time for a in self.anchors
                      if segment.source_start <= a.source_time <= segment.source_end
                      and a.hamming_distance <= 15
                      and abs(a.target_time - (segment.target_start +
                              segment.slope * (a.source_time - segment.source_start))) <= tolerance)

    def _safe_piecewise_export(self) -> bool:
        """A cut is eligible only when *each* interval has independent visual proof."""
        segments = self.segments
        if any(not s.enabled for s in segments):
            return False
        if not (2 <= len(segments) <= 5 and self.confidence >= 0.70
                and self.coverage >= 0.55 and self.inlier_ratio >= 0.85
                and self.visual_distinct_hashes >= 16 and self.source_duration > 0
                and self.target_duration > 0):
            return False
        previous_source = previous_target = 0.0
        for segment in segments:
            values = (segment.source_start, segment.source_end,
                      segment.target_start, segment.target_end,
                      segment.median_hamming, segment.rms_error)
            if (not all(math.isfinite(value) for value in values)
                    or segment.source_start < previous_source - 1e-6
                    or segment.target_start < previous_target - 1e-6
                    or segment.source_start < 0 or segment.target_start < 0
                    or segment.source_end > self.source_duration + 0.02
                    or segment.target_end > self.target_duration + 0.02
                    or not (0.90 <= segment.slope <= 1.10)
                    or segment.anchor_count < 8 or segment.distinct_hashes < 8
                    or segment.median_hamming > 10
                    or segment.rms_error > max(0.75, self.sample_interval * 1.20)):
                return False
            times = self._segment_anchor_target_times(segment)
            edge_allowance = max(2.0, self.sample_interval * 2.0)
            if (len(times) < 8 or times[0] - segment.target_start > edge_allowance
                    or segment.target_end - times[-1] > edge_allowance):
                return False
            previous_source, previous_target = segment.source_end, segment.target_end
        source_coverage = sum(s.source_end - s.source_start for s in segments) / self.source_duration
        return (source_coverage >= 0.75
                and self.mapped_overlap_ratio >= 0.82
                and self.max_unverified_middle_ratio <= MAX_UNVERIFIED_MIDDLE_RATIO
                and self.max_unverified_edge_ratio <= MAX_UNVERIFIED_EDGE_RATIO)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["anchors"] = [asdict(anchor) for anchor in self.anchors]
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AlignmentResult":
        value = dict(value)
        value["anchors"] = [Anchor(**item) for item in value.get("anchors", [])]
        value["segments"] = [TimelineSegment(**item) for item in value.get("segments", [])]
        return cls(**value)


def validate_timeline_layout(segments: list[TimelineSegment], source_duration: float,
                             target_duration: float) -> str | None:
    """Structural safety only. Valid segments still need explicit manual approval."""
    if not (1 <= len(segments) <= 12):
        return "Zwischen 1 und 12 Abschnitte sind erforderlich."
    if not any(segment.enabled for segment in segments):
        return "Mindestens ein Abschnitt muss aktiv bleiben."
    if not all(math.isfinite(v) and v > 0 for v in (source_duration, target_duration)):
        return "Die Laufzeiten der Quell- und Masterdatei sind ungültig."
    previous_source = previous_target = 0.0
    for index, segment in enumerate(segments, 1):
        numbers = (segment.source_start, segment.source_end,
                   segment.target_start, segment.target_end)
        if not all(math.isfinite(v) for v in numbers):
            return f"Abschnitt {index}: Zeitwerte müssen endlich sein."
        if (segment.source_start < previous_source - 0.001 or
                segment.target_start < previous_target - 0.001):
            return f"Abschnitt {index}: Abschnitte dürfen sich nicht überlappen oder rückwärts laufen."
        if segment.source_start < 0 or segment.target_start < 0:
            return f"Abschnitt {index}: negative Zeitwerte sind nicht erlaubt."
        if (segment.source_end > source_duration + 0.001 or
                segment.target_end > target_duration + 0.001):
            return f"Abschnitt {index}: Abschnitt reicht über das Dateiende hinaus."
        if (segment.source_end - segment.source_start < 0.1 or
                segment.target_end - segment.target_start < 0.1):
            return f"Abschnitt {index}: Mindestdauer ist 0,1 Sekunden."
        if not 0.5 <= segment.slope <= 2.0:
            return f"Abschnitt {index}: Zeitmaßstab muss zwischen 0,5 und 2,0 liegen."
        previous_source, previous_target = segment.source_end, segment.target_end
    return None


@dataclass(slots=True)
class TrackChoice:
    path: str
    audio_ordinal: int
    language: str = "und"
    title: str = ""
    include: bool = True
    alignment: AlignmentResult | None = None


@dataclass(slots=True)
class EpisodeGroup:
    key: str
    display_name: str
    files: list[str] = field(default_factory=list)
    series_name: str = ""
    season_number: int | None = None
    episode_number: int | None = None

    @property
    def count(self) -> int:
        return len(self.files)
