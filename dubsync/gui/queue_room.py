"""Inline control room for persisted batches; never starts a job on its own."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class QueueControlRoom(QFrame):
    startRequested = Signal(str)
    retryRequested = Signal(str)
    drainRequested = Signal()
    refreshRequested = Signal()

    STATE_LABELS = {
        "queued": "Bereit", "running": "In Arbeit", "paused": "Pausiert",
        "interrupted": "Unterbrochen", "cancelled": "Abgebrochen",
        "failed": "Fehler", "needs_review": "Prüfung nötig",
        "completed": "Abgeschlossen",
    }
    STARTABLE = {"queued", "interrupted", "cancelled", "failed", "needs_review"}
    RETRYABLE = {"failed", "cancelled", "needs_review", "interrupted", "completed"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("queueRoom")
        self.setAccessibleName("Stapelzentrale mit fortsetzbaren Aufträgen")
        self._entries: list[dict[str, Any]] = []
        self._busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 12, 13, 13)
        layout.setSpacing(9)
        line = QHBoxLayout()
        title = QLabel("STAPELZENTRALE")
        title.setObjectName("eyebrow")
        line.addWidget(title)
        line.addStretch(1)
        self.count_label = QLabel("Keine Aufträge")
        self.count_label.setObjectName("mutedText")
        line.addWidget(self.count_label)
        refresh = QPushButton("Aktualisieren")
        refresh.clicked.connect(self.refreshRequested)
        line.addWidget(refresh)
        layout.addLayout(line)

        self.table = QTableWidget(0, 5)
        self.table.setAccessibleName("Gespeicherte Stapelaufträge und Ausführungsstatus")
        self.table.setHorizontalHeaderLabels(["Status", "Auftrag", "Fortschritt", "Aktualisiert", "Details"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.setMinimumHeight(175)
        self.queue_surface = QStackedWidget()
        self.queue_empty = QLabel("Noch keine Stapelaufträge\n\nScanne deine Sprachfassungen und plane den ersten Serien-Stapel ein.")
        self.queue_empty.setAccessibleName("Keine gespeicherten Stapelaufträge")
        self.queue_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.queue_empty.setWordWrap(True)
        self.queue_empty.setObjectName("emptyState")
        self.queue_surface.addWidget(self.queue_empty)
        self.queue_surface.addWidget(self.table)
        layout.addWidget(self.queue_surface, 1)

        self.detail_label = QLabel("Nach einem Neustart bleiben Aufträge erhalten. Fortsetzen verifiziert fertige MKVs erneut.")
        self.detail_label.setObjectName("mutedText")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Starten / fortsetzen")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self._start)
        buttons.addWidget(self.start_button)
        self.retry_button = QPushButton("Probleme erneut einplanen")
        self.retry_button.clicked.connect(self._retry)
        buttons.addWidget(self.retry_button)
        self.drain_button = QPushButton("Wartende abarbeiten")
        self.drain_button.clicked.connect(self.drainRequested)
        buttons.addWidget(self.drain_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.set_entries([])

    def selected(self) -> dict[str, Any] | None:
        row = self.table.currentRow()
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def set_entries(self, entries: list[dict[str, Any]], *, busy: bool = False) -> None:
        selected = self.selected()
        selected_id = selected["id"] if selected else None
        self._busy = busy
        self._entries = list(entries)
        self.table.blockSignals(True)
        self.table.setRowCount(len(entries))
        restore = 0
        for row, entry in enumerate(entries):
            if entry["id"] == selected_id:
                restore = row
            current, total = int(entry.get("progress_current") or 0), int(entry.get("progress_total") or 0)
            progress = f"{current}/{total}" if total else "—"
            state = self.STATE_LABELS.get(entry.get("state", ""), entry.get("state", "?"))
            fields = [state, entry.get("label", ""), progress,
                      entry.get("updated_at", ""), entry.get("error_text") or entry.get("progress_message", "")]
            for col, value in enumerate(fields):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry["id"])
                self.table.setItem(row, col, item)
        if entries:
            self.table.selectRow(restore)
        self.table.blockSignals(False)
        self.queue_surface.setCurrentIndex(1 if entries else 0)
        pending = sum(entry.get("state") in self.STARTABLE for entry in entries)
        self.count_label.setText(f"{len(entries)} Aufträge · {pending} startbar")
        self._selection_changed()

    def _selection_changed(self) -> None:
        entry = self.selected()
        state = entry.get("state") if entry else ""
        self.start_button.setEnabled(not self._busy and state in self.STARTABLE)
        self.retry_button.setEnabled(bool(not self._busy and entry and
                                          state in self.RETRYABLE and entry.get("journal_path")))
        self.drain_button.setEnabled(not self._busy and any(
            item.get("state") == "queued" for item in self._entries))
        if entry:
            self.detail_label.setText(
                f"{entry.get('resource_reason') or 'Ressourcenprofil beim Start ermitteln.'} "
                f"{'Prüfprotokoll vorhanden.' if entry.get('journal_path') else 'Noch kein Prüfprotokoll.'} "
                "Wiederaufnahme prüft Quellen und fertige Dateien neu; vorhandene Ausgaben werden nicht still ersetzt."
            )
        else:
            self.detail_label.setText("Noch keine Aufträge gespeichert. Du kannst links oben einen Serien-Stapel planen.")

    def _start(self) -> None:
        entry = self.selected()
        if self.start_button.isEnabled() and entry:
            self.startRequested.emit(entry["id"])

    def _retry(self) -> None:
        entry = self.selected()
        if self.retry_button.isEnabled() and entry:
            self.retryRequested.emit(entry["id"])
