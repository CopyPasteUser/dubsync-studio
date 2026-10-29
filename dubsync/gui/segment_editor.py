"""Manual timeline editor for a single source/master pair.

The editable mapping is intentionally separate from visual matcher evidence. An
accepted edit always disables automatic export approval for this pairing.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from dubsync.core.models import AlignmentResult, TimelineSegment, validate_timeline_layout
from dubsync.gui.theme import TOKENS


def _initial_segment(result: AlignmentResult) -> TimelineSegment:
    """Seed an editable affine overlap, never an automatically trusted section."""
    if result.slope > 0:
        start = max(0.0, result.offset)
        end = min(result.target_duration, result.offset + result.slope * result.source_duration)
        if end - start >= 0.1:
            return TimelineSegment(
                source_start=max(0.0, (start - result.offset) / result.slope),
                source_end=min(result.source_duration, (end - result.offset) / result.slope),
                target_start=start, target_end=end, anchor_count=0,
                median_hamming=99.0, rms_error=99.0, distinct_hashes=0,
            )
    duration = min(result.source_duration, result.target_duration)
    return TimelineSegment(0.0, duration, 0.0, duration, 0, 99.0, 99.0, 0)


class SegmentTimeline(QWidget):
    """Source and master bands; bars are temporal coverage, not waveform claims."""

    def __init__(self, source_duration: float, target_duration: float, parent=None):
        super().__init__(parent)
        self.source_duration = max(0.1, source_duration)
        self.target_duration = max(0.1, target_duration)
        self.segments: list[TimelineSegment] = []
        self.selected_index = -1
        self.setMinimumHeight(120)
        self.setAccessibleName("Zeitachsenübersicht: Quelle und Master")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(TOKENS["muted"]))
        left, right = 72.0, 15.0
        width = max(2.0, self.width() - left - right)
        for y, label, duration, start_attr, end_attr, accent in (
            (30, "Quelle", self.source_duration, "source_start", "source_end", TOKENS["accent"]),
            (80, "Master", self.target_duration, "target_start", "target_end", TOKENS["success"]),
        ):
            painter.drawText(5, y + 13, label)
            painter.fillRect(QRectF(left, y, width, 19), QColor(TOKENS["raised"]))
            for index, item in enumerate(self.segments):
                a = min(1.0, max(0.0, getattr(item, start_attr) / duration))
                b = min(1.0, max(0.0, getattr(item, end_attr) / duration))
                if b > a:
                    color = QColor(accent if item.enabled else TOKENS["disabled"])
                    rect = QRectF(left + a * width, y, max(1.0, (b - a) * width), 19)
                    painter.fillRect(rect, color)
                    if index == self.selected_index:
                        painter.setPen(QPen(QColor(TOKENS["text"]), 2))
                        painter.drawRect(rect.adjusted(1, 1, -1, -1))
            painter.setPen(QColor(TOKENS["muted"]))
            painter.drawText(int(left), y - 5, "0:00")
            minutes, seconds = divmod(int(duration), 60)
            painter.drawText(int(left + width - 95), y - 5, 95, 16,
                             Qt.AlignmentFlag.AlignRight, f"{minutes:d}:{seconds:02d}")
        painter.end()


class SegmentEditorDialog(QDialog):
    """A precise and keyboard-accessible editor with reversible in-dialog edits."""

    def __init__(self, result: AlignmentResult, parent=None):
        super().__init__(parent)
        self.alignment = result
        self.segments = list(result.segments) or [_initial_segment(result)]
        self._baseline = list(self.segments)
        self._building = False
        self._undo_states: list[list[TimelineSegment]] = []
        self._redo_states: list[list[TimelineSegment]] = []
        self._last_snapshot = list(self.segments)
        self.setWindowTitle("Schnittbereiche bearbeiten")
        self.resize(940, 580)
        self.setMinimumSize(740, 490)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        intro = QLabel(
            "Ordne Zeitbereiche der Quelldatei dem Bildmaster zu. Quelle und Master laufen "
            "von links nach rechts; Lücken im Master bleiben im Export stumm."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.timeline = SegmentTimeline(result.source_duration, result.target_duration, self)
        layout.addWidget(self.timeline)
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(("Aktiv", "Quelle von (s)", "Quelle bis (s)",
                                              "Master von (s)", "Master bis (s)"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

        self.selection_label = QLabel("Abschnitt auswählen, um Laufzeit und Versatz zu sehen.")
        self.selection_label.setObjectName("statusMuted")
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)

        actions = QHBoxLayout()
        self.split_button = QPushButton("Abschnitt teilen")
        self.split_button.setObjectName("secondaryButton")
        self.split_button.clicked.connect(self._split_selected)
        actions.addWidget(self.split_button)
        self.remove_button = QPushButton("Abschnitt entfernen")
        self.remove_button.setObjectName("secondaryButton")
        self.remove_button.clicked.connect(self._remove_selected)
        actions.addWidget(self.remove_button)
        self.undo_button = QPushButton("Rückgängig")
        self.undo_button.setObjectName("secondaryButton")
        self.undo_button.clicked.connect(self._undo)
        actions.addWidget(self.undo_button)
        self.redo_button = QPushButton("Wiederholen")
        self.redo_button.setObjectName("secondaryButton")
        self.redo_button.clicked.connect(self._redo)
        actions.addWidget(self.redo_button)
        reset_button = QPushButton("Änderungen verwerfen")
        reset_button.setObjectName("secondaryButton")
        reset_button.clicked.connect(self._reset)
        actions.addWidget(reset_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.validation_label = QLabel()
        self.validation_label.setWordWrap(True)
        self.validation_label.setObjectName("statusMuted")
        layout.addWidget(self.validation_label)
        warning = QLabel(
            "Wichtig: Nach manuellen Änderungen gelten diese Grenzen nicht mehr als visuell "
            "bestätigt. DubSync fragt vor dem Export ausdrücklich nach deiner Freigabe."
        )
        warning.setWordWrap(True)
        warning.setObjectName("statusWarn")
        layout.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText("Abschnitte übernehmen")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.table.itemChanged.connect(self._refresh)
        self.table.itemSelectionChanged.connect(self._refresh)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self._undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self._redo)
        self._rebuild_table()

    def _push_history(self, new: list[TimelineSegment]):
        if new != self._last_snapshot:
            self._undo_states.append(list(self._last_snapshot))
            self._undo_states = self._undo_states[-80:]
            self._redo_states.clear()
            self._last_snapshot = list(new)

    def _spin(self, maximum: float, value: float, name: str) -> QDoubleSpinBox:
        editor = QDoubleSpinBox(self.table)
        editor.setRange(0.0, max(0.0, maximum))
        editor.setDecimals(3)
        editor.setSingleStep(0.1)
        editor.setSuffix(" s")
        editor.setAccessibleName(name)
        editor.setValue(value)
        editor.valueChanged.connect(self._refresh)
        return editor

    def _rebuild_table(self, select: int = 0):
        self._building = True
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.segments))
        for index, segment in enumerate(self.segments):
            flag = QTableWidgetItem(str(index + 1))
            flag.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled |
                          Qt.ItemFlag.ItemIsUserCheckable)
            flag.setCheckState(Qt.CheckState.Checked if segment.enabled else Qt.CheckState.Unchecked)
            flag.setToolTip("Deaktivierte Abschnitte erzeugen Stille auf der Master-Zeitachse")
            self.table.setItem(index, 0, flag)
            for column, value, maximum, name in (
                (1, segment.source_start, self.alignment.source_duration, "Quellstart"),
                (2, segment.source_end, self.alignment.source_duration, "Quellende"),
                (3, segment.target_start, self.alignment.target_duration, "Masterstart"),
                (4, segment.target_end, self.alignment.target_duration, "Masterende"),
            ):
                self.table.setCellWidget(index, column, self._spin(maximum, value, f"Abschnitt {index + 1}: {name}"))
        self.table.blockSignals(False)
        self._building = False
        self._last_snapshot = list(self.segments)
        if self.segments:
            self.table.selectRow(min(select, len(self.segments) - 1))
        self._refresh()

    def _read_segments(self) -> list[TimelineSegment]:
        items: list[TimelineSegment] = []
        for index, previous in enumerate(self.segments):
            checked = self.table.item(index, 0).checkState() == Qt.CheckState.Checked
            values = [self.table.cellWidget(index, column).value() for column in range(1, 5)]
            items.append(replace(previous, source_start=values[0], source_end=values[1],
                                 target_start=values[2], target_end=values[3], enabled=checked))
        return items

    def _refresh(self, *_unused):
        if self._building:
            return
        current = self._read_segments()
        self._push_history(current)
        self.timeline.segments = current
        index = self.table.currentRow()
        self.timeline.selected_index = index
        self.timeline.update()
        selected = current[index] if 0 <= index < len(current) else None
        self.split_button.setEnabled(bool(selected and len(current) < 12 and
                                          selected.source_end - selected.source_start >= 0.002 and
                                          selected.target_end - selected.target_start >= 0.002))
        self.remove_button.setEnabled(selected is not None and len(current) > 1)
        self.undo_button.setEnabled(bool(self._undo_states))
        self.redo_button.setEnabled(bool(self._redo_states))
        if selected is not None:
            duration = selected.target_end - selected.target_start
            offset = selected.target_start - selected.source_start
            self.selection_label.setText(
                f"Abschnitt {index + 1}/{len(current)} · Masterdauer {duration:.3f} s · "
                f"Startversatz {offset:+.3f} s · {'aktiv' if selected.enabled else 'stumm'}"
            )
        problem = validate_timeline_layout(current, self.alignment.source_duration,
                                           self.alignment.target_duration)
        self.apply_button.setEnabled(problem is None)
        if problem:
            self.validation_label.setObjectName("statusWarn")
            self.validation_label.setText(problem)
        else:
            active = [item for item in current if item.enabled]
            covered = sum(item.target_end - item.target_start for item in active)
            self.validation_label.setObjectName("statusMuted")
            self.validation_label.setText(
                f"{len(active)} aktive Abschnitte · {covered:.1f} s von "
                f"{self.alignment.target_duration:.1f} s Master mit Audio belegt"
            )
        self.validation_label.style().unpolish(self.validation_label)
        self.validation_label.style().polish(self.validation_label)

    def _split_selected(self):
        index = self.table.currentRow()
        if not 0 <= index < len(self.segments) or len(self.segments) >= 12:
            return
        self.segments = self._read_segments()
        self._push_history(self.segments)
        item = self.segments[index]
        mid_source = (item.source_start + item.source_end) / 2
        mid_target = (item.target_start + item.target_end) / 2
        self.segments[index:index + 1] = [
            replace(item, source_end=mid_source, target_end=mid_target,
                    anchor_count=0, distinct_hashes=0),
            replace(item, source_start=mid_source, target_start=mid_target,
                    anchor_count=0, distinct_hashes=0),
        ]
        self._push_history(self.segments)
        self._rebuild_table(index + 1)

    def _remove_selected(self):
        index = self.table.currentRow()
        if not 0 <= index < len(self.segments) or len(self.segments) <= 1:
            return
        self.segments = self._read_segments()
        self.segments.pop(index)
        self._push_history(self.segments)
        self._rebuild_table(index)

    def _reset(self):
        self._push_history(self._read_segments())
        self.segments = list(self._baseline)
        self._push_history(self.segments)
        self._rebuild_table()

    def _undo(self):
        if not self._undo_states:
            return
        current = self._read_segments()
        self._redo_states.append(current)
        self.segments = self._undo_states.pop()
        self._rebuild_table()

    def _redo(self):
        if not self._redo_states:
            return
        current = self._read_segments()
        self._undo_states.append(current)
        self.segments = self._redo_states.pop()
        self._rebuild_table()

    def reject(self):
        if self._read_segments() != self._baseline:
            answer = QMessageBox.question(
                self, "Bearbeitung verwerfen?",
                "Die Änderungen an den Schnittbereichen wurden nicht übernommen. Verwerfen?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        super().reject()

    def _accept(self):
        segments = self._read_segments()
        if validate_timeline_layout(segments, self.alignment.source_duration,
                                    self.alignment.target_duration) is None:
            self.segments = segments
            self.accept()
