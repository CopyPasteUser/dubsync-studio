"""Bounded, privacy-conscious per-alignment diagnostics; never affect export decisions.

One JSON report is emitted per fresh visual alignment, including rejected matches and
unexpected errors. It contains no frames, audio, full paths or ffprobe raw metadata.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import platform
import tempfile
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logging_setup import default_log_path
from .models import (AlignmentResult, MediaInfo,
                     MAX_UNVERIFIED_EDGE_RATIO, MAX_UNVERIFIED_MIDDLE_RATIO)

MAX_REPORTS = 40


def diagnostics_directory() -> Path:
    return default_log_path().parent / "alignments"


def recent_diagnostics(limit: int = 40) -> list[Path]:
    """Enumerate bounded reports newest first; ignore removed or broken entries."""
    directory = diagnostics_directory()
    try:
        return sorted(directory.glob("alignment-*.json"),
                      key=lambda p: p.stat().st_mtime_ns, reverse=True)[:max(0, min(int(limit), MAX_REPORTS))]
    except (OSError, ValueError, TypeError):
        return []


def latest_diagnostic() -> Path | None:
    reports = recent_diagnostics(1)
    return reports[0] if reports else None


def _number(value: float) -> float | None:
    value = float(value)
    return round(value, 7) if math.isfinite(value) else None


def _describe_media(info: MediaInfo) -> dict[str, Any]:
    video = info.video[0] if info.video else None
    return {
        "filename": Path(info.path).name,
        # A pseudonymous identifier for comparing runs: directory/user not exported.
        "path_id": hashlib.sha256(os.path.abspath(info.path).encode("utf-8", "replace")).hexdigest()[:16],
        "size_bytes": info.size_bytes,
        "duration_s": _number(info.duration),
        "container": info.format_name,
        "video_streams": info.video_streams,
        "audio_streams": info.audio_streams,
        "video": ({"codec": video.codec_name, "width": video.width,
                   "height": video.height, "fps": _number(video.fps) if video.fps is not None else None}
                  if video else None),
    }


def summarize_diagnostic(payload: dict[str, Any]) -> str:
    """Human-readable, privacy-conscious explanation of an alignment report."""
    files = payload.get("files", {})
    source = files.get("source", {})
    master = files.get("master", {})
    stages = payload.get("stages", {})
    decision = payload.get("decision", {})
    status = decision.get("status", "unknown")
    names = {"safe": "SICHERE SYNCHRONISATION", "unsafe": "UNSICHERE SYNCHRONISATION",
             "cancelled": "ABGEBROCHEN – KEINE BEWERTUNG", "error": "TECHNISCHER FEHLER"}
    lines = [names.get(status, "ANALYSESTATUS UNBEKANNT"), "",
             "Quellvideo: " + str(source.get("filename", "?")),
             "Bildmaster: " + str(master.get("filename", "?"))]
    context = stages.get("batch_context")
    if isinstance(context, dict):
        code = (f"S{int(context['season']):02d}E{int(context['episode']):02d}"
                if context.get("season") is not None and context.get("episode") is not None
                else "nicht ermittelt")
        lines.append(f"Stapelposition: {context.get('position', '?')}/{context.get('total', '?')} · "
                     f"Episodennummer: {code} (unabhängig von der Stapelposition)")
    if source.get("path_id") and master.get("path_id"):
        lines.append("Zwei unterschiedliche Quelldateipfade: " +
                     ("ja" if source["path_id"] != master["path_id"] else "nein"))
    source_duration = source.get("duration_s")
    master_duration = master.get("duration_s")
    if isinstance(source_duration, (int, float)) and isinstance(master_duration, (int, float)):
        lines.append(f"Laufzeiten: {source_duration:.3f}s / {master_duration:.3f}s (Abweichung {abs(source_duration-master_duration):.3f}s)")
        if abs(source_duration-master_duration) >= 30:
            lines.append("Hinweis: Große Laufzeitabweichung; mögliche andere Intro-/Abspann- oder Schnittfassung.")
    long_offset = stages.get("long_offset")
    if isinstance(long_offset, dict):
        if long_offset.get("status") == "safe":
            lines.append("Langer Zeitversatz: anhand vieler gemeinsamer Bildstellen bestätigt "
                         f"(Versatz {long_offset.get('selected_offset_s', '?')}s).")
        elif long_offset.get("status") == "not_validated":
            lines.append("Langer Zeitversatz: kein sicherer Versatz bestätigt; "
                         "der normale Bildabgleich wurde ebenfalls versucht.")
            ranked = sorted(long_offset.get("candidates", []),
                            key=lambda c: c.get("anchors", 0), reverse=True)
            if ranked:
                item = ranked[0]
                lines.append(f"  Stärkster Kandidat: {item.get('offset_candidate_s', '?')}s · "
                             f"{item.get('anchors', '?')} Anker · "
                             f"{item.get('coverage', '?')} Abdeckung · "
                             f"Randprüfung {'OK' if item.get('edge_verified') else 'nicht erfüllt'}")
        else:
            lines.append("Langer Zeitversatz: keine unterscheidbaren Bildtreffer gefunden.")
    for key, label in (("sample_source", "Quellvideo-Sampling"), ("sample_master", "Master-Sampling")):
        stage = stages.get(key)
        if isinstance(stage, dict):
            if stage.get("status") == "cache_hit":
                lines.append(f"{label}: {stage.get('samples_collected', '?')} gültige Bildfingerprints "
                             "aus dem Datei-geprüften Cache geladen; Szenenabgleich neu ausgeführt.")
            elif stage.get("backend") == "ffmpeg":
                lines.append(f"{label}: FFmpeg · {stage.get('samples_collected', '?')} Bildproben · "
                             f"Decoder-Wunsch {stage.get('selected_gpu_candidate') or stage.get('selected_decoder', '?')} "
                             "(Hardware-Ausführung nicht nachgewiesen)")
            else:
                lines.append(f"{label}: {stage.get('status', 'unbekannt')} · {stage.get('frames_grabbed', '?')} Frames gelesen · "
                             f"{stage.get('samples_collected', '?')} Bildproben · letzte Position "
                             f"{stage.get('last_decoded_timestamp_s', '?')}s · {stage.get('retrieve_failures', '?')} Lesefehler")
            if stage.get("timestamp_anomalies"):
                lines.append(f"  Zeitstempel-Anomalien: {stage['timestamp_anomalies']}")
    profile = stages.get("pipeline_profile")
    if isinstance(profile, dict):
        scheduler = stages.get("sampling_scheduler", {})
        lines.append(
            f"Laufzeitprofil: {profile.get('total_wall_seconds', '?')}s insgesamt · "
            f"Einlesen {profile.get('sampling_wall_seconds', '?')}s · "
            f"Abgleich {profile.get('matching_wall_seconds', '?')}s · "
            f"{profile.get('worker_count', 1)} Video-Worker"
        )
        if scheduler.get("reason"):
            lines.append("  Decoder-Plan: " + str(scheduler["reason"]))
    if status == "cancelled":
        origin = decision.get("cancellation_origin")
        translations = {"user_button": "Abbruch-Schaltfläche (Einzelaufgabe)",
                        "user_button_confirmed": "Schaltfläche ‚Stapel stoppen‘ mit bestätigter Rückfrage",
                        "window_close": "Schließen des Fensters",
                        "external_or_unspecified": "Abbruchsignal, Auslöser unbekannt"}
        lines += ["", "Abbruchauslöser: " + translations.get(origin, "nicht feststellbar"),
                  "Die Bildsynchronisation wurde nicht vollständig geprüft. Dieser Bericht ist kein Beleg für unsichere Synchronisation."]
    elif status == "error":
        lines += ["", "Fehler: " + str(decision.get("reason", decision.get("exception_type", "?"))),
                  "Die Sicherheitsbewertung konnte nicht abgeschlossen werden."]
    else:
        lines.extend(["", "Verfahren: " + str(decision.get("method", "?")),
                      "Versatz: " + str(decision.get("offset_s", "?")) + " s"])
        piecewise = stages.get("piecewise_fit")
        if isinstance(piecewise, dict) and piecewise.get("segments"):
            lines.append(f"Abschnittsweise korrigiert: {len(piecewise['segments'])} unabhängig bestätigte Zeitbereiche.")
            lines.append("Unpassende Master-Szenen werden in der neuen Audiospur nicht mit fremdem Dialog aufgefüllt.")
        conditions = decision.get("conditions") or {}
        for key, value in conditions.items():
            if isinstance(value, dict):
                symbol = "OK" if value.get("passed") else "NICHT ERFUELLT"
                bounds = ("mindestens " + str(value["minimum"]) if "minimum" in value else "")
                if "maximum" in value:
                    bounds += (", " if bounds else "") + "hoechstens " + str(value["maximum"])
                lines.append(f"{symbol}: {key} = {value.get('value', '?')} ({bounds})")
        for warning in decision.get("warnings", [])[:12]:
            lines.append("Warnung: " + str(warning))
    lines += ["", "Für Support: den JSON-Bericht kopieren. Er enthält die detaillierten Zwischenschritte."]
    return "\n".join(lines)


class AlignmentDiagnostic:
    def __init__(self, source_path: str, target_path: str, interval: float,
                 max_samples: int, hardware_accel: str):
        from dubsync import __version__
        self._paths = (os.path.abspath(source_path), os.path.abspath(target_path))
        self._started = time.monotonic()
        self.payload: dict[str, Any] = {
            "schema_version": 1,
            "application_version": __version__,
            "analysis_id": uuid.uuid4().hex,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "platform": f"{platform.system()} {platform.machine()}",
            "requested": {"interval_s": _number(interval), "max_samples": int(max_samples),
                          "hardware_accel": hardware_accel},
            "files": {"source": {"filename": Path(source_path).name},
                      "master": {"filename": Path(target_path).name}},
            "stages": {},
            "events": [],
            "decision": {"status": "running"},
            "privacy": "No media bytes, full paths, environment variables or credentials are included.",
        }

    def metadata(self, source: MediaInfo, master: MediaInfo) -> None:
        self.payload["files"] = {"source": _describe_media(source), "master": _describe_media(master)}
        self.payload["stages"]["duration_difference_s"] = _number(abs(source.duration - master.duration))

    def stage(self, name: str, **values: Any) -> None:
        self.payload["stages"][name] = values

    def event(self, name: str, **values: Any) -> None:
        events = self.payload["events"]
        if len(events) < 60:
            events.append({"event": name, **values})

    def complete(self, result: AlignmentResult, method: str) -> None:
        conditions = {
            "confidence": {"value": _number(result.confidence), "minimum": 0.62,
                           "passed": result.confidence >= 0.62},
            "inlier_ratio": {"value": _number(result.inlier_ratio), "minimum": 0.65,
                             "passed": result.inlier_ratio >= 0.65},
            "coverage": {"value": _number(result.coverage), "minimum": 0.55,
                         "passed": result.coverage >= 0.55},
            "unverified_middle_ratio": {"value": _number(result.max_unverified_middle_ratio),
                                        "maximum": MAX_UNVERIFIED_MIDDLE_RATIO,
                                        "passed": result.max_unverified_middle_ratio <= MAX_UNVERIFIED_MIDDLE_RATIO},
            "unverified_edge_ratio": {"value": _number(result.max_unverified_edge_ratio),
                                      "maximum": MAX_UNVERIFIED_EDGE_RATIO,
                                      "passed": result.max_unverified_edge_ratio <= MAX_UNVERIFIED_EDGE_RATIO},
            "median_hamming": {"value": _number(result.median_hamming), "maximum": 15,
                               "passed": result.median_hamming <= 15},
            "rms_error_s": {"value": _number(result.rms_error),
                            "maximum": _number(max(0.75, result.sample_interval * 1.20)),
                            "passed": result.rms_error <= max(0.75, result.sample_interval * 1.20)},
            "slope": {"value": _number(result.slope), "minimum": 0.90, "maximum": 1.10,
                      "passed": 0.90 <= result.slope <= 1.10},
            "overlap_ratio": {"value": _number(result.mapped_overlap_ratio), "minimum": 0.55,
                              "passed": result.mapped_overlap_ratio >= 0.55},
            "anchor_count": {"value": len(result.anchors), "minimum": 8,
                             "passed": len(result.anchors) >= 8},
            "visual_distinct_hashes": {"value": result.visual_distinct_hashes, "minimum": 8,
                                       "passed": result.visual_distinct_hashes >= 8},
        }
        self.payload["decision"] = {
            "status": "safe" if result.safe_for_automatic_export else "unsafe",
            "method": method,
            "conditions": conditions,
            "failed_conditions": [key for key, val in conditions.items() if not val["passed"]],
            "warnings": list(result.warnings),
            "offset_s": _number(result.offset),
            "sample_interval_s": _number(result.sample_interval),
        }

    def failure(self, exc: Exception) -> None:
        # Never expose network mount points or the home directory in shared reports.
        rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        for idx, path in enumerate(self._paths):
            rendered = rendered.replace(path, f"<{'source' if idx == 0 else 'master'} file>")
        rendered = rendered.replace(str(Path.home()), "<home>")
        short_reason = str(exc)[:350]
        for idx, path in enumerate(self._paths):
            short_reason = short_reason.replace(path, f"<{'source' if idx == 0 else 'master'} file>")
        short_reason = short_reason.replace(str(Path.home()), "<home>")
        cancellation = next((event for event in reversed(self.payload["events"])
                             if event.get("event") == "cancellation_requested"), None)
        is_cancelled = bool(cancellation) or "abgebrochen" in str(exc).lower()
        self.payload["decision"] = {
            "status": "cancelled" if is_cancelled else "error",
            "reason": short_reason,
            "cancellation_origin": cancellation.get("reason") if cancellation else None,
            "last_completed_stage": list(self.payload["stages"])[-1] if self.payload["stages"] else None,
            "exception_type": type(exc).__name__,
            "traceback": rendered[-16000:],
        }

    def save(self) -> Path | None:
        directory = diagnostics_directory()
        tmp_path: str | None = None
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
            self.payload["elapsed_seconds"] = _number(time.monotonic() - self._started)
            self.payload["finished_utc"] = datetime.now(timezone.utc).isoformat()
            output = directory / f"alignment-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{self.payload['analysis_id'][:12]}.json"
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp", prefix=".alignment-",
                                             dir=directory, delete=False) as file:
                tmp_path = file.name
                json.dump(self.payload, file, indent=2, ensure_ascii=False, allow_nan=False)
                file.write("\n")
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, output)
            # Each analysis is separate: never rewrite another worker's report.
            try:
                all_reports = sorted(directory.glob("alignment-*.json"),
                                     key=lambda p: p.stat().st_mtime_ns, reverse=True)
                for old in all_reports[MAX_REPORTS:]:
                    old.unlink(missing_ok=True)
            except OSError:
                pass
            logging.getLogger("dubsync").info(
                "Synchronisationsdiagnose %s: %s (%s)",
                self.payload["analysis_id"][:12], output,
                self.payload["decision"].get("status", "unknown"),
            )
            return output
        except (OSError, ValueError, TypeError) as exc:
            logging.getLogger("dubsync").warning("Synchronisationsdiagnose konnte nicht gespeichert werden: %s", exc)
            return None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
