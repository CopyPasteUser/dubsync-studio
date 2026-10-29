"""Explicit, fail-closed resumption of completed *exports*, never alignments.

Proofs contain only salted-by-content hashes and pseudonymous path IDs. They
are not a substitute for a full file-integrity checksum or a fresh alignment;
they merely prevent accidental skipping of unrelated/changed outputs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import EpisodeGroup


def _digest(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def recipe_id(*, output_directory: str, organize_by_series: bool,
              alignment_options: dict, roots: list[str] | None,
              root_languages: dict | None, track_overrides: dict | None,
              master_overrides: dict | None) -> str:
    """Invalidate resumption when any output-affecting choice changes."""
    return _digest({
        "output_directory": str(Path(output_directory).resolve()),
        "organize_by_series": organize_by_series,
        "alignment_options": alignment_options,
        "roots": roots or [], "root_languages": root_languages or {},
        "track_overrides": track_overrides or {},
        "master_overrides": master_overrides or {},
    })


def file_signature(path: str | Path) -> dict[str, Any] | None:
    """Freshly fingerprint size and distributed content; do not trust mtime."""
    try:
        source = Path(path)
        if not source.is_file() or source.is_symlink():
            return None
        before = source.stat()
        size = before.st_size
        if size <= 0:
            return None
        digest = hashlib.blake2b(digest_size=24)
        digest.update(size.to_bytes(8, "little", signed=False))
        window = 65536
        positions = sorted({0, max(0, size // 4 - window // 2),
                            max(0, size // 2 - window // 2),
                            max(0, 3 * size // 4 - window // 2), max(0, size - window)})
        with source.open("rb") as handle:
            for offset in positions:
                handle.seek(offset)
                digest.update(offset.to_bytes(8, "little", signed=False))
                digest.update(handle.read(window))
        after = source.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                after.st_size, after.st_mtime_ns, after.st_ino):
            return None
        return {"size": size, "fingerprint": digest.hexdigest()}
    except (OSError, ValueError, OverflowError):
        return None


def proof_for(group: EpisodeGroup, output: Path, recipe: str) -> dict | None:
    inputs = []
    for path in group.files:
        signature = file_signature(path)
        if signature is None:
            return None
        inputs.append([_digest(str(Path(path).resolve())), signature])
    exported = file_signature(output)
    if exported is None:
        return None
    return {"recipe_id": recipe, "episode_key": _digest(group.key),
            "inputs": sorted(inputs), "output_path_id": _digest(str(output.resolve())),
            "output": exported}


def load_resume_report(path: str | Path, recipe: str) -> list[dict]:
    """An incompatible/missing journal is a visible error, not a silent fallback."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Stapelprotokoll fehlt oder ist zu groß.")
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("Stapelprotokoll ist nicht lesbar.") from exc
    if (not isinstance(payload, dict) or not isinstance(payload.get("settings"), dict)
            or payload["settings"].get("resume_recipe_id") != recipe
            or not isinstance(payload.get("episodes"), list)):
        raise ValueError("Stapelprotokoll passt nicht zu Ausgabeordner, Quellen oder Einstellungen.")
    return [item for item in payload["episodes"]
            if isinstance(item, dict) and isinstance(item.get("resume_proof"), dict)
            and (item.get("status") == "exported" or
                 (item.get("status") == "skipped"
                  and "verifiziert; sicher fortgesetzt" in str(item.get("reason") or "")
                  and isinstance(item.get("events"), list)
                  and any(isinstance(event, dict) and event.get("event") == "verified_resume"
                          for event in item["events"])))]


def verified_resume_entry(entries: list[dict], group: EpisodeGroup,
                          output: Path, recipe: str) -> bool:
    proof = proof_for(group, output, recipe)
    return proof is not None and any(item["resume_proof"] == proof for item in entries)
