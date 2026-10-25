"""Small, durable SQLite job journal for production troubleshooting and recovery."""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_history_path() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return base / "dubsync-studio" / "jobs.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any, max_chars: int = 256_000) -> str:
    """Encode bounded, always-valid JSON for the SQLite journal.

    Never slice serialized JSON directly: that can leave the database with an
    invalid JSON fragment and break the entire history dialog when one error
    report or list of source paths is unusually large.
    """
    max_chars = max(32, int(max_chars))
    try:
        result = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        result = json.dumps({"note": "Details konnten nicht serialisiert werden."}, ensure_ascii=False)
    if len(result) <= max_chars:
        return result

    marker = "… (gekürzt)"
    if isinstance(value, list):
        kept: list[Any] = []
        for item in value:
            candidate = json.dumps(kept + [item, marker], ensure_ascii=False,
                                   separators=(",", ":"), default=str)
            if len(candidate) > max_chars:
                break
            kept.append(item)
        candidate = json.dumps(kept + [marker], ensure_ascii=False,
                               separators=(",", ":"), default=str)
        return candidate if len(candidate) <= max_chars else "[]"

    if isinstance(value, dict):
        kept_dict: dict[str, Any] = {}
        for key, item in value.items():
            candidate_dict = {**kept_dict, str(key): item, "_truncated": True}
            candidate = json.dumps(candidate_dict, ensure_ascii=False,
                                   separators=(",", ":"), default=str)
            if len(candidate) > max_chars:
                break
            kept_dict[str(key)] = item
        kept_dict["_truncated"] = True
        encoded = json.dumps(kept_dict, ensure_ascii=False,
                             separators=(",", ":"), default=str)
        while len(encoded) > max_chars and kept_dict:
            removable = next((key for key in reversed(list(kept_dict)) if key != "_truncated"), None)
            if removable is None:
                break
            kept_dict.pop(removable, None)
            encoded = json.dumps(kept_dict, ensure_ascii=False,
                                 separators=(",", ":"), default=str)
        return encoded if len(encoded) <= max_chars else '{"_truncated":true}'

    # For oversized scalar values, include a safe preview of the encoded text.
    low, high = 0, min(len(result), max_chars)
    best = '{"_truncated":true,"preview":""}'
    while low <= high:
        mid = (low + high) // 2
        candidate = json.dumps({"_truncated": True, "preview": result[:mid]},
                               ensure_ascii=False, separators=(",", ":"))
        if len(candidate) <= max_chars:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


class JobHistory:
    def __init__(self, path: Path | None = None, max_entries: int = 500):
        self.path = path or default_history_path()
        self.max_entries = max(50, min(5000, int(max_entries)))
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _db(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                source_roots_json TEXT NOT NULL DEFAULT '[]',
                output_directory TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}',
                error_text TEXT NOT NULL DEFAULT ''
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_started_idx ON jobs(started_at DESC)")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def recover_interrupted(self) -> int:
        with self._db() as db:
            cursor = db.execute(
                "UPDATE jobs SET status='unterbrochen', finished_at=?, "
                "summary=CASE WHEN summary='' THEN 'Anwendung wurde vor Abschluss beendet.' ELSE summary END "
                "WHERE status='läuft'", (_now(),)
            )
            return max(0, cursor.rowcount)

    def start(self, kind: str, label: str, roots: list[str] | None = None,
              output_directory: str = "") -> str:
        job_id = uuid.uuid4().hex
        with self._db() as db:
            db.execute(
                "INSERT INTO jobs(id,kind,label,status,started_at,source_roots_json,output_directory) "
                "VALUES(?,?,?,?,?,?,?)",
                (job_id, str(kind)[:40], str(label)[:500], "läuft", _now(),
                 _json(roots or [], 100_000), str(output_directory)[:8192]),
            )
            db.execute(
                "DELETE FROM jobs WHERE id IN (SELECT id FROM jobs ORDER BY started_at DESC, rowid DESC LIMIT -1 OFFSET ?)",
                (self.max_entries,),
            )
        return job_id

    def finish(self, job_id: str, status: str, summary: str, *, details: Any = None,
               error_text: str = "") -> None:
        allowed = {"erfolgreich", "mit Warnungen", "fehlgeschlagen", "abgebrochen", "übersprungen", "unterbrochen"}
        status = status if status in allowed else "mit Warnungen"
        with self._db() as db:
            db.execute(
                "UPDATE jobs SET status=?,finished_at=?,summary=?,details_json=?,error_text=? WHERE id=?",
                (status, _now(), str(summary)[:2000], _json(details), str(error_text)[:100_000], job_id),
            )

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(1000, int(limit)))
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM jobs ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            try:
                item["source_roots"] = json.loads(item.pop("source_roots_json"))
            except (ValueError, TypeError):
                item["source_roots"] = []
            try:
                item["details"] = json.loads(item.pop("details_json"))
            except (ValueError, TypeError):
                item["details"] = {}
            results.append(item)
        return results

    def clear_finished(self) -> int:
        with self._db() as db:
            cursor = db.execute("DELETE FROM jobs WHERE status != 'läuft'")
            return max(0, cursor.rowcount)
