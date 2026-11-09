"""Native Qt inspection dialog for visually evaluated episode candidates."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)


_STATUS = {
    "verified": "Bildinhalt bestätigt",
    "insufficient": "Nicht ausreichend bestätigt",
    "no_match": "Keine passenden Bildanker",
    "error": "Analyse nicht abgeschlossen",
}
_VERDICT = {
    "single_visual_candidate": "Eine Fassung hat belastbare Bildbelege. Vor einer Zuordnung bitte selbst prüfen.",
    "multiple_visual_candidates": "Mehrere Fassungen passen visuell. Keine eindeutige Empfehlung möglich.",
    "analysis_incomplete": "Mindestens eine Datei konnte nicht untersucht werden. Kein negatives Match daraus ableiten.",
    "unverified": "Keine Fassung konnte mit ausreichenden Bildbelegen bestätigt werden.",
}


class EpisodeExplorerDialog(QDialog):
    """Shows evidence and optionally collects one explicit, non-export approval."""

    def __init__(self, report: dict, parent=None, *, allow_assignment: bool = False):
        super().__init__(parent)
        self.report = report
        self.selected_candidate: int | None = None
        self.setWindowTitle("Kandidatenprüfung · DubSync Studio")
        self.resize(870, 405)
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(12)
        heading = QLabel(f"BILDABGLEICH · {report['master_filename']}")
        heading.setObjectName("eyebrow")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        verdict = QLabel(_VERDICT.get(report["verdict"], "Kandidatenprüfung abgeschlossen"))
        verdict.setObjectName("sectionTitle")
        verdict.setWordWrap(True)
        layout.addWidget(verdict)

        self.results = QTableWidget(len(report["candidates"]), 5)
        self.results.setObjectName("candidateExplorerResults")
        self.results.setAccessibleName("Ergebnis der visuellen Episodenkandidatenprüfung")
        self.results.setHorizontalHeaderLabels(("Fassung", "Bildbeleg", "Anker", "Abdeckung", "Abweichung"))
        self.results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # The global dark QSS does not define Qt's native alternate row brush;
        # that palette can otherwise produce white rows with white text.
        self.results.setAlternatingRowColors(False)
        self.results.verticalHeader().setVisible(False)
        self.results.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for column in (2, 3, 4):
            self.results.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for row, item in enumerate(report["candidates"]):
            proof = item["visual_proof"] or {}
            values = (
                item["filename"], _STATUS.get(item["status"], item["status"]),
                str(proof.get("anchors", "–")),
                f"{proof['coverage']:.1%}" if "coverage" in proof else "–",
                f"{proof['median_hamming']:.1f} Bit" if "median_hamming" in proof else "–",
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setToolTip(f"Auswahl #{item['candidate']}: {value}")
                if item["error"]:
                    cell.setToolTip(item["error"])
                if column >= 2:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.results.setItem(row, column, cell)
        layout.addWidget(self.results, 1)
        safety = QLabel(
            "Die Bildprüfung ist keine Exportfreigabe. Eine bestätigte Fassung wird nur im aktuellen "
            "Projekt zugeordnet; Dateien werden nicht umbenannt oder verändert. "
            "Vor einem Einzelexport sind neue Bildanalyse und Sicherheitsprüfung erforderlich. "
            "Serienstapel überspringen manuell zugeordnete Gruppen."
            if allow_assignment else
            "Nur zur Sichtprüfung: Es werden keine Dateinamen, Episodengruppen oder Spuren geändert. "
            "Die Exportfreigabe benötigt weiterhin eine separate vollständige Bildanalyse "
            "und alle bestehenden Sicherheitsprüfungen."
        )
        safety.setObjectName("mutedText")
        safety.setWordWrap(True)
        layout.addWidget(safety)
        self.approval_checkbox = None
        self.assign_button = None
        if allow_assignment:
            self.approval_checkbox = QCheckBox(
                "Ich habe die ausgewählte Zeile geprüft und möchte diese Fassung der Episode zuordnen."
            )
            self.approval_checkbox.setAccessibleName("Manuelle Fassungszuordnung bestätigen")
            layout.addWidget(self.approval_checkbox)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        if allow_assignment:
            self.assign_button = buttons.addButton("Fassung zuordnen", QDialogButtonBox.ButtonRole.AcceptRole)
            self.assign_button.setObjectName("primaryButton")
            self.assign_button.setEnabled(False)
            self.assign_button.clicked.connect(self._approve_assignment)
            self.results.itemSelectionChanged.connect(self._update_assignment_state)
            self.approval_checkbox.toggled.connect(self._update_assignment_state)
            self._update_assignment_state()
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_assignment_state(self):
        if self.assign_button is None or self.approval_checkbox is None:
            return
        row = self.results.currentRow()
        selected = self.results.selectionModel().isRowSelected(row, self.results.rootIndex()) if row >= 0 else False
        item = self.report["candidates"][row] if selected and row < len(self.report["candidates"]) else None
        proof = item.get("visual_proof") if item else None
        verified = (item is not None and item.get("status") == "verified"
                    and isinstance(proof, dict) and proof.get("independent_episode_proof") is True
                    and proof.get("safe_for_automatic_export") is True)
        self.assign_button.setEnabled(bool(verified and self.approval_checkbox.isChecked()))

    def _approve_assignment(self):
        if self.assign_button is None or not self.assign_button.isEnabled():
            return
        row = self.results.currentRow()
        if not 0 <= row < len(self.report["candidates"]):
            return
        self.selected_candidate = self.report["candidates"][row]["candidate"]
        self.accept()
