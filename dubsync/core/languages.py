from __future__ import annotations

from pathlib import Path
from typing import Any

# Codes written into Matroska metadata are ISO 639-2/3 codes. UI labels are
# deliberately human-readable; the user may edit them per source folder.
LANGUAGE_CHOICES: tuple[tuple[str, str], ...] = (
    ("Automatisch erkennen", "auto"),
    ("Deutsch", "deu"),
    ("English", "eng"),
    ("Français", "fra"),
    ("Español", "spa"),
    ("Italiano", "ita"),
    ("日本語", "jpn"),
    ("한국어", "kor"),
    ("Русский", "rus"),
    ("中文", "zho"),
    ("Português", "por"),
    ("Polski", "pol"),
    ("Nederlands", "nld"),
    ("Türkçe", "tur"),
    ("Čeština", "ces"),
    ("العربية", "ara"),
    ("Svenska", "swe"),
    ("Dansk", "dan"),
    ("Suomi", "fin"),
    ("Norsk", "nor"),
    ("עברית", "heb"),
    ("Magyar", "hun"),
    ("Română", "ron"),
    ("Українська", "ukr"),
    ("Ελληνικά", "ell"),
)

_LANGUAGE_NAMES = {code: name for name, code in LANGUAGE_CHOICES if code != "auto"}
_ALIASES = {
    "de": "deu", "deu": "deu", "ger": "deu", "german": "deu", "deutsch": "deu",
    "en": "eng", "eng": "eng", "english": "eng", "en-us": "eng", "en-gb": "eng",
    "fr": "fra", "fra": "fra", "fre": "fra", "french": "fra", "français": "fra", "francais": "fra",
    "es": "spa", "spa": "spa", "spanish": "spa", "español": "spa", "espanol": "spa",
    "it": "ita", "ita": "ita", "italian": "ita", "italiano": "ita",
    "ja": "jpn", "jp": "jpn", "jpn": "jpn", "japanese": "jpn", "日本語": "jpn",
    "ko": "kor", "kor": "kor", "korean": "kor", "한국어": "kor",
    "ru": "rus", "rus": "rus", "russian": "rus", "русский": "rus",
    "zh": "zho", "chi": "zho", "zho": "zho", "chinese": "zho", "中文": "zho",
    "pt": "por", "por": "por", "portuguese": "por", "português": "por", "portugues": "por",
    "pl": "pol", "pol": "pol", "polish": "pol", "polski": "pol",
    "nl": "nld", "dut": "nld", "nld": "nld", "dutch": "nld", "nederlands": "nld",
    "tr": "tur", "tur": "tur", "turkish": "tur", "türkçe": "tur", "turkce": "tur",
    "cs": "ces", "cze": "ces", "ces": "ces", "czech": "ces", "čeština": "ces", "cestina": "ces",
    "ar": "ara", "ara": "ara", "arabic": "ara", "العربية": "ara",
    "sv": "swe", "swe": "swe", "swedish": "swe", "svenska": "swe",
    "da": "dan", "dan": "dan", "danish": "dan", "dansk": "dan",
    "fi": "fin", "fin": "fin", "finnish": "fin", "suomi": "fin",
    "no": "nor", "nor": "nor", "nob": "nor", "nno": "nor", "norwegian": "nor", "norsk": "nor",
    "he": "heb", "heb": "heb", "hebrew": "heb", "עברית": "heb",
    "hu": "hun", "hun": "hun", "hungarian": "hun", "magyar": "hun",
    "ro": "ron", "rum": "ron", "ron": "ron", "romanian": "ron", "română": "ron", "romana": "ron",
    "uk": "ukr", "ukr": "ukr", "ukrainian": "ukr", "українська": "ukr",
    "el": "ell", "gre": "ell", "ell": "ell", "greek": "ell", "ελληνικά": "ell",
}


def normalize_language_code(value: str | None) -> str:
    token = (value or "und").strip().casefold().replace("_", "-")
    if token == "auto":
        return "auto"
    if token in _ALIASES:
        return _ALIASES[token]
    base = token.split("-", 1)[0]
    if base in _ALIASES:
        return _ALIASES[base]
    return token if len(token) == 3 and token.isalpha() else "und"


def language_display_name(value: str | None) -> str:
    code = normalize_language_code(value)
    return _LANGUAGE_NAMES.get(code, "")


def default_track_title(language: str | None, stream_title: str = "", path: str | None = None) -> str:
    known = language_display_name(language)
    if known:
        return known
    if stream_title.strip():
        return stream_title.strip()
    return Path(path).stem if path else "Audio"


def _best_root_rule(path: str, roots: list[str], root_languages: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    if not root_languages:
        return {}
    file_path = Path(path).expanduser().resolve()
    matches: list[tuple[int, dict[str, Any]]] = []
    for root, rule in root_languages.items():
        if not isinstance(root, str) or not isinstance(rule, dict):
            continue
        try:
            base = Path(root).expanduser().resolve()
            if file_path == base or base in file_path.parents:
                matches.append((len(str(base)), rule))
        except (OSError, RuntimeError, TypeError):
            continue
    return max(matches, key=lambda item: item[0])[1] if matches else {}


def resolve_track_label(
    path: str,
    stream_language: str = "und",
    stream_title: str = "",
    *,
    roots: list[str] | None = None,
    root_languages: dict[str, dict[str, Any]] | None = None,
    inferred_language: str = "und",
) -> tuple[str, str]:
    """Resolve a mux language code/title, preferring explicit source-folder rules."""
    rule = _best_root_rule(path, roots or [], root_languages)
    configured = normalize_language_code(str(rule.get("language", "auto")))
    if configured != "auto" and configured != "und":
        language = configured
    else:
        language = normalize_language_code(stream_language)
        if language == "und":
            language = normalize_language_code(inferred_language)

    title = str(rule.get("title", "") or "").strip()
    if not title:
        title = default_track_title(language, stream_title, path)
    return language, title
