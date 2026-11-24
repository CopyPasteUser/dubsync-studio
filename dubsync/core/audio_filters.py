"""Small, explicit filter graph optimizations for aligned audio."""
from __future__ import annotations


def delay_filter(delay_ms: int) -> str:
    """Apply integer delay and use a continuous sample clock after inserted silence."""
    if delay_ms < 0:
        raise ValueError("Negative audio delay")
    return f"adelay={delay_ms}:all=1,asetpts=N/SR/TB," if delay_ms else ""
