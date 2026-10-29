"""Durable, explicit batch job queue built on the existing safe batch engine.

Payloads are private local user data (source paths and export settings). Each
run still performs fresh episode/visual safety checks; the only resumed state
from a previous run is its journal's *verified exported outputs*.
"""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .batch import batch_export_groups
from .models import EpisodeGroup


class QueueError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_queue_path() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return root / "dubsync-studio" / "batch-queue.sqlite3"


def _bounded_json(value: Any) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise QueueError("Der Stapelauftrag enthält ungültige Einstellungen.") from exc
    if len(payload.encode("utf-8")) > 4_000_000:
        raise QueueError("Zu viele Mediendateien für einen einzelnen Stapelauftrag.")
    return payload


def _groups_from_spec(spec: dict) -> list[EpisodeGroup]:
    groups = spec.get("groups")
    if not isinstance(groups, list) or not 1 <= len(groups) <= 2000:
        raise QueueError("Stapelauftrag enthält keine gültigen Episoden.")
    loaded = []
    for row in groups:
        if not isinstance(row, dict) or not isinstance(row.get("files"), list):
            raise QueueError("Beschädigte Episoden in der Warteschlange.")
        try:
            group = EpisodeGroup(**row)
        except (TypeError, ValueError) as exc:
            raise QueueError("Beschädigte Episodenzuordnung in der Warteschlange.") from exc
        if (not isinstance(group.key, str) or not isinstance(group.display_name, str)
                or not 1 <= len(group.files) <= 32
                or any(not isinstance(path, str) or not path for path in group.files)):
            raise QueueError("Ungültige Quelldateien in der Warteschlange.")
        loaded.append(group)
    return loaded


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name != "posix":
        # Windows process owners are not authoritatively inspected here.
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, OverflowError):
        return False


def adaptive_resource_options(options: dict, *, specs: dict | None = None,
                              load_average: float | None = None,
                              available_ram_gib: float | None = None) -> tuple[dict, str]:
    """Conservatively cap decoder concurrency, never lower matching quality.

    This is sampled once per fresh job and persisted: a recovered job retains
    the same complete export recipe required by its prior journal.
    """
    from .performance import system_specs
    hardware = specs if specs is not None else system_specs()
    logical = max(1, int(hardware.get("logical_cpus", 1)))
    ram = float(hardware.get("ram_gib", 4))
    requested = max(1, min(2, int(options.get("sampling_workers", 1))))
    if load_average is None:
        try:
            load_average = os.getloadavg()[0]
        except (AttributeError, OSError):
            load_average = 0.0
    if available_ram_gib is None and os.name == "posix":
        try:
            for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
                if line.startswith("MemAvailable:"):
                    available_ram_gib = int(line.split()[1]) / (1024**2)
                    break
        except (OSError, ValueError, IndexError):
            pass
    available = ram if available_ram_gib is None else min(ram, available_ram_gib)
    settings = dict(options)
    if requested == 1:
        reason = "Profil: ein Decoder"
    elif logical < 4 or available < 4:
        reason = "CPU-/RAM-Reserve: auf einen Decoder begrenzt"
    elif float(load_average) >= logical * .85:
        reason = "Hohe laufende CPU-Last: auf einen Decoder begrenzt"
    else:
        reason = "Zwei Decoder angefordert; NAS-/GPU-Regeln greifen zusätzlich pro Episode"
    settings["sampling_workers"] = 2 if reason.startswith("Zwei Decoder") else 1
    return settings, reason


class BatchJobQueue:
    """SQLite queue, fail-closed recovery and stable assignment snapshots."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else default_queue_path()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(self.path.parent, 0o700)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS batch_jobs (
                id TEXT PRIMARY KEY, label TEXT NOT NULL, state TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                spec_json TEXT NOT NULL, effective_options_json TEXT,
                journal_path TEXT, progress_current INTEGER NOT NULL DEFAULT 0,
                progress_total INTEGER NOT NULL DEFAULT 0,
                progress_message TEXT NOT NULL DEFAULT '',
                resource_reason TEXT NOT NULL DEFAULT '',
                result_json TEXT, error_text TEXT NOT NULL DEFAULT '',
                owner_pid INTEGER NOT NULL DEFAULT 0
            )""")
            fields = {row[1] for row in db.execute("PRAGMA table_info(batch_jobs)")}
            if "owner_pid" not in fields:
                db.execute("ALTER TABLE batch_jobs ADD COLUMN owner_pid INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE INDEX IF NOT EXISTS batch_jobs_order ON batch_jobs(created_at DESC)")
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, groups: list[EpisodeGroup], output_directory: str, *,
                label: str = "Serien-Stapel", options: dict | None = None,
                initial_resume_journal: str | None = None) -> str:
        spec = {"groups": [asdict(group) for group in groups],
                "output_directory": str(Path(output_directory).expanduser().resolve()),
                "options": dict(options or {}),
                "initial_resume_journal": str(initial_resume_journal) if initial_resume_journal else None}
        _groups_from_spec(spec)
        if not output_directory or not isinstance(spec["options"], dict):
            raise QueueError("Ausgabeordner oder Optionen fehlen.")
        serialized = _bounded_json(spec)
        job_id = uuid.uuid4().hex
        with self._db() as db:
            db.execute("""INSERT INTO batch_jobs
                (id,label,state,created_at,updated_at,spec_json)
                VALUES (?,?,?,?,?,?)""",
                (job_id, label[:200], "queued", _now(), _now(), serialized))
        return job_id

    def get(self, job_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM batch_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise QueueError("Stapelauftrag wurde nicht gefunden.")
        item = dict(row)
        try:
            item["spec"] = json.loads(item.pop("spec_json"))
            item["effective_options"] = (json.loads(item.pop("effective_options_json"))
                                         if item["effective_options_json"] else None)
            item["result"] = (json.loads(item.pop("result_json"))
                              if item["result_json"] else None)
        except (ValueError, TypeError) as exc:
            raise QueueError("Stapelauftrag ist beschädigt; keine automatische Ausführung.") from exc
        return item

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("""SELECT id,label,state,created_at,updated_at,journal_path,
                progress_current,progress_total,progress_message,resource_reason,error_text
                FROM batch_jobs ORDER BY created_at DESC,rowid DESC LIMIT ?""",
                (max(1, min(200, int(limit))),)).fetchall()
        return [dict(row) for row in rows]

    def recover_interrupted(self) -> int:
        with self._db() as db:
            active = db.execute("SELECT id,owner_pid FROM batch_jobs WHERE state IN ('running','paused')").fetchall()
        abandoned = [(row["id"], int(row["owner_pid"])) for row in active
                     if not _process_alive(int(row["owner_pid"]))]
        if not abandoned:
            return 0
        with self._db() as db:
            count = 0
            for job_id, old_pid in abandoned:
                cursor = db.execute("""UPDATE batch_jobs SET state='interrupted',updated_at=?,owner_pid=0,
                    progress_message='Anwendung beendet; Wiederaufnahme ausdrücklich starten'
                    WHERE id=? AND owner_pid=? AND state IN ('running','paused')""",
                    (_now(), job_id, old_pid))
                count += cursor.rowcount
        return count

    def pause(self, job_id: str, paused: bool) -> None:
        with self._db() as db:
            db.execute("UPDATE batch_jobs SET state=?,updated_at=? WHERE id=? AND state=?",
                       ("paused" if paused else "running", _now(), job_id,
                        "running" if paused else "paused"))

    def _claim(self, job_id: str) -> dict:
        with self._db() as db:
            cursor = db.execute("""UPDATE batch_jobs SET state='running',updated_at=?,error_text='',owner_pid=?
                WHERE id=? AND state IN ('queued','interrupted','cancelled','failed','needs_review')""",
                (_now(), os.getpid(), job_id))
            if cursor.rowcount != 1:
                raise QueueError("Auftrag ist bereits aktiv, abgeschlossen oder nicht vorhanden.")
        return self.get(job_id)

    def _update(self, job_id: str, **updates) -> None:
        if not updates:
            return
        allowed = {"state", "journal_path", "effective_options_json", "progress_current",
                   "progress_total", "progress_message", "resource_reason", "result_json",
                   "error_text", "owner_pid"}
        if any(key not in allowed for key in updates):
            raise ValueError("Unbekanntes Warteschlangenfeld")
        updates["updated_at"] = _now()
        fields = ",".join(f"{field}=?" for field in updates)
        with self._db() as db:
            db.execute(f"UPDATE batch_jobs SET {fields} WHERE id=?", (*updates.values(), job_id))

    def run(self, job_id: str, *, progress: Callable[[int, int, str], None] | None = None,
            cancel_event=None, cache=None) -> dict[str, Any]:
        job = self._claim(job_id)
        spec = job["spec"]
        try:
            groups = _groups_from_spec(spec)
            options = dict(spec["options"])
            requested_settings = dict(options.pop("alignment_options", {}))
            if job["effective_options"] is not None:
                effective_settings = job["effective_options"]
            elif spec.get("initial_resume_journal"):
                # A manually chosen journal already commits to an exact recipe.
                # Changing decoder settings at this point makes it incompatible.
                effective_settings = requested_settings
                self._update(job_id, effective_options_json=_bounded_json(effective_settings),
                             resource_reason="Manuelles Protokoll: ursprüngliches Leistungsprofil beibehalten")
            else:
                effective_settings, reason = adaptive_resource_options(requested_settings)
                self._update(job_id, effective_options_json=_bounded_json(effective_settings),
                             resource_reason=reason)
            prior_report = job["journal_path"] or spec.get("initial_resume_journal")
            resume = bool(job["journal_path"])
            # Recovery is intentionally more restrictive than an initial run:
            # a previous confirmation to overwrite never transfers to restart.
            if resume:
                options["overwrite_existing"] = False
                options["skip_existing"] = True
            options["alignment_options"] = effective_settings
            last_progress = [0.0, -1]

            def update_progress(index: int, total: int, message: str) -> None:
                if progress is not None:
                    progress(index, total, message)
                now = time.monotonic()
                if index != last_progress[1] or now - last_progress[0] >= 2.0:
                    self._update(job_id, progress_current=index, progress_total=total,
                                 progress_message=str(message)[:350])
                    last_progress[:] = [now, index]

            def record_journal(path: str) -> None:
                self._update(job_id, journal_path=str(path))

            report = batch_export_groups(
                groups, spec["output_directory"], cache=cache, progress=update_progress,
                cancel_event=cancel_event, resume_from_journal=prior_report,
                journal_created=record_journal, **options,
            )
            unverified_skips = [reason for reason in report.get("skipped", [])
                                if "verifiziert" not in str(reason).casefold()]
            state = ("cancelled" if report.get("cancelled") or
                     (cancel_event is not None and cancel_event.is_set()) else
                     "needs_review" if report.get("failed") or unverified_skips else
                     "completed")
            self._update(job_id, state=state, owner_pid=0, result_json=_bounded_json(report),
                         progress_message=f"{len(report.get('exported', []))} exportiert, "
                                          f"{len(report.get('skipped', []))} übersprungen, "
                                          f"{len(report.get('failed', []))} fehlgeschlagen")
            return report
        except Exception as exc:
            self._update(job_id, state="interrupted" if cancel_event is not None and cancel_event.is_set()
                         else "failed", owner_pid=0,
                         error_text=f"{type(exc).__name__}: {exc}"[:1500])
            raise

    def retry_problematic(self, job_id: str) -> str:
        """Create a fresh queued subset, never modify/destroy an old job."""
        previous = self.get(job_id)
        if previous["state"] in {"running", "paused", "queued"}:
            raise QueueError("Nur abgeschlossene oder unterbrochene Aufträge erneut einplanen.")
        report_path = previous["journal_path"]
        if not report_path or not Path(report_path).is_file():
            raise QueueError("Kein Stapelprotokoll für die Episodenauswahl vorhanden.")
        if Path(report_path).stat().st_size > 8 * 1024 * 1024:
            raise QueueError("Stapelprotokoll ist für einen sicheren Wiederholungsauftrag zu groß.")
        try:
            with open(report_path, "r", encoding="utf-8") as file:
                episodes = json.load(file)["episodes"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise QueueError("Stapelprotokoll konnte nicht geladen werden.") from exc
        groups = _groups_from_spec(previous["spec"])
        if not isinstance(episodes, list) or len(episodes) != len(groups):
            raise QueueError("Episodenanzahl im Protokoll passt nicht zum Auftrag.")
        for index, (episode, group) in enumerate(zip(episodes, groups), 1):
            if not isinstance(episode, dict) or episode.get("position") != index:
                raise QueueError("Episodenreihenfolge im Protokoll ist nicht gültig.")
            expected = [{"filename": Path(path).name,
                         "path_id": hashlib.sha256(os.path.abspath(path).encode(
                             "utf-8", "replace")).hexdigest()[:16]}
                        for path in group.files]
            if episode.get("files") != expected:
                raise QueueError("Quellzuordnung im Protokoll passt nicht zum Auftrag.")
        selected = [groups[i] for i, episode in enumerate(episodes[:len(groups)])
                    if isinstance(episode, dict) and episode.get("status") in
                    {"failed", "cancelled", "pending", "skipped"}
                    and "verifiziert; sicher fortgesetzt" not in str(episode.get("reason") or "")]
        if not selected:
            raise QueueError("Keine fehlgeschlagenen oder unbearbeiteten Episoden gefunden.")
        options = dict(previous["spec"]["options"])
        options["skip_existing"] = True
        options["overwrite_existing"] = False
        return self.enqueue(selected, previous["spec"]["output_directory"],
                            label=f"Erneut: {previous['label']}", options=options)
