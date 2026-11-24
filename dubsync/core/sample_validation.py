"""Validation shared by persistent and in-memory sample caches."""
from __future__ import annotations

import math

MAX_SAMPLES = 6000


def validate_samples(pairs, duration: float, interval: float) -> list[tuple[float, int]]:
    if (not math.isfinite(duration) or not math.isfinite(interval)
            or duration <= 0 or interval <= 0):
        raise ValueError("Ungültige Cache-Laufzeit oder Sample-Abstand")
    if not isinstance(pairs, list) or not 4 <= len(pairs) <= MAX_SAMPLES:
        raise ValueError("Ungültige Anzahl gecachter Samples")
    samples = []
    last = -1.0
    for item in pairs:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("Ungültige Sample-Struktur")
        stamp, value = item
        if (type(stamp) not in (int, float) or not math.isfinite(stamp) or
                stamp <= last or stamp < 0 or stamp > duration + interval * 2 or
                type(value) is not int or not 0 <= value < 2**64):
            raise ValueError("Beschädigter Sample-Eintrag")
        samples.append((float(stamp), value))
        last = stamp
    if last < max(0.0, duration - interval * 2):
        raise ValueError("Sample-Cache deckt das Dateiende nicht ausreichend ab")
    return samples
