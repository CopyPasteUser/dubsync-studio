"""Native evidence timeline widget; presentation only, never export authorization."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from dubsync.core.models import AlignmentResult
from dubsync.core.evidence import EvidenceSpan, evidence_spans
from dubsync.gui.theme import TOKENS
from dubsync.gui.timeline_index import TimelineIndex


def formatted_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


class AlignmentTimeline(QWidget):
    """Paints measured anchors and segments, with keyboard-accessible seek/zoom."""

    positionChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.alignment: AlignmentResult | None = None
        self.duration = 0.0
        self.position = 0.0
        self.zoom = 1.0
        self.window_start = 0.0
        self.evidence: list[EvidenceSpan] = []
        self._index = TimelineIndex()
        self.setMinimumHeight(125)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Synchronisations-Timeline")
        self.setAccessibleDescription("Blau: Bildproben. Orange: ungeprüft. Grau: ohne Zuordnung. Manuelle Schnitte sind nicht verifiziert.")
        self.setToolTip("Blau: Bildproben · Orange: ungeprüft · Grau: ohne Zuordnung\n"
                        "Klick: Position · Pfeiltasten: ±5 s · Bild auf/ab: ±30 s · Plus/Minus: Zoom")

    def set_data(self, duration: float, alignment: AlignmentResult | None) -> None:
        self.duration = max(0.0, float(duration))
        self.alignment = alignment
        self.evidence = evidence_spans(alignment) if alignment is not None else []
        self._index = TimelineIndex(alignment)
        self.position = min(self.position, self.duration)
        self.window_start = 0.0
        self.zoom = 1.0
        self.update()

    def set_position(self, position: float, *, emit: bool = False) -> None:
        new_position = max(0.0, min(self.duration, float(position)))
        if math.isclose(new_position, self.position, abs_tol=0.001):
            return
        self.position = new_position
        span = self._visible_span()
        if position < self.window_start or position > self.window_start + span:
            self.window_start = max(0.0, min(self.duration - span, position - span / 2))
        self.update()
        if emit:
            self.positionChanged.emit(new_position)

    def set_zoom(self, value: float) -> None:
        value = max(1.0, min(16.0, float(value)))
        self.zoom = value
        span = self._visible_span()
        self.window_start = max(0.0, min(self.duration - span, self.position - span / 2))
        self.update()

    def _visible_span(self) -> float:
        return self.duration / self.zoom if self.duration else 1.0

    def _x(self, seconds: float, width: float) -> float:
        return 15.0 + (seconds - self.window_start) / self._visible_span() * width

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = max(1, self.width() - 30)
        bar_y = 40.0
        painter.fillRect(QRectF(15, bar_y, width, 23), QColor(TOKENS["raised"]))
        painter.setPen(QColor(TOKENS["muted"]))
        painter.setFont(QFont("Noto Sans", 9))
        painter.drawText(15, 18, "BILDMASTER · ZEITACHSE")
        manual = bool(self.alignment and self.alignment.manual_segments)
        legend = ("MANUELL · NICHT VERIFIZIERT" if manual else
                  "Blau: Bildproben · Orange: ungeprüft · Grau: ohne Zuordnung")
        painter.drawText(15, 90, painter.fontMetrics().elidedText(
            legend, Qt.TextElideMode.ElideRight, int(width)))
        painter.fillRect(QRectF(15, 96, width, 8), QColor(TOKENS["raised"]))
        for span in self.evidence:
            left = max(15.0, self._x(span.start, width))
            right = min(15.0 + width, self._x(span.end, width))
            if right > left:
                color = {"sampled": TOKENS["accent"], "unverified": TOKENS["warning"],
                         "unmapped": TOKENS["raised"]}[span.status]
                painter.fillRect(QRectF(left, 96, right - left, 8), QColor(color))
        for start, end in self._index.segments:
            left = max(15.0, self._x(start, width))
            right = min(15.0 + width, self._x(end, width))
            if right > left:
                painter.fillRect(QRectF(left, 40, right - left, 23), QColor(TOKENS["selection"]))
        # Display-only anchor projection: no safety recomputation on paint.
        painter.setPen(QPen(QColor(TOKENS["warning"] if manual else TOKENS["accent"]), 1.4))
        for stamp in self._index.visible_anchors(self.window_start,
                                                 self.window_start + self._visible_span()):
            x = self._x(stamp, width)
            painter.drawLine(int(x), 96, int(x), 104)
        painter.setPen(QPen(QColor(TOKENS["accent"]), 2))
        cursor = self._x(self.position, width)
        painter.drawLine(int(cursor), 31, int(cursor), 112)
        painter.setPen(QColor(TOKENS["muted"]))
        painter.drawText(15, 121, formatted_time(self.window_start))
        painter.drawText(QRectF(15, 108, width, 17), Qt.AlignmentFlag.AlignRight,
                         formatted_time(min(self.duration, self.window_start + self._visible_span())))
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.duration:
            fraction = max(0.0, min(1.0, (event.position().x() - 15) / max(1, self.width() - 30)))
            self.set_position(self.window_start + fraction * self._visible_span(), emit=True)
            self.setFocus()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            delta = {Qt.Key.Key_Left: -5, Qt.Key.Key_Right: 5,
                     Qt.Key.Key_PageUp: -30, Qt.Key.Key_PageDown: 30}[key]
            self.set_position(self.position + delta, emit=True)
            event.accept()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.set_zoom(self.zoom * 2)
            event.accept()
        elif key == Qt.Key.Key_Minus:
            self.set_zoom(self.zoom / 2)
            event.accept()
        elif key == Qt.Key.Key_Home:
            self.set_position(0, emit=True)
            event.accept()
        elif key == Qt.Key.Key_End:
            self.set_position(self.duration, emit=True)
            event.accept()
        else:
            super().keyPressEvent(event)
