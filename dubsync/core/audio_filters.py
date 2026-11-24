"""Small, explicit filter graph optimizations for aligned audio."""
from __future__ import annotations


def delay_filter(delay_ms: int) -> str:
    """An exact zero delay needs no filter; positive delays remain unchanged."""
    if delay_ms < 0:
        raise ValueError("Negative audio delay")
    return f"adelay={delay_ms}:all=1," if delay_ms else ""
