from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


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

    def target_time(self, source_time: float) -> float:
        return self.slope * source_time + self.offset

    @property
    def mapped_overlap_ratio(self) -> float:
        start = self.offset
        end = self.slope * self.source_duration + self.offset
        overlap = max(0.0, min(self.target_duration, end) - max(0.0, start))
        return overlap / max(self.target_duration, 0.001)

    @property
    def safe_for_automatic_export(self) -> bool:
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
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["anchors"] = [asdict(anchor) for anchor in self.anchors]
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AlignmentResult":
        value = dict(value)
        value["anchors"] = [Anchor(**item) for item in value.get("anchors", [])]
        return cls(**value)


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
