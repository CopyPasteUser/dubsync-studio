"""Explicit project-local candidate assignment; never an export authorization."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .models import EpisodeGroup
from .probe import VIDEO_EXTENSIONS


def approved_candidate_path(report: dict, candidate_number: int, *,
                            master: str, inspected_paths: list[str]) -> str:
    """Bind a human-selected result to the exact files checked by the visual worker.

    The approval is only permission to show the candidate in an episode editor.
    Subsequent audio analysis and export safety evidence are always fresh.
    """
    if report.get("automatic_reassignment") is not False or report.get("automatic_export_allowed") is not False:
        raise ValueError("Kandidatenprüfung enthält keine sichere manuelle Freigabe.")
    resolved_master = str(Path(master).expanduser().resolve(strict=True))
    if report.get("master_path") != resolved_master:
        raise ValueError("Die geprüfte Bildmaster-Datei hat gewechselt.")
    if type(candidate_number) is not int or candidate_number < 1 or candidate_number > len(inspected_paths):
        raise ValueError("Ungültige Kandidatenauswahl.")
    expected = str(Path(inspected_paths[candidate_number - 1]).expanduser().resolve(strict=True))
    if expected == resolved_master:
        raise ValueError("Die Bildmaster-Datei kann nicht als Zusatzfassung zugeordnet werden.")
    if not Path(expected).is_file() or Path(expected).suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Der Kandidat ist keine vorhandene Videodatei.")
    match = next((item for item in report.get("candidates", [])
                  if item.get("candidate") == candidate_number), None)
    proof = match.get("visual_proof") if match else None
    if (not match or match.get("path") != expected or match.get("status") != "verified"
            or not isinstance(proof, dict) or proof.get("independent_episode_proof") is not True
            or proof.get("safe_for_automatic_export") is not True):
        raise ValueError("Dieser Kandidat besitzt keinen ausreichenden unabhängigen Bildbeleg.")
    signature = Path(expected).stat()
    if signature.st_size != match.get("size") or signature.st_mtime_ns != match.get("mtime_ns"):
        raise ValueError("Die Kandidatendatei wurde seit der Bildprüfung verändert. Erneut prüfen.")
    return expected


def apply_candidate_assignments(groups: list[EpisodeGroup], assignments: dict[str, str],
                                *, require_files: bool = True) -> list[EpisodeGroup]:
    """Move declared paths to an existing group, without changing any source file.

    Unavailable files or vanished group keys are ignored when reopening a project.
    Empty original groups remain visible so a missing edition is not concealed.
    """
    result = [replace(group, files=list(group.files)) for group in groups]
    by_key = {group.key: group for group in result}
    owners: dict[str, list[EpisodeGroup]] = {}
    for group in result:
        for member in group.files:
            owners.setdefault(member, []).append(group)
    for source, target_key in assignments.items():
        target = by_key.get(target_key)
        if target is None:
            continue
        path = str(Path(source).expanduser().resolve())
        if Path(path).suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if require_files and not Path(path).is_file():
            continue
        for group in owners.get(path, []):
            if path in group.files:
                group.files.remove(path)
        if path not in target.files:
            target.files.append(path)
            target.files.sort()
        owners[path] = [target]
    return result
