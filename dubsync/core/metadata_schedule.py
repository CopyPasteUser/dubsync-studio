"""Bounded per-episode metadata prefetch using the existing probe implementation."""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def prefetch_missing(paths: list[str], known: dict[str, T], *,
                     permit_parallel: Callable[[list[str]], bool],
                     probe_many: Callable[[list[str]], list[T]],
                     cancelled: Callable[[], bool]) -> None:
    """Populate only missing metadata; publish no partial result on failure.

    Paths must already be canonical. Sequential fallback remains the caller's
    responsibility. Only the current episode may be prefetched.
    """
    missing = list(dict.fromkeys(path for path in paths if path not in known))
    if len(missing) < 2 or cancelled() or not permit_parallel(missing):
        return
    results = probe_many(missing)
    if cancelled():
        return
    if len(results) != len(missing):
        raise ValueError("Incomplete episode metadata prefetch")
    known.update(zip(missing, results))
