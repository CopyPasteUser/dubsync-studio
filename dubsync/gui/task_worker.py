"""Qt job lifecycle and cooperative pause/cancellation; independent of MainWindow."""
from __future__ import annotations

import threading
import traceback
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QThread, Signal


class NamedCancelEvent(threading.Event):
    """Cooperative cancellation with provenance for support diagnostics."""

    def __init__(self):
        super().__init__()
        self.reason = None
        self.requested_utc = None

    def request(self, reason: str = "unspecified") -> None:
        if not self.is_set():
            self.reason = reason
            self.requested_utc = datetime.now().astimezone().isoformat()
        self.set()


class TaskWorker(QThread):
    progress = Signal(str, int, int)
    def __init__(self, task: Callable, parent=None):
        super().__init__(parent)
        self.task = task
        self.cancel_event = NamedCancelEvent()
        self.pause_event = threading.Event()
        self.result_value = None
        self.error_text = ""
        self.cancelled = False

    def report(self, message: str, current: int = 0, total: int = 0):
        # Cooperative pause point: existing scan/alignment/export callbacks already
        # funnel through here. Cancel always releases a paused worker immediately.
        while self.pause_event.is_set() and not self.cancel_event.is_set():
            self.cancel_event.wait(0.10)
        if self.cancel_event.is_set():
            raise RuntimeError("Aufgabe abgebrochen")
        self.progress.emit(str(message), int(current), int(total))

    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.pause_event.clear()

    def cancel(self, reason: str = "unspecified"):
        self.cancel_event.request(reason)
        self.pause_event.clear()

    def run(self):
        try:
            self.result_value = self.task(self.report, self.cancel_event)
            self.cancelled = self.cancel_event.is_set()
        except Exception as exc:
            self.cancelled = self.cancel_event.is_set()
            if not self.cancelled:
                self.error_text = f"{exc}\n\n{traceback.format_exc()}"
