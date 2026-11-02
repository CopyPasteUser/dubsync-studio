"""Explicit, bounded audio waveform excerpts for the native review workbench.

Waveforms help a human inspect the two soundtracks; they are not proof of
episode identity and cannot authorize automatic muxing.
"""
from __future__ import annotations

import array
import math
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QThread, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from dubsync.gui.theme import TOKENS


@dataclass(frozen=True, slots=True)
class WaveformExcerpt:
    start: float
    peaks: tuple[float, ...]


def extract_waveform(path: str, center: float, *, cancelled: threading.Event,
                     window_seconds: float = 18., buckets: int = 128,
                     timeout: float = 14.) -> WaveformExcerpt:
    """Extract a mono 8 kHz preview, capped independently of the source size."""
    if not math.isfinite(center) or center < 0 or not 1 <= window_seconds <= 30:
        raise ValueError("Ungültige Audiovorschau-Position oder Dauer")
    if not 16 <= buckets <= 512:
        raise ValueError("Ungültige Auflösung der Wellenform")
    binary = shutil.which("ffmpeg")
    if not binary:
        raise RuntimeError("FFmpeg ist nicht verfügbar")
    if cancelled.is_set():
        raise RuntimeError("Audiovorschau abgebrochen")
    start = max(0., center - window_seconds / 2)
    command = [binary, "-hide_banner", "-nostdin", "-loglevel", "error",
               "-protocol_whitelist", "file,pipe", "-ss", f"{start:.3f}",
               "-threads", "2", "-i", str(path), "-map", "0:a:0",
               "-vn", "-sn", "-dn", "-t", f"{window_seconds:.3f}",
               "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        begun = time.monotonic()
        while True:
            if cancelled.is_set():
                raise RuntimeError("Audiovorschau abgebrochen")
            try:
                raw, stderr = process.communicate(timeout=.25)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() - begun > timeout:
                    raise RuntimeError("Audiovorschau: Zeitüberschreitung – ist die Quelle erreichbar?")
        if process.returncode or not raw:
            error = stderr.decode("utf-8", errors="replace").strip()[-220:]
            raise RuntimeError(error or "Audiospur konnte nicht gelesen werden")
        if len(raw) > int(window_seconds * 8000 * 2) + 8192:
            raise RuntimeError("Audiovorschau hat ihr Speicherlimit überschritten")
        samples = array.array("h")
        samples.frombytes(raw[:len(raw) // 2 * 2])
        if sys.byteorder != "little":
            samples.byteswap()
        # Bounded O(samples) peak calculation; no large NumPy or disk cache.
        step = max(1, math.ceil(len(samples) / buckets))
        peaks = tuple(min(1., max(abs(s) for s in samples[pos:pos + step]) / 32768.)
                      for pos in range(0, len(samples), step))
        return WaveformExcerpt(start, peaks[:buckets])
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


class WaveformWorker(QThread):
    ready = Signal(object)
    error = Signal(str)

    def __init__(self, master: str, source: str, master_time: float,
                 source_time: float | None, parent=None):
        super().__init__(parent)
        self.master, self.source = master, source
        self.master_time, self.source_time = master_time, source_time
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        try:
            master = extract_waveform(self.master, self.master_time, cancelled=self.cancelled)
            source = (extract_waveform(self.source, self.source_time, cancelled=self.cancelled)
                      if self.source_time is not None and not self.cancelled.is_set() else None)
            if not self.cancelled.is_set():
                self.ready.emit((master, source))
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            if not self.cancelled.is_set():
                self.error.emit(str(exc))


class PeakWaveform(QWidget):
    """Minimal QPainter waveform; baseline is visible even for genuine silence."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.peaks: tuple[float, ...] = ()
        self.setMinimumHeight(68)
        self.setAccessibleName("Audioamplitude des gewählten Zeitfensters")

    def set_peaks(self, peaks: tuple[float, ...]) -> None:
        self.peaks = peaks
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(TOKENS["raised"]))
        middle = self.height() / 2
        painter.setPen(QPen(QColor(TOKENS["muted"]), 1))
        painter.drawLine(4, int(middle), self.width() - 4, int(middle))
        if self.peaks:
            painter.setPen(QPen(QColor(TOKENS["accent"]), max(1.,
                                  (self.width() - 8) / len(self.peaks) * .7)))
            step = (self.width() - 8) / len(self.peaks)
            for index, peak in enumerate(self.peaks):
                x = 4 + (index + .5) * step
                height = min(1., max(0., peak)) * max(1, middle - 5)
                painter.drawLine(int(x), int(middle - height), int(x), int(middle + height))
        painter.end()
