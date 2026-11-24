"""Reusable Qt progress and synchronization residual plots."""
from __future__ import annotations

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush
from PySide6.QtWidgets import QWidget

from dubsync.core.models import AlignmentResult


class ProgressHistoryPlot(QWidget):
    """Compact, accessible sparkline showing recent job-progress samples."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values: list[float] = []
        self.setMinimumSize(110, 28)
        self.setMaximumWidth(150)
        self.setAccessibleName("Verlauf des Aufgabenfortschritts")
        self.setToolTip("Verlauf des zuletzt gemeldeten Fortschritts")

    def reset(self):
        self.values.clear()
        self.update()

    def add_sample(self, value: float):
        self.values.append(max(0.0, min(1.0, float(value))))
        self.values = self.values[-48:]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        area = self.rect().adjusted(5, 4, -5, -4)
        painter.setPen(QPen(QColor("#263b55"), 1))
        painter.drawLine(area.left(), area.bottom(), area.right(), area.bottom())
        if not self.values:
            painter.setPen(QColor("#8298b2"))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "Fortschritt")
            return
        points = []
        span = max(1, len(self.values) - 1)
        for i, value in enumerate(self.values):
            x = area.left() + area.width() * i / span
            y = area.bottom() - area.height() * value
            points.append(QPointF(x, y))
        painter.setPen(QPen(QColor("#54a7ff"), 2, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for left, right in zip(points, points[1:]):
            painter.drawLine(left, right)


class AlignmentResidualPlot(QWidget):
    """Compact, accessible confidence/residual plot of synchronization anchors."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: AlignmentResult | None = None
        self.offset = 0.0
        self.scale = 1.0
        self.setObjectName("alignmentResidualPlot")
        self.setMinimumHeight(62)
        self.setMaximumHeight(78)
        self.setAccessibleName("Synchronisationsqualität und Ankerabweichungen")
        description = "Diagramm der Zeitabweichung einzelner Videoanker von der aktuellen linearen Synchronisationskorrektur."
        self.setAccessibleDescription(description)
        self.setToolTip(description)

    def set_alignment(self, result: AlignmentResult | None, offset: float | None = None,
                      scale: float | None = None):
        self.result = result
        self.offset = float(result.offset if offset is None and result else offset or 0.0)
        self.scale = float(result.slope if scale is None and result else scale or 1.0)
        piecewise = bool(result and result.segments
                         and abs(self.offset - result.offset) <= 0.00051
                         and abs(self.scale - result.slope) <= 0.00000051)
        description = (("Diagramm zu manuell bearbeiteten Abschnitten; Anker belegen die neuen Grenzen nicht automatisch."
                        if result and result.manual_segments else
                        "Diagramm der Abweichungen von den einzeln bestätigten Zeitabschnitten.")
                       if piecewise else
                       "Diagramm der Zeitabweichung einzelner Videoanker von der aktuellen linearen Synchronisationskorrektur.")
        self.setAccessibleDescription(description)
        self.setToolTip(description)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor("#293b54"), 1))
        painter.setBrush(QBrush(QColor("#0c1727")))
        painter.drawRoundedRect(outer, 9, 9)
        result = self.result
        if result is None or not result.anchors:
            painter.setPen(QColor("#93a4bb"))
            painter.drawText(outer.adjusted(12, 0, -10, 0),
                             int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                             "Synchronisationsanker erscheinen nach der Analyse")
            painter.end()
            return

        anchors = result.anchors
        piecewise = bool(result.segments
                         and abs(self.offset - result.offset) <= 0.00051
                         and abs(self.scale - result.slope) <= 0.00000051)
        residuals = ([a.target_time - result.target_time(a.source_time) for a in anchors]
                     if piecewise else
                     [a.target_time - (self.scale * a.source_time + self.offset) for a in anchors])
        threshold = max(0.75, result.sample_interval * 1.20)
        max_abs = max(threshold * 1.8, max((abs(value) for value in residuals), default=0.0) * 1.15, 0.2)
        plot = outer.adjusted(12, 22, -10, -10)
        if plot.width() < 10 or plot.height() < 10:
            painter.end()
            return
        x_for = lambda value: plot.left() + max(0.0, min(1.0, value / max(result.source_duration, 0.001))) * plot.width()
        y_for = lambda value: plot.center().y() - (value / max_abs) * (plot.height() / 2)

        # Tolerance band shows the residual range used by the confidence calculation.
        band_top = y_for(threshold)
        band_bottom = y_for(-threshold)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(52, 211, 153, 24)))
        painter.drawRoundedRect(QRectF(plot.left(), band_top, plot.width(), max(1.0, band_bottom-band_top)), 3, 3)
        painter.setPen(QPen(QColor("#29415d"), 1, Qt.PenStyle.DotLine))
        for fraction in (0.25, 0.5, 0.75):
            x = plot.left() + plot.width() * fraction
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        painter.setPen(QPen(QColor("#6f89a8"), 1))
        painter.drawLine(QPointF(plot.left(), y_for(0.0)), QPointF(plot.right(), y_for(0.0)))
        painter.setPen(QPen(QColor("#6f89a8"), 1, Qt.PenStyle.DotLine))
        painter.drawLine(QPointF(plot.left(), band_top), QPointF(plot.right(), band_top))
        painter.drawLine(QPointF(plot.left(), band_bottom), QPointF(plot.right(), band_bottom))

        for anchor, residual in zip(anchors, residuals):
            if abs(residual) <= threshold:
                color = QColor("#34d399")
            elif abs(residual) <= max_abs * 0.8:
                color = QColor("#fbbf24")
            else:
                color = QColor("#fb7185")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QPointF(x_for(anchor.source_time), y_for(residual)), 2.4, 2.4)

        painter.setPen(QColor("#d8e5f6"))
        painter.drawText(QRectF(outer.left()+10, outer.top()+3, outer.width()*0.6, 16),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         "ABSCHNITTS-ANKER" if piecewise else "ANKER-ABWEICHUNG")
        painter.setPen(QColor("#91a7c4"))
        painter.drawText(QRectF(outer.right()-170, outer.top()+3, 158, 16),
                         int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                         f"{result.confidence:.0%} · RMS {self._rms(residuals):.2f}s")
        painter.end()

    @staticmethod
    def _rms(values: list[float]) -> float:
        return (sum(value * value for value in values) / len(values)) ** 0.5 if values else 0.0
