from __future__ import annotations

import re
import os
import math
import shutil
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Callable, Any
from .batch_diagnostics import BatchDiagnostic
from .batch_resume import (recipe_id, proof_for, load_resume_report,
                           verified_resume_entry)

from .alignment import align_videos
from .cache import AlignmentCache
from .export import export_multilingual
from .models import EpisodeGroup, MediaInfo, TrackChoice
from .languages import normalize_language_code, resolve_track_label
from .probe import MediaToolError, choose_default_audio, probe_media
from .scanner import infer_language, episode_metadata


class BatchCancelled(RuntimeError):
    pass


def episode_identity_errors(group: EpisodeGroup) -> list[str]:
    """Reject contradicting explicit episode numbers before any media is decoded.

    A batch position (e.g. 1/13) must never overwrite the actual episode
    identity. Unknown/unparseable filenames remain allowed, but a file named
    S01E001 cannot silently participate in the S01E004 group.
    """
    expected = (group.season_number, group.episode_number)
    match = re.search(r"(?i)s(\d{1,2})e(\d{1,3})(?!\d)", group.key)
    if match:
        key_identity = (int(match.group(1)), int(match.group(2)))
        if all(value is not None for value in expected) and expected != key_identity:
            return [f"Gruppenschlüssel {group.key} widerspricht Staffel/Folge {expected}."]
        expected = key_identity
    if any(value is None for value in expected):
        return []
    problems = []
    for file_path in group.files:
        _series, season, episode = episode_metadata(file_path)
        if season is not None and episode is not None and (season, episode) != expected:
            problems.append(
                f"{Path(file_path).name} nennt S{season:02d}E{episode:02d}, "
                f"Gruppe erwartet S{expected[0]:02d}E{expected[1]:02d}."
            )
    return problems


def batch_step_message(index: int, total: int, group: EpisodeGroup, activity: str) -> str:
    """Keep the batch ordinal and episode number visibly separate."""
    episode_code = (f"S{group.season_number:02d}E{group.episode_number:02d}"
                    if group.season_number is not None and group.episode_number is not None
                    else group.display_name)
    return f"Stapelposition {index}/{total} · Episode {episode_code} · {activity}"


def _is_independent_episodic_match(result) -> bool:
    """Cross-episode recovery needs stronger, *interior* evidence than a normal sync.

    Shared intros/outros can produce matching anchors even if the actual episodes
    are unrelated. Only accept a full, safe alignment whose anchors demonstrably
    cover at least three separate parts of the episode's middle.
    """
    if not result.safe_for_automatic_export or result.coverage < 0.65:
        return False
    if result.inlier_ratio < 0.80 or result.median_hamming > 12:
        return False
    if result.visual_distinct_hashes < 20 or result.source_duration <= 0:
        return False
    duration = float(result.source_duration)
    # Five interior sectors, excluding shared opening/closing cards.
    sectors = [0] * 5
    distinct_times: set[int] = set()
    for anchor in result.anchors:
        ratio = anchor.source_time / duration
        if 0.10 <= ratio < 0.90 and anchor.hamming_distance <= 12:
            sector = min(4, int((ratio - 0.10) / 0.16))
            sectors[sector] += 1
            distinct_times.add(round(anchor.source_time))
    return sum(count >= 3 for count in sectors) >= 3 and len(distinct_times) >= 15


def _suspected_content_mismatch(result) -> bool:
    """A bookend-only match is a likely identity problem, not bad video decoding."""
    duration = float(result.source_duration)
    if duration <= 0 or result.coverage >= 0.10 or not result.anchors:
        return False
    middle = sum(.12 * duration <= a.source_time <= .88 * duration for a in result.anchors)
    return middle < max(3, len(result.anchors) // 10)


def _fresh_source_fingerprint(path: str, sample_bytes: int = 32768) -> str | None:
    """Read fresh content windows; intentionally bypass all persistent fingerprint caches."""
    try:
        size = os.path.getsize(path)
        digest = hashlib.blake2b(digest_size=20)
        digest.update(str(size).encode("ascii"))
        offsets = sorted({0, max(0, size // 2 - sample_bytes // 2), max(0, size - sample_bytes)})
        with open(path, "rb", buffering=0) as stream:
            for offset in offsets:
                stream.seek(offset)
                chunk = stream.read(sample_bytes)
                digest.update(offset.to_bytes(8, "little", signed=False))
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        # Missing/unreadable sources are reported by the normal probe path.
        return None


def _safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" ._")
    value = re.sub(r"\s+", " ", value)
    return (value[:160] or "episode")


def output_path_for_group(
    output_directory: str,
    group: EpisodeGroup,
    *,
    organize_by_series: bool = True,
) -> Path:
    """Build a stable output path under the user-selected destination folder."""
    output_root = Path(output_directory).expanduser().resolve()
    match = re.search(r"(?i)s(?P<season>\d{1,2})e(?P<episode>\d{1,3})", group.key)
    season = group.season_number
    episode = group.episode_number
    if match:
        season = season if season is not None else int(match.group("season"))
        episode = episode if episode is not None else int(match.group("episode"))

    series = (group.series_name or "").strip()
    if not series:
        display = group.display_name
        display_match = re.match(r"^(.*?)\s*[·|—-]\s*S\d{1,2}E\d{1,3}$", display, re.IGNORECASE)
        series = display_match.group(1).strip() if display_match else ""
    if not series:
        key_series = group.key.split("|", 1)[0]
        series = key_series.replace("_", " ").strip()
    if not series or series.casefold() in {"series", "title", "unknown"}:
        series = "Unsortierte Serie"
    safe_series = _safe_name(series)

    if season is not None and episode is not None:
        episode_code = f"S{int(season):02d}E{int(episode):02d}"
        filename = _safe_name(f"{safe_series} - {episode_code}") + ".multilingual.mkv"
    else:
        filename = _safe_name(group.display_name) + ".multilingual.mkv"

    destination = output_root
    if organize_by_series:
        destination = destination / safe_series
        if season is not None:
            destination = destination / f"Staffel {int(season):02d}"
    return destination / filename


def _master_rank(info):
    """Sort key for a compact reference video: smallest file wins first.

    Ties prefer a source carrying audio, then higher resolution and a more
    complete timeline. The user may still choose another reference manually
    in the per-episode editor.
    """
    video = info.video[0] if info.video else None
    pixels = (video.width or 0) * (video.height or 0) if video else 0
    return (max(0, int(info.size_bytes)), 0 if info.audio else 1, -pixels,
            -float(info.duration or 0), str(info.path).casefold())


def choose_smallest_video(infos: list[MediaInfo]) -> MediaInfo:
    """Select the smallest valid video source, with stable sensible tie-breakers."""
    candidates = [info for info in infos if info.video]
    if not candidates:
        raise ValueError("Keine Datei mit Videospur für die Referenz gefunden.")
    return min(candidates, key=_master_rank)


def _manual_alignment(result, override: dict) -> object:
    """Apply only explicitly-manual timing values and recompute anchor residuals."""
    if not isinstance(override, dict) or not override.get("manual_alignment", False):
        return result
    try:
        offset = float(override.get("offset", result.offset))
        slope = float(override.get("scale", result.slope))
    except (TypeError, ValueError):
        return result
    if not math.isfinite(offset) or abs(offset) > 7200 or not math.isfinite(slope) or not 0.5 <= slope <= 2.0:
        return result
    residuals = [a.target_time - (slope * a.source_time + offset) for a in result.anchors]
    if not residuals:
        return replace(result, offset=offset, slope=slope, segments=[], manual_segments=False)
    threshold = max(0.75, result.sample_interval * 1.20)
    rms = (sum(value * value for value in residuals) / len(residuals)) ** 0.5
    inlier_ratio = sum(abs(value) <= threshold for value in residuals) / len(residuals)
    return replace(result, offset=offset, slope=slope, rms_error=rms,
                   inlier_ratio=inlier_ratio, segments=[], manual_segments=False)


def _valid_audio_ordinal(info: MediaInfo, override: dict | None) -> int:
    """Use a saved audio stream only if it still exists in this source file."""
    try:
        ordinal = int((override or {}).get("audio_ordinal", choose_default_audio(info)))
    except (TypeError, ValueError, MediaToolError):
        ordinal = choose_default_audio(info)
    return ordinal if 0 <= ordinal < len(info.audio) else choose_default_audio(info)


def estimate_output_size_bytes(master_size_bytes: int, duration_seconds: float,
                               added_tracks: int, audio_bitrate_kbps: int = 128) -> int:
    """Conservative output size estimate: copied master plus encoded extra audio."""
    master_size = max(0, int(master_size_bytes))
    duration = max(0.0, float(duration_seconds))
    track_count = max(0, int(added_tracks))
    bitrate = max(64, min(320, int(audio_bitrate_kbps)))
    encoded_audio = duration * track_count * bitrate * 1000 / 8
    return int(master_size + encoded_audio * 1.08 + 1024 * 1024)


def batch_export_groups(
    groups: list[EpisodeGroup], output_directory: str,
    cache: AlignmentCache | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    cancel_event=None,
    *,
    roots: list[str] | None = None,
    root_languages: dict[str, dict[str, str]] | None = None,
    organize_by_series: bool = True,
    skip_existing: bool = False,
    overwrite_existing: bool = False,
    media_cache: dict[str, MediaInfo] | None = None,
    alignment_options: dict | None = None,
    track_overrides: dict[str, dict] | None = None,
    master_overrides: dict[str, str] | None = None,
    manual_section_sources: set[str] | None = None,
    journal_directory: Path | None = None,
    resume_from_journal: str | Path | None = None,
    journal_created: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Safely process grouped episodes and skip untrustworthy automatic alignments."""
    cache = cache or AlignmentCache()
    alignment_options = dict(alignment_options or {})
    interval = float(alignment_options.get("interval", 0.5))
    max_samples = int(alignment_options.get("max_samples", 1800))
    hardware_accel = str(alignment_options.get("hardware_accel", "auto"))
    frame_cache_enabled = bool(alignment_options.get("frame_cache_enabled", True))
    sampling_workers = max(1, min(2, int(alignment_options.get("sampling_workers", 1))))
    # Batch jobs deliberately recompute every alignment: a prior result must never
    # decide which episode is fused into a new output.
    cache_enabled = False
    audio_bitrate_kbps = max(64, min(320, int(alignment_options.get("audio_bitrate_kbps", 128))))
    output_dir = Path(output_directory).expanduser().resolve()
    resume_recipe = recipe_id(
        output_directory=str(output_dir), organize_by_series=organize_by_series,
        alignment_options=alignment_options, roots=roots,
        root_languages=root_languages, track_overrides=track_overrides,
        master_overrides=master_overrides)
    # Resolve and validate the selected run before creating an output folder.
    resume_entries = (load_resume_report(resume_from_journal, resume_recipe)
                      if resume_from_journal is not None else [])
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {"exported": [], "skipped": [], "failed": [], "warnings": [], "cancelled": []}
    journal = BatchDiagnostic(groups, alignment_options=alignment_options,
                              skip_existing=skip_existing,
                              overwrite_existing=overwrite_existing,
                              organize_by_series=organize_by_series,
                              directory=journal_directory,
                              output_directory=str(output_dir))
    journal.payload["settings"]["resume_recipe_id"] = resume_recipe
    if resume_from_journal is not None:
        journal.payload["settings"]["resume_from_run"] = Path(resume_from_journal).name
    journal.save()
    report["batch_report_path"] = str(journal.path)
    report["batch_run_id"] = journal.payload["run_id"]
    if journal_created is not None:
        journal_created(str(journal.path))
    manual_section_sources = {str(Path(path).expanduser().resolve())
                              for path in (manual_section_sources or set())}

    def finish_report() -> dict[str, Any]:
        journal.finish(cancelled=bool(report["cancelled"]) or cancelled(), report=report)
        return report

    def skip_episode(index: int, detail: str) -> None:
        report["skipped"].append(detail)
        journal.conclude(index, "skipped", detail)

    def fail_episode(index: int, detail: str) -> None:
        report["failed"].append(detail)
        journal.conclude(index, "failed", detail)

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def announce(current: int, total_count: int, message: str) -> None:
        # TaskWorker.report raises when cancelled. Never allow that exception
        # to replace a useful per-episode result or mask another failure.
        if progress is not None and not cancelled():
            try:
                progress(current, total_count, message)
            except RuntimeError:
                if not cancelled():
                    raise
                # Worker.report might observe cancellation between the guard
                # above and the callback. This is an expected race, not a
                # failed export or a reason to discard the batch summary.

    def mark_cancelled(group: EpisodeGroup, index: int) -> None:
        report["cancelled"].append(f"Abbruch bei {group.display_name} (Stapelposition {index}/{total})")
        journal.conclude(index, "cancelled", "Analyse abgebrochen, kein Synchronisationsurteil")
        report["skipped"].append(f"{group.display_name}: Analyse abgebrochen, kein Synchronisationsurteil")
        for pending in groups[index:]:
            report["skipped"].append(f"{pending.display_name}: wegen Abbruch nicht mehr gestartet")
        # Remaining entries stay 'pending' in the journal: nothing was analyzed.

    # Only skipped, still-unresolved episodes participate in cross-episode
    # recovery. Already-exported sources are never borrowed for another episode.
    unresolved_content: list[dict[str, Any]] = []
    swap_checked: set[tuple[str, str]] = set()

    def try_recover_pair(left: dict[str, Any], right: dict[str, Any]) -> None:
        """Recover a *mutual* E02/E03 swap after both original pairings failed.

        The filenames remain untouched. Each source gets matched against the
        other episode's master, and BOTH matches must pass stricter visual tests.
        No unilateral reassignment or use of a successful episode is permitted.
        """
        if cancelled():
            return
        a, b = left["group"], right["group"]
        if (a.series_name or a.key.split("|", 1)[0]).casefold() != (b.series_name or b.key.split("|", 1)[0]).casefold():
            return
        if a.season_number != b.season_number or a.episode_number is None or b.episode_number is None:
            return
        if abs(a.episode_number - b.episode_number) > 3:
            return
        pair_key = tuple(sorted((a.key, b.key)))
        if pair_key in swap_checked:
            return
        swap_checked.add(pair_key)
        if any(item["index"] in (left["index"], right["index"]) and item.get("recovered")
               for item in unresolved_content):
            return
        announce(right["index"] - 1, total,
                 f"Inhaltsprüfung: Vertauschung {a.display_name} ↔ {b.display_name} testen")
        proposed = []
        for destination, source in ((left, right), (right, left)):
            dst_index = destination["index"]
            source_info = source["alternate"]
            master_info = destination["master"]
            attempt = journal.new_attempt(dst_index, source_info.path, master_info.path,
                                          max_samples, retry=False)
            attempt["cross_episode_check"] = True
            attempt["source_episode"] = (f"S{source['group'].season_number:02d}"
                                         f"E{source['group'].episode_number:02d}")
            try:
                result = align_videos(
                    source_info.path, master_info.path,
                    interval=interval, cancel_event=cancel_event,
                    max_samples=max_samples, hardware_accel=hardware_accel,
                    frame_cache_enabled=frame_cache_enabled,
                    sampling_workers=sampling_workers,
                    source_info=source_info, target_info=master_info,
                    progress=lambda phase, current, amount: announce(
                        dst_index - 1, total,
                        f"Inhaltsprüfung {destination['group'].display_name}: {phase} {current}/{amount}"),
                    batch_context={"position": dst_index, "total": total,
                                   "season": destination["group"].season_number,
                                   "episode": destination["group"].episode_number},
                    diagnostic_sink=lambda payload: journal.capture_alignment(attempt, payload),
                )
                journal.finish_attempt(attempt, result)
                attempt["cross_episode_verified"] = _is_independent_episodic_match(result)
                journal.save()
                proposed.append((destination, source, result))
                if not attempt["cross_episode_verified"]:
                    journal.event(dst_index, "content_mismatch",
                                  "Keine belastbare Inhaltsübereinstimmung mit der anderen Folge")
                    return
            except Exception as exc:
                journal.finish_attempt(attempt, error=f"{type(exc).__name__}: {exc}")
                if cancelled():
                    raise BatchCancelled("Inhaltsprüfung abgebrochen") from exc
                journal.event(dst_index, "content_mismatch", "Kreuzprüfung technisch fehlgeschlagen")
                return
        # Both directions are confirmed before any output is written.
        for destination, source, result in proposed:
            idx = destination["index"]
            from_info = source["alternate"]
            master_info = destination["master"]
            track_override = override_by_path.get(str(Path(from_info.path).resolve()), {})
            ordinal = _valid_audio_ordinal(from_info, track_override)
            stream = from_info.audio[ordinal]
            language, title = resolve_track_label(
                from_info.path, stream.language, stream.title,
                roots=source_roots, root_languages=root_languages,
                inferred_language=infer_language(from_info.path))
            if track_override.get("language"):
                language = normalize_language_code(str(track_override["language"]))
            if track_override.get("title"):
                title = str(track_override["title"])
            track = TrackChoice(from_info.path, ordinal, language, title, True, result)
            journal.stage(idx, "export", "Quellen nach beidseitig bestätigter Episodenvertauschung korrigiert")
            try:
                created = export_multilingual(
                    master_info.path, [track], str(destination["output"]),
                    allow_low_confidence=False, overwrite=destination["overwrite"],
                    master_audio_metadata=destination["master_audio_metadata"],
                    progress=lambda pct, msg: announce(idx - 1, total,
                        f"{destination['group'].display_name}: {msg} {pct}%"),
                    cancel_event=cancel_event,
                    audio_bitrate_kbps=audio_bitrate_kbps)
            except Exception as exc:
                if cancelled():
                    raise BatchCancelled("Korrekturexport abgebrochen") from exc
                fail_episode(idx, f"{destination['group'].display_name}: "
                             f"Korrekturexport fehlgeschlagen: {type(exc).__name__}: {exc}")
                destination["recovered"] = True
                if destination["skip_reason"] in report["skipped"]:
                    report["skipped"].remove(destination["skip_reason"])
                continue
            destination["recovered"] = True
            if destination["skip_reason"] in report["skipped"]:
                report["skipped"].remove(destination["skip_reason"])
            report["exported"].append(created)
            message = (f"{destination['group'].display_name}: Inhalt bestätigt – "
                       f"Audiospur aus der als {source['group'].display_name} benannten Datei "
                       "übernommen; Originaldateien unverändert")
            report["warnings"].append(message)
            journal.event(idx, "confirmed_episode_swap", message)
            journal.conclude(idx, "exported", message, output_filename=Path(created).name)
            announce(idx, total, "KORRIGIERT: " + message)

    total = len(groups)
    # Preflight all destination names before doing expensive alignment. Different
    # input groups can occasionally normalize to the same series/season/episode
    # path; silently letting the first output make the second look "already done"
    # is dangerous. Fail every member of a collision instead of choosing a winner.
    destinations: dict[str, list[EpisodeGroup]] = {}
    for candidate in groups:
        target = str(output_path_for_group(
            str(output_dir), candidate, organize_by_series=organize_by_series,
        ).resolve()).casefold()
        destinations.setdefault(target, []).append(candidate)
    collided_keys: set[str] = set()
    preflight_issues: dict[str, list[str]] = {}

    def preflight_problem(group: EpisodeGroup, reason: str) -> None:
        collided_keys.add(group.key)
        preflight_issues.setdefault(group.key, []).append(reason)
    for candidate in groups:
        for issue in episode_identity_errors(candidate):
            preflight_problem(candidate, issue)
            report["failed"].append(
                f"Episodenzuordnung widersprüchlich: {candidate.display_name}: {issue} "
                "Kein automatischer Export."
            )
    # A source file assigned to multiple episode groups is a strong sign of stale
    # selection/grouping state. Never let the same source silently become E05, E06, etc.
    source_owners: dict[str, list[EpisodeGroup]] = {}
    for candidate in groups:
        for source_path in candidate.files:
            resolved_source = str(Path(source_path).expanduser().resolve()).casefold()
            source_owners.setdefault(resolved_source, []).append(candidate)
    for source_path, owners in source_owners.items():
        unique_keys = {owner.key for owner in owners}
        if len(unique_keys) > 1:
            for owner in owners:
                preflight_problem(owner, "Quelldatei ist mehreren Episodengruppen zugeordnet")
            labels = " / ".join(sorted({owner.display_name for owner in owners}))
            report["failed"].append(
                f"Quelldatei mehreren Episoden zugeordnet: {Path(source_path).name} ({labels}). "
                "Diese Gruppen wurden aus Sicherheitsgründen nicht exportiert; Bibliothek neu scannen und Quellenzuordnung prüfen."
            )
    # Also catch byte-identical copies that have different paths and filenames. This
    # uses fresh content reads instead of the persisted quick-fingerprint cache.
    fingerprints: dict[str, list[tuple[str, EpisodeGroup]]] = {}
    for candidate in groups:
        if cancelled():
            report["cancelled"].append("Abbruch während der Stapel-Vorprüfung")
            return finish_report()
        for source_path in candidate.files:
            if cancelled():
                report["cancelled"].append("Abbruch während der Datei-Vorprüfung")
                return finish_report()
            resolved_source = str(Path(source_path).expanduser().resolve())
            fingerprint = _fresh_source_fingerprint(resolved_source)
            if fingerprint:
                fingerprints.setdefault(fingerprint, []).append((resolved_source, candidate))
    for entries in fingerprints.values():
        owners = {group.key: group for _path, group in entries}
        if len(owners) > 1:
            unique_keys = set(owners)
            for owner in owners.values():
                preflight_problem(owner, "Identischer Videoinhalt in mehreren Episodengruppen")
            names = " / ".join(sorted(group.display_name for group in owners.values()))
            paths = ", ".join(sorted({Path(path).name for path, _group in entries}))
            report["failed"].append(
                f"Identischer Videoinhalt in mehreren Episodengruppen ({paths}): {names}. "
                "Keine betroffene Gruppe exportiert; Dateizuordnung und Episodennamen prüfen."
            )
    for target, members in destinations.items():
        if len(members) > 1:
            for member in members:
                preflight_problem(member, "Mehrere Episoden haben denselben Ausgabezielpfad")
            names = " / ".join(member.display_name for member in members)
            report["failed"].append(
                f"Zielkonflikt: {names} würden dieselbe Ausgabedatei erhalten ({target}). "
                "Keine dieser Gruppen wurde exportiert; prüfe Serien-/Episodennamen."
            )
    source_roots = [str(Path(root).expanduser().resolve()) for root in (roots or [])]
    known_media = {
        str(Path(path).expanduser().resolve()): info
        for path, info in (media_cache or {}).items()
    }
    # Normalize persisted per-file choices once before iterating the series.
    override_by_path = {
        str(Path(path).expanduser().resolve()): dict(value)
        for path, value in (track_overrides or {}).items()
        if isinstance(path, str) and isinstance(value, dict)
    }
    master_by_group = {
        str(key): str(Path(value).expanduser().resolve())
        for key, value in (master_overrides or {}).items()
        if isinstance(key, str) and isinstance(value, str) and value.strip()
    }

    def get_info(path: str) -> MediaInfo:
        resolved = str(Path(path).expanduser().resolve())
        info = known_media.get(resolved)
        if info is None:
            info = probe_media(resolved)
            known_media[resolved] = info
            if media_cache is not None:
                media_cache[resolved] = info
        return info

    for index, group in enumerate(groups, 1):
        if cancelled():
            mark_cancelled(group, index)
            break
        if group.key in collided_keys:
            journal.conclude(index, "failed", "; ".join(preflight_issues.get(group.key, ["Zuordnung/Zielkonflikt"])))
            announce(index, total, batch_step_message(index, total, group, "Zuordnung/Zielkonflikt · nicht gestartet"))
            continue
        journal.start(index)
        announce(index - 1, total, batch_step_message(index, total, group, "Vorprüfung"))
        if len(group.files) < 2:
            skip_episode(index, f"{group.display_name}: nur eine Datei gefunden")
            continue
        if any(str(Path(path).expanduser().resolve()) in manual_section_sources
               for path in group.files):
            detail = (f"{group.display_name}: manuelle Schnittbereiche vorhanden; "
                      "nur der Einzelexport bietet die erforderliche Bestätigung")
            skip_episode(index, detail)
            announce(index, total, "ÜBERSPRUNGEN: " + detail)
            continue

        # A completed output can be resumed/skipped before FFprobe or alignment
        # work starts. This is important when the user reruns a whole-season job.
        output = output_path_for_group(
            str(output_dir), group, organize_by_series=organize_by_series,
        )
        overwrite = False
        if output.exists():
            if resume_entries and verified_resume_entry(resume_entries, group, output, resume_recipe):
                # Proof alone does not establish a decodable output. Always
                # probe the actual MKV and reject missing video/audio streams.
                try:
                    existing_info = probe_media(str(output))
                    valid_output = bool(existing_info.video and existing_info.audio)
                except (OSError, MediaToolError, ValueError):
                    valid_output = False
                if valid_output:
                    detail = f"{group.display_name}: durch Stapelprotokoll und aktuelle Ausgabe verifiziert; sicher fortgesetzt"
                    skip_episode(index, detail)
                    # Carry the newly verified proof into this run's journal.
                    # Otherwise a second crash would lose evidence for outputs
                    # completed *before* the first restart.
                    journal.resume_proof(index, proof_for(group, output, resume_recipe))
                    journal.event(index, "verified_resume", "Quellen und Ausgabe erneut geprüft")
                    announce(index, total, "FORTGESETZT: " + detail)
                    continue
            if skip_existing:
                detail = f"{group.display_name}: fertige Ausgabe übersprungen: Ausgabe existiert bereits (Option 'überspringen' aktiviert) ({output})"
                skip_episode(index, detail)
                announce(index, total, "ÜBERSPRUNGEN: " + detail)
                if progress:
                    progress(index, total, f"Bereits fertig: {group.display_name}")
                continue
            if not overwrite_existing:
                detail = f"{group.display_name}: Ausgabe existiert; Überschreiben nicht freigegeben ({output})"
                skip_episode(index, detail)
                announce(index, total, "ÜBERSPRUNGEN: " + detail)
                if progress:
                    progress(index, total, f"Überspringe vorhandene Ausgabe: {group.display_name}")
                continue
            overwrite = True

        try:
            journal.stage(index, "metadata", "Metadaten aus Quellen lesen")
            infos = [get_info(path) for path in group.files]
            info_by_path = {str(Path(info.path).resolve()): info for info in infos}
            video_candidates = [info for info in infos if info.video]
            if len(video_candidates) < 2:
                skip_episode(index, f"{group.display_name}: weniger als zwei Videofassungen gefunden")
                continue
            master_info = choose_smallest_video(video_candidates)
            manual_master_path = master_by_group.get(group.key)
            if manual_master_path:
                manual_master_info = info_by_path.get(manual_master_path)
                if manual_master_info is not None and manual_master_info.video:
                    master_info = manual_master_info
                else:
                    report["warnings"].append(
                        f"{group.display_name}: gespeichertes Bildmaster fehlt oder enthält kein Video; "
                        "die kleinste verfügbare Fassung wird verwendet."
                    )
            master = master_info.path
            journal.master(index, master, master_info.duration)
            # The smallest file is the default picture reference, but saved per-episode
            # overrides are respected. Explicitly excluded audio sources stay excluded.
            alternates = [
                info for info in video_candidates
                if info.path != master and info.audio
                and override_by_path.get(str(Path(info.path).resolve()), {}).get("include", True) is not False
            ]
            if not alternates:
                skip_episode(index, f"{group.display_name}: keine zusätzliche Audiospur gefunden")
                continue
            master_mib = master_info.size_bytes / (1024 * 1024)
            if progress:
                progress(index - 1, total,
                         f"Kompaktes Mastervideo: {Path(master).name} ({master_mib:.1f} MiB)")
            known_durations = [info.duration for info in video_candidates if info.duration > 0]
            if known_durations and max(known_durations) - master_info.duration > 2.0:
                warning = (
                    f"{group.display_name}: kleinste Bildmaster-Datei ist {max(known_durations) - master_info.duration:.1f}s "
                    "kürzer als eine andere Fassung; das Ausgabevideo übernimmt die Laufzeit des Bildmasters."
                )
                report["warnings"].append(warning)
                journal.event(index, "warning", warning)
                if progress:
                    progress(index - 1, total, "WARNUNG: " + warning)
            estimated_size = estimate_output_size_bytes(
                master_info.size_bytes, master_info.duration, len(alternates), audio_bitrate_kbps
            )
            try:
                free_bytes = shutil.disk_usage(output_dir).free
            except OSError:
                free_bytes = None
            reserve = max(64 * 1024 * 1024, int(estimated_size * 0.10))
            if free_bytes is not None and free_bytes < estimated_size + reserve:
                fail_episode(index,
                    f"{group.display_name}: zu wenig freier Speicher; grob benötigt "
                    f"{(estimated_size + reserve) / (1024**3):.2f} GiB inkl. Reserve, "
                    f"verfügbar {free_bytes / (1024**3):.2f} GiB."
                )
                if progress:
                    progress(index, total, f"Nicht genug Speicher für {group.display_name}")
                continue
            if progress:
                progress(index - 1, total,
                         f"Geschätzte Ausgabegröße: {estimated_size / (1024 * 1024):.1f} MiB · "
                         f"Bildmaster: {Path(master).name}")
            master_audio_metadata: dict[int, tuple[str, str]] = {}
            master_override = override_by_path.get(str(Path(master).resolve()), {})
            if master_info.audio:
                master_ordinal = _valid_audio_ordinal(master_info, master_override)
                master_stream = master_info.audio[master_ordinal]
                language, title = resolve_track_label(
                    master, master_stream.language, master_stream.title,
                    roots=source_roots, root_languages=root_languages,
                    inferred_language=infer_language(master),
                )
                language_override = str(master_override.get("language", "")).strip()
                title_override = str(master_override.get("title", "")).strip()
                if language_override:
                    language = normalize_language_code(language_override)
                if title_override:
                    title = title_override
                master_audio_metadata[master_ordinal] = (language, title)
            tracks: list[TrackChoice] = []
            unsafe: list[str] = []

            for alternate_index, info in enumerate(alternates, 1):
                if cancel_event is not None and cancel_event.is_set():
                    raise BatchCancelled("Abbruch angefordert")
                result = cache.get(
                    info.path, master, interval=interval,
                    max_samples=max_samples, hardware_accel=hardware_accel,
                ) if cache_enabled else None
                if result is None:
                    def subprogress(phase, current, amount):
                        announce(index - 1, total, batch_step_message(
                            index, total, group,
                            f"Bildabgleich {Path(info.path).name} · {phase} {current}/{amount}"
                        ))
                    attempt = journal.new_attempt(index, info.path, master, max_samples)
                    try:
                        result = align_videos(
                        info.path, master, interval=interval, progress=subprogress,
                        cancel_event=cancel_event, max_samples=max_samples,
                        hardware_accel=hardware_accel,
                        frame_cache_enabled=frame_cache_enabled,
                        sampling_workers=sampling_workers,
                        source_info=info_by_path[str(Path(info.path).resolve())],
                        target_info=master_info,
                        batch_context={"position": index, "total": total,
                                       "season": group.season_number,
                                       "episode": group.episode_number},
                        diagnostic_sink=lambda payload: journal.capture_alignment(attempt, payload),
                        )
                    except Exception as exc:
                        journal.finish_attempt(attempt, error=type(exc).__name__ + ": " + str(exc))
                        raise
                    journal.finish_attempt(attempt, result)
                    if cache_enabled:
                        cache.put(result)
                track_override = override_by_path.get(str(Path(info.path).resolve()), {})
                result = _manual_alignment(result, track_override)
                if not result.safe_for_automatic_export:
                    # Do not silently skip a difficult episode after only the
                    # user's fast sampling preset. Different encodes and long
                    # intros can become ambiguous at a coarse sampling rate.
                    # A one-time higher-resolution retry may confirm a safe
                    # mapping, but cannot override the normal export rules.
                    retry_budget = min(3600, max(2400, max_samples * 2))
                    needs_retry = (
                        abs(info.duration - master_info.duration) >= 30.0
                        and max_samples < retry_budget
                        and not cancelled()
                        and not track_override.get("manual_alignment", False)
                    )
                    if needs_retry:
                        announce(index - 1, total, batch_step_message(
                            index, total, group,
                            f"Unsicherer Bildabgleich: gründliche Zweitanalyse mit {retry_budget} Proben",
                        ))
                        retry_attempt = journal.new_attempt(index, info.path, master, retry_budget, retry=True)
                        try:
                            retry = align_videos(
                            info.path, master, interval=interval,
                            progress=subprogress, cancel_event=cancel_event,
                            max_samples=retry_budget, hardware_accel=hardware_accel,
                            frame_cache_enabled=frame_cache_enabled,
                            sampling_workers=sampling_workers,
                            source_info=info_by_path[str(Path(info.path).resolve())],
                            target_info=master_info,
                            batch_context={"position": index, "total": total,
                                           "season": group.season_number,
                                           "episode": group.episode_number},
                            diagnostic_sink=lambda payload: journal.capture_alignment(retry_attempt, payload),
                            )
                        except Exception as exc:
                            journal.finish_attempt(retry_attempt, error=type(exc).__name__ + ": " + str(exc))
                            raise
                        journal.finish_attempt(retry_attempt, retry)
                        retry = _manual_alignment(retry, track_override)
                        if retry.safe_for_automatic_export:
                            report["warnings"].append(
                                f"{group.display_name}: erst die gründliche Zweitanalyse mit "
                                f"{retry_budget} Bildproben bestätigte die Synchronisation."
                            )
                            result = retry
                        else:
                            report["warnings"].append(
                                f"{group.display_name}: auch die gründliche Zweitanalyse mit "
                                f"{retry_budget} Bildproben blieb unsicher."
                            )
                            # Show the second (more informative) result in the summary.
                            result = retry
                if not result.safe_for_automatic_export:
                    failed_conditions = []
                    if result.confidence < 0.62:
                        failed_conditions.append(f"Konfidenz {result.confidence:.2f} < 0.62")
                    if result.inlier_ratio < 0.65:
                        failed_conditions.append(f"Anker-Konsistenz {result.inlier_ratio:.2f} < 0.65")
                    if result.coverage < 0.55:
                        failed_conditions.append(f"Abdeckung {result.coverage:.2f} < 0.55")
                    if result.median_hamming > 15:
                        failed_conditions.append(f"Bildabstand {result.median_hamming:.1f} > 15")
                    if result.rms_error > max(0.75, result.sample_interval * 1.20):
                        failed_conditions.append(f"Zeitfehler {result.rms_error:.2f}s zu groß")
                    if not 0.90 <= result.slope <= 1.10:
                        failed_conditions.append(f"Zeitfaktor {result.slope:.4f} außerhalb 0.90–1.10")
                    if result.mapped_overlap_ratio < 0.55:
                        failed_conditions.append(f"Zeitüberdeckung {result.mapped_overlap_ratio:.2f} < 0.55")
                    if len(result.anchors) < 8:
                        failed_conditions.append(f"nur {len(result.anchors)} Bildanker")
                    if result.visual_distinct_hashes < 8:
                        failed_conditions.append("zu wenige verschiedene Bildmuster")
                    unsafe.append(Path(info.path).name + " (" + "; ".join(
                        failed_conditions or result.warnings or ["keine validierte Übereinstimmung"]
                    ) + ")")
                    announce(index, total, batch_step_message(
                        index, total, group, "Bildabgleich unsicher: " + ", ".join(failed_conditions[:2])
                    ))
                    continue
                audio_ordinal = _valid_audio_ordinal(info, track_override)
                stream = info.audio[audio_ordinal]
                language, title = resolve_track_label(
                    info.path, stream.language, stream.title,
                    roots=source_roots, root_languages=root_languages,
                    inferred_language=infer_language(info.path),
                )
                language_override = str(track_override.get("language", "")).strip()
                title_override = str(track_override.get("title", "")).strip()
                if language_override:
                    language = normalize_language_code(language_override)
                if title_override:
                    title = title_override
                tracks.append(TrackChoice(
                    path=info.path, audio_ordinal=audio_ordinal,
                    language=language, title=title, include=True, alignment=result,
                ))

            if unsafe:
                detail = f"{group.display_name}: unsichere Synchronisation; Export übersprungen: " + ", ".join(unsafe)
                content_problem = (len(alternates) == 1 and len(group.files) == 2
                                   and _suspected_content_mismatch(result))
                if content_problem:
                    detail += ("; INHALTSVERDACHT: Bildtreffer fast nur am Anfang/Ende. "
                               "Die Dateien könnten unterschiedliche Folgen enthalten; "
                               "DubSync prüft bekannte weitere unsichere Episoden auf eine Vertauschung.")
                skip_episode(index, detail)
                announce(index, total, "ÜBERSPRUNGEN: " + detail)
                # In the common two-library case, verify whether E02 and E03
                # have been interchanged in one source library. Never borrow
                # the audio of an already-successfully-exported episode.
                if len(alternates) == 1 and len(group.files) == 2 and not master_by_group.get(group.key):
                    if content_problem:
                        journal.event(index, "suspected_content_mismatch",
                                      "Bildübereinstimmungen überwiegend am Anfang/Ende; "
                                      "möglicherweise andere oder vertauschte Folge")
                        entry = {"index": index, "group": group, "master": master_info,
                                 "alternate": alternates[0], "output": output,
                                 "overwrite": overwrite, "master_audio_metadata": master_audio_metadata,
                                 "skip_reason": detail, "recovered": False}
                        unresolved_content.append(entry)
                        for prior in unresolved_content[:-1][-4:]:
                            if cancelled() or entry["recovered"]:
                                break
                            if not prior["recovered"]:
                                try_recover_pair(prior, entry)
                continue
            if len(tracks) != len(alternates) or not tracks:
                skip_episode(index, f"{group.display_name}: keine sicher synchronisierbaren Zusatzspuren")
                continue

            journal.stage(index, "export", f"{len(tracks)} zusätzliche Audiospuren verarbeiten")
            created = export_multilingual(
                master, tracks, str(output), allow_low_confidence=False,
                overwrite=overwrite, master_audio_metadata=master_audio_metadata,
                progress=lambda pct, msg: progress(index - 1, total, f"{group.display_name}: {msg} {pct}%") if progress else None,
                cancel_event=cancel_event,
                audio_bitrate_kbps=audio_bitrate_kbps,
            )
            report["exported"].append(created)
            journal.conclude(index, "exported", f"{len(tracks)} Audiospuren erfolgreich ausgerichtet",
                             output_filename=Path(created).name)
            journal.resume_proof(index, proof_for(group, output, resume_recipe))
        except BatchCancelled:
            mark_cancelled(group, index)
            break
        except Exception as exc:
            if cancelled():
                # Cancellation during frame sampling, export, or progress must
                # not be counted as a decoder failure or an unsafe alignment.
                mark_cancelled(group, index)
                break
            fail_episode(index, f"{group.display_name}: {type(exc).__name__}: {exc}")
        finally:
            announce(index, total, batch_step_message(index, total, group, "Bearbeitung beendet"))

    return finish_report()
