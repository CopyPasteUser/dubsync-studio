"""Explicit, cancellable hardware-bound FFmpeg CPU calibration UI."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QLabel,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QVBoxLayout)

from dubsync.core.autotune import tune_local_cpu_decoder
from dubsync.core.network import is_network_path


def summarize_cpu_autotune(result: dict) -> str:
    report = result["report"]
    lines = [f"CPU-Decoder-Test · {result['selected_filename']}",
             f"CPU-Budget: {report['logical_cpus']} logische Threads",
             f"3 Messdurchläufe · {len(report['windows'])} Videobereiche · "
             f"{report['tested_seconds']:g} s je Durchlauf", ""]
    for row in report["runs"]:
        if not row["exact_frame_parity"]:
            state = f"nicht gültig ({row.get('error') or 'Bildabweichung / unvollständig'})"
        else:
            state = (f"{row['median_seconds']:.2f} s  |  "
                     f"{row['speedup_vs_default']:.2f}× Standard  |  "
                     f"schwächstes Fenster {row['worst_window_speedup']:.2f}×")
        lines.append(f"{row['label']:>8}: {state}")
    lines.append("")
    if result["tuning"] is not None:
        threads = result["tuning"]["threads"]
        lines.append(f"Empfehlung: {threads} FFmpeg-CPU-Threads für diesen Rechner")
        lines.append("Bild-Fingerprints exakt gleich; mindestens 15 % schneller "
                     "und je Bereich höchstens 10 % langsamer als Standard.")
        lines.append("Übernehmen ist optional; erst „Speichern“ aktiviert das Profil.")
    else:
        lines.append("Keine zuverlässige Verbesserung – bisherige Einstellungen beibehalten.")
    lines += ["", "Der Test gilt für dieses Video, nicht für jeden Codec.",
              "Bei zwei gleichzeitigen Decodern wird die Threadzahl zum Schutz geteilt.",
              "GPU-Automatik und Export-Sicherheitsregeln bleiben unverändert."]
    return "\n".join(lines)


class CpuAutotuneWorker(QThread):
    progress = Signal(int, int, str)
    ready = Signal(object)
    error = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        try:
            result = tune_local_cpu_decoder(
                self.path, cancel_event=self.cancelled,
                progress=lambda done, total, label: self.progress.emit(done, total, label),
            )
            if not self.cancelled.is_set():
                self.ready.emit(result)
        except (OSError, ValueError, RuntimeError) as exc:
            if not self.cancelled.is_set():
                self.error.emit(str(exc))


class CpuAutotuneDialog(QDialog):
    """Never writes settings: the parent only receives a validated suggestion."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CPU-Decoder automatisch einmessen")
        self.resize(660, 490)
        self.worker: CpuAutotuneWorker | None = None
        self.recommendation: dict | None = None
        self._close_when_idle = False
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        intro = QLabel(
            "Wähle ein repräsentatives Video von deiner lokalen Festplatte. "
            "DubSync misst verschiedene FFmpeg-Threadzahlen mehrfach und an langen Videos "
            "an drei Stellen. Nur identische Bild-Fingerprints mit spürbarem Zeitgewinn "
            "dürfen übernommen werden. Der Test kann einige Minuten dauern. "
            "NAS-Dateien und Exporte werden nicht angefasst."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.choose = QPushButton("Lokales Video auswählen und Messung starten …")
        self.choose.setAccessibleName("Lokale Videodatei für CPU-Autotuning auswählen")
        self.choose.clicked.connect(self._choose_file)
        layout.addWidget(self.choose)
        self.status = QLabel("Noch keine Messung gestartet.")
        self.status.setObjectName("mutedText")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setAccessibleName("Fortschritt der CPU-Decoder-Messung")
        layout.addWidget(self.bar)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setAccessibleName("Vergleich der CPU-Decoder und Ergebnis")
        self.report.setPlainText("Der Vergleich ändert keine Einstellungen und liest nur lokale Videos.")
        layout.addWidget(self.report, 1)
        self.buttons = QDialogButtonBox()
        self.apply_button = self.buttons.addButton(
            "Messung übernehmen", QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setEnabled(False)
        self.cancel_button = self.buttons.addButton(
            "Schließen", QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self._apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Lokale Videodatei einmessen", "",
            "Videodateien (*.mkv *.mp4 *.mov *.webm *.avi *.ts);;Alle Dateien (*)",
        )
        if path:
            self.start_test(path)

    def start_test(self, path: str) -> None:
        if self.worker is not None:
            return
        selected = os.path.abspath(os.path.expanduser(path))
        resolved = os.path.realpath(selected)
        self.recommendation = None
        self.apply_button.setEnabled(False)
        # Network checks precede filesystem stat, including symlink targets.
        if is_network_path(selected) or is_network_path(resolved) or not Path(resolved).is_file():
            self.report.setPlainText("Bitte eine erreichbare lokale Videodatei wählen (kein NAS).")
            self.status.setText("Keine Messung gestartet.")
            return
        self.choose.setEnabled(False)
        self.cancel_button.setText("Messung abbrechen")
        self.status.setText(f"Messung gestartet: {Path(selected).name}")
        self.report.setPlainText("Decoder werden wiederholt getestet. "
                                 "CPU- und Dateicache können Ergebnisse beeinflussen …")
        self.bar.setValue(0)
        worker = CpuAutotuneWorker(resolved, self)
        self.worker = worker
        worker.progress.connect(self._progress)
        worker.ready.connect(self._ready)
        worker.error.connect(self._error)
        worker.finished.connect(lambda: self._finished(worker))
        worker.start()

    def _progress(self, done: int, total: int, label: str) -> None:
        if self._close_when_idle:
            return
        self.bar.setValue(min(100, round(100 * done / max(1, total))))
        self.status.setText(f"Messung {done}/{total} · {label}")

    def _ready(self, result: dict) -> None:
        if self._close_when_idle:
            return
        self.recommendation = result["tuning"]
        self.report.setPlainText(summarize_cpu_autotune(result))
        self.status.setText("Messung abgeschlossen. " +
                            ("Sichere Empfehlung verfügbar." if self.recommendation
                             else "Kein messbarer Vorteil bestätigt."))
        self.bar.setValue(100)

    def _error(self, error: str) -> None:
        if not self._close_when_idle:
            self.report.setPlainText("Autotuning fehlgeschlagen: " + error)
            self.status.setText("Keine Einstellungen geändert.")

    def _finished(self, worker: CpuAutotuneWorker) -> None:
        if self.worker is worker:
            self.worker = None
        worker.deleteLater()
        if self._close_when_idle:
            super().reject()
            return
        self.choose.setEnabled(True)
        self.cancel_button.setText("Schließen")
        self.apply_button.setEnabled(self.recommendation is not None)

    def _apply(self) -> None:
        if self.worker is None and self.recommendation is not None:
            self.accept()

    def reject(self) -> None:
        if self.worker is not None:
            self._close_when_idle = True
            self.worker.cancel()
            self.status.setText("Messung wird abgebrochen …")
            self.choose.setEnabled(False)
            self.cancel_button.setEnabled(False)
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self.worker is not None:
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)
