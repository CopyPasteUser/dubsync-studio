"""Opt-in subtitle acquisition for the picture master's own timeline.

Sidecars/embedded tracks are tried before optional online and speech backends.
All intermediate files live outside the source library and are removed after export.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .languages import normalize_language_code
from .models import MediaInfo


class SubtitleError(RuntimeError):
    pass


@dataclass(frozen=True)
class SubtitleOptions:
    mode: str = "off"  # off, auto, existing, file, sidecar, archive, embedded, download, subdl, url, generate
    language: str = "auto"  # Speech language; not an automatic translation request.
    file_path: str = ""
    whisper_model: str = "small"


_ISO2 = {
    "deu": "de", "eng": "en", "fra": "fr", "spa": "es", "ita": "it",
    "jpn": "ja", "kor": "ko", "rus": "ru", "zho": "zh", "por": "pt",
    "pol": "pl", "nld": "nl", "tur": "tr", "ces": "cs", "ara": "ar",
    "swe": "sv", "dan": "da", "fin": "fi", "nor": "no", "heb": "he",
    "hun": "hu", "ron": "ro", "ukr": "uk", "ell": "el",
}
_TEXT_SUBTITLES = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}
_FORMATS = ("srt", "ass", "ssa", "vtt")
_MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
_MAX_SUBTITLE_BYTES = 5 * 1024 * 1024
_MAX_PROVIDER_CANDIDATES = 3


def _run(command: list[str], *, timeout: int, cancel_event=None) -> None:
    """Bound long external operations and allow cancellation without pipe deadlocks."""
    from tempfile import TemporaryFile

    with TemporaryFile() as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    raise SubtitleError("Untertitelverarbeitung abgebrochen.")
                if time.monotonic() >= deadline:
                    raise SubtitleError("Zeitlimit bei der Untertitelverarbeitung überschritten.")
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode:
                log.seek(0)
                detail = log.read()[-1500:].decode("utf-8", "replace")
                raise SubtitleError("Untertitelprogramm fehlgeschlagen: " + detail)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def _usable(path: Path) -> bool:
    return path.is_file() and 12 < path.stat().st_size < 20 * 1024 * 1024


def _find_program(name: str) -> str | None:
    # ./run.sh starts the venv Python without activating its bin directory.
    local = Path(sys.executable).parent / name
    return str(local) if local.is_file() and os.access(local, os.X_OK) else shutil.which(name)


def _find_sidecar(master: Path, language: str) -> Path | None:
    suffixes = []
    if language not in {"und", "auto"}:
        suffixes.extend([language, _ISO2.get(language, "")])
    suffixes.append("")
    for suffix in dict.fromkeys(suffixes):
        for extension in _FORMATS:
            name = f"{master.stem}.{suffix}.{extension}" if suffix else f"{master.stem}.{extension}"
            candidate = master.with_name(name)
            if _usable(candidate):
                return candidate
    return None


def _episode_id(name: str) -> tuple[int, int] | None:
    match = re.search(r"(?i)\bS(\d{1,2})[ ._-]*E(\d{1,3})\b", name)
    if not match:
        match = re.search(r"(?i)\b(\d{1,2})x(\d{2,3})\b", name)
    return (int(match[1]), int(match[2])) if match else None


def _extract_archive(archive: Path, master: Path, language: str, directory: Path) -> Path:
    """Read a single caption from ZIP without extracting arbitrary archive paths."""
    if not archive.is_file() or not 0 < archive.stat().st_size <= _MAX_ARCHIVE_BYTES:
        raise SubtitleError("Untertitel-ZIP fehlt oder ist zu groß.")
    episode = _episode_id(master.stem)
    desired_languages = {language, _ISO2.get(language, "")} - {"", "auto", "und"}
    try:
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            if len(members) > 1000:
                raise SubtitleError("Untertitelarchiv enthält zu viele Dateien.")
            candidates = []
            for item in members:
                name = Path(item.filename).name
                if item.is_dir() or Path(name).suffix.lower().lstrip(".") not in _FORMATS:
                    continue
                if not 12 < item.file_size <= _MAX_SUBTITLE_BYTES:
                    continue
                if item.compress_size == 0 or item.file_size > item.compress_size * 100:
                    continue
                item_episode = _episode_id(name)
                if episode and item_episode and episode != item_episode:
                    continue
                if episode is None and item_episode is not None:
                    continue
                language_tags = set(re.split(r"[._\-\s]+", Path(name).stem.casefold()))
                known_tags = language_tags & ({*(_ISO2.values()), *(_ISO2.keys())})
                if desired_languages and known_tags and not (desired_languages & known_tags):
                    continue
                score = (4 if item_episode == episode and episode else 0)
                score += (2 if desired_languages & language_tags else 0)
                score += (1 if master.stem.casefold() in name.casefold() else 0)
                candidates.append((score, item))
            if not candidates:
                raise SubtitleError("Keine passende Text-Untertiteldatei im ZIP gefunden.")
            _, chosen = max(candidates, key=lambda pair: pair[0])
            output = directory / ("archive" + Path(chosen.filename).suffix.lower())
            with package.open(chosen) as source, output.open("wb") as target:
                data = source.read(_MAX_SUBTITLE_BYTES + 1)
                if len(data) > _MAX_SUBTITLE_BYTES:
                    raise SubtitleError("Untertiteldatei im ZIP überschreitet das Größenlimit.")
                target.write(data)
            if not _usable(output):
                raise SubtitleError("Untertiteldatei im ZIP ist leer.")
            return output
    except SubtitleError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
        raise SubtitleError("Untertitel-ZIP kann nicht gelesen werden.") from exc


def _find_archive(master: Path, directory: Path, language: str) -> Path:
    suffixes = [language, _ISO2.get(language, "")] if language not in {"auto", "und"} else []
    suffixes.append("")
    for suffix in dict.fromkeys(suffixes):
        name = master.stem + ("." + suffix if suffix else "") + ".zip"
        archive = master.with_name(name)
        if archive.is_file():
            return _extract_archive(archive, master, language, directory)
    raise SubtitleError("Kein passendes ZIP neben dem Bildmaster gefunden.")


def _embedded_stream(info: MediaInfo, language: str) -> int | None:
    text_tracks = [(ordinal, stream) for ordinal, stream in enumerate(
        stream for stream in info.streams if stream.codec_type == "subtitle")
        if stream.codec_name in _TEXT_SUBTITLES]
    if language not in {"auto", "und"}:
        matching = [ordinal for ordinal, stream in text_tracks
                    if normalize_language_code(stream.language) == language]
        return matching[0] if matching else None
    return text_tracks[0][0] if text_tracks else None


def _convert_to_srt(ffmpeg: str, source: Path, destination: Path, *,
                    stream: int | None = None, cancel_event=None) -> None:
    command = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(source)]
    command += ["-map", f"0:s:{stream}" if stream is not None else "0:0",
                "-c:s", "srt", str(destination)]
    _run(command, timeout=120, cancel_event=cancel_event)
    if not _usable(destination):
        raise SubtitleError("Untertiteldatei fehlt oder ist leer/zu groß.")
    with destination.open("rb") as stream_file:
        sample = stream_file.read(16384)
    if not re.search(rb"\d\d:\d\d:\d\d[,\.]\d{3}\s*-->", sample):
        raise SubtitleError("Untertiteldatei enthält keine gültigen SRT-Zeitmarken.")


def _check_timing(path: Path, duration: float) -> None:
    """Reject grossly mismatched editions before attaching their captions."""
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    stamps = re.findall(r"(?m)^\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*"
                        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", content)
    if not stamps:
        raise SubtitleError("Keine Untertitel-Zeitmarken gefunden.")
    last = max(int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
               for h, m, s, ms in stamps)
    if last > duration + max(5.0, duration * 0.01):
        raise SubtitleError("Untertitel laufen deutlich länger als der Bildmaster; "
                            "wahrscheinlich eine andere Schnittfassung.")


def _download(master: Path, directory: Path, language: str, cancel_event=None) -> Path:
    executable = _find_program("subliminal")
    if not executable:
        raise SubtitleError("Online-Suche erfordert das optionale Programm 'subliminal'.")
    iso2 = _ISO2.get(language)
    if not iso2:
        raise SubtitleError("Für die Online-Suche eine unterstützte Untertitelsprache auswählen.")
    _run([executable, "download", "-l", iso2, "-d", str(directory),
          "--max-workers", "4", "--min-score", "65", "--force", str(master)],
         timeout=120, cancel_event=cancel_event)
    results = sorted(directory.glob("*.srt"))
    if not results:
        raise SubtitleError("Kein passender Online-Untertitel gefunden. Anderen Modus wählen.")
    return results[0]


class _HttpsHostRedirect(HTTPRedirectHandler):
    """Do not follow provider-supplied redirects to arbitrary hosts or HTTP."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        old, new = urlsplit(request.full_url), urlsplit(newurl)
        if new.scheme != "https" or new.hostname != old.hostname:
            raise SubtitleError("Untertitelanbieter leitet auf einen fremden Server um.")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _read_https(host: str, path: str, *, parameters: dict | None = None,
                limit: int = _MAX_ARCHIVE_BYTES, cancel_event=None) -> bytes:
    if cancel_event is not None and cancel_event.is_set():
        raise SubtitleError("Untertitelverarbeitung abgebrochen.")
    if not path.startswith("/") or path.startswith("//") or ".." in path.split("/") or "?" in path:
        raise SubtitleError("Ungültiger Downloadpfad vom Untertitelanbieter.")
    url = "https://" + host + path
    if parameters:
        url += "?" + urlencode(parameters)
    request = Request(url, headers={"User-Agent": "DubSync-Studio/3", "Accept": "application/json, */*"})
    try:
        with build_opener(_HttpsHostRedirect()).open(request, timeout=15) as response:
            payload = response.read(limit + 1)
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        # Never include the request URL: SubDL API keys travel in its query string.
        raise SubtitleError("Untertitelanbieter nicht erreichbar (" + type(exc).__name__ + ").") from None
    if len(payload) > limit:
        raise SubtitleError("Antwort des Untertitelanbieters überschreitet das Größenlimit.")
    if cancel_event is not None and cancel_event.is_set():
        raise SubtitleError("Untertitelverarbeitung abgebrochen.")
    return payload


def _subdl_candidates(master: Path, directory: Path, language: str,
                      cancel_event=None) -> Iterator[Path]:
    """Yield bounded alternative releases in preference order, fetching on demand."""
    key = os.environ.get("DUBSYNC_SUBDL_API_KEY", "").strip()
    if not key:
        raise SubtitleError("SubDL benötigt DUBSYNC_SUBDL_API_KEY in der Umgebung.")
    iso2 = _ISO2.get(language)
    if not iso2:
        raise SubtitleError("Für SubDL eine unterstützte Untertitelsprache auswählen.")
    parameters = {"api_key": key, "file_name": master.name,
                  "languages": iso2.upper(), "unpack": 1, "client": "custom_integration"}
    try:
        result = json.loads(_read_https("api.subdl.com", "/api/v1/subtitles",
                                        parameters=parameters, limit=512 * 1024,
                                        cancel_event=cancel_event))
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        raise SubtitleError("Ungültige Suchantwort von SubDL.") from exc
    if not isinstance(result, dict) or result.get("status") is not True:
        raise SubtitleError("SubDL-Suche fehlgeschlagen (API-Schlüssel und Kontingent prüfen).")
    episode = _episode_id(master.stem)
    subtitles = result.get("subtitles", [])
    if not isinstance(subtitles, list):
        raise SubtitleError("Ungültige SubDL-Ergebnisse.")
    ranked: list[tuple[int, int, dict]] = []
    master_terms = set(re.findall(r"[a-z0-9]{3,}", master.stem.casefold()))
    for entry in subtitles[:30]:
        if not isinstance(entry, dict):
            continue
        files = entry.get("unpack_files") or []
        if files:
            choices = [part for part in files if isinstance(part, dict)
                       and (episode is None or (part.get("season"), part.get("episode")) == episode)
                       and str(part.get("language", iso2)).lower() == iso2]
        elif not entry.get("full_season") and (
                episode is None or (entry.get("season"), entry.get("episode")) == episode):
            choices = [entry]
        else:
            continue
        for part in choices:
            relative = part.get("url")
            if not isinstance(relative, str) or not relative.startswith("/subtitle/"):
                continue
            extension = str(part.get("format", Path(str(part.get("name", ""))).suffix.lstrip("."))).lower()
            if relative.lower().endswith(".zip"):
                extension = "zip"
            if extension not in (*_FORMATS, "zip"):
                continue
            release = str(part.get("release_name") or entry.get("release_name") or "")
            overlap = len(master_terms & set(re.findall(r"[a-z0-9]{3,}", release.casefold())))
            ranked.append((overlap, len(ranked), {"url": relative, "extension": extension}))
    if not ranked:
        raise SubtitleError("SubDL hat keine zur Folge und Sprache passenden Untertitel gefunden.")
    attempts = 0
    for _score, _order, candidate in sorted(ranked, key=lambda item: (-item[0], item[1])):
        if attempts >= _MAX_PROVIDER_CANDIDATES:
            break
        attempts += 1
        try:
            payload = _read_https("dl.subdl.com", candidate["url"], cancel_event=cancel_event)
            extension = candidate["extension"]
            downloaded = directory / ("subdl." + extension)
            downloaded.write_bytes(payload)
            if extension == "zip":
                downloaded = _extract_archive(downloaded, master, language, directory)
            if _usable(downloaded):
                yield downloaded
        except SubtitleError as exc:
            if "abgebrochen" in str(exc):
                raise
    raise SubtitleError(f"SubDL: {attempts} Kandidat(en) geprüft, kein gültiger Untertitel.")


def _download_video_captions(url: str, directory: Path, language: str, cancel_event=None) -> Path:
    """Fetch only manually identified, human-created subtitles; never the video."""
    if urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
        raise SubtitleError("Eine gültige HTTPS-Adresse des Videos angeben.")
    executable = _find_program("yt-dlp")
    if not executable:
        raise SubtitleError("URL-Untertitel benötigen das optionale Programm 'yt-dlp'.")
    iso2 = _ISO2.get(language)
    if not iso2:
        raise SubtitleError("Für URL-Untertitel eine unterstützte Sprache auswählen.")
    try:
        _run([executable, "--ignore-config", "--no-playlist", "--skip-download",
              "--write-subs", "--no-write-auto-subs", "--sub-langs", iso2,
              "--sub-format", "srt/vtt/best", "--paths", str(directory),
              "-o", "caption.%(ext)s", url], timeout=120, cancel_event=cancel_event)
    except SubtitleError as exc:
        if "abgebrochen" in str(exc):
            raise
        # yt-dlp may include URL tokens in its diagnostic output.
        raise SubtitleError("yt-dlp konnte keine Untertitel abrufen; URL, Sprache und "
                            "Netzwerkverbindung prüfen.") from None
    for ext in _FORMATS:
        found = sorted(directory.glob("caption*." + ext))
        if found and _usable(found[0]):
            return found[0]
    raise SubtitleError("Keine von Menschen erstellten Untertitel an der Video-URL gefunden.")


def _generate(master: Path, info: MediaInfo, directory: Path, language: str,
              model: str, ffmpeg: str, cancel_event=None) -> Path:
    if not info.audio:
        raise SubtitleError("Für Spracherkennung fehlt eine Audiospur im Bildmaster.")
    track = next((index for index, stream in enumerate(info.audio)
                  if normalize_language_code(stream.language) == language), None)
    if language not in {"auto", "und"} and track is None:
        if any(normalize_language_code(stream.language) not in {"und", "auto"} for stream in info.audio):
            raise SubtitleError("Der Bildmaster enthält keine Audiospur in der gewählten Sprache. "
                                "Whisper übersetzt nicht automatisch in die Zielsprache.")
    native_model = Path(os.environ.get("DUBSYNC_WHISPER_MODEL", ""))
    native = _find_program("whisper-cli")
    use_native = bool(native and native_model.is_file())
    fast = _find_program("whisper-ctranslate2")
    whisper = _find_program("whisper")
    if not (fast or use_native or whisper):
        raise SubtitleError("Spracherkennung erfordert whisper-ctranslate2, "
                            "whisper-cli + DUBSYNC_WHISPER_MODEL oder 'whisper'.")
    if (fast or whisper) and model not in {"tiny", "base", "small", "medium", "large", "turbo"}:
        raise SubtitleError("Ungültige Whisper-Modellgröße.")
    ordinal = track if track is not None else 0
    wav = directory / "speech.wav"
    language_arg = _ISO2.get(language)
    srt = directory / "speech.srt"
    commands: list[list[str]] = []
    if fast:
        from .performance import available_memory_gib

        memory_gib = available_memory_gib()
        batch_size = (8 if memory_gib is not None and memory_gib >= 12 else
                      4 if memory_gib is not None and memory_gib >= 6 else
                      2 if memory_gib is not None and memory_gib >= 3 else 1)
        command = [fast, str(wav), "--model", model, "--task", "transcribe",
                   "--output_format", "srt", "--output_dir", str(directory),
                   "--compute_type", "int8", "--batched", "True",
                   "--batch_size", str(batch_size), "--verbose", "False"]
        if language_arg:
            command += ["--language", language_arg]
        commands.append(command)
    if use_native:
        command = [native, "-m", str(native_model), "-f", str(wav),
                   "-osrt", "-of", str(directory / "speech")]
        if language_arg:
            command += ["-l", language_arg]
        commands.append(command)
    if whisper:
        command = [whisper, str(wav), "--model", model, "--task", "transcribe",
                   "--output_format", "srt", "--output_dir", str(directory), "--verbose", "False"]
        if language_arg:
            command += ["--language", language_arg]
        commands.append(command)
    try:
        _run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(master),
              "-map", f"0:a:{ordinal}", "-vn", "-ac", "1", "-ar", "16000",
              "-c:a", "pcm_s16le", str(wav)], timeout=3600, cancel_event=cancel_event)
        last_error = None
        for command in commands:
            try:
                srt.unlink(missing_ok=True)
                _run(command, timeout=12 * 3600, cancel_event=cancel_event)
                if _usable(srt):
                    _check_timing(srt, info.duration)
                    return srt
                last_error = SubtitleError("Spracherkennung hat keine Untertitel erzeugt.")
            except SubtitleError as exc:
                if "abgebrochen" in str(exc):
                    raise
                last_error = exc
        raise SubtitleError("Alle installierten Spracherkennungs-Backends sind fehlgeschlagen: "
                            + str(last_error))
    finally:
        wav.unlink(missing_ok=True)


def prepare_subtitles(master: Path, info: MediaInfo, options: SubtitleOptions,
                      directory: Path, ffmpeg: str, cancel_event=None,
                      progress: Callable[[str], None] | None = None) -> Path:
    """Return a *local*, canonical SRT, timed only to the picture master."""
    if options.mode not in {"auto", "existing", "file", "sidecar", "archive", "embedded", "download",
                            "subdl", "url", "generate"}:
        raise SubtitleError("Unbekannter Untertitelmodus.")
    language = normalize_language_code(options.language)
    target = directory / "captions.srt"
    errors = []
    strategies = (["sidecar", "archive", "embedded", "subdl", "subliminal"]
                  + (["generate"] if options.mode == "auto" else [])
                  if options.mode in {"auto", "existing"} else
                  ["subdl", "subliminal"] if options.mode == "download" else [options.mode])
    for strategy in strategies:
        if cancel_event is not None and cancel_event.is_set():
            raise SubtitleError("Untertitelverarbeitung abgebrochen.")
        if progress is not None:
            label = {
                "sidecar": "Prüfe lokale Untertiteldateien …",
                "archive": "Prüfe Untertitelarchive …",
                "embedded": "Prüfe vorhandene MKV-Untertitel …",
                "subliminal": "Suche online über Subliminal …",
                "subdl": "Suche online über SubDL …",
                "generate": "Letzte Option: Spracherkennung (Whisper) …",
                "file": "Prüfe gewählte Untertiteldatei …",
                "url": "Lade Untertitel der angegebenen Video-URL …",
            }[strategy]
            progress(label)
        try:
            target.unlink(missing_ok=True)
            if strategy == "file":
                path = Path(options.file_path).expanduser().resolve()
                if path == master or not path.is_file():
                    raise SubtitleError("Eine gültige Untertiteldatei auswählen.")
                if path.suffix.casefold() == ".zip":
                    path = _extract_archive(path, master, language, directory)
                elif not _usable(path) or path.suffix.casefold()[1:] not in _FORMATS:
                    raise SubtitleError("Eine gültige SRT/ASS/VTT/ZIP-Datei auswählen.")
                _convert_to_srt(ffmpeg, path, target, cancel_event=cancel_event)
            elif strategy == "sidecar":
                path = _find_sidecar(master, language)
                if path is None:
                    raise SubtitleError("Keine passende Untertiteldatei neben dem Bildmaster.")
                _convert_to_srt(ffmpeg, path, target, cancel_event=cancel_event)
            elif strategy == "archive":
                path = _find_archive(master, directory, language)
                _convert_to_srt(ffmpeg, path, target, cancel_event=cancel_event)
            elif strategy == "embedded":
                ordinal = _embedded_stream(info, language)
                if ordinal is None:
                    raise SubtitleError("Keine passende eingebettete Text-Untertitelspur.")
                _convert_to_srt(ffmpeg, master, target, stream=ordinal, cancel_event=cancel_event)
            elif strategy == "subliminal":
                path = _download(master, directory, language, cancel_event=cancel_event)
                _convert_to_srt(ffmpeg, path, target, cancel_event=cancel_event)
            elif strategy == "subdl":
                rejected = 0
                for path in _subdl_candidates(master, directory, language, cancel_event=cancel_event):
                    target.unlink(missing_ok=True)
                    try:
                        _convert_to_srt(ffmpeg, path, target, cancel_event=cancel_event)
                        _check_timing(target, info.duration)
                        return target
                    except SubtitleError as exc:
                        if "abgebrochen" in str(exc):
                            raise
                        rejected += 1
                raise SubtitleError(f"SubDL: {rejected} konvertierte Kandidaten unbrauchbar.")
            elif strategy == "url":
                path = _download_video_captions(options.file_path, directory, language,
                                                cancel_event=cancel_event)
                _convert_to_srt(ffmpeg, path, target, cancel_event=cancel_event)
            else:
                path = _generate(master, info, directory, language,
                                 options.whisper_model, ffmpeg, cancel_event=cancel_event)
                _convert_to_srt(ffmpeg, path, target, cancel_event=cancel_event)
            _check_timing(target, info.duration)
            return target
        except SubtitleError as exc:
            errors.append(f"{strategy}: {exc}")
            if options.mode not in {"auto", "existing", "download"} or "abgebrochen" in str(exc):
                raise
    raise SubtitleError("Keine Untertitel verfügbar (" + "; ".join(errors) + ").")
