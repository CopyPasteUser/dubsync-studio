"""Compact, schema-preserving serialization of batch journal snapshots."""
from __future__ import annotations

import json


def snapshot_json(payload: dict) -> str:
    """Serialize once; retain every field and reject non-finite numbers."""
    return json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")) + "\n"
