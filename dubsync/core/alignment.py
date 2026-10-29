from __future__ import annotations

import math
import os
import threading
from bisect import bisect_left
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .models import (AlignmentResult, Anchor, MediaInfo,
                     MAX_UNVERIFIED_EDGE_RATIO, MAX_UNVERIFIED_MIDDLE_RATIO)
from .alignment_diagnostics import AlignmentDiagnostic
from .piecewise import detect_piecewise
from .probe import probe_media
from .ffmpeg_sampling import (SamplingBackendError, ffmpeg_sample,
                              benchmark_decoder_candidates,
                              gpu_decode_candidates, vaapi_render_device)
from .frame_cache import FrameSampleCache, source_identity
from .network import is_network_path
from .performance import sampling_worker_policy, system_specs

Progress = Callable[[str, int, int], None]
_BYTE_POPCOUNT = np.fromiter((value.bit_count() for value in range(256)), dtype=np.uint8)

# A process-local *ordering hint*, never a cached verification result. Every new
# video still runs a fresh CPU/GPU frame comparison before hardware is used.
_GPU_HINT_LOCK = threading.Lock()
_GPU_HINTS: dict[tuple[str, tuple[str, ...]], tuple[str, int]] = {}


def _gpu_candidates_for_codec(codec: str | None, candidates):
    if not codec or len(candidates) < 2:
        return candidates, False
    key = (codec.lower(), tuple(candidate.key for candidate in candidates))
    with _GPU_HINT_LOCK:
        hint = _GPU_HINTS.get(key)
        if hint is None:
            return candidates, False
        winner, uses = hint
        if uses >= 6:
            _GPU_HINTS.pop(key, None)  # Full adapter comparison periodically.
            return candidates, False
        _GPU_HINTS[key] = (winner, uses + 1)
    preferred = [candidate for candidate in candidates if candidate.key == winner]
    return preferred or candidates, bool(preferred)


def _remember_gpu_candidate(codec: str | None, candidates, winner: str | None):
    if not codec or len(candidates) < 2:
        return
    key = (codec.lower(), tuple(candidate.key for candidate in candidates))
    with _GPU_HINT_LOCK:
        if winner is None:
            _GPU_HINTS.pop(key, None)
        else:
            if len(_GPU_HINTS) >= 32:
                _GPU_HINTS.pop(next(iter(_GPU_HINTS)))
            _GPU_HINTS[key] = (winner, 0)


class AlignmentError(RuntimeError):
    pass


@dataclass(slots=True)
class FrameSample:
    time: float
    phash: int


def perceptual_hash(frame: np.ndarray) -> int:
    """64-bit low-frequency DCT hash, tolerant of moderate encoding differences."""
    if frame is None or frame.size == 0:
        raise ValueError("Leerer Videoframe")
    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame
    tiny = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    coeff = cv2.dct(np.float32(tiny))[:8, :8].flatten()
    values = coeff[1:]  # ignore DC brightness
    median = float(np.median(values))
    result = 0
    for bit, value in enumerate(values):
        if value >= median:
            result |= 1 << bit
    return result


def _open_capture(path: str, hardware_accel: str, progress: Progress | None, side: str):
    """Open a decoder with optional OpenCV/FFmpeg hardware acceleration.

    Hardware support depends on the OpenCV build and driver. Every requested
    backend therefore has a reliable software fallback instead of making a job fail.
    """
    mode = (hardware_accel or "auto").strip().lower()
    constant_name = {
        "auto": "VIDEO_ACCELERATION_ANY",
        "vaapi": "VIDEO_ACCELERATION_VAAPI",
        "qsv": "VIDEO_ACCELERATION_MFX",
        "d3d11": "VIDEO_ACCELERATION_D3D11",
        "drm": "VIDEO_ACCELERATION_DRM",
    }.get(mode)
    cap = None
    if mode != "none" and constant_name and hasattr(cv2, "CAP_PROP_HW_ACCELERATION") and hasattr(cv2, constant_name):
        try:
            cap = cv2.VideoCapture(
                path, cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_HW_ACCELERATION, int(getattr(cv2, constant_name))],
            )
            if cap.isOpened():
                if progress:
                    progress(f"{side}: Hardware-Decoding angefordert ({mode.upper()})", 0, 0)
                return cap, True
            cap.release()
        except (cv2.error, TypeError, ValueError):
            if cap is not None:
                cap.release()
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        cap.release()
        raise AlignmentError(f"Video kann nicht geöffnet werden: {path}")
    if mode not in ("none", "auto") and progress:
        progress(f"{side}: {mode.upper()} nicht verfügbar – CPU-Fallback", 0, 0)
    return cap, False


def sample_video(path: str, duration: float, interval: float = 0.5,
                 progress: Progress | None = None, side: str = "Video", cancel_event=None,
                 hardware_accel: str = "auto", diagnostic: AlignmentDiagnostic | None = None,
                 decode_threads: int | None = None,
                 video_codec: str | None = None) -> list[FrameSample]:
    if interval < 0.5 or interval > 15:
        raise ValueError("Sampleintervall muss zwischen 0,5 und 15 Sekunden liegen")
    mode = (hardware_accel or "auto").strip().lower()
    # FFmpeg scales frames before they cross the process boundary. This avoids
    # Python/OpenCV handling every full-resolution decoded frame. Auto measures
    # GPU vs CPU on a short prefix before selecting hardware for long videos.
    if mode in {"auto", "ffmpeg", "cuda", "vaapi", "amd-vaapi", "intel-vaapi", "d3d11"}:
        ffmpeg_started = time.monotonic()
        benchmark = None
        selected_gpu = None
        selected_candidate = None
        gpu_device = None
        hinted = False
        use_ffmpeg = mode != "auto"
        try:
            if mode == "auto":
                candidates = gpu_decode_candidates()
                network_source = is_network_path(path) if candidates and duration >= 45 else False
                if candidates and duration >= 45 and not network_source:
                    measured_candidates, hinted = _gpu_candidates_for_codec(video_codec, candidates)
                    benchmark = benchmark_decoder_candidates(
                        path, duration, interval, perceptual_hash,
                        candidates=measured_candidates, cancel_event=cancel_event,
                        seconds=6.0 if len(measured_candidates) > 1 else 8.0)
                    cpu = benchmark.get("cpu", {})
                    cpu_speed = float(cpu.get("samples_per_second", 0))
                    ranked = []
                    for candidate in measured_candidates:
                        accelerated = benchmark.get(candidate.key, {})
                        gpu_speed = float(accelerated.get("samples_per_second", 0))
                        if ("frames" in cpu and gpu_speed > cpu_speed * 1.15 and
                                float(accelerated.get("frame_match_ratio", 0)) >= 0.95 and
                                int(accelerated.get("median_hamming_to_cpu", 99)) <= 10 and
                                int(accelerated.get("frames", 0)) >= 4 and
                                abs(int(accelerated["frames"]) - int(cpu["frames"])) <= 1):
                            ranked.append((gpu_speed, candidate))
                    if ranked:
                        _, winner = max(ranked, key=lambda pair: pair[0])
                        selected_gpu, gpu_device = winner.accel, winner.device
                        selected_candidate = winner.key
                        use_ffmpeg = True
                        if not hinted:
                            _remember_gpu_candidate(video_codec, candidates, winner.key)
                    elif hinted:
                        _remember_gpu_candidate(video_codec, candidates, None)
                    elif progress:
                        progress(f"{side}: Kein geprüfter GPU-Decoder schneller – OpenCV bleibt aktiv", 0, 0)
                elif candidates and network_source and progress:
                    progress(f"{side}: NAS-Quelle – mehrfaches Einlesen für GPU-Benchmarks vermieden", 0, 0)
            else:
                selected_gpu = None if mode == "ffmpeg" else {
                    "d3d11": "d3d11va", "amd-vaapi": "vaapi", "intel-vaapi": "vaapi"}.get(mode, mode)
                if mode in {"amd-vaapi", "intel-vaapi"}:
                    vendor = "amd" if mode == "amd-vaapi" else "intel"
                    gpu_device = vaapi_render_device(vendor)
                    if gpu_device is None:
                        raise SamplingBackendError(f"Kein zugreifbares {vendor.upper()}-VAAPI-Rendergerät gefunden.")
                elif selected_gpu == "vaapi":
                    gpu_device = vaapi_render_device()
            if use_ffmpeg:
                if progress:
                    detail = (f"GPU {selected_candidate or selected_gpu.upper()} angefordert"
                              if selected_gpu else "CPU über FFmpeg")
                    progress(f"{side}: {detail} · Frames direkt auf 32×32 reduziert", 0, 0)
                hashes = ffmpeg_sample(path, duration, interval, perceptual_hash,
                                       accel=selected_gpu, device=gpu_device, cancel_event=cancel_event,
                                       progress=progress, side=side, decode_threads=decode_threads)
                samples = [FrameSample(timestamp, phash) for timestamp, phash in hashes]
                if diagnostic is not None:
                    elapsed = time.monotonic() - ffmpeg_started
                    unique = len({item.phash for item in samples})
                    diagnostic.stage(
                        f"sample_{'source' if side == 'Quellvideo' else 'master'}",
                        status="completed", backend="ffmpeg", requested_hwaccel=mode,
                        selected_decoder=selected_gpu or "software",
                        selected_gpu_candidate=selected_candidate,
                        gpu_hint_used=hinted,
                        gpu_candidates_measured=len(measured_candidates) if benchmark is not None else 0,
                        gpu_preference_scope=video_codec if mode == "auto" else None,
                        render_device_name=Path(gpu_device).name if gpu_device else None,
                        hardware_execution_verified=False,
                        # FFmpeg may silently transfer from or fall back to system RAM.
                        benchmark=benchmark, interval_s=round(interval, 4),
                        metadata_duration_s=round(duration, 4), samples_collected=len(samples),
                        first_sample_s=samples[0].time, last_sample_s=samples[-1].time,
                        elapsed_s=round(elapsed, 4), unique_hashes=unique,
                        repeated_hash_ratio=round(1 - unique / len(samples), 4),
                    )
                return samples
        except SamplingBackendError as exc:
            if mode == "auto" and selected_candidate is not None:
                _remember_gpu_candidate(video_codec, candidates, None)
            if cancel_event is not None and cancel_event.is_set():
                raise AlignmentError("Videoanalyse abgebrochen.") from exc
            if progress:
                progress(f"{side}: FFmpeg-Sampling nicht nutzbar – OpenCV-Fallback ({str(exc)[:110]})", 0, 0)
            if diagnostic is not None:
                diagnostic.stage("sampling_backend_fallback", side=side,
                                 attempted_decoder=selected_gpu or "software",
                                 error=str(exc)[:500])
    cap, hardware_requested = _open_capture(path, hardware_accel, progress, side)
    samples: list[FrameSample] = []
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    fps = fps if math.isfinite(fps) and fps > 0 else 0.0
    raw_frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    frame_count = int(raw_frame_count) if math.isfinite(raw_frame_count) and raw_frame_count > 0 else 0
    estimated_frames = frame_count if frame_count > 0 else max(1, int(max(duration, 1.0) * max(fps, 24.0)))
    progress_sample_count = -1
    previous_timestamp = -1.0
    timestamp_anomalies = 0
    next_sample_time = 0.0
    frame_index = 0
    retrieve_attempts = 0
    retrieve_failures = 0
    timestamp_sources = {"container": 0, "fps": 0, "fallback_24fps": 0}
    hardware_fallback = False
    sampling_started = time.monotonic()
    sampling_outcome = "completed"
    last_timestamp = None
    try:
        # Frames sequenziell decodieren. Wiederholte Zeitsprünge über CAP_PROP_POS_MSEC
        # können bei langen GOPs jeden Sample erneut vom letzten Keyframe decodieren.
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise AlignmentError("Videoanalyse abgebrochen.")
            # grab() advances decoding without materializing a NumPy image for every
            # frame. retrieve() is only called for frames that are actually sampled.
            ok = cap.grab()
            if not ok:
                # Some OpenCV builds report that the HW-backed capture opened but
                # fail on the first decoded frame. Retry once with ordinary CPU decode.
                if frame_index == 0 and hardware_requested:
                    cap.release()
                    cap = cv2.VideoCapture(path)
                    hardware_requested = False
                    hardware_fallback = True
                    if cap.isOpened():
                        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                        fps = fps if math.isfinite(fps) and fps > 0 else 0.0
                        raw_frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
                        frame_count = int(raw_frame_count) if math.isfinite(raw_frame_count) and raw_frame_count > 0 else 0
                        estimated_frames = frame_count if frame_count > 0 else max(1, int(max(duration, 1.0) * max(fps, 24.0)))
                        next_sample_time = 0.0
                        if progress:
                            progress(f"{side}: Hardware-Decoding nicht nutzbar – CPU-Fallback", 0, 0)
                        continue
                    cap.release()
                break
            reported_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
            valid_pts = math.isfinite(reported_ms) and reported_ms > 0.0
            if valid_pts:
                timestamp = reported_ms / 1000.0
                # Some OpenCV/FFmpeg combinations return frozen or regressing PTS.
                # Fall back to frame-position timing instead of silently losing samples.
                if timestamp <= previous_timestamp:
                    valid_pts = False
                    timestamp_anomalies += 1
            if valid_pts:
                timestamp_sources["container"] += 1
            elif math.isfinite(fps) and fps > 0:
                timestamp = frame_index / fps
                timestamp_sources["fps"] += 1
            else:
                timestamp = frame_index / 24.0
                timestamp_sources["fallback_24fps"] += 1
            if timestamp <= previous_timestamp:
                timestamp_anomalies += 1
                timestamp = previous_timestamp + (1.0 / fps if math.isfinite(fps) and fps > 0 else 1.0 / 24.0)
            previous_timestamp = timestamp
            last_timestamp = timestamp

            if timestamp + 0.0001 >= next_sample_time:
                if timestamp <= duration + max(interval, 0.5):
                    retrieve_attempts += 1
                    retrieved, frame = cap.retrieve()
                    if retrieved and frame is not None:
                        samples.append(FrameSample(timestamp, perceptual_hash(frame)))
                    else:
                        retrieve_failures += 1
                while next_sample_time <= timestamp + 0.0001:
                    next_sample_time += interval
                if progress and samples and len(samples) % 20 == 0 and len(samples) != progress_sample_count:
                    progress_sample_count = len(samples)
                    estimate_samples = max(1, int(math.ceil(max(duration, 0.1) / interval)))
                    progress(side, min(len(samples), estimate_samples), estimate_samples)
            frame_index += 1
            # A broken/misreported metadata duration must not make the worker read forever.
            if duration > 0 and timestamp > duration + max(5.0, interval * 3):
                break
    except Exception:
        sampling_outcome = "cancelled" if cancel_event is not None and cancel_event.is_set() else "error"
        raise
    finally:
        # Persist partial evidence even if decoding, callbacks or cancellation
        # interrupts the run. Previously the most useful fields were lost.
        cap.release()
        if diagnostic is not None:
            unique = len({item.phash for item in samples})
            diagnostic.stage(f"sample_{'source' if side == 'Quellvideo' else 'master'}",
                             status=sampling_outcome,
                             metadata_duration_s=round(duration, 4), interval_s=round(interval, 4),
                             capture_fps=round(fps, 4), capture_frame_count=frame_count,
                             frames_grabbed=frame_index, samples_collected=len(samples),
                             retrieve_attempts=retrieve_attempts, retrieve_failures=retrieve_failures,
                             first_sample_s=round(samples[0].time, 4) if samples else None,
                             last_sample_s=round(samples[-1].time, 4) if samples else None,
                             last_decoded_timestamp_s=round(last_timestamp, 4) if last_timestamp is not None else None,
                             elapsed_s=round(time.monotonic() - sampling_started, 4),
                             unique_hashes=unique, repeated_hash_ratio=round(1 - unique / len(samples), 4) if samples else None,
                             timestamp_sources=timestamp_sources, timestamp_anomalies=timestamp_anomalies,
                             hardware_decode_initially_requested=(hardware_accel != 'none'),
                             hardware_decode_fallback=hardware_fallback)
    if progress:
        estimate_samples = max(1, int(math.ceil(max(duration, 0.1) / interval)))
        progress(side, min(len(samples), estimate_samples), estimate_samples)
    if len(samples) < 4:
        raise AlignmentError(
            f"Zu wenige lesbare Videoframes in {Path(path).name}; mindestens 4 Samples nötig."
        )
    return samples


def _sample_with_cache(path: str, duration: float, interval: float,
                       progress: Progress | None, side: str, cancel_event,
                       hardware_accel: str, diagnostic: AlignmentDiagnostic | None,
                       cache: FrameSampleCache | None,
                       decode_threads: int | None = None,
                       video_codec: str | None = None) -> list[FrameSample]:
    """Reuse only frame hashes; always rerun identity checks and alignment."""
    identity = None
    stage = "source" if side == "Quellvideo" else "master"
    if cache is not None:
        try:
            identity = source_identity(path)
            cached = cache.get(identity, duration, interval, hardware_accel)
        except OSError:
            cached = None
        if cached is not None:
            if cancel_event is not None and cancel_event.is_set():
                raise AlignmentError("Videoanalyse abgebrochen.")
            # A source modified while reading from the cache must not enter
            # a fresh safety evaluation with potentially stale fingerprints.
            try:
                stable = source_identity(path) == identity
            except OSError:
                stable = False
            if not stable:
                raise AlignmentError("Quelldatei während des Fingerprint-Cache-Zugriffs verändert.")
            if progress:
                progress(f"{side}: {len(cached)} Bildproben aus geprüftem Cache", len(cached), len(cached))
            if diagnostic is not None:
                diagnostic.stage(f"sample_{stage}", status="cache_hit", backend="fingerprint_cache",
                                 samples_collected=len(cached), interval_s=round(interval, 4),
                                 metadata_duration_s=round(duration, 4))
            return [FrameSample(stamp, value) for stamp, value in cached]

    samples = sample_video(path, duration, interval, progress, side,
                           cancel_event, hardware_accel, diagnostic, decode_threads,
                           video_codec)
    if identity is not None and cache is not None:
        try:
            after = source_identity(path)
        except OSError:
            # Cache failure is not a decoder failure.
            after = None
        if after is not None and after != identity:
            raise AlignmentError("Quelldatei während der Videoanalyse verändert; Analyse abgebrochen.")
        stored = (after is not None and
                  cache.put(identity, duration, interval, [(x.time, x.phash) for x in samples],
                            hardware_accel))
        if diagnostic is not None:
            diagnostic.stage(f"sample_cache_{stage}", hit=False, stored=stored,
                             samples_collected=len(samples))
    return samples


class _CombinedCancel:
    """Stop the sibling decoder when one worker fails, without changing user events."""

    def __init__(self, external, internal: threading.Event):
        self.external = external
        self.internal = internal

    def is_set(self) -> bool:
        return self.internal.is_set() or (self.external is not None and self.external.is_set())


def _sample_video_pair(source_path: str, target_path: str,
                       source_duration: float, target_duration: float,
                       interval: float, progress: Progress | None, cancel_event,
                       hardware_accel: str, diagnostic: AlignmentDiagnostic | None,
                       frame_cache_enabled: bool, requested_workers: int,
                       source_codec: str | None = None, target_codec: str | None = None
                       ) -> tuple[list[FrameSample], list[FrameSample]]:
    """Read independent sources concurrently only when the resource policy permits."""
    specs = system_specs()
    workers, reason = sampling_worker_policy(source_path, target_path,
                                             requested_workers, hardware_accel, specs)
    if diagnostic is not None:
        diagnostic.stage("sampling_scheduler", requested_workers=requested_workers,
                         effective_workers=workers, reason=reason)
    started = time.monotonic()
    tasks = (("source", source_path, source_duration, "Quellvideo", source_codec),
             ("master", target_path, target_duration, "Referenzvideo", target_codec))
    # Initialize SQLite schema serially; concurrent initialization may briefly
    # lock the database even though subsequent per-worker connections are safe.
    caches = {key: FrameSampleCache() for key, *_ in tasks} if frame_cache_enabled else {}
    results: dict[str, list[FrameSample]] = {}
    timings: dict[str, float] = {}
    local_stop = threading.Event()
    stop = _CombinedCancel(cancel_event, local_stop)
    progress_lock = threading.Lock()
    decode_threads = (max(1, min(12, int(specs.get("logical_cpus", 2)) // 2))
                      if workers == 2 else None)

    def run(task):
        key, path, duration, side, codec = task
        began = time.monotonic()

        def report(name: str, done: int, total: int) -> None:
            if progress is not None:
                # User-supplied callbacks need not be reentrant. Qt signal
                # emissions remain safe from these background worker threads.
                with progress_lock:
                    progress(name, done, total)

        # Separate in-memory caches, short-lived SQLite connections on disk.
        cache = caches.get(key)
        samples = _sample_with_cache(path, duration, interval, report if progress else None,
                                     side, stop, hardware_accel, diagnostic, cache,
                                     decode_threads, codec)
        return key, samples, time.monotonic() - began

    if workers == 1:
        for task in tasks:
            key, samples, elapsed = run(task)
            results[key], timings[key] = samples, elapsed
    else:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="dubsync-sample") as executor:
            futures = [executor.submit(run, task) for task in tasks]
            try:
                for future in as_completed(futures):
                    key, samples, elapsed = future.result()
                    results[key], timings[key] = samples, elapsed
            except BaseException:
                local_stop.set()
                raise
    if diagnostic is not None:
        diagnostic.stage("sample_runtime",
                         source_seconds=round(timings["source"], 4),
                         master_seconds=round(timings["master"], 4),
                         wall_seconds=round(time.monotonic() - started, 4),
                         effective_workers=workers)
    return results["source"], results["master"]


def _sequence_alignment(source: list[FrameSample], target: list[FrameSample],
                        progress: Progress | None = None, cancel_event=None,
                        diagnostic: AlignmentDiagnostic | None = None) -> list[Anchor]:
    """Monotone DP-Framezuordnung. Unpassende Szenen dürfen übersprungen werden."""
    n, m = len(source), len(target)
    # 2.0 Sekunden sind teuer genug, dass falsche Paare zugunsten von Lücken vermieden werden.
    gap_cost = 0.205
    max_match_distance = 18
    distances = np.empty((n, m), dtype=np.uint8)
    target_hashes = np.asarray([sample.phash for sample in target], dtype=np.uint64)
    for i, src in enumerate(source):
        if cancel_event is not None and cancel_event.is_set():
            raise AlignmentError("Frameabgleich abgebrochen.")
        xor_bytes = np.bitwise_xor(target_hashes, np.uint64(src.phash)).view(np.uint8)
        distances[i] = _BYTE_POPCOUNT[xor_bytes].reshape(m, 8).sum(axis=1, dtype=np.uint8)

    dp = np.empty((n + 1, m + 1), dtype=np.float32)
    trace = np.zeros((n + 1, m + 1), dtype=np.uint8)
    dp[:, 0] = np.arange(n + 1, dtype=np.float32) * gap_cost
    dp[0, :] = np.arange(m + 1, dtype=np.float32) * gap_cost
    trace[1:, 0] = 2  # source-only frame
    trace[0, 1:] = 3  # target-only frame
    column_costs = np.arange(m + 1, dtype=np.float64) * gap_cost

    for i in range(1, n + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise AlignmentError("Frameabgleich abgebrochen.")
        previous = dp[i - 1]
        current = dp[i]
        previous_trace = trace[i]
        distance_row = distances[i - 1]
        # dp[j] = min(diagonal[j], above[j], dp[j-1] + gap_cost).
        # Subtracting j*gap_cost makes the last term a prefix minimum, so one
        # NumPy scan replaces the Python O(m) inner loop without changing the DP.
        diagonal = previous[:-1].astype(np.float64) + distance_row / 64.0
        above = previous[1:].astype(np.float64) + gap_cost
        best = np.minimum(diagonal, above)
        candidates = np.empty(m + 1, dtype=np.float64)
        candidates[0] = current[0]
        candidates[1:] = best - column_costs[1:]
        np.minimum.accumulate(candidates, out=candidates)
        current[1:] = candidates[1:] + column_costs[1:]
        left = current[:-1].astype(np.float64) + gap_cost
        previous_trace[1:] = np.where(
            (diagonal <= above) & (diagonal <= left), 1,
            np.where(above <= left, 2, 3),
        )
        if progress and (i % 30 == 0 or i == n):
            progress("Frameabgleich", i, n)

    pairs: list[Anchor] = []
    i, j = n, m
    while i > 0 or j > 0:
        code = int(trace[i, j])
        if i > 0 and j > 0 and code == 1:
            distance = int(distances[i - 1, j - 1])
            if distance <= max_match_distance:
                pairs.append(Anchor(source[i - 1].time, target[j - 1].time, distance))
            i -= 1
            j -= 1
        elif i > 0 and (j == 0 or code == 2):
            i -= 1
        elif j > 0:
            j -= 1
        else:
            break
    pairs.reverse()

    # Wiederholte zeitliche Paare sowie nicht-monotone Zuordnungen entfernen.
    clean: list[Anchor] = []
    last_src = last_target = -1.0
    for item in pairs:
        if item.source_time <= last_src or item.target_time <= last_target:
            continue
        clean.append(item)
        last_src, last_target = item.source_time, item.target_time
    if diagnostic is not None:
        histogram = {"0_5": 0, "6_10": 0, "11_15": 0, "16_18": 0}
        for a in clean:
            if a.hamming_distance <= 5:
                histogram["0_5"] += 1
            elif a.hamming_distance <= 10:
                histogram["6_10"] += 1
            elif a.hamming_distance <= 15:
                histogram["11_15"] += 1
            else:
                histogram["16_18"] += 1
        diagnostic.stage("sequence_match", source_sample_count=n, target_sample_count=m,
                         matched_anchors=len(clean), coverage=round(len(clean) / max(1, min(n, m)), 5),
                         hamming_histogram=histogram,
                         first_source_time_s=round(clean[0].source_time, 4) if clean else None,
                         last_source_time_s=round(clean[-1].source_time, 4) if clean else None)
    return clean


def _feature_candidate_pairs(source: list[FrameSample], target: list[FrameSample],
                             cancel_event=None) -> list[tuple[int, int, int, float, float]]:
    """Find the same two strongest visual matches per source frame as the scalar path.

    Each vector is only as wide as the target sample list; no quadratic matrix
    is retained in memory. Equal distances still prefer the earlier target.
    """
    target_hashes = np.asarray([sample.phash for sample in target], dtype=np.uint64)
    target_times = [sample.time for sample in target]
    candidates: list[tuple[int, int, int, float, float]] = []
    for source_index, src in enumerate(source):
        if cancel_event is not None and cancel_event.is_set():
            raise AlignmentError("Frameabgleich abgebrochen.")
        xor = np.bitwise_xor(target_hashes, np.uint64(src.phash)).view(np.uint8)
        distances = _BYTE_POPCOUNT[xor].reshape(len(target), 8).sum(axis=1, dtype=np.uint8)
        matching = np.flatnonzero(distances <= 16)
        if not len(matching):
            continue
        # lexsort makes the old stable (distance, target position) ordering
        # explicit, including collisions and repeated cartoon frames.
        best = matching[np.lexsort((matching, distances[matching]))[:2]]
        for target_index in best:
            candidates.append((source_index, int(target_index), int(distances[target_index]),
                               src.time, target_times[target_index]))
    return candidates


def _feature_anchor_fallback(source: list[FrameSample], target: list[FrameSample],
                            interval: float, cancel_event=None,
                            diagnostic: AlignmentDiagnostic | None = None) -> list[Anchor]:
    """Recover shared scenes despite a different intro or mild linear timeline drift.

    Unlike a pure offset vote, this fallback tests bounded affine hypotheses. It
    still requires visually similar frames, unique monotonic target matches, and
    a plausible slope; it does not relax automatic-export safety thresholds.
    """
    if len(source) < 4 or len(target) < 4:
        if diagnostic is not None:
            diagnostic.stage("feature_fallback", status="insufficient_samples",
                             source_sample_count=len(source), target_sample_count=len(target))
        return []
    candidate_started = time.monotonic()
    candidates = _feature_candidate_pairs(source, target, cancel_event)
    candidate_scan_seconds = round(time.monotonic() - candidate_started, 4)
    if len(candidates) < 8:
        if diagnostic is not None:
            diagnostic.stage("feature_fallback", status="insufficient_visual_candidates",
                             candidate_count=len(candidates), min_candidate_count=8,
                             max_hamming=16, candidate_scan_seconds=candidate_scan_seconds)
        return []

    tolerance = max(0.85, interval * 1.5)
    # Keep hypothesis generation bounded even for high sample counts. Prefer
    # visually strong candidates, then take a deterministic spread of them.
    ranked = sorted(candidates, key=lambda item: (item[2], item[0], item[1]))
    pool_size = min(180, len(ranked))
    if len(ranked) > pool_size:
        pool = [ranked[round(i * (len(ranked) - 1) / (pool_size - 1))]
                for i in range(pool_size)]
    else:
        pool = ranked

    hypotheses: set[tuple[float, float]] = {(1.0, item[4] - item[3]) for item in pool}
    min_separation = max(8.0, interval * 4.0)
    for i, left in enumerate(pool):
        if cancel_event is not None and cancel_event.is_set():
            raise AlignmentError("Frameabgleich abgebrochen.")
        for right in pool[i + 1:]:
            dx = right[3] - left[3]
            dy = right[4] - left[4]
            if dx < min_separation or dy <= 0:
                continue
            slope = dy / dx
            if 0.90 <= slope <= 1.10:
                offset = left[4] - slope * left[3]
                hypotheses.add((round(slope, 6), round(offset, 3)))

    # Each source contributes at most two candidates. Keep their indices in
    # source order so every affine hypothesis can be evaluated without building
    # and sorting thousands of Python dictionaries/lists.
    first = np.full(len(source), -1, dtype=np.int32)
    second = np.full(len(source), -1, dtype=np.int32)
    for position, (source_index, *_rest) in enumerate(candidates):
        if first[source_index] < 0:
            first[source_index] = position
        else:
            second[source_index] = position
    first_safe = np.maximum(first, 0)
    second_safe = np.maximum(second, 0)
    target_indices = np.asarray([item[1] for item in candidates], dtype=np.int32)
    distances = np.asarray([item[2] for item in candidates], dtype=np.uint8)
    source_times = np.asarray([item[3] for item in candidates], dtype=np.float64)
    target_times = np.asarray([item[4] for item in candidates], dtype=np.float64)
    first_distance = distances[first_safe]
    second_distance = distances[second_safe]

    scoring_started = time.monotonic()
    best_model: tuple[tuple[int, float, float], float, float] | None = None
    # Sorted hypotheses make equal-scoring choices reproducible across runs.
    for slope, offset in sorted(hypotheses):
        if cancel_event is not None and cancel_event.is_set():
            raise AlignmentError("Frameabgleich abgebrochen.")
        residuals = np.abs(target_times - (slope * source_times + offset))
        first_residual = residuals[first_safe]
        second_residual = residuals[second_safe]
        valid_first = (first >= 0) & (first_residual <= tolerance)
        valid_second = (second >= 0) & (second_residual <= tolerance)
        choose_first = valid_first & (
            ~valid_second | (first_distance < second_distance) |
            ((first_distance == second_distance) & (first_residual <= second_residual))
        )
        selected = np.where(choose_first, first, np.where(valid_second, second, -1))
        selected = selected[selected >= 0]
        if not len(selected):
            continue
        # Greedily retain a target only if it exceeds the last retained one.
        # That is exactly the strict prefix-maximum condition, but vectorized.
        selected_targets = target_indices[selected]
        prefix_max = np.maximum.accumulate(selected_targets)
        keep = np.empty(len(selected), dtype=np.bool_)
        keep[0] = True
        keep[1:] = selected_targets[1:] > prefix_max[:-1]
        unique = selected[keep]
        score = (len(unique), -float(np.median(distances[unique])),
                 -float(np.median(residuals[unique])))
        if best_model is None or score > best_model[0]:
            best_model = (score, slope, offset)
    hypothesis_scoring_seconds = round(time.monotonic() - scoring_started, 4)

    if best_model is None:
        if diagnostic is not None:
            diagnostic.stage("feature_fallback", status="no_plausible_time_model",
                             candidate_count=len(candidates), pool_count=len(pool),
                             hypotheses_tested=len(hypotheses),
                             candidate_scan_seconds=candidate_scan_seconds,
                             hypothesis_scoring_seconds=hypothesis_scoring_seconds,
                             max_hamming=16, time_tolerance_s=round(tolerance, 4))
        return []
    _, best_slope, best_offset = best_model
    chosen: dict[int, tuple[int, int, float]] = {}
    for source_index, target_index, distance, source_time, target_time in candidates:
        residual = abs(target_time - (best_slope * source_time + best_offset))
        if residual > tolerance:
            continue
        previous = chosen.get(source_index)
        quality = (distance, residual)
        if previous is None or quality < (previous[0], previous[2]):
            chosen[source_index] = (distance, target_index, residual)
    ordered = sorted((idx, value[1], value[0]) for idx, value in chosen.items())
    anchors: list[Anchor] = []
    last_target = -1
    for source_index, target_index, distance in ordered:
        if target_index <= last_target:
            continue
        anchors.append(Anchor(source[source_index].time, target[target_index].time, distance))
        last_target = target_index
    if diagnostic is not None:
        diagnostic.stage("feature_fallback", status="matched",
                         candidate_count=len(candidates), pool_count=len(pool),
                         hypotheses_tested=len(hypotheses), selected_anchors=len(anchors),
                         candidate_scan_seconds=candidate_scan_seconds,
                         hypothesis_scoring_seconds=hypothesis_scoring_seconds,
                         selected_slope=round(best_slope, 7), selected_offset_s=round(best_offset, 4),
                         max_hamming=16, time_tolerance_s=round(tolerance, 4),
                         median_hamming=round(float(np.median([a.hamming_distance for a in anchors])), 4) if anchors else None)
    return anchors



def _long_offset_alignment(
    source: list[FrameSample], target: list[FrameSample],
    source_path: str, target_path: str, source_info: MediaInfo,
    target_info: MediaInfo, effective_interval: float,
    requested_interval: float, max_samples: int, hardware_accel: str,
    cancel_event=None, diagnostic: AlignmentDiagnostic | None = None,
) -> AlignmentResult | None:
    """Try strongly supported timeline shifts without a duration-delta cutoff.

    A master can contain a 2+ minute extra intro/outro while sharing the episode
    itself. Estimate candidate shifts only from distinctive visual fingerprints,
    then verify a monotonic alignment throughout the *whole* common timeline.
    No result bypasses AlignmentResult.safe_for_automatic_export.
    """
    n, m = len(source), len(target)
    if min(n, m) < 16:
        return None
    # Repeated black frames and static title cards must not generate votes.
    source_frequency = Counter(item.phash for item in source)
    target_frequency = Counter(item.phash for item in target)
    source_repeat_limit = max(5, int(n * 0.015))
    target_repeat_limit = max(5, int(m * 0.015))
    probe_indices = [int(round(i * (n - 1) / (min(n, 110) - 1)))
                     for i in range(min(n, 110))]
    bin_width = max(0.8, effective_interval * 1.25)
    votes: dict[int, dict[int, tuple[float, int]]] = {}
    for source_index in probe_indices:
        if cancel_event is not None and cancel_event.is_set():
            raise AlignmentError("Frameabgleich abgebrochen.")
        item = source[source_index]
        if source_frequency[item.phash] > source_repeat_limit:
            continue
        # Take several candidates, not only the two visually nearest images:
        # short repeated cartoon scenes can otherwise obscure the true match.
        candidates = []
        for target_index, other in enumerate(target):
            if target_frequency[other.phash] > target_repeat_limit:
                continue
            distance = (item.phash ^ other.phash).bit_count()
            if distance <= 18:
                candidates.append((distance, target_index))
        for distance, target_index in sorted(candidates)[:6]:
            shift = target[target_index].time - item.time
            bin_index = round(shift / bin_width)
            bucket = votes.setdefault(bin_index, {})
            current = bucket.get(source_index)
            if current is None or distance < current[1]:
                bucket[source_index] = (shift, distance)

    if not votes:
        if diagnostic is not None:
            diagnostic.stage("long_offset", status="no_distinctive_visual_matches",
                             duration_difference_s=round(abs(source_info.duration-target_info.duration), 3),
                             probe_count=len(probe_indices), hash_distance_limit=18)
        return None

    # Neighboring shift bins represent the same hypothesis due to sample phase.
    ranked = sorted(votes, key=lambda key: (-len(votes[key]),
                                           sum(entry[1] for entry in votes[key].values()) / len(votes[key]),
                                           abs(key)))
    hypotheses: list[float] = []
    for key in ranked:
        estimate = float(np.median([value[0] for value in votes[key].values()]))
        if not any(abs(estimate - old) <= bin_width * 1.5 for old in hypotheses):
            hypotheses.append(estimate)
        if len(hypotheses) >= 12:
            break

    target_times = [item.time for item in target]
    tolerance = max(0.85, effective_interval * 1.5)
    sample_count = min(n, m)
    distinct_required = max(16, int(math.ceil(sample_count * 0.06)))
    winning: AlignmentResult | None = None
    candidates_tested: list[dict] = []
    for shift in hypotheses:
        if cancel_event is not None and cancel_event.is_set():
            raise AlignmentError("Frameabgleich abgebrochen.")
        anchors: list[Anchor] = []
        matched_hashes: set[int] = set()
        last_target_index = -1
        for item in source:
            predicted = item.time + shift
            j = bisect_left(target_times, predicted)
            matches = []
            for target_index in range(max(last_target_index + 1, j - 2), min(m, j + 3)):
                other = target[target_index]
                phase_error = abs(other.time - predicted)
                if phase_error > tolerance:
                    continue
                distance = (item.phash ^ other.phash).bit_count()
                if distance <= 18:
                    matches.append((distance, phase_error, target_index))
            if matches:
                distance, _, target_index = min(matches)
                anchors.append(Anchor(item.time, target[target_index].time, distance))
                matched_hashes.add(item.phash)
                last_target_index = target_index
        # A short matching intro/credits section or a collection of duplicated
        # stills must never establish synchronization for an entire episode.
        span = anchors[-1].source_time - anchors[0].source_time if anchors else 0.0
        distinct = len(matched_hashes)
        coverage = len(anchors) / max(1, sample_count)
        # This fast path is intentionally stricter than the generic matcher.
        # If a big middle/end section diverges, one constant offset cannot
        # safely align the remaining audio. Demand evidence near BOTH ends of
        # the predicted common interval, not just a long matching intro.
        shared_start = max(0.0, -shift)
        shared_end = min(source_info.duration, target_info.duration - shift)
        shared_span = max(0.0, shared_end - shared_start)
        edge_tolerance = max(12.0, shared_span * 0.08)
        edge_verified = bool(anchors and shared_span > 0 and
                             anchors[0].source_time <= shared_start + edge_tolerance and
                             anchors[-1].source_time >= shared_end - edge_tolerance)
        eligible = (coverage >= 0.75 and len(anchors) >= 16 and
                    span >= shared_span * 0.82 and edge_verified and
                    distinct >= distinct_required)
        summary = {"offset_candidate_s": round(shift, 3),
                   "anchors": len(anchors), "coverage": round(coverage, 4),
                   "matched_span_s": round(span, 3),
                   "predicted_shared_span_s": round(shared_span, 3),
                   "edge_verified": edge_verified,
                   "distinct_matched_source_hashes": distinct, "eligible": eligible}
        if eligible:
            result = _evaluate_anchors(
                anchors, source_path, target_path, source_info, target_info,
                effective_interval, requested_interval, max_samples, hardware_accel,
                sample_count,
                visual_distinct_hashes=min(len(source_frequency), len(target_frequency)),
            )
            summary["safe"] = result.safe_for_automatic_export
            summary["slope"] = round(result.slope, 6)
            summary["fit_offset_s"] = round(result.offset, 3)
            if result.safe_for_automatic_export and (
                    winning is None or _candidate_rank(result) > _candidate_rank(winning)):
                winning = result
        candidates_tested.append(summary)
        if winning is not None and winning.coverage >= 0.80:
            break
    if diagnostic is not None:
        diagnostic.stage("long_offset", status="safe" if winning else "not_validated",
                         duration_difference_s=round(abs(source_info.duration-target_info.duration), 3),
                         hash_distance_limit=18, time_tolerance_s=round(tolerance, 4),
                         required_distinct_hashes=distinct_required,
                         unique_shift_bins=len(votes),
                         candidates=candidates_tested,
                         selected_offset_s=round(winning.offset, 4) if winning else None)
    return winning


def _robust_affine(anchors: list[Anchor], interval: float,
                   diagnostic: AlignmentDiagnostic | None = None) -> tuple[float, float, list[bool], float]:
    x = np.asarray([a.source_time for a in anchors], dtype=np.float64)
    y = np.asarray([a.target_time for a in anchors], dtype=np.float64)
    if len(x) < 2:
        raise AlignmentError("Nicht genug Zeitanker für eine Zeitabbildung")

    # Robuster Theil-Sen-ähnlicher Startwert aus weit auseinanderliegenden Ankern.
    take = np.linspace(0, len(x) - 1, min(len(x), 160), dtype=int)
    xs, ys = x[take], y[take]
    slopes: list[float] = []
    for i in range(len(xs) - 1):
        dx = xs[i + 1:] - xs[i]
        dy = ys[i + 1:] - ys[i]
        valid = dx >= max(8.0, interval * 4)
        if np.any(valid):
            slopes.extend((dy[valid] / dx[valid]).tolist())
    plausible = [value for value in slopes if 0.5 <= value <= 1.5 and math.isfinite(value)]
    slope = float(np.median(plausible)) if plausible else 1.0
    offset = float(np.median(y - slope * x))

    threshold = max(0.75, interval * 1.20)
    residual = y - (slope * x + offset)
    inliers = np.abs(residual) <= threshold
    if int(inliers.sum()) >= max(4, min(8, len(x) // 3)):
        design = np.column_stack((x[inliers], np.ones(int(inliers.sum()))))
        fitted, fitted_offset = np.linalg.lstsq(design, y[inliers], rcond=None)[0]
        if 0.5 <= fitted <= 1.5:
            slope, offset = float(fitted), float(fitted_offset)
            residual = y - (slope * x + offset)
            inliers = np.abs(residual) <= threshold

    rms = float(np.sqrt(np.mean(np.square(residual[inliers])))) if inliers.any() else float("inf")
    if diagnostic is not None:
        # Ordered segments help identify a mismatched intro, local edit or outro.
        groups = np.array_split(np.arange(len(x)), min(12, len(x)))
        segments = [{"source_start_s": round(float(x[indices[0]]), 3),
                     "source_end_s": round(float(x[indices[-1]]), 3),
                     "count": len(indices),
                     "inliers": int(inliers[indices].sum()),
                     "median_residual_s": round(float(np.median(residual[indices])), 4),
                     "max_abs_residual_s": round(float(np.max(np.abs(residual[indices]))), 4)}
                    for indices in groups if len(indices)]
        diagnostic.stage("affine_fit", slope=round(slope, 8), offset_s=round(offset, 4),
                         inlier_threshold_s=round(threshold, 4),
                         inlier_count=int(inliers.sum()), outlier_count=int((~inliers).sum()),
                         inlier_ratio=round(float(inliers.mean()), 5),
                         rms_error_s=round(rms, 5) if math.isfinite(rms) else None,
                         residual_p05_s=round(float(np.percentile(residual, 5)), 4),
                         residual_p50_s=round(float(np.median(residual)), 4),
                         residual_p95_s=round(float(np.percentile(residual, 95)), 4),
                         segments=segments)
    return slope, offset, inliers.tolist(), rms


def _same_timeline_result(source_path: str, target_path: str,
                          source_info: MediaInfo, target_info: MediaInfo,
                          source_samples: list[FrameSample], target_samples: list[FrameSample],
                          interval: float, max_samples: int, hardware_accel: str,
                          diagnostic: AlignmentDiagnostic | None = None) -> AlignmentResult | None:
    """Recognize alternate audio encodes when their video timelines match directly.

    This is intentionally stricter than episode-name matching: timestamps must line up,
    durations must be close, and the sampled pictures must agree at those same timestamps.
    """
    duration_delta = abs(source_info.duration - target_info.duration)
    duration_tolerance = max(1.25, interval * 2.0)
    if duration_delta > duration_tolerance:
        if diagnostic is not None:
            diagnostic.stage("same_timeline", status="rejected", reason="duration_difference",
                             duration_delta_s=round(duration_delta, 5),
                             duration_tolerance_s=round(duration_tolerance, 5))
        return None
    if min(len(source_samples), len(target_samples)) < 12:
        if diagnostic is not None:
            diagnostic.stage("same_timeline", status="rejected", reason="too_few_samples",
                             source_sample_count=len(source_samples),
                             master_sample_count=len(target_samples), required=12)
        return None

    minimum_distinct = max(8, min(10, int(math.ceil(min(len(source_samples), len(target_samples)) * 0.08))))
    distinct_source = len({sample.phash for sample in source_samples})
    distinct_master = len({sample.phash for sample in target_samples})
    if min(distinct_source, distinct_master) < minimum_distinct:
        if diagnostic is not None:
            diagnostic.stage("same_timeline", status="rejected", reason="insufficient_visual_diversity",
                             distinct_source_hashes=distinct_source,
                             distinct_master_hashes=distinct_master,
                             required_distinct_hashes=minimum_distinct)
        return None
    tolerance = max(0.35, interval * 0.75)
    anchors: list[Anchor] = []
    good = 0
    j = 0
    for src in source_samples:
        while j + 1 < len(target_samples) and abs(target_samples[j + 1].time - src.time) < abs(target_samples[j].time - src.time):
            j += 1
        tgt = target_samples[j]
        if abs(tgt.time - src.time) > tolerance:
            continue
        distance = (src.phash ^ tgt.phash).bit_count()
        anchors.append(Anchor(src.time, tgt.time, distance))
        if distance <= 15:
            good += 1

    required = max(12, int(math.ceil(min(len(source_samples), len(target_samples)) * 0.55)))
    distances = [a.hamming_distance for a in anchors]
    median_hamming = float(np.median(distances)) if distances else None
    agreement = good / max(1, len(anchors))
    if diagnostic is not None:
        diagnostic.stage("same_timeline", status="accepted" if (
            len(anchors) >= required and median_hamming is not None and median_hamming <= 10
            and agreement >= 0.78) else "rejected",
            reason=("insufficient_time_pairs" if len(anchors) < required else
                    "image_hash_disagreement" if median_hamming is not None and
                    (median_hamming > 10 or agreement < 0.78) else "accepted"),
            matched_timestamp_pairs=len(anchors), minimum_pairs=required,
            timestamp_tolerance_s=round(tolerance, 4), good_hash_pairs=good,
            good_hash_threshold=15, agreement=round(agreement, 5), required_agreement=0.78,
            median_hamming=round(median_hamming, 4) if median_hamming is not None else None,
            max_allowed_median_hamming=10,
            p90_hamming=round(float(np.percentile(distances, 90)), 4) if distances else None)
    if len(anchors) < required or median_hamming is None:
        return None
    if median_hamming > 10 or agreement < 0.78:
        return None

    coverage = len(anchors) / max(1, min(len(source_samples), len(target_samples)))
    confidence = min(1.0, 0.42 * min(1.0, coverage) + 0.38 * agreement + 0.20 * max(0.0, 1.0 - median_hamming / 64.0))
    return AlignmentResult(
        source_path=os.path.abspath(source_path), target_path=os.path.abspath(target_path),
        slope=1.0, offset=0.0, anchors=anchors, confidence=confidence,
        inlier_ratio=agreement, coverage=coverage, median_hamming=median_hamming,
        rms_error=0.0, source_duration=source_info.duration, target_duration=target_info.duration,
        sample_interval=interval, warnings=[], requested_interval=interval,
        max_samples=int(max_samples), hardware_accel=hardware_accel,
        visual_distinct_hashes=min(distinct_source, distinct_master),
    )


def _evaluate_anchors(anchors: list[Anchor], source_path: str, target_path: str,
                      source_info: MediaInfo, target_info: MediaInfo,
                      effective_interval: float, requested_interval: float,
                      max_samples: int, hardware_accel: str,
                      sample_count: int,
                      diagnostic: AlignmentDiagnostic | None = None,
                      visual_distinct_hashes: int = 10) -> AlignmentResult:
    """Evaluate a candidate mapping using *unchanged* automatic-export rules."""
    if len(anchors) < 4:
        raise AlignmentError("Zu wenige passende Bildanker gefunden. Prüfe, ob beide Dateien dieselbe Schnittfassung enthalten.")
    slope, offset, inlier_flags, rms = _robust_affine(anchors, effective_interval, diagnostic)
    inlier_count = sum(inlier_flags)
    inlier_ratio = inlier_count / len(anchors)
    coverage = len(anchors) / max(1, sample_count)
    median_hamming = float(np.median([a.hamming_distance for a in anchors]))
    hash_quality = max(0.0, 1.0 - median_hamming / 64.0)
    confidence = min(1.0, max(0.0, 0.42 * min(1.0, coverage) + 0.38 * inlier_ratio + 0.20 * hash_quality))

    warnings: list[str] = []
    if len(anchors) < 8:
        warnings.append("Zu wenige Bildanker für einen sicheren automatischen Export; weitere Frames prüfen.")
    elif len(anchors) < 10:
        warnings.append("Wenige übereinstimmende Bildanker; Synchronisation manuell kontrollieren.")
    if coverage < 0.55:
        warnings.append("Geringe Bildankerabdeckung; möglicherweise unterscheiden sich Szenen oder Schnittfassungen.")
    if inlier_ratio < 0.65 or rms > max(0.75, effective_interval * 1.20):
        warnings.append("Der Zeitversatz ist nicht durch eine einzige lineare Korrektur erklärbar; Schnittunterschiede oder lokaler Drift möglich.")
    if not 0.90 <= slope <= 1.10:
        warnings.append(f"Ungewöhnliche Zeitdehnung erkannt (Faktor {slope:.5f}); Export nur nach Prüfung.")
    mapped_start = offset
    mapped_end = slope * source_info.duration + offset
    overlap = max(0.0, min(target_info.duration, mapped_end) - max(0.0, mapped_start))
    if overlap / max(target_info.duration, 0.001) < 0.65:
        warnings.append("Nur ein Teil der Referenzlaufzeit wird von dieser Fassung abgedeckt.")
    if median_hamming > 15:
        warnings.append("Die passenden Frames unterscheiden sich deutlich; Bildanker könnten ungenau sein.")
    if confidence < 0.62:
        warnings.append("Niedrige Synchronisationskonfidenz. Kein unbeaufsichtigter Export empfohlen.")
    if visual_distinct_hashes < 8:
        warnings.append("Wenige unterschiedliche Bildinhalte; wiederholte Standbilder erlauben keinen sicheren automatischen Abgleich.")

    result = AlignmentResult(
        source_path=os.path.abspath(source_path), target_path=os.path.abspath(target_path),
        slope=slope, offset=offset, anchors=anchors, confidence=confidence,
        inlier_ratio=inlier_ratio, coverage=coverage, median_hamming=median_hamming,
        rms_error=rms, source_duration=source_info.duration,
        target_duration=target_info.duration, sample_interval=effective_interval,
        warnings=warnings, requested_interval=requested_interval,
        max_samples=int(max_samples), hardware_accel=hardware_accel,
        visual_distinct_hashes=int(visual_distinct_hashes),
    )
    if result.max_unverified_middle_ratio > MAX_UNVERIFIED_MIDDLE_RATIO:
        result.warnings.append("Langer ungeprüfter Abschnitt zwischen Bildankern; automatische Synchronisierung nicht sicher.")
    if result.max_unverified_edge_ratio > MAX_UNVERIFIED_EDGE_RATIO:
        result.warnings.append("Episodenanfang oder -ende nicht ausreichend durch Bildanker bestätigt; automatische Synchronisierung nicht sicher.")
    return result


def _candidate_metrics(result: AlignmentResult | None) -> dict:
    if result is None:
        return {"present": False, "reason": "fewer_than_four_anchors"}
    return {"present": True, "safe": result.safe_for_automatic_export,
            "sections": len(result.segments),
            "anchors": len(result.anchors), "coverage": round(result.coverage, 5),
            "inlier_ratio": round(result.inlier_ratio, 5),
            "median_hamming": round(result.median_hamming, 4),
            "rms_error_s": round(result.rms_error, 5) if math.isfinite(result.rms_error) else None,
            "slope": round(result.slope, 8), "offset_s": round(result.offset, 4),
            "confidence": round(result.confidence, 5), "warnings": list(result.warnings)}


def _candidate_rank(result: AlignmentResult) -> tuple:
    """Safe status first; then temporal agreement, coverage, and visual quality."""
    return (int(result.safe_for_automatic_export),
            round(result.inlier_ratio, 4), round(result.coverage, 4),
            -round(result.rms_error, 4), -round(result.median_hamming, 4))


def _align_videos_impl(source_path: str, target_path: str, interval: float = 0.5,
                 progress: Progress | None = None, cancel_event=None,
                 max_samples: int = 1800, hardware_accel: str = "auto",
                 source_info: MediaInfo | None = None,
                 target_info: MediaInfo | None = None,
                 diagnostic: AlignmentDiagnostic | None = None,
                 frame_cache_enabled: bool = True,
                 sampling_workers: int = 1) -> AlignmentResult:
    """Map source video time to target/master video time using perceptual frame alignment.

    Callers running a batch may pass already-probed metadata to avoid launching
    FFprobe a second time for the same files. Paths are verified before reuse.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise AlignmentError("Analyse abgebrochen.")
    probe_started = time.monotonic()
    expected_source = os.path.abspath(source_path)
    expected_target = os.path.abspath(target_path)
    metadata_reused = (source_info is not None and target_info is not None and
                       os.path.abspath(source_info.path) == expected_source and
                       os.path.abspath(target_info.path) == expected_target)
    if source_info is None or os.path.abspath(source_info.path) != expected_source:
        source_info = probe_media(source_path)
    if target_info is None or os.path.abspath(target_info.path) != expected_target:
        target_info = probe_media(target_path)
    if not source_info.video or not target_info.video:
        raise AlignmentError("Beide Dateien müssen mindestens eine Videospur besitzen.")
    if source_info.duration <= 0 or target_info.duration <= 0:
        raise AlignmentError("Eine Videolaufzeit konnte nicht zuverlässig ermittelt werden.")

    if diagnostic is not None:
        diagnostic.metadata(source_info, target_info)
        diagnostic.stage("probe_runtime", wall_seconds=round(time.monotonic() - probe_started, 4),
                         metadata_reused=metadata_reused)
    if not 200 <= int(max_samples) <= 5000:
        raise ValueError("max_samples muss zwischen 200 und 5000 liegen")
    if hardware_accel not in {"auto", "none", "ffmpeg", "cuda", "vaapi", "amd-vaapi", "intel-vaapi", "qsv", "d3d11", "drm"}:
        raise ValueError("Unbekannter Hardware-Decoding-Modus")
    # Bound the O(n*m) monotone matcher. This is the main memory/performance knob:
    # 1400 samples needs ~14 MB of working arrays; 2400 samples needs ~35 MB.
    effective_interval = max(interval, max(source_info.duration, target_info.duration) / int(max_samples))
    if diagnostic is not None:
        diagnostic.stage("sampling_plan", requested_interval_s=round(interval, 5),
                         effective_interval_s=round(effective_interval, 5),
                         max_samples=int(max_samples),
                         expected_source_samples=math.ceil(source_info.duration / effective_interval),
                         expected_master_samples=math.ceil(target_info.duration / effective_interval))
    source_samples, target_samples = _sample_video_pair(
        source_path, target_path, source_info.duration, target_info.duration,
        effective_interval, progress, cancel_event, hardware_accel, diagnostic,
        frame_cache_enabled, sampling_workers,
        source_info.video[0].codec_name, target_info.video[0].codec_name,
    )
    # First test for the common case: same episode/video timeline, different audio
    # track or re-encode. A same-timestamp visual check is both faster and more
    # reliable than forcing these files through global sequence alignment.
    identity = _same_timeline_result(
        source_path, target_path, source_info, target_info,
        source_samples, target_samples, effective_interval, max_samples, hardware_accel, diagnostic,
    )
    if identity is not None:
        if diagnostic is not None:
            diagnostic.event("method_selected", method="same_timeline")
        return identity
    # Long or short extra segments are not automatically disqualifying. A large
    # duration delta skips the strict same-timestamp fast path above, so first
    # try to locate a genuinely shared episode with a constant shifted timeline.
    if abs(source_info.duration - target_info.duration) >= 30.0:
        if progress:
            progress("Gemeinsame Szenen trotz Laufzeitunterschied suchen", 0, 1)
        shifted = _long_offset_alignment(
            source_samples, target_samples, source_path, target_path,
            source_info, target_info, effective_interval, interval,
            max_samples, hardware_accel, cancel_event, diagnostic,
        )
        if shifted is not None:
            if diagnostic is not None:
                diagnostic.event("method_selected", method="long_offset_verified")
            if progress:
                progress("Gemeinsame Szenen trotz Laufzeitunterschied suchen", 1, 1)
            return shifted
        if progress:
            progress("Gemeinsame Szenen trotz Laufzeitunterschied suchen", 1, 1)
    started_sequence = time.monotonic()
    anchors = _sequence_alignment(source_samples, target_samples, progress, cancel_event, diagnostic)
    if diagnostic is not None:
        diagnostic.event("sequence_runtime", seconds=round(time.monotonic() - started_sequence, 4))
    sample_count = min(len(source_samples), len(target_samples))
    min_distinct = min(len({item.phash for item in source_samples}),
                       len({item.phash for item in target_samples}))
    sequence = (_evaluate_anchors(anchors, source_path, target_path, source_info, target_info,
                                 effective_interval, interval, max_samples, hardware_accel, sample_count,
                                 visual_distinct_hashes=min_distinct)
                if len(anchors) >= 4 else None)
    primary_quality = len(anchors) / max(1, sample_count)
    selected, method = sequence, "sequence"
    fallback_needed = (sequence is None or not sequence.safe_for_automatic_export)
    if fallback_needed:
        # Test visually confirmed cuts before the more expensive feature-vote
        # fallback. Most genuine edit differences are already evident in DP.
        piecewise = detect_piecewise(
            anchors, {item.time: item.phash for item in source_samples},
            expected_source, expected_target, source_info.duration,
            target_info.duration, effective_interval, sample_count,
            interval, max_samples, hardware_accel,
        )
        if diagnostic is not None:
            diagnostic.stage("piecewise_candidate",
                             accepted=piecewise is not None,
                             segment_count=len(piecewise.segments) if piecewise else 0,
                             source_anchor_count=len(anchors))
        if piecewise is not None:
            selected, method = piecewise, "piecewise_verified"
            fallback_needed = False
    if diagnostic is not None:
        diagnostic.stage("fallback_decision", sequence_anchors=len(anchors),
                         sequence_coverage=round(primary_quality, 5),
                         sequence_safe=sequence.safe_for_automatic_export if sequence else False,
                         sequence_inlier_ratio=round(sequence.inlier_ratio, 5) if sequence else None,
                         sequence_rms_s=round(sequence.rms_error, 5) if sequence else None,
                         fallback_started=fallback_needed,
                         trigger=("unsafe_sequence" if fallback_needed else
                                  "piecewise_verified" if selected and selected.segments else "not_needed"))
    fallback = []
    if fallback_needed:
        if progress:
            progress("Alternativen Bildabgleich prüfen", 0, 1)
        started_fallback = time.monotonic()
        fallback = _feature_anchor_fallback(
            source_samples, target_samples, effective_interval, cancel_event, diagnostic,
        )
        if diagnostic is not None:
            diagnostic.event("feature_fallback_runtime", seconds=round(time.monotonic() - started_fallback, 4))
        alternate = (_evaluate_anchors(fallback, source_path, target_path, source_info, target_info,
                                      effective_interval, interval, max_samples, hardware_accel, sample_count,
                                      visual_distinct_hashes=min_distinct)
                     if len(fallback) >= 4 else None)
        if diagnostic is not None:
            diagnostic.stage("candidate_comparison",
                             sequence=_candidate_metrics(sequence),
                             feature_fallback=_candidate_metrics(alternate))
        # Prefer a validated safe hypothesis. If neither is safe, preserve the
        # best evidence, but do not auto-export. Never select merely by anchor count.
        if alternate is not None and (selected is None or
             (alternate.safe_for_automatic_export and not selected.safe_for_automatic_export) or
             (alternate.safe_for_automatic_export == selected.safe_for_automatic_export and
              _candidate_rank(alternate) > _candidate_rank(selected))):
            selected, method = alternate, "feature_fallback"
        if progress:
            progress("Alternativen Bildabgleich prüfen", 1, 1)
    if diagnostic is not None:
        diagnostic.event("method_selected", method=method, final_anchors=len(selected.anchors) if selected else 0)
    if selected is None:
        raise AlignmentError("Zu wenige passende Bildanker gefunden. Prüfe, ob beide Dateien dieselbe Schnittfassung enthalten.")
    # Record residual details only for the chosen mapping.
    if diagnostic is not None and not selected.segments:
        _robust_affine(selected.anchors, effective_interval, diagnostic)
    elif diagnostic is not None:
        diagnostic.stage("piecewise_fit", segments=[{
            "source_start_s": round(s.source_start, 4),
            "source_end_s": round(s.source_end, 4),
            "master_start_s": round(s.target_start, 4),
            "master_end_s": round(s.target_end, 4),
            "slope": round(s.slope, 7), "anchors": s.anchor_count,
            "median_hamming": round(s.median_hamming, 3),
            "rms_error_s": round(s.rms_error, 4),
        } for s in selected.segments])
    if diagnostic is not None:
        # Sparse clusters may indicate an episode that only shares an intro/outro.
        selected_times = [a.source_time for a in selected.anchors]
        source_span = max(selected_times) - min(selected_times) if selected_times else 0.0
        diagnostic.stage("temporal_distribution",
                         source_covered_span_s=round(source_span, 4),
                         source_span_ratio=round(source_span / max(source_info.duration, 0.001), 5),
                         earliest_source_anchor_s=round(min(selected_times), 4) if selected_times else None,
                         latest_source_anchor_s=round(max(selected_times), 4) if selected_times else None)
    return selected


def align_videos(source_path: str, target_path: str, interval: float = 0.5,
                 progress: Progress | None = None, cancel_event=None,
                 max_samples: int = 1800, hardware_accel: str = "auto",
                 source_info: MediaInfo | None = None,
                 target_info: MediaInfo | None = None,
                 batch_context: dict | None = None,
                 diagnostic_sink=None,
                 frame_cache_enabled: bool = True,
                 sampling_workers: int = 1) -> AlignmentResult:
    """Run visual alignment and always persist bounded per-pair diagnostics.

    The diagnostic reporter cannot change the computed result or turn an unsafe
    alignment into an acceptable export. It also records failed/cancelled runs.
    """
    diagnostic = AlignmentDiagnostic(source_path, target_path, interval, max_samples, hardware_accel)
    profiled_since = time.monotonic()
    if batch_context:
        # Store only explicit scalar metadata, never full paths or raw user data.
        diagnostic.stage(
            "batch_context",
            position=int(batch_context["position"]),
            total=int(batch_context["total"]),
            season=(int(batch_context["season"]) if batch_context.get("season") is not None else None),
            episode=(int(batch_context["episode"]) if batch_context.get("episode") is not None else None),
        )
    try:
        result = _align_videos_impl(
            source_path, target_path, interval, progress, cancel_event,
            max_samples, hardware_accel, source_info, target_info, diagnostic,
            frame_cache_enabled, sampling_workers,
        )
        selected = next((event["method"] for event in diagnostic.payload["events"]
                         if event.get("event") == "method_selected"), "unknown")
        diagnostic.complete(result, selected)
        return result
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            diagnostic.event("cancellation_requested",
                             reason=str(getattr(cancel_event, "reason", None) or "external_or_unspecified"),
                             requested_utc=getattr(cancel_event, "requested_utc", None))
        diagnostic.failure(exc)
        raise
    finally:
        elapsed = time.monotonic() - profiled_since
        stages = diagnostic.payload["stages"]
        probe_s = float(stages.get("probe_runtime", {}).get("wall_seconds", 0.0))
        sampling_s = float(stages.get("sample_runtime", {}).get("wall_seconds", 0.0))
        matching_s = max(0.0, elapsed - probe_s - sampling_s) if sampling_s else 0.0
        measured = {"probe": probe_s, "sampling": sampling_s, "matching": matching_s}
        included = {key: value for key, value in measured.items() if value > 0}
        diagnostic.stage("pipeline_profile", total_wall_seconds=round(elapsed, 4),
                         probe_wall_seconds=round(probe_s, 4),
                         sampling_wall_seconds=round(sampling_s, 4),
                         matching_wall_seconds=round(matching_s, 4),
                         longest_phase=max(included, key=included.get) if included else "unavailable",
                         complete=diagnostic.payload["decision"].get("status") in ("safe", "unsafe"),
                         worker_count=stages.get("sampling_scheduler", {}).get("effective_workers", 1))
        diagnostic.save()
        if diagnostic_sink is not None:
            try:
                diagnostic_sink(diagnostic.payload)
            except Exception:
                # Reporting must never alter an alignment/export decision.
                import logging
                logging.getLogger("dubsync").exception("Stapel-Diagnoseübertragung fehlgeschlagen")
