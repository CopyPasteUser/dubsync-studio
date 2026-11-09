"""Read-only, human-readable preflight for a *single* export.

The exporter remains the final authority: this snapshot does not certify an
output or replace its file, timeline, overwrite or mux validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .batch import _is_independent_episodic_match
from .models import TrackChoice


@dataclass(frozen=True)
class PreflightLine:
    title: str
    status: str  # blocked, caution, ready
    detail: str


def inspect_single_export(master: str, tracks: list[TrackChoice], destination: str, *,
                          overwrite: bool = False, allow_low_confidence: bool = False,
                          manually_assigned_paths: set[str] | None = None) -> list[PreflightLine]:
    """Evaluate obvious blockers without decoding or modifying any media."""
    manual = manually_assigned_paths or set()
    master_path = Path(master).expanduser().resolve()
    output_path = Path(destination).expanduser().resolve()
    source_paths = {master_path, *(Path(t.path).expanduser().resolve() for t in tracks)}
    lines: list[PreflightLine] = []
    if not master_path.is_file():
        lines.append(PreflightLine("Bildmaster", "blocked", "Bildmaster fehlt oder ist nicht lesbar."))
    else:
        lines.append(PreflightLine("Bildmaster", "ready", master_path.name + " · Video bleibt unverändert"))
    if output_path in source_paths:
        lines.append(PreflightLine("Ausgabe", "blocked", "Ausgabepfad verweist auf eine Quelldatei."))
    elif output_path.is_dir():
        lines.append(PreflightLine("Ausgabe", "blocked", "Ausgabepfad ist ein Verzeichnis, keine MKV-Datei."))
    elif output_path.exists() and not overwrite:
        lines.append(PreflightLine("Ausgabe", "blocked", "Zieldatei existiert, Ersetzen wurde nicht bestätigt."))
    elif output_path.exists():
        lines.append(PreflightLine("Ausgabe", "caution", "Bestehende Ausgabe wird nur nach erfolgreicher Prüfung ersetzt."))
    elif not output_path.parent.is_dir():
        lines.append(PreflightLine("Ausgabe", "caution", "Ausgabeordner fehlt noch und muss beim Export angelegt werden."))
    else:
        lines.append(PreflightLine("Ausgabe", "ready", "Neue Datei · " + output_path.name))
    if not tracks:
        lines.append(PreflightLine("Zusatzspuren", "blocked", "Es sind keine Zusatzspuren ausgewählt."))
    for track in tracks:
        path = Path(track.path).expanduser().resolve()
        title = path.name
        result = track.alignment
        if not path.is_file():
            lines.append(PreflightLine(title, "blocked", "Quelldatei fehlt oder ist nicht lesbar."))
        elif result is None or Path(result.source_path).resolve() != path or Path(result.target_path).resolve() != master_path:
            lines.append(PreflightLine(title, "blocked", "Keine aktuelle Bildanalyse für dieses Quellenpaar."))
        elif str(path) in manual and not _is_independent_episodic_match(result):
            lines.append(PreflightLine(title, "blocked", "Manuell zugeordnete Episode: unabhängiger Bildbeleg fehlt."))
        elif not result.safe_for_automatic_export and not allow_low_confidence:
            lines.append(PreflightLine(title, "blocked", "Unsichere Zuordnung wurde nicht ausdrücklich bestätigt."))
        elif not result.safe_for_automatic_export:
            lines.append(PreflightLine(title, "caution", "Manuelle/unsichere Zeitkorrektur ausdrücklich bestätigt."))
        elif str(path) in manual:
            lines.append(PreflightLine(title, "caution", "Manuell zugeordnet · unabhängiger Bildbeleg aktuell bestätigt."))
        else:
            lines.append(PreflightLine(title, "ready", "Bildanalyse bestätigt · Zeitkorrektur vorhanden."))
    return lines
