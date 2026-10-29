"""System-aware performance defaults and conservative hardware-decoder detection."""
from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path
from typing import Any

# Stable preset IDs preserve existing configuration and project compatibility.
# Resource profiles must not change frame density, match thresholds or audio
# quality: the same analysis should yield the same result on every preset.
ANALYSIS_DEFAULTS: dict[str, int | float] = {
    "sample_interval": 0.75, "max_samples": 1200, "audio_bitrate_kbps": 128,
}
PRESETS = ("fast", "balanced", "quality")
PROFILE_LABELS = {"system": "Auto", "fast": "Leicht", "balanced": "Mittel",
                  "quality": "Stark", "custom": "Benutzerdefiniert"}
HARDWARE_MODES = ("auto", "none", "ffmpeg", "cuda", "vaapi", "amd-vaapi", "intel-vaapi", "qsv", "d3d11", "drm")


def _cpu_count() -> int:
    """Return CPUs available to this process, respecting affinity and cgroup quota."""
    available = max(1, int(os.cpu_count() or 2))
    try:
        affinity = os.sched_getaffinity(0)
        if affinity:
            available = min(available, len(affinity))
    except (AttributeError, OSError):
        pass

    # Containers and some systemd services may have a CPU quota even when the
    # cpuset exposes more logical CPUs.
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="ascii").split()[:2]
        if quota != "max":
            quota_cpus = max(1, int(int(quota) / max(1, int(period))))
            available = min(available, quota_cpus)
    except (OSError, ValueError, IndexError):
        try:
            quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text().strip())
            period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text().strip())
            if quota > 0 and period > 0:
                available = min(available, max(1, quota // period))
        except (OSError, ValueError):
            pass
    return max(1, available)


def _physical_core_count() -> int:
    # Linux: count physical/core pairs when the kernel exposes them.
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
        pairs: set[tuple[str, str]] = set()
        current: dict[str, str] = {}
        for line in text.splitlines() + [""]:
            if not line.strip():
                if current.get("physical id") is not None and current.get("core id") is not None:
                    pairs.add((current["physical id"], current["core id"]))
                current = {}
            elif ":" in line:
                key, value = line.split(":", 1)
                if key.strip() in {"physical id", "core id"}:
                    current[key.strip()] = value.strip()
        if pairs:
            return max(1, min(len(pairs), _cpu_count()))
    except OSError:
        pass
    # Conservative fallback: do not assume hyperthreading exists.
    return _cpu_count()


def _memory_bytes() -> int:
    """Detect RAM visible to this process, respecting common container limits."""
    total = 0
    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            match = re.match(r"^(MemTotal|MemAvailable):\s+(\d+)\s+kB", line)
            if match:
                values[match.group(1)] = int(match.group(2)) * 1024
        total = values.get("MemTotal", 0)
    except OSError:
        pass
    if total <= 0:
        try:
            total = int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
        except (ValueError, OSError, AttributeError):
            total = 8 * 1024**3

    # Cgroup v2 / v1 limits can be far lower than host RAM.
    for path in (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if raw and raw != "max":
                limit = int(raw)
                if 64 * 1024**2 < limit < total:
                    total = limit
                    break
        except (OSError, ValueError):
            continue
    return max(512 * 1024**2, total)


def _gpu_devices() -> list[str]:
    """Read GPU vendor IDs from DRM sysfs without launching slow external commands."""
    vendors = {"0x8086": "Intel", "0x1002": "AMD", "0x10de": "NVIDIA", "0x10005": "Apple"}
    found: list[str] = []
    for device in Path("/sys/class/drm").glob("card[0-9]*/device/vendor"):
        try:
            name = vendors.get(device.read_text(encoding="ascii").strip().lower())
            if name and name not in found:
                found.append(name)
        except OSError:
            pass
    return found


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("model name") and ":" in line:
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine() or "Unbekannte CPU"


def system_specs() -> dict[str, Any]:
    """A lightweight hardware snapshot used to choose first-run defaults."""
    logical = _cpu_count()
    physical = max(1, min(_physical_core_count(), logical))
    ram_bytes = _memory_bytes()
    return {
        "logical_cpus": logical,
        "physical_cores": physical,
        "cpu_model": _cpu_model(),
        "ram_bytes": ram_bytes,
        "ram_gib": ram_bytes / (1024**3),
        "gpu_vendors": _gpu_devices(),
    }


def recommended_profile(specs: dict[str, Any] | None = None) -> str:
    specs = specs or system_specs()
    cores = int(specs.get("physical_cores", specs.get("logical_cpus", 2)) or 2)
    logical = int(specs.get("logical_cpus", cores) or cores)
    ram = float(specs.get("ram_gib", 8.0) or 8.0)
    if logical <= 2 or ram < 8.0:
        return "fast"
    if cores >= 8 and logical >= 12 and ram >= 24.0:
        return "quality"
    return "balanced"


def _recommended_values(preset: str, specs: dict[str, Any]) -> dict[str, Any]:
    """Allocate three resource budgets without changing alignment inputs.

    CPU affinity/cgroups and RAM restrict workers; runtime load, NAS and GPU
    benchmarks may reduce decoder concurrency further for each job.
    """
    chosen = preset if preset in PRESETS else "balanced"
    values: dict[str, Any] = dict(ANALYSIS_DEFAULTS)
    physical = max(1, int(specs.get("physical_cores", 2) or 2))
    logical = max(1, int(specs.get("logical_cpus", physical) or physical))
    ram = max(0.5, float(specs.get("ram_gib", 8.0) or 8.0))
    if chosen == "fast":
        threads = max(1, (logical + 3) // 4)
        probes = 1
    elif chosen == "balanced":
        threads = max(1, (logical + 1) // 2)
        probes = min(4, max(1, (physical + 1) // 2))
    else:
        # Leave one logical thread for the desktop/OS on multi-core systems.
        threads = logical - 1 if logical >= 4 else logical
        probes = min(8, physical)
    values["cpu_threads"] = min(logical, threads)
    values["probe_workers"] = max(1, min(logical, probes))
    values["sampling_workers"] = (2 if chosen == "quality" and logical >= 4
                                  and ram >= 8 else 1)
    values["prewarm_metadata"] = chosen == "quality" and ram >= 8
    if ram < 6.0:
        values["cpu_threads"] = min(values["cpu_threads"], 2 if chosen != "quality" else 4)
        values["probe_workers"] = 1
        values["sampling_workers"] = 1
    elif ram < 10.0:
        values["probe_workers"] = min(values["probe_workers"], 2)
    return values


def settings_for_preset(preset: str, specs: dict[str, Any] | None = None) -> dict[str, Any]:
    specs = specs if specs is not None else system_specs()
    chosen = recommended_profile(specs) if preset == "system" else preset
    return _recommended_values(chosen, specs)


def _hardware_decoder_available(specs: dict[str, Any]) -> bool:
    from .ffmpeg_sampling import likely_gpu_accel
    if likely_gpu_accel() is not None:
        return True
    modes = supported_hardware_modes()
    if sys.platform.startswith("linux"):
        render_nodes = list(Path("/dev/dri").glob("renderD*"))
        return bool(render_nodes and any(mode in modes for mode in ("vaapi", "qsv", "drm")))
    if sys.platform == "win32":
        return "d3d11" in modes
    return any(mode in modes for mode in ("vaapi", "qsv", "d3d11", "drm"))


def default_performance_settings() -> dict[str, Any]:
    specs = system_specs()
    chosen = recommended_profile(specs)
    result: dict[str, Any] = _recommended_values(chosen, specs)
    result.update({
        "preset": "system",
        # Auto includes a safe CPU path. A newly attached/usable GPU can be
        # discovered at the next run without resetting preferences.
        "hardware_accel": "auto",
        "cache_enabled": False,
        "frame_cache_enabled": True,
        "prewarm_metadata": bool(result.get("prewarm_metadata", False)),
        "probe_workers": int(result.get("probe_workers", 2)),
        "auto_system_profile": True,
    })
    return result


def normalize_performance_settings(value: Any) -> dict[str, Any]:
    default = default_performance_settings()
    if not isinstance(value, dict):
        return default
    result = dict(default)
    preset = str(value.get("preset", result["preset"])).lower()
    valid_presets = (*PRESETS, "system", "custom")
    result["preset"] = preset if preset in valid_presets else "balanced"
    specs = system_specs()
    if result["preset"] != "custom":
        # Saved presets are *intent*, not frozen CPU counts from another PC.
        # Only resource knobs refresh; user-selected sampling/codec settings
        # survive upgrades and switching between resource profiles.
        adaptive = settings_for_preset(result["preset"], specs)
        for key in ("cpu_threads", "probe_workers", "sampling_workers", "prewarm_metadata"):
            result[key] = adaptive[key]
    else:
        for key in ("cpu_threads", "probe_workers", "sampling_workers", "prewarm_metadata"):
            if key in value:
                result[key] = value[key]
    try:
        result["cpu_threads"] = max(1, min(int(specs["logical_cpus"]), int(result["cpu_threads"])))
    except (TypeError, ValueError):
        pass
    try:
        result["sample_interval"] = max(0.5, min(5.0, float(value.get("sample_interval", result["sample_interval"]))))
    except (TypeError, ValueError):
        pass
    try:
        result["max_samples"] = max(200, min(4000, int(value.get("max_samples", result["max_samples"]))))
    except (TypeError, ValueError):
        pass
    try:
        result["audio_bitrate_kbps"] = max(64, min(320, int(value.get("audio_bitrate_kbps", result["audio_bitrate_kbps"]))))
    except (TypeError, ValueError):
        pass
    hw = str(value.get("hardware_accel", result["hardware_accel"])).lower()
    result["hardware_accel"] = hw if hw in HARDWARE_MODES else "auto"
    # Previous alignment caches are intentionally disabled until source/episode identity
    # is independently verified for every batch export.
    result["cache_enabled"] = False
    result["frame_cache_enabled"] = bool(value.get("frame_cache_enabled", result["frame_cache_enabled"]))
    try:
        result["sampling_workers"] = max(1, min(2, int(result["sampling_workers"])))
    except (TypeError, ValueError):
        pass
    try:
        result["probe_workers"] = max(1, min(8, int(result["probe_workers"])))
    except (TypeError, ValueError):
        pass
    result["prewarm_metadata"] = bool(result["prewarm_metadata"])
    result["auto_system_profile"] = result["preset"] == "system"
    return result


def apply_cpu_thread_limit(threads: int) -> int:
    """Apply the OpenCV CPU-thread cap and return the effective setting."""
    try:
        import cv2
        effective = max(1, min(_cpu_count(), int(threads)))
        if hasattr(cv2, "setUseOptimized"):
            cv2.setUseOptimized(True)
        cv2.setNumThreads(effective)
        return effective
    except (ImportError, AttributeError, TypeError, ValueError):
        return 1


def supported_hardware_modes() -> list[str]:
    """Return exposed decoder requests, not a guarantee of driver support."""
    from .ffmpeg_sampling import ffmpeg_hardware_accels, vaapi_render_device
    try:
        import cv2
    except ImportError:
        cv2 = None
    values = ["auto", "none"]
    offered = ffmpeg_hardware_accels()
    if offered:
        values.append("ffmpeg")
    if "cuda" in offered:
        values.append("cuda")
    platform_modes = (
        (("vaapi", "VIDEO_ACCELERATION_VAAPI"), ("qsv", "VIDEO_ACCELERATION_MFX"),
         ("drm", "VIDEO_ACCELERATION_DRM"))
        if sys.platform.startswith("linux") else
        (("d3d11", "VIDEO_ACCELERATION_D3D11"),)
        if sys.platform == "win32" else ()
    )
    for mode, constant in platform_modes:
        ffmpeg_mode = "d3d11va" if mode == "d3d11" else mode
        if (cv2 is not None and hasattr(cv2, constant)) or ffmpeg_mode in offered:
            values.append(mode)
    if "vaapi" in offered and vaapi_render_device("amd") is not None:
        values.append("amd-vaapi")
    if "vaapi" in offered and vaapi_render_device("intel") is not None:
        values.append("intel-vaapi")
    return values


def auto_decoder_summary() -> str:
    """Honest hardware preview; actual use requires a per-file benchmark."""
    from .ffmpeg_sampling import gpu_decode_candidates
    candidates = gpu_decode_candidates()
    if not candidates:
        return "CPU-Fallback · derzeit kein zugänglicher GPU-Decoder-Kandidat"
    labels = []
    for candidate in candidates:
        if candidate.key.startswith("intel-vaapi"):
            name = "Intel iGPU (VAAPI)"
        elif candidate.key.startswith("amd-vaapi"):
            name = "AMD-GPU (VAAPI)"
        elif candidate.key.startswith("cuda"):
            name = "NVIDIA (CUDA)"
        else:
            name = candidate.key.upper()
        if name not in labels:
            labels.append(name)
    return " / ".join(labels) + " · GPU-Auswahl nach Video-Benchmark, sonst CPU"


def sampling_worker_policy(source: str, master: str, requested: int,
                           hardware_accel: str, specs: dict[str, Any] | None = None) -> tuple[int, str]:
    """Select 1 or 2 independent decoders, with a conservative NAS/CPU guard.

    This is evaluated per episode: a local profile can never accidentally
    parallelize reads from an SMB/NFS mount after the source path changes.
    """
    if requested < 2:
        return 1, "Profil verwendet sequenzielles Einlesen"
    from .network import is_network_path
    if is_network_path(source) or is_network_path(master):
        return 1, "Netzwerkquelle: paralleles Einlesen zum Schutz des NAS deaktiviert"
    detected = specs if specs is not None else system_specs()
    if int(detected.get("logical_cpus", 1)) < 4 or float(detected.get("ram_gib", 0)) < 8:
        return 1, "CPU- oder RAM-Budget für zwei Decoder zu klein"
    if hardware_accel == "auto":
        from .ffmpeg_sampling import likely_gpu_accel
        if likely_gpu_accel() is not None:
            # A short CPU-vs-GPU benchmark on each video becomes misleading
            # when both jobs compete for the same GPU or CPU simultaneously.
            return 1, "Automatischer GPU-Vergleich benötigt exklusive Messung"
    return 2, "Zwei unabhängige lokale Videodecoder"


def performance_diagnostics(settings: dict[str, Any] | None = None) -> str:
    from .ffmpeg_sampling import (ffmpeg_hardware_accels, likely_gpu_accel,
                                  vaapi_render_device, gpu_decode_candidates)
    from .frame_cache import FrameSampleCache
    cfg = normalize_performance_settings(settings or {})
    specs = system_specs()
    lines = [
        f"CPU model: {specs['cpu_model']}",
        f"CPU logical threads available: {specs['logical_cpus']}",
        f"Physical CPU cores detected: {specs['physical_cores']}",
        f"Memory limit/visible RAM: {specs['ram_gib']:.1f} GiB",
        "GPU vendors detected: " + (", ".join(specs["gpu_vendors"]) if specs["gpu_vendors"] else "not detected via DRM sysfs"),
        f"Hardware decoder interface available: {'yes' if _hardware_decoder_available(specs) else 'not detected; CPU path recommended'}",
        f"Recommended profile: {recommended_profile(specs)}",
        f"Auto decoder preview: {auto_decoder_summary()}",
        f"Active profile: {PROFILE_LABELS.get(cfg['preset'], cfg['preset'])}",
        f"OpenCV threads requested: {cfg['cpu_threads']}",
        f"Frame budget per video: {cfg['max_samples']}",
        "NAS metadata concurrency: at most 2 workers; frame alignment: one episode at a time",
        f"Minimum sample interval: {cfg['sample_interval']} s",
        f"Added audio bitrate: {cfg['audio_bitrate_kbps']} kbit/s AAC",
        f"Hardware decoding request: {cfg['hardware_accel']}",
        f"Local video sample workers requested: {cfg['sampling_workers']} (effective per source pair)",
        "FFmpeg hardware interfaces: " + (", ".join(sorted(ffmpeg_hardware_accels())) or "none"),
        "Plausible local GPU path: " + (likely_gpu_accel() or "none; CPU sampling"),
        "Automatic GPU candidates: " + (", ".join(c.key for c in gpu_decode_candidates()) or "none"),
        "AMD VAAPI render node: " + (vaapi_render_device("amd") or "not detected"),
        "Intel VAAPI render node: " + (vaapi_render_device("intel") or "not detected"),
        "Auto compares up to four local GPU candidates against one CPU reference; only the fastest verified candidate may replace OpenCV.",
        "Alignment-result cache: disabled (every episode is freshly validated)",
        f"Decoded frame-fingerprint cache: {'enabled' if cfg['frame_cache_enabled'] else 'disabled'}",
    ]
    cache_stats = FrameSampleCache().stats()
    lines.append(f"Frame cache: {cache_stats['entries']} entries / {cache_stats['bytes']} bytes"
                 if cache_stats["available"] else "Frame cache: not available")
    try:
        import cv2
        lines.extend([
            f"OpenCV: {cv2.__version__}",
            f"OpenCV threads active: {cv2.getNumThreads()}",
            "OpenCV hardware modes exposed: " + ", ".join(supported_hardware_modes()),
            "Note: Hardware execution is not proven by an FFmpeg -hwaccel request; frame matching remains on CPU.",
        ])
    except ImportError:
        lines.append("OpenCV: unavailable")
    return "\n".join(lines)
