"""Rotating per-user log files and exception hooks for support diagnostics."""
from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path


def default_log_path() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return root / "dubsync-studio" / "logs" / "dubsync.log"


def configure_logging(path: Path | None = None) -> logging.Logger:
    logger = logging.getLogger("dubsync")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if any(getattr(handler, "_dubsync_handler", False) for handler in logger.handlers):
        return logger
    target = path or default_log_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(target.parent, 0o700)
        except OSError:
            pass
        handler = RotatingFileHandler(target, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        handler._dubsync_handler = True  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
    except OSError as exc:
        handler = logging.NullHandler()
        handler._dubsync_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
        logger.warning("Logdatei konnte nicht geöffnet werden: %s", exc)

    def _main_exception(exc_type, exc_value, exc_traceback):
        logger.critical(
            "Nicht abgefangener Hauptthread-Fehler",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        # Preserve Python's normal stderr traceback for terminal launches.
        traceback.print_exception(exc_type, exc_value, exc_traceback)

    sys.excepthook = _main_exception

    if hasattr(threading, "excepthook"):
        def _thread_exception(args):
            logger.critical(
                "Nicht abgefangener Thread-Fehler in %s",
                getattr(args.thread, "name", "unbekannt"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        threading.excepthook = _thread_exception
    return logger
