"""System-aware performance defaults and conservative hardware-decoder detection."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

# Stable preset IDs preserve existing configuration and project compatibility.
# "quality" denotes a high-resource *accuracy* profile, not a guaranteed
# faster run: searching more frames costs additional decoding and RAM.
PRESETS: dict[str, dict[str, int | float | bool]] = {
    "fast": {"sample_interval": 1.25, "max_samples": 700, "audio_bitrate_kbps": 128,
             "prewarm_metadata": False},
    "balanced": {"sample_interval": 0.75, "max_samples": 1200, "audio_bitrate_kbps": 128,
                 "prewarm_metadata": False},
    "quality": {"sample_interval": 0.5, "max_samples": 2200, "audio_bitrate_kbps": 160,
                "prewarm_metadata": False},
}
PROFILE_LABELS = {"system": "Auto", "fast": "Leicht", "balanced": "Ausgewogen",
                  "quality": "Stark", "custom": "Benutzerdefiniert"}
HARDWARE_MODES = ("auto", "none", "vaapi", "qsv", "d3d11", "drm")


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


def system_specs() -> dict[str, Any]:
    """A lightweight hardware snapshot used to choose first-run defaults."""
    logical = _cpu_count()
    physical = max(1, min(_physical_core_count(), logical))
    ram_bytes = _memory_bytes()
    return {
        "logical_cpus": logical,
        "physical_cores": physical,
        "ram_bytes": ram_bytes,
        "ram_gib": ram_bytes / (1024**3),
        "gpu_vendors": _gpu_devices(),
    }


def recommended_profile(specs: dict[str, Any] | None = None) -> str:
    specs = specs or system_specs()
    cores = int(specs.get("physical_cores", specs.get("logical_cpus", 2)) or 2)
    ram = float(specs.get("ram_gib", 8.0) or 8.0)
    if cores <= 2 or ram < 8.0:
        return "fast"
    if cores >= 10 and ram >= 24.0:
        return "quality"
    return "balanced"


def _recommended_values(preset: str, specs: dict[str, Any]) -> dict[str, Any]:
    """Allocate genuinely different CPU, probe and frame budgets per profile.

    Threads are capped by *available* logical CPUs (affinity/cgroup) and RAM.
    Strong favors throughput on dedicated machines; light leaves headroom for
    gaming/desktop. Probe workers are later limited to two for network mounts.
    """
    chosen = preset if preset in PRESETS else "balanced"
    values = dict(PRESETS[chosen])
    physical = max(1, int(specs.get("physical_cores", 2) or 2))
    logical = max(1, int(specs.get("logical_cpus", physical) or physical))
    ram = max(0.5, float(specs.get("ram_gib", 8.0) or 8.0))
    if chosen == "fast":
        threads = max(1, (physical + 1) // 2)
        probes = 1
    elif chosen == "balanced":
        threads = max(1, physical - 1 if physical > 2 else physical)
        probes = min(2, physical)
    else:
        # Hyperthreading can improve decoding; leave one logical CPU free.
        threads = min(logical, max(physical, int(round(physical * 1.5))))
        probes = min(4, physical)
    values["cpu_threads"] = min(logical, 12, threads)
    values["probe_workers"] = max(1, min(logical, probes))
    if ram < 6.0:
        values["max_samples"] = min(int(values["max_samples"]),
                                    {"fast": 400, "balanced": 500, "quality": 600}[chosen])
        values["sample_interval"] = max(float(values["sample_interval"]), 1.25)
        values["cpu_threads"] = min(values["cpu_threads"], 2)
        values["probe_workers"] = 1
    elif ram < 10.0:
        values["max_samples"] = min(int(values["max_samples"]),
                                    {"fast": 600, "balanced": 800, "quality": 900}[chosen])
        values["sample_interval"] = max(float(values["sample_interval"]), 1.0)
    elif ram < 16.0:
        values["max_samples"] = min(int(values["max_samples"]),
                                    {"fast": 700, "balanced": 1200, "quality": 1500}[chosen])
    return values


def settings_for_preset(preset: str, specs: dict[str, Any] | None = None) -> dict[str, Any]:
    specs = specs if specs is not None else system_specs()
    chosen = recommended_profile(specs) if preset == "system" else preset
    return _recommended_values(chosen, specs)


def _hardware_decoder_available(specs: dict[str, Any]) -> bool:
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
        "hardware_accel": "auto" if _hardware_decoder_available(specs) else "none",
        "cache_enabled": False,
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
    valid_presets = (*PRESETS.keys(), "system", "custom")
    result["preset"] = preset if preset in valid_presets else "balanced"
    specs = system_specs()
    try:
        result["cpu_threads"] = max(1, min(int(specs["logical_cpus"]), int(value.get("cpu_threads", result["cpu_threads"]))))
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
    try:
        result["probe_workers"] = max(1, min(8, int(value.get("probe_workers", result.get("probe_workers", 2)))))
    except (TypeError, ValueError):
        pass
    result["prewarm_metadata"] = bool(value.get("prewarm_metadata", result["prewarm_metadata"]))
    result["auto_system_profile"] = bool(value.get("auto_system_profile", result["auto_system_profile"]))
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
    """Return decoder request modes exposed by this OpenCV build, not a promise of GPU support."""
    try:
        import cv2
    except ImportError:
        return ["auto", "none"]
    values = ["auto", "none"]
    platform_modes = (
        (("vaapi", "VIDEO_ACCELERATION_VAAPI"), ("qsv", "VIDEO_ACCELERATION_MFX"),
         ("drm", "VIDEO_ACCELERATION_DRM"))
        if sys.platform.startswith("linux") else
        (("d3d11", "VIDEO_ACCELERATION_D3D11"),)
        if sys.platform == "win32" else ()
    )
    for mode, constant in platform_modes:
        if hasattr(cv2, constant):
            values.append(mode)
    return values


def performance_diagnostics(settings: dict[str, Any] | None = None) -> str:
    cfg = normalize_performance_settings(settings or {})
    specs = system_specs()
    lines = [
        f"CPU logical threads available: {specs['logical_cpus']}",
        f"Physical CPU cores detected: {specs['physical_cores']}",
        f"Memory limit/visible RAM: {specs['ram_gib']:.1f} GiB",
        "GPU vendors detected: " + (", ".join(specs["gpu_vendors"]) if specs["gpu_vendors"] else "not detected via DRM sysfs"),
        f"Hardware decoder interface available: {'yes' if _hardware_decoder_available(specs) else 'not detected; CPU path recommended'}",
        f"Recommended profile: {recommended_profile(specs)}",
        f"Active profile: {PROFILE_LABELS.get(cfg['preset'], cfg['preset'])}",
        f"OpenCV threads requested: {cfg['cpu_threads']}",
        f"Frame budget per video: {cfg['max_samples']}",
        "NAS metadata concurrency: at most 2 workers; frame alignment: one episode at a time",
        f"Minimum sample interval: {cfg['sample_interval']} s",
        f"Added audio bitrate: {cfg['audio_bitrate_kbps']} kbit/s AAC",
        f"Hardware decoding request: {cfg['hardware_accel']}",
        f"Alignment cache: {'enabled' if cfg['cache_enabled'] else 'disabled'}",
    ]
    try:
        import cv2
        lines.extend([
            f"OpenCV: {cv2.__version__}",
            f"OpenCV threads active: {cv2.getNumThreads()}",
            "OpenCV hardware modes exposed: " + ", ".join(supported_hardware_modes()),
            "Note: GPU use depends on OpenCV/FFmpeg build and drivers; frame matching itself runs on CPU.",
        ])
    except ImportError:
        lines.append("OpenCV: unavailable")
    return "\n".join(lines)
