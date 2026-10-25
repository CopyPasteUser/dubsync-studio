"""Portable DubSync project files with schema validation and atomic writes."""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_FORMAT = "dubsync-studio-project"
PROJECT_SCHEMA_VERSION = 1
MAX_PROJECT_BYTES = 4 * 1024 * 1024
MAX_ROOTS = 1000


class ProjectFileError(ValueError):
    """Invalid, damaged, or unsupported project file."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _string(value: Any, field: str, *, empty: bool = False, limit: int = 8192) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ProjectFileError(f"Ungültiger Wert für {field}.")
    value = value.strip()
    if not empty and not value:
        raise ProjectFileError(f"{field} darf nicht leer sein.")
    return value


def normalize_project_payload(value: Any) -> dict[str, Any]:
    """Validate project content. Unknown future fields are ignored intentionally."""
    if not isinstance(value, dict):
        raise ProjectFileError("Projektinhalt ist kein Objekt.")
    roots_raw = value.get("roots", [])
    if not isinstance(roots_raw, list) or len(roots_raw) > MAX_ROOTS:
        raise ProjectFileError(f"Ein Projekt darf höchstens {MAX_ROOTS} Quellordner enthalten.")
    roots: list[str] = []
    seen: set[str] = set()
    for raw in roots_raw:
        root = _string(raw, "Quellordner")
        normalized = str(Path(root).expanduser())
        key = os.path.normcase(os.path.normpath(normalized))
        if key not in seen:
            seen.add(key)
            roots.append(normalized)

    rules_raw = value.get("root_languages", {})
    if not isinstance(rules_raw, dict) or len(rules_raw) > MAX_ROOTS:
        raise ProjectFileError("Sprachregeln sind ungültig.")
    rules: dict[str, dict[str, str]] = {}
    for root, rule in rules_raw.items():
        root = _string(root, "Sprachregelpfad")
        if not isinstance(rule, dict):
            continue
        language = rule.get("language", "auto")
        title = rule.get("title", "")
        if not isinstance(language, str) or len(language) > 16:
            language = "auto"
        if not isinstance(title, str) or len(title) > 180:
            title = ""
        rules[str(Path(root).expanduser())] = {
            "language": language.strip() or "auto",
            "title": title.replace("\x00", "").strip(),
        }

    output_directory = value.get("output_directory", str(Path.home() / "Videos" / "DubSync Output"))
    output_directory = _string(output_directory, "Ausgabeordner")
    performance = value.get("performance", {})
    if not isinstance(performance, dict):
        performance = {}
    # Only JSON-safe primitives are accepted. The performance module performs
    # semantic/range validation when the application loads the project.
    clean_performance: dict[str, Any] = {}
    for key, item in performance.items():
        if not isinstance(key, str) or len(key) > 80:
            continue
        if isinstance(item, float) and not math.isfinite(item):
            continue
        if isinstance(item, (str, int, float, bool)) and not isinstance(item, (list, dict)):
            clean_performance[key] = item

    overrides_raw = value.get("track_overrides", {})
    if not isinstance(overrides_raw, dict) or len(overrides_raw) > 10000:
        raise ProjectFileError("Dateispezifische Track-Einstellungen sind ungültig.")
    track_overrides: dict[str, dict[str, Any]] = {}
    for raw_path, raw_override in overrides_raw.items():
        path = _string(raw_path, "Track-Override-Pfad")
        if not isinstance(raw_override, dict):
            continue
        clean: dict[str, Any] = {}
        ordinal = raw_override.get("audio_ordinal", 0)
        if isinstance(ordinal, int) and not isinstance(ordinal, bool) and 0 <= ordinal <= 255:
            clean["audio_ordinal"] = ordinal
        language = raw_override.get("language", "")
        title = raw_override.get("title", "")
        if isinstance(language, str) and len(language) <= 16:
            clean["language"] = language.strip()
        if isinstance(title, str) and len(title) <= 180:
            clean["title"] = title.replace("\x00", "").strip()
        include = raw_override.get("include", True)
        if isinstance(include, bool):
            clean["include"] = include
        manual_alignment = raw_override.get("manual_alignment", False)
        clean["manual_alignment"] = manual_alignment if isinstance(manual_alignment, bool) else False
        # Automatic alignments must always be recalculated from the current frames.
        # Persist offset/scale only when the user explicitly edited those controls.
        if clean["manual_alignment"]:
            offset = raw_override.get("offset")
            if isinstance(offset, (int, float)) and not isinstance(offset, bool) and math.isfinite(float(offset)) and abs(float(offset)) <= 7200:
                clean["offset"] = float(offset)
            scale = raw_override.get("scale")
            if isinstance(scale, (int, float)) and not isinstance(scale, bool) and math.isfinite(float(scale)) and 0.5 <= float(scale) <= 2.0:
                clean["scale"] = float(scale)
        track_overrides[str(Path(path).expanduser())] = clean

    masters_raw = value.get("master_overrides", {})
    if not isinstance(masters_raw, dict) or len(masters_raw) > 10000:
        raise ProjectFileError("Bildmaster-Auswahl ist ungültig.")
    master_overrides: dict[str, str] = {}
    for group_key, master_path in masters_raw.items():
        group_key = _string(group_key, "Episodenschlüssel", limit=2048)
        master_overrides[group_key] = _string(master_path, "Bildmaster-Pfad")

    return {
        "roots": roots,
        "root_languages": rules,
        "output_directory": output_directory,
        "organize_output": bool(value.get("organize_output", True)),
        "skip_existing": bool(value.get("skip_existing", True)),
        "performance": clean_performance,
        "track_overrides": track_overrides,
        "master_overrides": master_overrides,
    }


def save_project_file(path: str | os.PathLike[str], payload: dict[str, Any], *, created_at: str | None = None) -> Path:
    target = Path(path).expanduser().resolve()
    if target.suffix.lower() != ".dubsync":
        target = target.with_suffix(".dubsync")
    normalized = normalize_project_payload(payload)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    envelope = {
        "format": PROJECT_FORMAT,
        "schema_version": PROJECT_SCHEMA_VERSION,
        "application": "DubSync Studio",
        "created_at": created_at or _timestamp(),
        "saved_at": _timestamp(),
        "project": normalized,
    }
    fd = -1
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            json.dump(envelope, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        return target
    except OSError as exc:
        raise ProjectFileError(f"Projekt konnte nicht sicher gespeichert werden: {exc}") from exc
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def load_project_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        if source.stat().st_size > MAX_PROJECT_BYTES:
            raise ProjectFileError("Projektdatei ist ungewöhnlich groß und wurde nicht geladen.")
        data = json.loads(source.read_text(encoding="utf-8"))
    except ProjectFileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProjectFileError(f"Projektdatei kann nicht gelesen werden: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != PROJECT_FORMAT:
        raise ProjectFileError("Die Datei ist kein DubSync-Studio-Projekt.")
    schema = data.get("schema_version", 0)
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        raise ProjectFileError("Die Projektversion ist ungültig oder zu alt.")
    if schema > PROJECT_SCHEMA_VERSION:
        raise ProjectFileError(
            f"Das Projekt verwendet Schema {schema}; diese Anwendung unterstützt bis Schema {PROJECT_SCHEMA_VERSION}."
        )
    payload = data.get("project")
    try:
        normalized = normalize_project_payload(payload)
    except ProjectFileError:
        raise
    return {
        "path": str(source),
        "created_at": str(data.get("created_at") or _timestamp()),
        "saved_at": str(data.get("saved_at") or ""),
        "project": normalized,
        "missing_roots": [root for root in normalized["roots"] if not Path(root).expanduser().is_dir()],
    }
