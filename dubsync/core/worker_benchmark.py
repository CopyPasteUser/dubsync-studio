"""Controlled one-versus-two video decoder benchmark; changes no preferences."""
from __future__ import annotations

import time

from .alignment import align_videos


def compare_sampling_workers(source_path: str, master_path: str, *,
                             max_samples: int = 1200,
                             hardware_accel: str = "auto",
                             cancel_event=None) -> dict:
    """Compare the actual sampling phase with a verified unchanged result.

    Disk cache is bypassed for frame fingerprints, but the operating system's
    page cache and ambient NAS I/O can still influence the measurements. This
    diagnostic never automatically changes worker counts or safety thresholds.
    """
    runs = []
    for requested in (1, 2):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Decodervergleich abgebrochen")
        captured: list[dict] = []
        started = time.monotonic()
        result = align_videos(
            source_path, master_path, interval=.75,
            max_samples=max_samples, hardware_accel=hardware_accel,
            sampling_workers=requested, frame_cache_enabled=False,
            cancel_event=cancel_event, diagnostic_sink=captured.append,
        )
        stages = captured[-1].get("stages", {}) if captured else {}
        sampling = stages.get("sample_runtime", {})
        runs.append({
            "requested_workers": requested,
            "effective_workers": int(sampling.get("effective_workers", 1)),
            "sampling_seconds": float(sampling.get("wall_seconds", 0.)),
            "total_wall_seconds": round(time.monotonic() - started, 4),
            "anchors": len(result.anchors),
            "safe_for_automatic_export": bool(result.safe_for_automatic_export),
            "anchor_pairs": [(a.source_time, a.target_time, a.hamming_distance)
                             for a in result.anchors],
        })
    base, candidate = runs
    anchors_equal = base["anchor_pairs"] == candidate["anchor_pairs"]
    decisions_equal = (base["safe_for_automatic_export"] ==
                       candidate["safe_for_automatic_export"])
    equivalent = anchors_equal and decisions_equal
    baseline_s, parallel_s = base["sampling_seconds"], candidate["sampling_seconds"]
    speedup = baseline_s / parallel_s if baseline_s > 0 and parallel_s > 0 else None
    advisable = (equivalent and candidate["effective_workers"] == 2 and
                 speedup is not None and speedup >= 1.15)
    return {
        "runs": [{key: value for key, value in run.items() if key != "anchor_pairs"}
                 for run in runs],
        "visual_anchor_parity": anchors_equal,
        "safety_decision_parity": decisions_equal,
        "sampling_speedup": round(speedup, 3) if speedup is not None else None,
        "suggested_workers": 2 if advisable else 1,
        "automatic_setting_change": False,
        "note": ("Ergebnisse divergieren: kein sicherer Performance-Schluss möglich."
                 if not equivalent else
                 "Zwei Decoder wurden durch den Ressourcen-/NAS-Schutz begrenzt."
                 if candidate["effective_workers"] != 2 else
                 "Messung kann durch Betriebssystem-Caches und NAS-Last beeinflusst werden; mehrfach prüfen."),
    }
