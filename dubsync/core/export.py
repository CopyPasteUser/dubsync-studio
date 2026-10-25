from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Callable

from .models import AlignmentResult, TrackChoice
from .languages import normalize_language_code
from .probe import MediaToolError, probe_media, require_media_tools

Progress = Callable[[int, str], None]


class ExportError(RuntimeError):
    pass


def _language_code(value: str | None) -> str:
    code = normalize_language_code(value)
    return code if len(code) == 3 else "und"


def _filter_number(value: float) -> str:
    return f"{value:.9f}".rstrip("0").rstrip(".") or "0"


def export_multilingual(
    master_path: str,
    tracks: list[TrackChoice],
    output_path: str,
    *,
    allow_low_confidence: bool = False,
    overwrite: bool = False,
    master_audio_metadata: dict[int, tuple[str, str]] | None = None,
    progress: Progress | None = None,
    cancel_event=None,
    audio_bitrate_kbps: int = 128,
) -> str:
    """Mux extra audio tracks into the master video, aligning by an affine time map.

    The master video and its existing audio/subtitle/attachment streams are copied.
    Each selected alternate audio is time-stretched with atempo and offset/padded to
    the master timeline. Complex edit differences are surfaced by alignment warnings;
    they are not silently claimed to be corrected by this affine export stage.
    """
    ffmpeg, _ = require_media_tools()
    audio_bitrate_kbps = max(64, min(320, int(audio_bitrate_kbps)))
    master = Path(master_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if not master.is_file():
        raise ExportError(f"Masterdatei nicht gefunden: {master}")
    if output == master or any(output == Path(t.path).expanduser().resolve() for t in tracks):
        raise ExportError("Die Ausgabedatei darf keine Eingabedatei überschreiben.")
    if output.exists() and not overwrite:
        raise ExportError(f"Ausgabedatei existiert bereits: {output}")

    master_info = probe_media(str(master))
    if not master_info.video:
        raise ExportError("Die Masterdatei enthält keinen Videostream.")
    if master_info.duration <= 0:
        raise ExportError("Laufzeit des Mastervideos konnte nicht ermittelt werden.")
    selected = [t for t in tracks if t.include]
    used_paths = {str(master)}
    for track in selected:
        source = Path(track.path).expanduser().resolve()
        if str(source) in used_paths:
            raise ExportError(f"Dieselbe Quelldatei wurde mehrfach ausgewählt: {source.name}")
        used_paths.add(str(source))
        if not source.is_file():
            raise ExportError(f"Audiodatei nicht gefunden: {source}")
        if track.alignment is None:
            raise ExportError(f"Keine Synchronisationsanalyse für {source.name} vorhanden.")
        if Path(track.alignment.target_path).resolve() != master:
            raise ExportError(f"Synchronisationsanalyse gehört nicht zum ausgewählten Master: {source.name}")
        if not track.alignment.safe_for_automatic_export and not allow_low_confidence:
            raise ExportError(
                f"Synchronisation für {source.name} ist unsicher. Prüfe die Warnungen oder erlaube den Export ausdrücklich."
            )
        if not 0.5 <= track.alignment.slope <= 2.0:
            raise ExportError(f"Unplausibler Zeitmaßstab für {source.name}: {track.alignment.slope:.5f}")

    if not selected:
        raise ExportError("Keine zusätzlichen Audiospuren ausgewählt.")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not overwrite:
        raise ExportError(f"Ausgabe wurde inzwischen angelegt: {output}")
    temporary = output.with_name(f".{output.stem}.dubsync-{uuid.uuid4().hex[:10]}.mkv")
    input_args = ["-i", str(master)]
    filters: list[str] = []
    metadata_args: list[str] = []
    master_audio_count = len(master_info.audio)

    # Optional user-defined label for a master audio stream. The master file itself
    # remains untouched; metadata is only rewritten in the new MKV container.
    for ordinal, (language, title) in (master_audio_metadata or {}).items():
        if not isinstance(ordinal, int) or ordinal < 0 or ordinal >= master_audio_count:
            raise ExportError(f"Ungültige Master-Audiospur für Metadaten: {ordinal}")
        language_code = _language_code(language)
        safe_title = (title or "").replace("\n", " ").replace("\r", " ").strip()[:180]
        if language_code != "und":
            metadata_args.extend([f"-metadata:s:a:{ordinal}", f"language={language_code}"])
        if safe_title:
            metadata_args.extend([f"-metadata:s:a:{ordinal}", f"title={safe_title}"])

    for index, track in enumerate(selected):
        source = str(Path(track.path).expanduser().resolve())
        input_index = index + 1
        input_args.extend(["-i", source])
        align = track.alignment
        slope = align.slope
        tempo = 1.0 / slope
        offset = align.offset
        # Mapping: target_time = slope * source_time + offset.
        # Positive offset means leading silence; negative offset means the early
        # source audio maps to negative target time and must be trimmed.
        source_skip = max(0.0, -offset / slope)
        delay_ms = max(0, int(round(offset * 1000.0)))
        audio_ordinal = max(0, int(track.audio_ordinal))
        label = f"dub{index}"
        chain = (
            f"[{input_index}:a:{audio_ordinal}]aresample=48000,"
            f"atrim=start={_filter_number(source_skip)},asetpts=PTS-STARTPTS,"
            f"atempo={_filter_number(tempo)},"
            f"adelay={delay_ms}:all=1,apad,"
            f"atrim=duration={_filter_number(master_info.duration)}[{label}]"
        )
        filters.append(chain)

        out_audio_index = master_audio_count + index
        language = _language_code(track.language)
        title = (track.title or Path(source).stem).replace("\n", " ").replace("\r", " ")[:180]
        metadata_args.extend([
            f"-metadata:s:a:{out_audio_index}", f"language={language}",
            f"-metadata:s:a:{out_audio_index}", f"title={title}",
            f"-disposition:a:{out_audio_index}", "0",
        ])

    # Das Mastervideo bleibt kopiert. Vorhandene Master-Audios und Untertitel werden
    # ebenfalls kopiert; nur die neu synchronisierten Audiospuren werden neu codiert.
    command = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "warning", "-progress", "pipe:1", "-nostats"]
    command += input_args
    command += ["-map", "0:v:0", "-map", "0:a?", "-map", "0:s?", "-map", "0:t?"]
    for index in range(len(selected)):
        command += ["-map", f"[{f'dub{index}'}]"]
    command += ["-map_metadata", "0", "-map_chapters", "0", "-c:v", "copy", "-c:a", "copy", "-c:s", "copy", "-c:t", "copy"]
    for index in range(len(selected)):
        output_audio_index = master_audio_count + index
        command += [f"-c:a:{output_audio_index}", "aac", f"-b:a:{output_audio_index}", f"{audio_bitrate_kbps}k"]
    command += metadata_args
    if master_audio_count:
        command += ["-disposition:a:0", "default"]
    else:
        command += ["-disposition:a:0", "default"]
    command += ["-max_interleave_delta", "0", "-f", "matroska", str(temporary)]
    if filters:
        # filter_complex must appear after inputs; it is inserted before map options.
        input_count_position = 1 + (len(input_args) // 2) * 2
        # Since the final command is assembled from options, safely insert directly
        # after all `-i` pairs and before maps.
        first_map = command.index("-map")
        command[first_map:first_map] = ["-filter_complex", ";".join(filters)]

    if progress:
        progress(0, "Starte FFmpeg-Muxing …")
    stderr_file = tempfile.NamedTemporaryFile(prefix="dubsync-ffmpeg-", suffix=".log", delete=False)
    stderr_name = stderr_file.name
    process = None
    try:
        with stderr_file:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=stderr_file,
                stdin=subprocess.DEVNULL, text=True, bufsize=1,
            )
            if process.stdout is not None:
                try:
                    for line in process.stdout:
                        if cancel_event is not None and cancel_event.is_set():
                            process.terminate()
                            try:
                                process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
                            raise ExportError("Export wurde abgebrochen.")
                        key, sep, value = line.strip().partition("=")
                        if not sep or not progress:
                            continue
                        if key in {"out_time_us", "out_time_ms"}:
                            try:
                                seconds = float(value) / 1_000_000.0
                                percent = min(99, max(0, int(100 * seconds / master_info.duration)))
                                progress(percent, "Muxing und Synchronisation …")
                            except (ValueError, ZeroDivisionError):
                                pass
                finally:
                    process.stdout.close()
            return_code = process.wait()
        if return_code != 0:
            log_text = Path(stderr_name).read_text(encoding="utf-8", errors="replace")[-5000:]
            raise ExportError("FFmpeg-Export fehlgeschlagen:\n" + log_text)

        if not temporary.is_file() or temporary.stat().st_size < 1024:
            raise ExportError("FFmpeg hat keine plausible Ausgabedatei erzeugt.")
        if progress:
            progress(98, "Prüfe exportierte Datei …")
        checked = probe_media(str(temporary))
        expected_audio = master_audio_count + len(selected)
        if not checked.video:
            raise ExportError("Exportprüfung fehlgeschlagen: Videostream fehlt.")
        if checked.audio_streams != expected_audio:
            raise ExportError(
                f"Exportprüfung: erwartet {expected_audio} Audiospuren, gefunden {checked.audio_streams}."
            )
        difference = abs(checked.duration - master_info.duration)
        if difference > max(2.0, master_info.duration * 0.005):
            raise ExportError(
                f"Exportprüfung: Laufzeit weicht vom Master ab ({checked.duration:.2f}s statt {master_info.duration:.2f}s)."
            )

        if output.exists() and not overwrite:
            raise ExportError(f"Die Ausgabedatei existiert inzwischen: {output}")
        os.replace(temporary, output)
        if progress:
            progress(100, "Export geprüft und abgeschlossen.")
        return str(output)
    except OSError as exc:
        raise ExportError(f"Datei- oder Prozessfehler beim Export: {exc}") from exc
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
        try:
            Path(stderr_name).unlink(missing_ok=True)
        except OSError:
            pass
