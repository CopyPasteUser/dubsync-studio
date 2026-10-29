"""Bounded FFmpeg frame sampling with optional measured GPU decode.

FFmpeg samples/scales video in native code and streams only 32x32 grayscale
images into Python. A working GPU driver is not assumed from -hwaccels alone.
"""
from __future__ import annotations

import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    import numpy as np


FRAME_SIZE = 32 * 32


class SamplingBackendError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DecoderCandidate:
    """An FFmpeg hardware *request*, not a proven working GPU decoder."""

    key: str
    accel: str
    device: str | None = None


@lru_cache(maxsize=1)
def ffmpeg_hardware_accels() -> frozenset[str]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return frozenset()
    try:
        process = subprocess.run([ffmpeg, "-hide_banner", "-hwaccels"],
                                 capture_output=True, text=True, timeout=4,
                                 check=False)
        if process.returncode == 0:
            return frozenset(line.strip().lower() for line in process.stdout.splitlines()[1:]
                             if line.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return frozenset()


def vaapi_render_device(vendor: str | None = None) -> str | None:
    """Return an accessible Linux DRM render node, optionally restricted by vendor.

    DRM vendor IDs are read from sysfs; a node merely being visible does not
    prove a usable VAAPI driver. The actual FFmpeg decode remains the test.
    """
    if not sys.platform.startswith("linux"):
        return None
    vendor_ids = {"amd": "0x1002", "intel": "0x8086"}
    if vendor is not None and vendor not in vendor_ids:
        return None
    nodes = []
    for path in sorted(Path("/dev/dri").glob("renderD*")):
        if not os.access(path, os.R_OK | os.W_OK):
            continue
        try:
            vendor_id = (Path("/sys/class/drm") / path.name / "device/vendor").read_text(
                encoding="ascii").strip().lower()
        except OSError:
            vendor_id = ""
        if vendor is not None and vendor_id != vendor_ids[vendor]:
            continue
        # Preserve the existing default preference for AMD on hybrid systems.
        # Explicit Intel/AMD modes select the vendor even when both are present.
        nodes.append((vendor_id != "0x1002", str(path)))
    return sorted(nodes)[0][1] if nodes else None


def likely_gpu_accel() -> str | None:
    """Choose a plausible device; capability listing is not driver verification."""
    candidates = gpu_decode_candidates()
    return candidates[0].accel if candidates else None


def gpu_decode_candidates() -> list[DecoderCandidate]:
    """Discover plausible decoders, including the Intel iGPU on hybrid hosts.

    This is intentionally cheap: no media probes, drivers or device contexts
    are opened. Each proposed backend must pass the content-matching benchmark
    before automatic selection; a driver can fail even with a visible DRM node.
    """
    offered = ffmpeg_hardware_accels()
    found: list[DecoderCandidate] = []
    if "cuda" in offered and (sys.platform == "win32" or
                              (sys.platform.startswith("linux") and
                               any(Path("/dev").glob("nvidia[0-9]*")))):
        found.append(DecoderCandidate("cuda", "cuda"))
    if sys.platform.startswith("linux") and "vaapi" in offered:
        vendors = {"0x1002": "amd-vaapi", "0x8086": "intel-vaapi"}
        for node in sorted(Path("/dev/dri").glob("renderD*")):
            if not os.access(node, os.R_OK | os.W_OK):
                continue
            try:
                vendor = (Path("/sys/class/drm") / node.name / "device/vendor").read_text(
                    encoding="ascii").strip().lower()
            except OSError:
                vendor = ""
            name = vendors.get(vendor, "vaapi")
            found.append(DecoderCandidate(f"{name}@{node.name}", "vaapi", str(node)))
    if sys.platform == "win32" and "d3d11va" in offered:
        found.append(DecoderCandidate("d3d11va", "d3d11va"))
    # Bound the cost of per-video benchmarks even on multi-GPU workstations.
    return found[:4]


def _capture_command(path: str, interval: float, max_frames: int,
                     accel: str | None, limit_seconds: float | None,
                     device: str | None = None,
                     decode_threads: int | None = None) -> list[str]:
    binary = shutil.which("ffmpeg")
    if binary is None:
        raise SamplingBackendError("FFmpeg ist nicht installiert.")
    if accel is not None and accel not in ffmpeg_hardware_accels():
        raise SamplingBackendError(f"FFmpeg unterstützt {accel.upper()} in diesem Build nicht.")
    command = [binary, "-hide_banner", "-nostdin", "-loglevel", "error"]
    if accel:
        command += ["-hwaccel", accel]
        if device is not None:
            if accel != "vaapi" or not sys.platform.startswith("linux"):
                raise SamplingBackendError("Explizite DRM-Geräteauswahl ist nur für Linux-VAAPI zulässig.")
            valid_devices = {str(node) for node in Path("/dev/dri").glob("renderD*")}
            if device not in valid_devices or not os.access(device, os.R_OK | os.W_OK):
                raise SamplingBackendError("VAAPI-Rendergerät ist nicht erreichbar.")
            command += ["-hwaccel_device", device]
    if decode_threads is not None:
        threads = max(1, int(decode_threads))
    else:
        try:
            import cv2
            threads = int(cv2.getNumThreads())
        except (ImportError, AttributeError, ValueError):
            threads = int(os.cpu_count() or 2)
    # The adaptive preset already caps the request. Large workstations may
    # use more than twelve threads without a hidden FFmpeg-side limit.
    command += ["-threads", str(max(1, min(int(os.cpu_count() or threads), threads))), "-i", path]
    command += ["-map", "0:v:0", "-an", "-sn", "-dn"]
    if limit_seconds is not None:
        command += ["-t", f"{limit_seconds:.3f}"]
    command += [
        "-vf", f"fps=fps=1/{interval:.9f}:start_time=0:round=near,"
               "format=gray,scale=32:32:flags=area",
        "-filter_threads", "1", "-frames:v", str(max_frames),
        "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    return command


def ffmpeg_sample(path: str, duration: float, interval: float,
                  fingerprint: Callable[[np.ndarray], int], *,
                  accel: str | None = None, device: str | None = None,
                  limit_seconds: float | None = None,
                  cancel_event=None, progress=None, side: str = "Video",
                  stall_timeout: float = 90.0,
                  decode_threads: int | None = None) -> list[tuple[float, int]]:
    """Return complete samples or raise; partial/broken GPU output is never used."""
    import numpy as np
    if interval <= 0 or duration <= 0 or not math.isfinite(interval) or not math.isfinite(duration):
        raise ValueError("Ungültige Laufzeit oder Sample-Abstand.")
    if cancel_event is not None and cancel_event.is_set():
        raise SamplingBackendError("Videoanalyse abgebrochen.")
    effective_duration = min(duration, limit_seconds) if limit_seconds is not None else duration
    max_frames = min(12000, max(4, int(math.ceil(effective_duration / interval)) + 2))
    command = _capture_command(path, interval, max_frames, accel, limit_seconds,
                               device, decode_threads)
    collected: list[tuple[float, int]] = []
    chunks: queue.Queue[tuple[str, bytes | BaseException | None]] = queue.Queue(maxsize=12)
    stop_reader = threading.Event()
    log = tempfile.TemporaryFile(mode="w+b")
    process: subprocess.Popen | None = None
    reader: threading.Thread | None = None

    def read_frames():
        try:
            assert process is not None and process.stdout is not None
            while not stop_reader.is_set():
                payload = process.stdout.read(FRAME_SIZE)
                if not payload:
                    break
                while not stop_reader.is_set():
                    try:
                        chunks.put(("frame", payload), timeout=0.2)
                        break
                    except queue.Full:
                        continue
        except BaseException as exc:
            while not stop_reader.is_set():
                try:
                    chunks.put(("error", exc), timeout=0.2)
                    break
                except queue.Full:
                    pass
        finally:
            while not stop_reader.is_set():
                try:
                    chunks.put(("end", None), timeout=0.2)
                    break
                except queue.Full:
                    pass

    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=log, stdin=subprocess.DEVNULL,
                                   bufsize=FRAME_SIZE * 4)
        reader = threading.Thread(target=read_frames, name="dubsync-frame-reader", daemon=True)
        reader.start()
        last_activity = time.monotonic()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise SamplingBackendError("Videoanalyse abgebrochen.")
            try:
                kind, data = chunks.get(timeout=0.2)
            except queue.Empty:
                if time.monotonic() - last_activity > stall_timeout:
                    raise SamplingBackendError("FFmpeg liefert keine Frames mehr (Timeout).")
                continue
            last_activity = time.monotonic()
            if kind == "end":
                break
            if kind == "error":
                raise SamplingBackendError(f"Frame-Reader fehlgeschlagen: {data}")
            assert isinstance(data, bytes)
            if len(data) != FRAME_SIZE:
                raise SamplingBackendError("FFmpeg lieferte einen unvollständigen Videoframe.")
            frame = np.frombuffer(data, dtype=np.uint8).reshape((32, 32))
            collected.append((round((len(collected)) * interval, 6), fingerprint(frame)))
            if progress and len(collected) % 20 == 0:
                progress(side, len(collected), max_frames)
        try:
            exit_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise SamplingBackendError("FFmpeg beendet sich nach Frame-Ende nicht.") from exc
        if exit_code != 0:
            log.seek(0)
            detail = log.read(5000).decode("utf-8", errors="replace").strip()[-500:]
            raise SamplingBackendError(f"FFmpeg-Dekodierung fehlgeschlagen: {detail or exit_code}")
        if len(collected) < 4:
            raise SamplingBackendError("Zu wenige FFmpeg-Samples für einen belastbaren Abgleich.")
        if progress:
            progress(side, len(collected), len(collected))
        return collected
    except OSError as exc:
        raise SamplingBackendError(f"FFmpeg konnte nicht gestartet werden: {exc}") from exc
    finally:
        stop_reader.set()
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdout is not None:
                process.stdout.close()
        if reader is not None:
            reader.join(timeout=1.0)
        log.close()


def benchmark_decoders(path: str, duration: float, interval: float,
                       fingerprint: Callable[[np.ndarray], int], *,
                       gpu: str | None = None, cancel_event=None,
                       gpu_device: str | None = None,
                       seconds: float = 8.0) -> dict[str, dict[str, float | int | str]]:
    """Backwards-compatible single-GPU benchmark (the CPU is measured once)."""
    candidates = [DecoderCandidate(gpu, gpu, gpu_device)] if gpu is not None else []
    return benchmark_decoder_candidates(path, duration, interval, fingerprint,
                                        candidates=candidates, cancel_event=cancel_event,
                                        seconds=seconds)


def benchmark_decoder_candidates(path: str, duration: float, interval: float,
                                 fingerprint: Callable[[np.ndarray], int], *,
                                 candidates: list[DecoderCandidate], cancel_event=None,
                                 seconds: float = 8.0
                                 ) -> dict[str, dict[str, float | int | str]]:
    """Compare CPU and every GPU on identical frames, without repeating CPU work.

    Sequential measurements avoid two adapters fighting over the same memory
    bandwidth. No GPU is accepted merely because FFmpeg listed an interface.
    """
    results: dict[str, dict[str, float | int | str]] = {}
    cpu_frames: list[tuple[float, int]] | None = None
    for candidate in [None, *candidates]:
        name = candidate.key if candidate is not None else "cpu"
        started = time.monotonic()
        try:
            frames = ffmpeg_sample(path, duration, interval, fingerprint,
                                   accel=candidate.accel if candidate else None,
                                   device=candidate.device if candidate else None,
                                   limit_seconds=min(seconds, duration),
                                   cancel_event=cancel_event,
                                   stall_timeout=15.0)
            elapsed = max(0.001, time.monotonic() - started)
            results[name] = {"frames": len(frames), "seconds": round(elapsed, 3),
                             "samples_per_second": round(len(frames) / elapsed, 2)}
            if candidate is None:
                cpu_frames = frames
            elif cpu_frames is not None:
                count = min(len(cpu_frames), len(frames))
                if count:
                    distances = [(cpu_frames[i][1] ^ frames[i][1]).bit_count()
                                 for i in range(count)]
                    results[name]["frame_match_ratio"] = round(
                        sum(value <= 16 for value in distances) / max(len(cpu_frames), len(frames)), 3)
                    results[name]["median_hamming_to_cpu"] = int(sorted(distances)[count // 2])
        except SamplingBackendError as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise
            results[name] = {"error": str(exc)}
    return results
