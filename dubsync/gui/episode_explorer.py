"""Native Qt inspection dialog for visually evaluated episode candidates."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget,
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
    """Displays evidence only. No actions to mutate identity or export state."""

    def __init__(self, report: dict, parent=None):
        super().__init__(parent)
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
            "Nur zur Sichtprüfung: Es werden keine Dateinamen, Episodengruppen oder Spuren geändert. "
            "Die Exportfreigabe benötigt weiterhin eine separate vollständige Bildanalyse "
            "und alle bestehenden Sicherheitsprüfungen."
        )
        safety.setObjectName("mutedText")
        safety.setWordWrap(True)
        layout.addWidget(safety)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
