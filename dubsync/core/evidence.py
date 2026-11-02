"""Read-only visual evidence map for the review workbench.

The matching/export gates live in :mod:`models`; this module never grants an
export. A verified interval here means *sampled visual evidence nearby*, not
continuous frame-by-frame verification. No media I/O is performed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .models import AlignmentResult, TimelineSegment


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    start: float
    end: float
    status: str  # sampled, unverified, or unmapped

    @property
    def duration(self) -> float:
        return self.end - self.start


def _merge(spans: list[EvidenceSpan]) -> list[EvidenceSpan]:
    merged: list[EvidenceSpan] = []
    for span in spans:
        if span.duration < 1e-6:
            continue
        if merged and merged[-1].status == span.status and abs(merged[-1].end - span.start) < 1e-5:
            prev = merged[-1]
            merged[-1] = EvidenceSpan(prev.start, span.end, span.status)
        else:
            merged.append(span)
    return merged


def _sample_windows(alignment: AlignmentResult, start: float, end: float,
                    segment: TimelineSegment | None) -> list[tuple[float, float]]:
    tolerance = max(.75, alignment.sample_interval * 1.20)
    half_window = min(1.0, max(.05, alignment.sample_interval * .45))
    windows: list[tuple[float, float]] = []
    for anchor in alignment.anchors:
        if not (math.isfinite(anchor.source_time) and math.isfinite(anchor.target_time)
                and anchor.hamming_distance <= 15):
            continue
        if segment is not None:
            if not segment.enabled or not segment.source_start <= anchor.source_time <= segment.source_end:
                continue
            expected = segment.target_start + segment.slope * (anchor.source_time - segment.source_start)
        else:
            if not 0 <= anchor.source_time <= alignment.source_duration:
                continue
            expected = alignment.slope * anchor.source_time + alignment.offset
        if abs(anchor.target_time - expected) > tolerance or not start <= anchor.target_time <= end:
            continue
        windows.append((max(start, anchor.target_time - half_window),
                        min(end, anchor.target_time + half_window)))
    windows.sort()
    combined: list[tuple[float, float]] = []
    for left, right in windows:
        if combined and left <= combined[-1][1] + 1e-6:
            combined[-1] = (combined[-1][0], max(right, combined[-1][1]))
        else:
            combined.append((left, right))
    return combined


def evidence_spans(alignment: AlignmentResult) -> list[EvidenceSpan]:
    """Partition the master timeline into nearby proof, unproved, and unmapped.

    These are display-only regions, computed from the actual matching anchors.
    A manually edited segment cannot gain approval through these markers.
    """
    duration = alignment.target_duration
    if not math.isfinite(duration) or duration <= 0:
        return []
    if alignment.segments:
        regions = [(max(0., s.target_start), min(duration, s.target_end), s)
                   for s in alignment.segments if s.enabled and
                   math.isfinite(s.target_start) and math.isfinite(s.target_end)
                   and s.target_end > s.target_start]
        regions.sort(key=lambda item: item[0])
    elif (math.isfinite(alignment.slope) and alignment.slope > 0
          and math.isfinite(alignment.offset) and math.isfinite(alignment.source_duration)):
        start = max(0., alignment.offset)
        end = min(duration, alignment.offset + alignment.slope * alignment.source_duration)
        regions = [(start, end, None)] if end > start else []
    else:
        regions = []
    output: list[EvidenceSpan] = []
    cursor = 0.
    for left, right, segment in regions:
        left = max(cursor, left)
        if right <= left:
            continue
        if left > cursor:
            output.append(EvidenceSpan(cursor, left, "unmapped"))
        position = left
        for win_start, win_end in _sample_windows(alignment, left, right, segment):
            if win_start > position:
                output.append(EvidenceSpan(position, win_start, "unverified"))
            if win_end > max(position, win_start):
                output.append(EvidenceSpan(max(position, win_start), win_end, "sampled"))
            position = max(position, win_end)
        if right > position:
            output.append(EvidenceSpan(position, right, "unverified"))
        cursor = right
    if cursor < duration:
        output.append(EvidenceSpan(cursor, duration, "unmapped"))
    return _merge(output)


def review_issues(alignment: AlignmentResult, limit: int = 30) -> list[EvidenceSpan]:
    """Largest places needing review, without implying export clearance."""
    threshold = max(1.0, alignment.sample_interval * 1.5)
    regions = [span for span in evidence_spans(alignment)
               if span.status != "sampled" and span.duration >= threshold]
    return sorted(regions, key=lambda s: (-s.duration, s.start))[:max(0, min(100, limit))]


def export_hold_reasons(alignment: AlignmentResult) -> list[str]:
    """Plain-language hints; authoritative eligibility remains in models.py."""
    if alignment.safe_for_automatic_export:
        return []
    if alignment.manual_segments:
        return ["Manuell geänderte Abschnitte benötigen eine ausdrückliche Bestätigung."]
    reasons = []
    if alignment.confidence < (.70 if alignment.segments else .62):
        reasons.append("Zu geringe Gesamtkonfidenz")
    if alignment.coverage < .55:
        reasons.append("Zu wenig gemeinsame Bildabdeckung")
    if alignment.visual_distinct_hashes < (16 if alignment.segments else 8):
        reasons.append("Zu wenige unterschiedliche Bildmotive")
    if alignment.max_unverified_middle_ratio > .20:
        reasons.append("Zu lange ungeprüfte Lücke innerhalb der Folge")
    if alignment.max_unverified_edge_ratio > .15:
        reasons.append("Anfang oder Ende nicht ausreichend geprüft")
    if alignment.segments:
        if any(not s.enabled for s in alignment.segments):
            reasons.append("Deaktivierter Abschnitt")
        if any(s.anchor_count < 8 or s.distinct_hashes < 8 for s in alignment.segments):
            reasons.append("Mindestens ein Schnittabschnitt ist nicht unabhängig belegt")
        if any(s.exclusive_hashes < 5 for s in alignment.segments):
            reasons.append("Schnittabschnitt enthält zu wenige eigenständige Bildmotive")
        if any(s.median_hamming > 10 for s in alignment.segments):
            reasons.append("Bildähnlichkeit eines Schnittabschnitts unzureichend")
    elif alignment.inlier_ratio < .65 or alignment.median_hamming > 15:
        reasons.append("Bildanker widersprechen der berechneten Zuordnung")
    return reasons or ["Weitere Sicherheitsgrenzen nicht erfüllt – Diagnosebericht öffnen"]
