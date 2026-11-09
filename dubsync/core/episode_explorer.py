"""Read-only visual candidate ranking; never authorizes reassignments or export."""
from __future__ import annotations

from pathlib import Path

from .alignment import align_videos
from .batch import _is_independent_episodic_match


MAX_CANDIDATES = 8


def rank_episode_candidates(master: str, candidates: list[str], *,
                            cancel_event=None, interval: float = .75,
                            max_samples: int = 1200,
                            hardware_accel: str = "auto",
                            progress=None) -> dict:
    """Inspect at most eight explicitly selected videos against one master.

    Results are informative and cannot override the normal batch filename,
    cross-episode, visual-confidence or export safety gates. An unexpected
    decoder/I/O failure is *not* presented as a negative match.
    """
    if not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise ValueError(f"Zwischen 1 und {MAX_CANDIDATES} Kandidaten angeben")
    if not 200 <= max_samples <= 5000 or interval <= 0:
        raise ValueError("Ungültiger Abgleichumfang")
    master_path = Path(master).expanduser().resolve(strict=True)
    paths = [Path(item).expanduser().resolve(strict=True) for item in candidates]
    if any(path == master_path for path in paths) or len(set(paths)) != len(paths):
        raise ValueError("Master und Kandidaten müssen unterschiedliche Dateien sein")
    if not master_path.is_file() or any(not item.is_file() for item in paths):
        raise ValueError("Nur vorhandene Videodateien dürfen verglichen werden")

    ranked = []
    for index, path in enumerate(paths, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Kandidatenprüfung abgebrochen")
        if progress:
            progress(index, len(paths), path.name)
        item = {"candidate": index, "filename": path.name, "path": str(path),
                "size": None, "mtime_ns": None,
                "status": "error", "visual_proof": None, "error": None}
        try:
            signature = path.stat()
            item["size"] = signature.st_size
            item["mtime_ns"] = signature.st_mtime_ns
            result = align_videos(
                str(path), str(master_path), interval=interval,
                max_samples=max_samples, cancel_event=cancel_event,
                hardware_accel=hardware_accel, frame_cache_enabled=True,
            )
            evidence = {
                "anchors": len(result.anchors),
                "visual_distinct_hashes": result.visual_distinct_hashes,
                "coverage": round(result.coverage, 5),
                "inlier_ratio": round(result.inlier_ratio, 5),
                "median_hamming": round(result.median_hamming, 4),
                "safe_for_automatic_export": result.safe_for_automatic_export,
                "independent_episode_proof": _is_independent_episodic_match(result),
            }
            item["visual_proof"] = evidence
            item["status"] = ("verified" if evidence["independent_episode_proof"]
                              else "insufficient")
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Kandidatenprüfung abgebrochen") from exc
            from .alignment import AlignmentError
            if isinstance(exc, AlignmentError) and "Zu wenige passende Bildanker" in str(exc):
                item["status"] = "no_match"
            else:
                item["status"] = "error"
            item["error"] = f"{type(exc).__name__}: {exc}"[:260]
        ranked.append(item)

    ranked.sort(key=lambda item: (
        item["status"] == "verified",
        (item["visual_proof"] or {}).get("coverage", 0),
        (item["visual_proof"] or {}).get("inlier_ratio", 0),
        (item["visual_proof"] or {}).get("anchors", 0),
    ), reverse=True)
    verified = [item for item in ranked if item["status"] == "verified"]
    verdict = ("single_visual_candidate" if len(verified) == 1 else
               "multiple_visual_candidates" if verified else
               "analysis_incomplete" if any(item["status"] == "error" for item in ranked) else
               "unverified")
    return {"master_filename": master_path.name, "master_path": str(master_path),
            "verdict": verdict, "suggested_candidate": verified[0]["candidate"] if len(verified) == 1 else None,
            "candidates": ranked, "automatic_reassignment": False,
            "automatic_export_allowed": False}
