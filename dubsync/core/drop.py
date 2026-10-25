"""Drag-and-drop path classification without probing remote paths on the GUI thread."""
from __future__ import annotations

import os
from pathlib import Path

from .probe import VIDEO_EXTENSIONS

PROJECT_EXTENSIONS = {".dubsync", ".json"}


def classify_dropped_paths(values: list[str] | tuple[str, ...]) -> dict[str, list[str]]:
    """Return normalized roots, project files and ignored paths. No filesystem stat calls.

    Video files add their parent directory as a source root. Directory validation is
    deliberately left to the caller so a sleepy/disconnected NAS is never probed
    synchronously just because a drag payload was inspected.
    """
    roots: list[str] = []
    projects: list[str] = []
    ignored: list[str] = []
    seen_roots: set[str] = set()
    seen_projects: set[str] = set()
    for value in values:
        raw = str(value or "").strip()
        if not raw or "\x00" in raw:
            continue
        path = Path(raw).expanduser()
        try:
            absolute = os.path.abspath(os.fspath(path))
        except (OSError, ValueError):
            ignored.append(raw)
            continue
        suffix = path.suffix.casefold()
        if suffix in PROJECT_EXTENSIONS:
            if absolute not in seen_projects:
                projects.append(absolute)
                seen_projects.add(absolute)
        elif suffix in VIDEO_EXTENSIONS:
            parent = os.path.abspath(os.path.dirname(absolute) or os.curdir)
            if parent not in seen_roots:
                roots.append(parent)
                seen_roots.add(parent)
        else:
            if absolute not in seen_roots:
                roots.append(absolute)
                seen_roots.add(absolute)
    return {"roots": roots, "projects": projects, "ignored": ignored}
