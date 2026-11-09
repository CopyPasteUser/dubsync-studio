"""Read-only CPU FFmpeg thread comparison with exact fingerprint parity."""
from __future__ import annotations

import math
import os
import statistics
import time

from .alignment import perceptual_hash
from .ffmpeg_sampling import SamplingBackendError, ffmpeg_sample


def compare_cpu_decode_threads(path: str, duration: float, *, seconds: float = 12.,
                               interval: float = .75, repetitions: int = 3,
                               logical_cpus: int | None = None,
                               cancel_event=None) -> dict:
    """Find a useful *suggestion*, without changing hardware settings.

    Each candidate decodes the same short prefix and must produce exactly the
    same sampled frame fingerprints as the default. Alternate the run order to
    reduce warm OS cache and thermal bias. No video or preferences are written.
    """
    if not (math.isfinite(duration) and duration > 0 and
            math.isfinite(seconds) and 3 <= seconds <= 120 and
            .5 <= interval <= 15 and 1 <= repetitions <= 5):
        raise ValueError("Ungültige Laufzeit, Testlänge oder Wiederholungszahl")
    if min(seconds, duration) < 4 * interval:
        raise ValueError("Video oder Testausschnitt ist für vier Bildproben zu kurz")
    logical = max(1, min(64, int(logical_cpus or os.cpu_count() or 1)))
    candidates = sorted({count for count in (1, 2, 4, 8, 12, logical)
                         if count <= logical})
    options: list[int | None] = [None, *candidates]
    timings: dict[int | None, list[float]] = {option: [] for option in options}
    errors: dict[int | None, str] = {}
    ref: list[tuple[float, int]] | None = None
    mismatches: set[int | None] = set()
    for trial in range(repetitions):
        for option in (options if trial % 2 == 0 else list(reversed(options))):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("CPU-Decodervergleich abgebrochen")
            if option in errors or option in mismatches:
                continue
            started = time.perf_counter()
            try:
                frames = ffmpeg_sample(path, duration, interval, perceptual_hash,
                                       accel=None, limit_seconds=seconds,
                                       decode_threads=option, cancel_event=cancel_event)
            except SamplingBackendError as exc:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("CPU-Decodervergleich abgebrochen") from exc
                errors[option] = str(exc)[:250]
                continue
            elapsed = max(.000001, time.perf_counter() - started)
            if ref is None:
                ref = frames
            elif frames != ref:
                mismatches.add(option)
                continue
            timings[option].append(elapsed)
    if not timings[None] or len(timings[None]) != repetitions:
        raise RuntimeError("Standard-FFmpeg-Decoder konnte nicht reproduzierbar gemessen werden")
    baseline = statistics.median(timings[None])
    rows = []
    eligible: list[tuple[float, int]] = []
    for option in options:
        times = timings[option]
        median = statistics.median(times) if times else None
        parity = option not in mismatches and option not in errors and len(times) == repetitions
        rows.append({"threads": option, "label": "default" if option is None else str(option),
                     "median_seconds": round(median, 5) if median is not None else None,
                     "speedup_vs_default": round(baseline / median, 3) if median else None,
                     "exact_frame_parity": parity, "completed_runs": len(times),
                     "error": errors.get(option)})
        if option is not None and parity and median is not None:
            eligible.append((median, option))
    best = min(eligible) if eligible else None
    worthwhile = best is not None and baseline / best[0] >= 1.15
    return {"frames_per_run": len(ref or []), "tested_seconds": min(seconds, duration),
            "repetitions": repetitions, "logical_cpus": logical, "runs": rows,
            "recommended_threads": best[1] if worthwhile else None,
            "measured_speedup": round(baseline / best[0], 3) if best else None,
            "automatic_setting_change": False,
            "note": ("Mindestens 15% schneller bei identischen Bild-Fingerprints; "
                     "mehrfach auf repräsentativen Dateien prüfen." if worthwhile else
                     "Keine ausreichend schnelle, exakt übereinstimmende Alternative zum Standard.")}
