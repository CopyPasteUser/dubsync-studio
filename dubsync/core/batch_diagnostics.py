"""Crash-resilient, privacy-conscious whole-batch diagnostic reports.

Each batch has one persistent JSON file, independent of the rotating alignment
reports.  A snapshot is committed atomically after material changes so an
interrupted process does not erase already-completed episode decisions.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logging_setup import default_log_path
from .models import EpisodeGroup


def batch_diagnostics_directory() -> Path:
    return default_log_path().parent / "batches"


def recent_batch_diagnostics(limit: int = 30) -> list[Path]:
    try:
        return sorted(batch_diagnostics_directory().glob("batch-*.json"),
                      key=lambda p: p.stat().st_mtime_ns, reverse=True)[:max(0, min(30, int(limit)))]
    except (OSError, ValueError, TypeError):
        return []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite_number(value: Any) -> float | None:
    try:
        result = float(value)
        return round(result, 8) if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _identifier(path: str) -> str:
    return hashlib.sha256(os.path.abspath(path).encode("utf-8", "replace")).hexdigest()[:16]


def _file(path: str) -> dict[str, str]:
    return {"filename": Path(path).name, "path_id": _identifier(path)}


def _episode_code(group: EpisodeGroup) -> str | None:
    if group.season_number is None or group.episode_number is None:
        return None
    return f"S{group.season_number:02d}E{group.episode_number:02d}"


class BatchDiagnostic:
    """Serially updated batch journal; failures to write never affect exports."""

    def __init__(self, groups: list[EpisodeGroup], *, alignment_options: dict,
                 skip_existing: bool, overwrite_existing: bool,
                 organize_by_series: bool, directory: Path | None = None,
                 output_directory: str | None = None):
        from dubsync import __version__

        self.directory = Path(directory) if directory is not None else batch_diagnostics_directory()
        # Keep all original paths in memory solely to redact untrusted error text.
        self._private_paths = sorted(
            ({os.path.abspath(path) for group in groups for path in group.files} |
             ({os.path.abspath(output_directory)} if output_directory else set()) |
             {str(Path.home())}), key=len, reverse=True,
        )
        run_id = uuid.uuid4().hex
        self.path = self.directory / f"batch-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{run_id[:12]}.json"
        self.payload: dict[str, Any] = {
            "schema_version": 1,
            "application_version": __version__,
            "run_id": run_id,
            "started_utc": _now(),
            "finished_utc": None,
            "status": "running",
            "settings": {
                "interval_s": alignment_options.get("interval", 0.5),
                "max_samples": alignment_options.get("max_samples", 1800),
                "hardware_accel": alignment_options.get("hardware_accel", "auto"),
                "audio_bitrate_kbps": alignment_options.get("audio_bitrate_kbps", 128),
                "skip_existing": skip_existing,
                "overwrite_existing": overwrite_existing,
                "organize_by_series": organize_by_series,
            },
            "episodes": [
                {"position": i, "total": len(groups), "episode": _episode_code(group),
                 "display_name": group.display_name, "files": [_file(f) for f in group.files],
                 "status": "pending", "stage": "not_started", "reason": None,
                 "master": None, "output_filename": None, "attempts": [], "events": [],
                 "started_utc": None, "finished_utc": None}
                for i, group in enumerate(groups, 1)
            ],
            "summary": {"exported": 0, "skipped": 0, "failed": 0, "cancelled": 0,
                        "not_started": len(groups)},
            "events": [],
            "batch_messages": {"exported": [], "skipped": [], "failed": [], "warnings": [], "cancelled": []},
            "privacy": "Only basenames, pseudonymous path identifiers, algorithm metrics and sanitized diagnostics. No full paths or media contents.",
        }
        self.save()

    def episode(self, position: int) -> dict[str, Any]:
        return self.payload["episodes"][position - 1]

    def event(self, position: int, kind: str, detail: str = "") -> None:
        entry = self.episode(position)
        if len(entry["events"]) < 80:
            entry["events"].append({"utc": _now(), "event": kind, "detail": detail[:400]})
        self.save()

    def start(self, position: int) -> None:
        entry = self.episode(position)
        entry.update(status="running", stage="preflight", started_utc=_now())
        self.event(position, "started")

    def stage(self, position: int, stage: str, detail: str = "") -> None:
        entry = self.episode(position)
        entry["stage"] = stage
        self.event(position, stage, detail)

    def master(self, position: int, path: str, duration: float) -> None:
        entry = self.episode(position)
        entry["master"] = {**_file(path), "duration_s": round(float(duration), 3)}
        self.save()

    def new_attempt(self, position: int, source: str, master: str, budget: int,
                    retry: bool = False) -> dict[str, Any]:
        entry = self.episode(position)
        item = {"attempt": len(entry["attempts"]) + 1,
                "retry": bool(retry), "max_samples": int(budget),
                "source": _file(source), "master": _file(master),
                "started_utc": _now(), "status": "running", "alignment": None,
                "metrics": None}
        entry["attempts"].append(item)
        self.stage(position, "alignment_retry" if retry else "alignment", f"Bildabgleich {item['attempt']}")
        return item

    def capture_alignment(self, item: dict, diagnostic: dict[str, Any]) -> None:
        """Embed the *actual* aligner report, not the globally latest report."""
        item["alignment"] = diagnostic
        item["status"] = diagnostic.get("decision", {}).get("status", "unknown")
        item["finished_utc"] = _now()
        self.save()

    def finish_attempt(self, item: dict, result=None, error: str | None = None) -> None:
        if result is not None:
            item["status"] = "safe" if result.safe_for_automatic_export else "unsafe"
            item["metrics"] = {
                "safe_for_automatic_export": result.safe_for_automatic_export,
                "confidence": _finite_number(result.confidence),
                "inlier_ratio": _finite_number(result.inlier_ratio),
                "coverage": _finite_number(result.coverage),
                "median_hamming": _finite_number(result.median_hamming),
                "rms_error_s": _finite_number(result.rms_error),
                "offset_s": _finite_number(result.offset),
                "slope": _finite_number(result.slope),
                "mapped_overlap_ratio": _finite_number(result.mapped_overlap_ratio),
                "anchor_count": len(result.anchors),
                "verified_segments": len(result.segments),
                "distinct_hashes": result.visual_distinct_hashes,
                "warnings": list(result.warnings),
            }
        if error:
            item["error"] = self._safe_reason(error)[:500]
            if item["status"] == "running":
                item["status"] = "error"
        item["finished_utc"] = _now()
        self.save()

    def _safe_reason(self, reason: str) -> str:
        text = str(reason)
        for path in self._private_paths:
            text = text.replace(path, "<Pfad>")
        # Avoid leaking other incidental absolute paths from ffmpeg/OS errors.
        text = re.sub(r"(?<!\w)/(?:[^\s()]+/)+[^\s()]+", "<Pfad>", text)
        return text[:1200]

    def conclude(self, position: int, status: str, reason: str,
                 output_filename: str | None = None) -> None:
        entry = self.episode(position)
        entry.update(status=status, stage="finished", reason=self._safe_reason(reason),
                     finished_utc=_now(), output_filename=output_filename)
        self.event(position, "finished", status)

    def resume_proof(self, position: int, proof: dict | None) -> None:
        """Only materialize a proof for completed, verifiable export files."""
        if proof is not None:
            self.episode(position)["resume_proof"] = proof
            self.save()

    def finish(self, *, cancelled: bool = False, report: dict | None = None) -> None:
        entries = self.payload["episodes"]
        for entry in entries:
            if entry["status"] == "running":
                entry.update(status="cancelled" if cancelled else "failed",
                             reason="Verarbeitung unterbrochen; kein Ergebnis protokolliert",
                             finished_utc=_now())
        counts = {name: sum(entry["status"] == name for entry in entries)
                  for name in ("exported", "skipped", "failed", "cancelled", "not_started")}
        counts["not_started"] = sum(entry["status"] == "pending" for entry in entries)
        self.payload["summary"] = counts
        if report is not None:
            self.payload["batch_messages"] = {
                key: [(Path(item).name if key == "exported" else self._safe_reason(item))
                      for item in report.get(key, [])]
                for key in ("exported", "skipped", "failed", "warnings", "cancelled")
            }
        self.payload["status"] = "cancelled" if cancelled else "completed"
        self.payload["finished_utc"] = _now()
        self.save()

    def save(self) -> None:
        temp_path = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.directory, 0o700)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".tmp",
                                             prefix=".batch-", dir=self.directory, delete=False) as handle:
                temp_path = handle.name
                json.dump(self.payload, handle, indent=2, ensure_ascii=False, allow_nan=False)
                handle.write("\n")
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self.path)
        except (OSError, ValueError, TypeError) as exc:
            logging.getLogger("dubsync").warning("Stapel-Gesamtprotokoll nicht gespeichert: %s", exc)
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass


def summarize_batch(payload: dict[str, Any]) -> str:
    """Human-readable full season overview with all reasons and attempts."""
    summary = payload.get("summary", {})
    translations = {"exported": "Exportiert", "skipped": "Übersprungen",
                    "failed": "Fehlgeschlagen", "cancelled": "Abgebrochen",
                    "pending": "Noch nicht gestartet", "running": "Läuft"}
    lines = ["STAPEL-GESAMTPROTOKOLL", f"Status: {payload.get('status', '?')}",
             f"Start: {payload.get('started_utc', '?')}",
             f"Ende: {payload.get('finished_utc') or 'noch nicht beendet'}",
             ("Ergebnis: " + " · ".join(f"{summary.get(key, 0)} {key}" for key in
                  ("exported", "skipped", "failed", "cancelled", "not_started"))), ""]
    for entry in payload.get("episodes", []):
        status = entry.get("status", "?")
        lines.append(f"{entry.get('position')}/{entry.get('total')} · "
                     f"{entry.get('episode') or entry.get('display_name')} · {translations.get(status, status)}")
        lines.append("  Quellen: " + ", ".join(f.get("filename", "?") for f in entry.get("files", [])))
        if entry.get("reason"):
            lines.append("  Grund: " + entry["reason"])
        for attempt in entry.get("attempts", []):
            metrics = attempt.get("metrics") or {}
            result = attempt.get("alignment") or {}
            details = result.get("decision") or {}
            lines.append(f"  Versuch {attempt.get('attempt')} ({attempt.get('max_samples')} Proben): "
                         f"{attempt.get('status', '?')}" +
                         (f" · Abdeckung {metrics['coverage']:.3f}" if isinstance(metrics.get('coverage'), (float, int)) else ""))
            if attempt.get("cross_episode_check"):
                verified = attempt.get("cross_episode_verified")
                outcome = ("belegt" if verified is True else "NICHT belegt" if verified is False
                           else "keine abschließende Bewertung")
                lines.append(f"    KREUZPRÜFUNG: Audioquelle laut Dateiname {attempt.get('source_episode')} "
                             f"→ Bildmaster {entry.get('episode')} · {outcome}")
            for failed in details.get("failed_conditions", []):
                lines.append("    Nicht erfüllt: " + str(failed))
            if attempt.get("error"):
                lines.append("    Fehler: " + str(attempt["error"]))
        lines.append("")
    warnings = payload.get("batch_messages", {}).get("warnings", [])
    if warnings:
        lines.append("WEITERE STAPELWARNUNGEN:")
        for warning in warnings:
            lines.append("  " + str(warning))
        lines.append("")
    lines.append("Im JSON-Reiter stehen zusätzlich alle Bildproben-, Anker- und Zeitmodell-Messwerte je Versuch.")
    return "\n".join(lines)
