"""Explicit boundary between preferences and disposable GUI session data.

DubSync always starts with an empty library. Persisting paths, saved sync
adjustments or episode assignments would silently reuse old analysis decisions.
Manually saved .dubsync projects remain available through File > Open.
"""
from __future__ import annotations

from typing import Any, Mapping

# Only actual user preferences survive application restarts. Do not add a
# project field here without making the fresh-start behavior explicit in UI.
PREFERENCE_KEYS = frozenset({
    "output_directory",
    "organize_output",
    "performance",
    "high_contrast",
})


def persistent_preferences(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    """Drop old session data without modifying the caller's object."""
    if not isinstance(settings, Mapping):
        return {}
    return {key: value for key, value in settings.items() if key in PREFERENCE_KEYS}
