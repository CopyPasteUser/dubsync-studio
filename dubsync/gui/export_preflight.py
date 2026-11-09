"""Focused native Qt single-export review dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from dubsync.core.export_preflight import PreflightLine


_STATES = {"blocked": "Gesperrt", "caution": "Prüfen", "ready": "Bereit"}


class ExportPreflightDialog(QDialog):
    """Show a complete, scrollable review before starting a potentially long mux."""

    def __init__(self, lines: list[PreflightLine], destination: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export-Vorprüfung · DubSync Studio")
        self.resize(820, 420)
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(12)
        header = QLabel("EXPORT-VORPRÜFUNG · EINZELEXPORT")
        header.setObjectName("eyebrow")
        layout.addWidget(header)
        blocked = [item for item in lines if item.status == "blocked"]
        intro = QLabel("Export gesperrt · erst die markierten Probleme beheben."
                       if blocked else "Quellen und Zeitkorrektur prüfen, bevor der Export gestartet wird.")
        intro.setObjectName("sectionTitle")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        table = QTableWidget(len(lines), 3)
        table.setObjectName("exportPreflightResults")
        table.setAccessibleName("Status und Begründungen der Export-Vorprüfung")
        table.setHorizontalHeaderLabels(("Element", "Status", "Begründung"))
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(False)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, item in enumerate(lines):
            for column, value in enumerate((item.title, _STATES.get(item.status, item.status), item.detail)):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                if column == 1:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row, column, cell)
        layout.addWidget(table, 1)
        target = QLabel(f"Zieldatei: {destination}")
        target.setObjectName("mutedText")
        target.setWordWrap(True)
        layout.addWidget(target)
        warning = QLabel("Diese Vorprüfung ersetzt nicht die vollständige Export- und Dateiintegritätsprüfung.")
        warning.setObjectName("mutedText")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Zurück zur Prüfung")
        self.start_button = buttons.addButton("Export starten", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start_button.setObjectName("primaryButton")
        self.start_button.setEnabled(not blocked)
        self.start_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
