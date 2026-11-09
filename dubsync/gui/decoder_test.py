"""User-initiated CPU/GPU decoder parity and throughput self-test."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QLabel,
                               QPushButton, QPlainTextEdit, QVBoxLayout)

from dubsync.core.ffmpeg_sampling import (benchmark_decoder_candidates,
                                         choose_verified_decoder, gpu_decode_candidates)


def summarize_decoder_selftest(measurements: dict, candidates: list) -> str:
    winner = choose_verified_decoder(measurements, candidates)
    cpu = measurements.get("cpu", {})
    lines = ["LOCAL DECODER SELF-TEST", "",
             f"CPU: {cpu.get('samples_per_second', 'n/a')} samples/s"]
    for candidate in candidates:
        values = measurements.get(candidate.key, {})
        if "error" in values:
            state = "FAILED: " + str(values["error"])[:150]
        elif not values:
            state = "NOT MEASURED"
        else:
            state = (f"{values.get('samples_per_second', '?')} samples/s · "
                     f"matching frames {float(values.get('frame_match_ratio', 0)):.0%} · "
                     f"median distance {values.get('median_hamming_to_cpu', '?')}")
        lines.append(f"{candidate.key}: {state}")
    lines += ["", "Recommended: " + (winner.key if winner else "FFmpeg CPU (OpenCV if unavailable)"),
              "A GPU is selected only when faster and visually equivalent to CPU output.",
              "This verifies decoder output parity, not physical GPU utilization."]
    return "\n".join(lines)


class DecoderTestWorker(QThread):
    ready = Signal(str)
    error = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        try:
            from dubsync.core.alignment import perceptual_hash
            from dubsync.core.probe import probe_media
            info = probe_media(self.path)
            if not info.video:
                raise ValueError("Selected file has no video track")
            candidates = gpu_decode_candidates()
            measured = benchmark_decoder_candidates(
                self.path, info.duration, .75, perceptual_hash,
                candidates=candidates, cancel_event=self.cancelled, seconds=8.,
            )
            if not self.cancelled.is_set():
                self.ready.emit(summarize_decoder_selftest(measured, candidates))
        except (OSError, RuntimeError, ValueError) as exc:
            if not self.cancelled.is_set():
                self.error.emit(str(exc))


class DecoderSelfTestDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Decoder-Selbsttest · keine Medienänderung")
        self.resize(650, 440)
        self.worker: DecoderTestWorker | None = None
        box = QVBoxLayout(self)
        explanation = QLabel("Prüft auf ausdrücklichen Wunsch einen kurzen Abschnitt einer lokalen "
                             "Videodatei. CPU und erkannte GPU-Kandidaten werden nacheinander "
                             "gemessen. Keine Ausgabe und keine Änderung an den Medien.")
        explanation.setWordWrap(True)
        box.addWidget(explanation)
        self.choose = QPushButton("Lokales Video wählen und Decoder testen …")
        self.choose.clicked.connect(self._choose_file)
        box.addWidget(self.choose)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setAccessibleName("Ergebnisse des CPU-GPU-Decoder-Selbsttests")
        self.report.setPlainText("Noch kein Test gestartet. NAS-Quellen werden ausgeschlossen.")
        box.addWidget(self.report, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.close)
        box.addWidget(self.buttons)

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Lokales Video für Decoder-Selbsttest", "",
            "Videodateien (*.mkv *.mp4 *.mov *.webm *.avi *.ts);;Alle Dateien (*)",
        )
        if path:
            self.start_test(path)

    def start_test(self, path: str) -> None:
        if self.worker is not None:
            return
        from dubsync.core.network import is_network_path
        # Consult the mount table *before* any stat: a dead hard-mounted NFS
        # filesystem may block Path.is_file() for an unbounded time.
        if is_network_path(path) or not Path(path).is_file():
            self.report.setPlainText("Bitte eine erreichbare lokale Videodatei wählen; "
                                     "ein NAS wird nicht mehrfach zum Test eingelesen.")
            return
        self.choose.setEnabled(False)
        self.report.setPlainText("CPU-Referenz und mögliche GPU-Decoder werden geprüft …\n"
                                 "Der Selbsttest kann bei mehreren GPUs etwas dauern.")
        worker = DecoderTestWorker(path, self)
        self.worker = worker
        worker.ready.connect(self.report.setPlainText)
        worker.error.connect(lambda message: self.report.setPlainText("Selbsttest fehlgeschlagen: " + message))

        def finished() -> None:
            self.worker = None
            self.choose.setEnabled(True)
            worker.deleteLater()

        worker.finished.connect(finished)
        worker.start()

    def closeEvent(self, event) -> None:
        if self.worker is not None:
            self.worker.cancel()
            if not self.worker.wait(3500):
                self.report.setPlainText("Decoder wird noch beendet … bitte erneut schließen.")
                event.ignore()
                return
        super().closeEvent(event)
