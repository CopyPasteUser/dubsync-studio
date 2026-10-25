from __future__ import annotations

import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from .models import EpisodeGroup
from .probe import VIDEO_EXTENSIONS

_LANGUAGE_WORDS = {
    "de", "deu", "ger", "german", "deutsch", "en", "eng", "english",
    "fr", "fra", "fre", "french", "franzosisch", "französisch", "es", "spa",
    "spanish", "it", "ita", "italian", "jp", "jpn", "japanese", "jap",
    "multi", "dual", "dub", "dubbed", "sub", "subbed", "original", "ov",
    "uk", "us", "ita", "kor", "korean", "rus", "russian", "und",
    "portuguese", "português", "portugues", "polish", "polski", "dutch", "nederlands",
    "turkish", "türkçe", "turkce", "czech", "čeština", "cestina", "arabic", "العربية",
    "swedish", "svenska", "danish", "dansk", "finnish", "suomi", "norwegian", "norsk",
    "hebrew", "עברית", "hungarian", "magyar", "romanian", "română", "romana",
    "ukrainian", "українська", "greek", "ελληνικά",
}
_TECH_WORDS = {
    "2160p", "1440p", "1080p", "720p", "576p", "480p", "4k", "uhd", "hdr",
    "web", "webdl", "web-dl", "webrip", "bluray", "blu-ray", "brrip", "hdtv",
    "x264", "x265", "h264", "h265", "hevc", "av1", "aac", "ac3", "eac3",
    "dts", "truehd", "atmos", "proper", "repack", "remux", "10bit", "8bit",
}
_EPISODE_RE = re.compile(r"(?i)S(?P<season>\d{1,2})[ ._\-]*E(?P<episode>\d{1,3})(?!\d)")
_ALT_EPISODE_RE = re.compile(r"(?i)(?:^|[ ._\-])(?P<season>\d{1,2})x(?P<episode>\d{1,3})(?:\b|$)")
_SEASON_DIR_RE = re.compile(r"(?i)^(?:season|staffel|series|disc|disk|cd|dvd)\s*[-._ ]?\d*$")


def _human_series_name(value: str) -> str:
    """Keep word boundaries for readable output folder names."""
    value = re.sub(r"[._\-]+", " ", value)
    value = re.sub(r"[\[\](){}]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" ._-")
    words = [word for word in value.split() if word.casefold() not in _LANGUAGE_WORDS
             and word.casefold() not in _TECH_WORDS]
    return " ".join(words).title()


def episode_metadata(path: str | os.PathLike[str]) -> tuple[str, int | None, int | None]:
    """Return a display-friendly series title and season/episode numbers."""
    file_path = Path(path)
    match = _EPISODE_RE.search(file_path.stem) or _ALT_EPISODE_RE.search(file_path.stem)
    if not match:
        return "", None, None
    name = _human_series_name(file_path.stem[:match.start()])
    if not name:
        for parent in file_path.parents:
            candidate_raw = parent.name.strip()
            if not candidate_raw or _SEASON_DIR_RE.fullmatch(candidate_raw):
                continue
            if candidate_raw.casefold() in _LANGUAGE_WORDS:
                continue
            if re.fullmatch(r"(?i)s\d{1,2}", candidate_raw):
                continue
            candidate = _human_series_name(candidate_raw)
            if candidate:
                name = candidate
                break
    return name, int(match.group("season")), int(match.group("episode"))


def _name_quality(value: str) -> tuple[int, int, int]:
    words = value.split()
    return (len(words), sum(1 for c in value if c.isupper()), len(value))


def _tokens(value: str) -> list[str]:
    return [token for token in re.split(r"[\s._\-\[\](){}]+", value.lower()) if token]


def _clean_series_prefix(value: str) -> str:
    tokens = _tokens(value)
    cleaned: list[str] = []
    for token in tokens:
        if token in _LANGUAGE_WORDS or token in _TECH_WORDS:
            continue
        if re.fullmatch(r"(?:proper|repack|part\d+|cd\d+|disc\d+)", token):
            continue
        cleaned.append(token)
    return " ".join(cleaned).strip()


def _series_key(value: str) -> str:
    """Canonicalize series names so 'Southpark' and 'South Park' can match."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_text)


def infer_language(path: str) -> str:
    parts = [Path(path).stem, *Path(path).parent.parts[-3:]]
    for part in parts:
        tokens = set(_tokens(part))
        if tokens & {"de", "deu", "ger", "german", "deutsch"}:
            return "deu"
        if tokens & {"en", "eng", "english", "england"}:
            return "eng"
        if tokens & {"fr", "fra", "fre", "french", "franzosisch", "französisch"}:
            return "fra"
        if tokens & {"es", "spa", "spanish"}:
            return "spa"
        if tokens & {"it", "ita", "italian"}:
            return "ita"
        if tokens & {"ja", "jp", "jpn", "japanese", "jap", "日本語"}:
            return "jpn"
        if tokens & {"ko", "kor", "korean", "한국어"}:
            return "kor"
        if tokens & {"ru", "rus", "russian", "русский"}:
            return "rus"
        if tokens & {"zh", "chi", "zho", "chinese", "中文"}:
            return "zho"
        if tokens & {"pt", "por", "portuguese", "português", "portugues"}:
            return "por"
        if tokens & {"pl", "pol", "polish", "polski"}:
            return "pol"
        if tokens & {"nl", "dut", "nld", "dutch", "nederlands"}:
            return "nld"
        if tokens & {"tr", "tur", "turkish", "türkçe", "turkce"}:
            return "tur"
        if tokens & {"cs", "cze", "ces", "czech", "čeština", "cestina"}:
            return "ces"
        if tokens & {"ar", "ara", "arabic", "العربية"}:
            return "ara"
        if tokens & {"sv", "swe", "swedish", "svenska"}:
            return "swe"
        if tokens & {"da", "dan", "danish", "dansk"}:
            return "dan"
        if tokens & {"fi", "fin", "finnish", "suomi"}:
            return "fin"
        if tokens & {"no", "nor", "nob", "nno", "norwegian", "norsk"}:
            return "nor"
        if tokens & {"he", "heb", "hebrew", "עברית"}:
            return "heb"
        if tokens & {"hu", "hun", "hungarian", "magyar"}:
            return "hun"
        if tokens & {"ro", "rum", "ron", "romanian", "română", "romana"}:
            return "ron"
        if tokens & {"uk", "ukr", "ukrainian", "українська"}:
            return "ukr"
        if tokens & {"el", "gre", "ell", "greek", "ελληνικά"}:
            return "ell"
    return "und"


def episode_key(path: str | os.PathLike[str]) -> tuple[str, str]:
    file_path = Path(path)
    stem = file_path.stem
    match = _EPISODE_RE.search(stem) or _ALT_EPISODE_RE.search(stem)
    if match:
        prefix = _clean_series_prefix(stem[:match.start()])
        season = int(match.group("season"))
        episode = int(match.group("episode"))
        if not prefix:
            # Files named only S01E01 are often kept in language subfolders.
            parents = [p for p in file_path.parents if p.name]
            for parent in parents:
                parent_name = parent.name.strip()
                if re.fullmatch(r"(?i)(?:season|staffel|series|disc|disk|cd|dvd)\s*[-._ ]?\d*", parent_name):
                    continue
                if re.fullmatch(r"(?i)s\d{1,2}", parent_name):
                    continue
                candidate = _clean_series_prefix(parent_name)
                if candidate and candidate not in _LANGUAGE_WORDS and candidate not in _TECH_WORDS:
                    prefix = candidate
                    break
        series_id = _series_key(prefix) or "series"
        key = f"{series_id}|s{season:02d}e{episode:03d}"
        display = f"{prefix.title() + ' · ' if prefix else ''}S{season:02d}E{episode:02d}"
        return key, display

    clean = _clean_series_prefix(stem)
    if not clean:
        clean = stem.lower()
    # If no recognizable episode number exists, use the cleaned title.
    return f"title|{clean}", clean.title()


def scan_directories(roots: list[str], progress=None, cancel_event=None) -> list[EpisodeGroup]:
    grouped: dict[str, dict[str, object]] = defaultdict(lambda: {
        "display": "", "files": [], "series_name": "",
        "season_number": None, "episode_number": None,
    })
    seen: set[str] = set()
    candidates: list[Path] = []
    for root in roots:
        base = Path(root).expanduser().resolve()
        if not base.is_dir():
            continue
        for current, dirs, files in os.walk(base):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Bibliotheksscan abgebrochen.")
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {".dubsync", "_dubsync_tmp", "DubSync_Output"}]
            for name in files:
                if name.startswith("."):
                    continue
                path = Path(current) / name
                stem_lower = path.stem.lower()
                if (".dubsync-" in stem_lower or stem_lower.endswith(".multilingual")
                        or stem_lower.endswith("_multilingual") or stem_lower.endswith("-multilingual")):
                    continue
                if path.suffix.lower() in VIDEO_EXTENSIONS:
                    resolved = str(path.resolve())
                    if resolved not in seen:
                        seen.add(resolved)
                        candidates.append(path)
    total = len(candidates)
    for index, path in enumerate(candidates, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Bibliotheksscan abgebrochen.")
        key, display = episode_key(path)
        name, season, episode = episode_metadata(path)
        bucket = grouped[key]
        current_name = str(bucket.get("series_name") or "")
        if name and (not current_name or _name_quality(name) > _name_quality(current_name)):
            bucket["series_name"] = name
            if season is not None and episode is not None:
                bucket["display"] = f"{name} · S{season:02d}E{episode:02d}"
            else:
                bucket["display"] = display
        elif not bucket.get("display"):
            bucket["display"] = display
        if season is not None:
            bucket["season_number"] = season
        if episode is not None:
            bucket["episode_number"] = episode
        bucket["files"].append(str(path.resolve()))  # type: ignore[union-attr]
        if progress and (index % 30 == 0 or index == total):
            progress(index, total, path.name)
    result = [
        EpisodeGroup(
            key, str(data["display"]), sorted(data["files"]),
            series_name=str(data.get("series_name") or ""),
            season_number=data.get("season_number"),
            episode_number=data.get("episode_number"),
        )
        for key, data in grouped.items()
    ]
    result.sort(key=lambda group: (group.key, group.display_name.casefold()))
    return result
