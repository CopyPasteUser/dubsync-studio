"""Read-only CPU FFmpeg thread comparison with exact fingerprint parity."""
from __future__ import annotations

import math
import statistics
import time

from .alignment import perceptual_hash
from .ffmpeg_sampling import SamplingBackendError, ffmpeg_sample
from .performance import system_specs


def _benchmark_windows(duration: float, seconds: float) -> tuple[float, ...]:
    """Sample the beginning, middle and end of long episodes without overlap."""
    if duration < 15 * 60 or duration < 3 * seconds:
        return (0.,)
    last = duration - seconds
    return (0., round(last / 2, 3), round(last, 3))


def compare_cpu_decode_threads(path: str, duration: float, *, seconds: float = 12.,
                               interval: float = .75, repetitions: int = 3,
                               logical_cpus: int | None = None,
                               cancel_event=None) -> dict:
    """Find a useful *suggestion*, without changing hardware settings.

    Candidates decode identical windows; on long episodes these cover three
    widely separated points. All frames must match the default exactly and a
    recommendation must be fast in aggregate without a slow individual window.
    Alternate run/window order to reduce cache and thermal bias. No writes.
    """
    if not (math.isfinite(duration) and duration > 0 and
            math.isfinite(seconds) and 3 <= seconds <= 120 and
            .5 <= interval <= 15 and 1 <= repetitions <= 5):
        raise ValueError("Ungültige Laufzeit, Testlänge oder Wiederholungszahl")
    if min(seconds, duration) < 4 * interval:
        raise ValueError("Video oder Testausschnitt ist für vier Bildproben zu kurz")
    # Respect CPU affinity and container cgroup quota, not the host's
    # potentially much larger os.cpu_count() value.
    budget = logical_cpus if logical_cpus is not None else system_specs()["logical_cpus"]
    logical = max(1, min(64, int(budget)))
    candidates = sorted({count for count in (1, 2, 4, 8, 12, logical)
                         if count <= logical})
    options: list[int | None] = [None, *candidates]
    starts = _benchmark_windows(duration, min(seconds, duration))
    window_lengths = [min(seconds, duration - start) for start in starts]
    timings: dict[int | None, list[float]] = {option: [] for option in options}
    window_timings: dict[int | None, list[list[float]]] = {
        option: [[] for _ in starts] for option in options
    }
    errors: dict[int | None, str] = {}
    refs: list[list[tuple[float, int]] | None] = [None for _ in starts]
    mismatches: set[int | None] = set()
    for trial in range(repetitions):
        for option in (options if trial % 2 == 0 else list(reversed(options))):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("CPU-Decodervergleich abgebrochen")
            if option in errors or option in mismatches:
                continue
            elapsed_total = 0.
            indices = range(len(starts)) if trial % 2 == 0 else reversed(range(len(starts)))
            for index in indices:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("CPU-Decodervergleich abgebrochen")
                started = time.perf_counter()
                try:
                    frames = ffmpeg_sample(path, duration, interval, perceptual_hash,
                                           accel=None, limit_seconds=window_lengths[index],
                                           start_seconds=starts[index], decode_threads=option,
                                           cancel_event=cancel_event)
                except SamplingBackendError as exc:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("CPU-Decodervergleich abgebrochen") from exc
                    errors[option] = str(exc)[:250]
                    break
                elapsed = max(.000001, time.perf_counter() - started)
                if refs[index] is None:
                    refs[index] = frames
                elif frames != refs[index]:
                    mismatches.add(option)
                    break
                window_timings[option][index].append(elapsed)
                elapsed_total += elapsed
            else:
                timings[option].append(elapsed_total)
    if not timings[None] or len(timings[None]) != repetitions:
        raise RuntimeError("Standard-FFmpeg-Decoder konnte nicht reproduzierbar gemessen werden")
    baseline = statistics.median(timings[None])
    rows = []
    fastest_parity: list[tuple[float, int]] = []
    eligible: list[tuple[float, int]] = []
    for option in options:
        times = timings[option]
        median = statistics.median(times) if times else None
        parity = option not in mismatches and option not in errors and len(times) == repetitions
        worst_speedup = None
        if parity:
            worst_speedup = min(
                statistics.median(window_timings[None][index]) /
                statistics.median(window_timings[option][index])
                for index in range(len(starts))
            )
        rows.append({"threads": option, "label": "default" if option is None else str(option),
                     "median_seconds": round(median, 5) if median is not None else None,
                     "speedup_vs_default": round(baseline / median, 3) if median else None,
                     "worst_window_speedup": round(worst_speedup, 3) if worst_speedup is not None else None,
                     "exact_frame_parity": parity, "completed_runs": len(times),
                     "error": errors.get(option)})
        if option is not None and parity and median is not None:
            fastest_parity.append((median, option))
            if (baseline / median >= 1.15 and worst_speedup is not None and
                    worst_speedup >= .9):
                eligible.append((median, option))
    best = min(eligible) if eligible else None
    fastest = min(fastest_parity) if fastest_parity else None
    return {"frames_per_run": sum(len(ref or []) for ref in refs),
            "tested_seconds": round(sum(window_lengths), 3),
            "windows": [{"start_seconds": start, "duration_seconds": round(length, 3),
                         "frames": len(refs[index] or [])}
                        for index, (start, length) in enumerate(zip(starts, window_lengths))],
            "repetitions": repetitions, "logical_cpus": logical, "runs": rows,
            "recommended_threads": best[1] if best else None,
            "measured_speedup": round(baseline / fastest[0], 3) if fastest else None,
            "automatic_setting_change": False,
            "note": ("Mindestens 15% insgesamt schneller bei identischen Bild-Fingerprints "
                     "und höchstens 10% langsamer je Fenster; mit weiteren Dateien prüfen."
                     if best else "Keine ausreichend schnelle Alternative mit exakter "
                     "Bildparität und stabiler Geschwindigkeit in allen Fenstern.")}
