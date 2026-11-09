"""Optional local CPU decoder calibration; never modifies preferences or media."""
from __future__ import annotations

import os
from pathlib import Path

from .network import is_network_path
from .performance import system_specs
from .probe import probe_media
from .thread_benchmark import compare_cpu_decode_threads


def tune_local_cpu_decoder(path: str, *, cancel_event=None, progress=None) -> dict:
    """Benchmark a representative *local* video with existing exact-parity gates.

    The recommendation is file-specific and must explicitly be accepted by the
    user. Network mounts (including local symlinks into mounts) are rejected
    before any potentially blocking file metadata request is issued.
    """
    selected = os.path.abspath(os.path.expanduser(os.fspath(path)))
    resolved = os.path.realpath(selected)
    if is_network_path(selected) or is_network_path(resolved):
        raise ValueError("NAS-/Netzwerkdateien sind für das Autotuning ausgeschlossen.")
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("CPU-Autotuning abgebrochen")
    if not Path(resolved).is_file():
        raise ValueError("Bitte eine erreichbare lokale Videodatei auswählen.")
    info = probe_media(resolved)
    if not info.video or info.duration <= 0:
        raise ValueError("Die Datei enthält keine geeignete Videospur oder Laufzeit.")
    specs = system_specs()
    report = compare_cpu_decode_threads(
        resolved, info.duration, seconds=8., interval=.75, repetitions=3,
        logical_cpus=specs["logical_cpus"], cancel_event=cancel_event,
        progress=progress,
    )
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("CPU-Autotuning abgebrochen")
    candidate = report["recommended_threads"]
    # Re-validate every application-critical gate; this wrapper doesn't trust
    # a speed figure on its own to enable the UI's acceptance button.
    eligible = next((row for row in report["runs"] if row["threads"] == candidate
                     and candidate is not None and row["exact_frame_parity"]
                     and row["completed_runs"] == report["repetitions"]
                     and (row["speedup_vs_default"] or 0) >= 1.15
                     and (row["worst_window_speedup"] or 0) >= .9), None)
    tuning = ({"cpu_model": specs["cpu_model"], "logical_cpus": specs["logical_cpus"],
               "threads": int(candidate)} if eligible is not None else None)
    return {"report": report, "tuning": tuning, "selected_filename": Path(selected).name}
