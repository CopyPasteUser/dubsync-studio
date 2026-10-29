from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit
import sys
import subprocess
import threading
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Callable, Any

from PySide6.QtCore import Qt, QThread, Signal, QSettings, QSize, QTimer, QUrl, QPointF, QRectF
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QDesktopServices, QPainter, QPen, QBrush
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLayout, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFileDialog, QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QSplitter, QFrame,
    QScrollArea, QRadioButton, QCheckBox, QComboBox, QLineEdit,
    QDoubleSpinBox, QProgressBar, QTextEdit, QMessageBox, QHeaderView,
    QGroupBox, QFormLayout, QStackedWidget, QStatusBar, QToolButton, QTabWidget,
    QButtonGroup, QSizePolicy, QDialog, QDialogButtonBox, QSpinBox, QInputDialog, QMenu,
)

from dubsync.gui.theme import studio_stylesheet
from dubsync.gui.workbench import StudioReviewPanel
from dubsync.gui.queue_room import QueueControlRoom

from dubsync.core.alignment import AlignmentError, align_videos
from dubsync.core.batch import (output_path_for_group, choose_smallest_video, estimate_output_size_bytes)
from dubsync.core.cache import AlignmentCache
from dubsync.core.session import persistent_preferences
from dubsync.core.export import ExportError, export_multilingual
from dubsync.core.fingerprint import fingerprint_many
from dubsync.core.models import (AlignmentResult, EpisodeGroup, MediaInfo, TrackChoice,
                                 TimelineSegment, validate_timeline_layout)
from dubsync.gui.segment_editor import SegmentEditorDialog
from dubsync.core.languages import (LANGUAGE_CHOICES, default_track_title, language_display_name, normalize_language_code, resolve_track_label)
from dubsync.core.probe import MediaToolError, choose_default_audio, describe_media, probe_media, probe_many, require_media_tools
from dubsync.core.network import network_mount_candidates, network_mount_for_path, network_mount_type_label, is_network_path
from dubsync.core.scanner import infer_language, scan_directories
from dubsync.core.project import ProjectFileError, load_project_file, save_project_file
from dubsync.core.history import JobHistory
from dubsync.core.job_queue import BatchJobQueue, QueueError
from dubsync.core.logging_setup import configure_logging, default_log_path
from dubsync.core.alignment_diagnostics import (
    latest_diagnostic, recent_diagnostics, diagnostics_directory, summarize_diagnostic,
)
from dubsync.core.batch_diagnostics import (
    recent_batch_diagnostics, batch_diagnostics_directory, summarize_batch,
)
from dubsync.core.drop import classify_dropped_paths
from dubsync.core.performance import (
    default_performance_settings, normalize_performance_settings,
    apply_cpu_thread_limit, supported_hardware_modes, performance_diagnostics,
    settings_for_preset, system_specs, recommended_profile, auto_decoder_summary, PROFILE_LABELS,
)


APP_TITLE = "DubSync Studio"
APP_VERSION = "2.0.0b2"
APP_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_LOGO = Path(__file__).resolve().parents[1] / "assets" / "dubsync-studio.svg"
APP_LOGO = _PACKAGE_LOGO if _PACKAGE_LOGO.is_file() else APP_ROOT / "assets" / "dubsync-studio.svg"
VIDEO_EXTENSIONS_NOTE = "MKV · MP4 · MOV · AVI · WebM · TS"


def app_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "dubsync-studio" / "settings.json"


def load_settings() -> dict[str, Any]:
    try:
        data = json.loads(app_config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_settings(data: dict[str, Any]) -> None:
    import tempfile
    path = app_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        fd, temp = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temp, path)
        except Exception:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
    except OSError:
        pass


class NamedCancelEvent(threading.Event):
    """Cooperative cancellation with provenance for support diagnostics."""

    def __init__(self):
        super().__init__()
        self.reason = None
        self.requested_utc = None

    def request(self, reason: str = "unspecified") -> None:
        if not self.is_set():
            self.reason = reason
            self.requested_utc = datetime.now().astimezone().isoformat()
        self.set()


class TaskWorker(QThread):
    progress = Signal(str, int, int)
    def __init__(self, task: Callable, parent=None):
        super().__init__(parent)
        self.task = task
        self.cancel_event = NamedCancelEvent()
        self.pause_event = threading.Event()
        self.result_value = None
        self.error_text = ""
        self.cancelled = False

    def report(self, message: str, current: int = 0, total: int = 0):
        # Cooperative pause point: existing scan/alignment/export callbacks already
        # funnel through here. Cancel always releases a paused worker immediately.
        while self.pause_event.is_set() and not self.cancel_event.is_set():
            self.cancel_event.wait(0.10)
        if self.cancel_event.is_set():
            raise RuntimeError("Aufgabe abgebrochen")
        self.progress.emit(str(message), int(current), int(total))

    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.pause_event.clear()

    def cancel(self, reason: str = "unspecified"):
        self.cancel_event.request(reason)
        self.pause_event.clear()

    def run(self):
        try:
            self.result_value = self.task(self.report, self.cancel_event)
            self.cancelled = self.cancel_event.is_set()
        except Exception as exc:
            self.cancelled = self.cancel_event.is_set()
            if not self.cancelled:
                self.error_text = f"{exc}\n\n{traceback.format_exc()}"


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


class SourceDropList(QListWidget):
    """Accessible source list that accepts local folders/videos via drag-and-drop."""
    pathsDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setToolTip("Ziehe Medienordner oder einzelne Videodateien hier hinein. NAS-Mounts werden unterstützt.")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        values = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if values:
            self.pathsDropped.emit(values)
            event.acceptProposedAction()
            return
        event.ignore()


class PerformanceSettingsDialog(QDialog):
    """User-facing settings for analysis accuracy, CPU budget and video decode."""

    def __init__(self, current: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Feineinstellungen · Leistung & Analyse")
        self.resize(640, 560)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        intro = QLabel(
            "Leicht, Mittel und Stark passen CPU und Parallelität an deine Hardware an. "
            "Die Analysegenauigkeit lässt sich unabhängig davon einstellen. DubSync berechnet Synchronisationen "
            "für neue Exporte frisch; frühere Analyseergebnisse werden nicht wiederverwendet."
        )
        intro.setWordWrap(True)
        intro.setObjectName("mutedText")
        layout.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.preset_combo = QComboBox()
        choice = PROFILE_LABELS[recommended_profile()]
        self.preset_combo.addItem(f"Auto – empfiehlt {choice} für diese Hardware", "system")
        self.preset_combo.addItem("Leicht – kleines CPU- und RAM-Budget", "fast")
        self.preset_combo.addItem("Mittel – ausgewogene CPU-Auslastung", "balanced")
        self.preset_combo.addItem("Stark – maximale sinnvolle Hardwareauslastung", "quality")
        self.preset_combo.addItem("Benutzerdefiniert", "custom")
        preset_index = self.preset_combo.findData(current.get("preset", "system"))
        self.preset_combo.setCurrentIndex(max(0, preset_index))
        form.addRow("Analyseprofil", self.preset_combo)

        self.cpu_threads = QSpinBox()
        self.cpu_threads.setRange(1, system_specs()["logical_cpus"])
        self.cpu_threads.setSuffix(" Threads")
        self.cpu_threads.setToolTip("Begrenzt die von OpenCV verwendeten CPU-Threads.")
        self.cpu_threads.setValue(int(current.get("cpu_threads", 4)))
        form.addRow("CPU-Limit", self.cpu_threads)

        self.probe_workers = QSpinBox()
        self.probe_workers.setRange(1, min(8, system_specs()["logical_cpus"]))
        self.probe_workers.setSuffix(" parallele Prüfungen")
        self.probe_workers.setValue(int(current.get("probe_workers", min(4, max(1, os.cpu_count() or 2)))))
        self.probe_workers.setToolTip(
            "Begrenzt gleichzeitige FFprobe-Prozesse. NAS-Freigaben werden zur Schonung automatisch auf höchstens zwei begrenzt."
        )
        form.addRow("Metadaten-Parallelität", self.probe_workers)

        self.sampling_workers_combo = QComboBox()
        self.sampling_workers_combo.addItem("1 Decoder – geringe Last, auch für NAS", 1)
        self.sampling_workers_combo.addItem("2 Decoder – lokale Videos parallel", 2)
        self.sampling_workers_combo.setCurrentIndex(
            max(0, self.sampling_workers_combo.findData(int(current.get("sampling_workers", 1))))
        )
        self.sampling_workers_combo.setToolTip(
            "Zwei Videodateien können gleichzeitig eingelesen werden, wenn CPU und RAM reichen. "
            "Auf SMB/NFS und bei parallelen GPU-Autobenchmarks wird automatisch ein Decoder verwendet."
        )
        form.addRow("Video-Einlesen", self.sampling_workers_combo)

        self.max_samples = QSpinBox()
        self.max_samples.setRange(200, 4000)
        self.max_samples.setSingleStep(100)
        self.max_samples.setSuffix(" Frames je Video")
        self.max_samples.setToolTip(
            "Begrenzt den dynamischen Frame-Abgleich: mehr Samples erhöhen die Genauigkeit, "
            "aber Speicherbedarf und Laufzeit steigen ungefähr quadratisch."
        )
        self.max_samples.setValue(int(current.get("max_samples", 1400)))
        form.addRow("Sample-Budget", self.max_samples)

        self.sample_interval = QDoubleSpinBox()
        self.sample_interval.setRange(0.5, 5.0)
        self.sample_interval.setDecimals(2)
        self.sample_interval.setSingleStep(0.25)
        self.sample_interval.setSuffix(" s")
        self.sample_interval.setToolTip("Minimaler Abstand zwischen ausgewerteten Videoframes.")
        self.sample_interval.setValue(float(current.get("sample_interval", 0.5)))
        form.addRow("Frame-Abstand", self.sample_interval)

        self.audio_bitrate = QComboBox()
        for bitrate, label in (
            (96, "96 kbit/s – besonders platzsparend"),
            (128, "128 kbit/s – empfohlen für Synchronspuren"),
            (160, "160 kbit/s – höhere Audioqualität"),
            (192, "192 kbit/s – hohe Qualität"),
            (256, "256 kbit/s – sehr hohe Qualität"),
        ):
            self.audio_bitrate.addItem(label, bitrate)
        bitrate_index = self.audio_bitrate.findData(int(current.get("audio_bitrate_kbps", 128)))
        self.audio_bitrate.setCurrentIndex(max(0, bitrate_index))
        self.audio_bitrate.setToolTip(
            "AAC-Bitrate für neu erzeugte, synchronisierte Zusatzspuren. "
            "Die vorhandenen Spuren im Referenzvideo werden unverändert übernommen."
        )
        form.addRow("Zusätzliche Audiospuren", self.audio_bitrate)

        self.hardware_combo = QComboBox()
        labels = {
            "auto": "Automatisch (Hardware versuchen, sonst CPU)",
            "none": "CPU-Decoding erzwingen",
            "ffmpeg": "FFmpeg CPU-Sampling (experimentell)",
            "cuda": "NVIDIA CUDA / NVDEC über FFmpeg",
            "vaapi": "VAAPI · Linux-GPU automatisch (AMD/Intel)",
            "amd-vaapi": "AMD Radeon · VAAPI-Rendergerät (Linux)",
            "intel-vaapi": "Intel-Grafik · integrierte GPU via VAAPI (Linux)",
            "qsv": "Intel Quick Sync (falls unterstützt)",
            "d3d11": "D3D11VA · Windows (AMD/Intel/NVIDIA)",
            "drm": "DRM (falls unterstützt)",
        }
        modes = supported_hardware_modes()
        for mode in modes:
            self.hardware_combo.addItem(labels.get(mode, mode), mode)
        index = self.hardware_combo.findData(current.get("hardware_accel", "auto"))
        self.hardware_combo.setCurrentIndex(max(0, index))
        self.hardware_combo.setToolTip(
            "Auto misst bei längeren Videos kurz GPU gegen CPU und wählt die schnellere Variante. "
            "Hardware-Decoding hängt von Treiber, FFmpeg und OpenCV-Build ab. "
            "Der Matcher selbst und die Zeitankerberechnung laufen auf der CPU; "
            "nicht verfügbare Decoder fallen auf CPU-Decoding zurück."
        )
        form.addRow("Video-Decoding", self.hardware_combo)

        self.cache_check = QCheckBox("Analyse-Cache (vorübergehend deaktiviert)")
        self.cache_check.setChecked(False)
        self.cache_check.setEnabled(False)
        self.cache_check.setToolTip("Alte Ausrichtungsdaten werden nicht verwendet, bis die Episodenidentität zusätzlich geprüft wird.")
        form.addRow("Frische Analyse", self.cache_check)

        self.frame_cache_check = QCheckBox("Bildfingerprints wiederverwenden (empfohlen)")
        self.frame_cache_check.setChecked(bool(current.get("frame_cache_enabled", True)))
        self.frame_cache_check.setToolTip(
            "Spart erneutes Video-Decoding bei wiederholten Analysen. "
            "Die Datei wird vorher erneut auf Änderungen geprüft; "
            "Episodenabgleich und Sicherheitsprüfung laufen immer vollständig neu."
        )
        form.addRow("Video-Cache", self.frame_cache_check)

        self.prewarm_check = QCheckBox("Metadaten der nächsten Episoden nach dem Scan im Hintergrund vorwärmen")
        self.prewarm_check.setChecked(bool(current.get("prewarm_metadata", True)))
        self.prewarm_check.setToolTip(
            "Lädt FFprobe-Metadaten für eine kleine Zahl kommender Episoden im Hintergrund. Bei NAS-Quellen wird langsamer und mit maximal zwei parallelen Verbindungen gearbeitet."
        )
        form.addRow("Vorladen", self.prewarm_check)
        layout.addLayout(form)

        note = QLabel(
            "Alle drei Profile analysieren mit denselben Bildproben und derselben Trefferprüfung. "
            "Leicht schont den Desktop; Stark nutzt mehr Threads und parallele lokale Zugriffe. "
            "Die verfügbaren CPU-/RAM-Budgets werden beim Start und vor Aufträgen neu ermittelt. GPU-Decoding "
            "wird pro Video gegen CPU geprüft; der Bildabgleich selbst läuft auf der CPU. "
            "Neue Zusatzspuren werden standardmäßig mit 128 kbit/s AAC erzeugt; vorhandene "
            "Referenz-Audiospuren bleiben unverändert."
        )
        note.setObjectName("mutedText")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._applying_preset = False
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        self.cpu_threads.valueChanged.connect(self._mark_custom)
        # Accuracy/audio/cache are independent of the selected resource preset.
        self.hardware_combo.currentIndexChanged.connect(self._mark_custom)
        self.probe_workers.valueChanged.connect(self._mark_custom)
        self.sampling_workers_combo.currentIndexChanged.connect(self._mark_custom)
        self.prewarm_check.toggled.connect(self._mark_custom)

    def _mark_custom(self, *_args):
        if not self._applying_preset and self.preset_combo.currentData() != "custom":
            index = self.preset_combo.findData("custom")
            if index >= 0:
                self.preset_combo.setCurrentIndex(index)

    def _apply_preset(self, _index: int):
        preset = str(self.preset_combo.currentData() or "system")
        if preset == "custom":
            return
        values = default_performance_settings() if preset == "system" else settings_for_preset(preset)
        self._applying_preset = True
        try:
            self.cpu_threads.setValue(int(values["cpu_threads"]))
            # Every built-in preset uses GPU auto-detection; a manual decoder
            # request is an advanced/custom configuration.
            hw_index = self.hardware_combo.findData("auto")
            if hw_index >= 0:
                self.hardware_combo.setCurrentIndex(hw_index)
            if "probe_workers" in values:
                self.probe_workers.setValue(int(values["probe_workers"]))
            if "sampling_workers" in values:
                workers_index = self.sampling_workers_combo.findData(int(values["sampling_workers"]))
                if workers_index >= 0:
                    self.sampling_workers_combo.setCurrentIndex(workers_index)
            if "prewarm_metadata" in values:
                self.prewarm_check.setChecked(bool(values["prewarm_metadata"]))
        finally:
            self._applying_preset = False

    def values(self) -> dict[str, Any]:
        return {
            "preset": str(self.preset_combo.currentData() or "balanced"),
            "cpu_threads": self.cpu_threads.value(),
            "max_samples": self.max_samples.value(),
            "sample_interval": self.sample_interval.value(),
            "audio_bitrate_kbps": int(self.audio_bitrate.currentData() or 128),
            "hardware_accel": str(self.hardware_combo.currentData() or "auto"),
            "cache_enabled": self.cache_check.isChecked(),
            "frame_cache_enabled": self.frame_cache_check.isChecked(),
            "probe_workers": self.probe_workers.value(),
            "sampling_workers": int(self.sampling_workers_combo.currentData() or 1),
            "prewarm_metadata": self.prewarm_check.isChecked(),
            "auto_system_profile": str(self.preset_combo.currentData() or "system") == "system",
        }


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


class SyncPreviewDialog(QDialog):
    """Choose a target-video time and an audio track for an aligned external-player sample."""
    preview_requested = Signal(object)

    def __init__(self, tracks: list[dict[str, Any]], duration: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Synchronisierte Hörprobe")
        self.setMinimumWidth(500)
        self.resize(560, 300)
        self.tracks = tracks
        self.duration = max(0.1, float(duration))
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        intro = QLabel(
            "Erzeuge eine kurze Hörprobe an mehreren Stellen der Referenz-Timeline. "
            "DubSync berechnet für Zusatzspuren die passende Quellzeit aus der aktuellen Zeitkorrektur."
        )
        intro.setWordWrap(True)
        intro.setObjectName("mutedText")
        layout.addWidget(intro)
        form = QFormLayout()
        self.track_combo = QComboBox()
        for track in tracks:
            self.track_combo.addItem(track["label"])
        self.track_combo.setAccessibleName("Audiospur für Hörprobe")
        form.addRow("Audiospur", self.track_combo)
        self.position_combo = QComboBox()
        self.position_combo.addItem("Früher Abschnitt · 15 %", 0.15)
        self.position_combo.addItem("Mitte · 50 %", 0.50)
        self.position_combo.addItem("Später Abschnitt · 85 %", 0.85)
        self.position_combo.addItem("Eigener Zeitpunkt", None)
        form.addRow("Zeitpunkt", self.position_combo)
        self.time_spin = QDoubleSpinBox()
        self.time_spin.setRange(0.0, self.duration)
        self.time_spin.setDecimals(2)
        self.time_spin.setSingleStep(15.0)
        self.time_spin.setSuffix(" s auf der Referenz-Timeline")
        self.time_spin.setAccessibleName("Zeitpunkt auf Referenz-Timeline")
        form.addRow("Position", self.time_spin)
        layout.addLayout(form)
        self.status = QLabel("Die Probe ist auf 12 Sekunden begrenzt.")
        self.status.setWordWrap(True)
        self.status.setObjectName("mutedText")
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.play_button = buttons.addButton("Hörprobe abspielen", QDialogButtonBox.ButtonRole.AcceptRole)
        self.play_button.setObjectName("primaryButton")
        self.play_button.clicked.connect(self._request_preview)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.position_combo.currentIndexChanged.connect(self._position_preset_changed)
        self._position_preset_changed(0)

    def _position_preset_changed(self, _index: int):
        fraction = self.position_combo.currentData()
        manual = fraction is None
        self.time_spin.setEnabled(manual)
        if not manual:
            self.time_spin.setValue(self.duration * float(fraction))

    def _request_preview(self):
        index = self.track_combo.currentIndex()
        if index < 0 or index >= len(self.tracks):
            return
        payload = dict(self.tracks[index])
        payload["target_time"] = self.time_spin.value()
        payload["sample_duration"] = 12.0
        self.status.setText(
            f"Referenzzeit {self.time_spin.value():.2f} s · die passende Quellposition wird anhand der Zeitkorrektur berechnet."
        )
        self.preview_requested.emit(payload)


class SourceRow(QFrame):
    masterToggled = Signal(str)
    trackChanged = Signal()
    segmentEditRequested = Signal(str)
    resetSegmentRequested = Signal(str)

    def __init__(self, path: str, info: MediaInfo, is_master: bool = False, parent=None):
        super().__init__(parent)
        self.path = path
        self.info = info
        self.manual_alignment = False
        self._last_alignment: AlignmentResult | None = None
        self._alignment_undo: list[tuple[float, float]] = []
        self._alignment_redo: list[tuple[float, float]] = []
        self._last_committed_alignment = (0.0, 1.0)
        self._history_applying = False
        self.setObjectName("sourceRow")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout.setSpacing(7)

        top = QHBoxLayout()
        self.master_radio = QRadioButton("Als Bildmaster verwenden")
        self.master_radio.setChecked(is_master)
        self.master_radio.toggled.connect(lambda active: self.masterToggled.emit(self.path) if active else None)
        top.addWidget(self.master_radio)
        top.addStretch(1)
        self.include_audio = QCheckBox("Audio hinzufügen")
        self.include_audio.setChecked(not is_master and bool(info.audio))
        self.include_audio.toggled.connect(lambda _v: self.trackChanged.emit())
        top.addWidget(self.include_audio)
        layout.addLayout(top)

        name = QLabel(Path(path).name)
        name.setObjectName("sourceName")
        name.setWordWrap(True)
        layout.addWidget(name)
        metadata = QLabel(describe_media(info))
        metadata.setObjectName("mutedText")
        metadata.setWordWrap(True)
        layout.addWidget(metadata)

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.addWidget(QLabel("Audiospur"), 0, 0)
        self.audio_combo = QComboBox()
        self.audio_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.audio_combo.setMinimumContentsLength(12)
        self.audio_combo.setAccessibleName("Audiospur auswählen")
        for ordinal, stream in enumerate(info.audio):
            lang = language_display_name(stream.language) or (stream.language.upper() if stream.language != "und" else "Sprache unbekannt")
            title = stream.title or default_track_title(stream.language, "", path) or f"Audio {ordinal + 1}"
            channels = f" · {stream.channels}ch" if stream.channels else ""
            self.audio_combo.addItem(f"{ordinal + 1}: {lang} · {title}{channels}", ordinal)
        if info.audio:
            try:
                self.audio_combo.setCurrentIndex(choose_default_audio(info))
            except MediaToolError:
                self.audio_combo.setCurrentIndex(0)
        form.addWidget(self.audio_combo, 0, 1, 1, 3)

        form.addWidget(QLabel("Sprache"), 1, 0)
        self.language_edit = QLineEdit()
        self.language_edit.setAccessibleName("Sprache der Audiospur")
        self.language_edit.setPlaceholderText("deu, eng, fra …")
        stream_language = info.audio[self.audio_combo.currentIndex()].language if info.audio else "und"
        lang = normalize_language_code(stream_language)
        if lang == "und":
            lang = normalize_language_code(infer_language(path))
        self.language_edit.setText(lang if lang != "und" else "")
        self.language_edit.setPlaceholderText("deu, eng, fra …")
        self.language_edit.setToolTip("Sprachkennung für die exportierte MKV-Spur, z. B. deu oder eng")
        form.addWidget(self.language_edit, 1, 1)
        form.addWidget(QLabel("Trackname"), 1, 2)
        self.title_edit = QLineEdit()
        self.title_edit.setAccessibleName("Name der Audiospur")
        stream_title = info.audio[self.audio_combo.currentIndex()].title if info.audio else ""
        self.title_edit.setText(default_track_title(lang, stream_title, path))
        self.title_edit.setToolTip("Anzeigename der Audiospur im Player, z. B. Deutsch oder English")
        form.addWidget(self.title_edit, 1, 3)

        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-7200.0, 7200.0)
        self.offset_spin.setDecimals(3)
        self.offset_spin.setSingleStep(0.100)
        self.offset_spin.setSuffix(" s")
        self.offset_spin.setToolTip("Zielzeit = Zeitmaßstab × Quellzeit + Offset")
        self.offset_spin.setAccessibleName(f"Zeitversatz für {Path(path).name}")
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.5, 2.0)
        self.scale_spin.setDecimals(6)
        self.scale_spin.setSingleStep(0.001)
        self.scale_spin.setValue(1.0)
        self.scale_spin.setToolTip("Zeitmaßstab; 1,0 bedeutet keine Driftkorrektur")
        self.scale_spin.setAccessibleName(f"Zeitmaßstab für {Path(path).name}")
        form.addWidget(QLabel("Zeitversatz"), 2, 0)
        form.addWidget(self.offset_spin, 2, 1)
        form.addWidget(QLabel("Zeitmaßstab"), 2, 2)
        form.addWidget(self.scale_spin, 2, 3)
        layout.addLayout(form)

        status_row = QHBoxLayout()
        self.status_label = QLabel("Noch nicht analysiert")
        self.status_label.setObjectName("statusMuted")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        self.undo_alignment_button = QPushButton("↶")
        self.undo_alignment_button.setObjectName("iconButton")
        self.undo_alignment_button.setToolTip("Letzte manuelle Zeitkorrektur rückgängig machen")
        self.undo_alignment_button.setAccessibleName(f"Zeitkorrektur für {Path(path).name} rückgängig machen")
        self.undo_alignment_button.setEnabled(False)
        self.undo_alignment_button.clicked.connect(self.undo_alignment)
        status_row.addWidget(self.undo_alignment_button, 0)
        self.redo_alignment_button = QPushButton("↷")
        self.redo_alignment_button.setObjectName("iconButton")
        self.redo_alignment_button.setToolTip("Rückgängig gemachte Zeitkorrektur wiederholen")
        self.redo_alignment_button.setAccessibleName(f"Zeitkorrektur für {Path(path).name} wiederholen")
        self.redo_alignment_button.setEnabled(False)
        self.redo_alignment_button.clicked.connect(self.redo_alignment)
        status_row.addWidget(self.redo_alignment_button, 0)
        self.reset_alignment_button = QPushButton("Auto")
        self.reset_alignment_button.setObjectName("secondaryButton")
        self.reset_alignment_button.setToolTip(
            "Manuelle Zeitkorrektur entfernen und die automatisch ermittelten Werte verwenden."
        )
        self.reset_alignment_button.setEnabled(False)
        self.reset_alignment_button.clicked.connect(self._reset_alignment)
        status_row.addWidget(self.reset_alignment_button, 0)
        self.segment_edit_button = QPushButton("Schnittbereiche …")
        self.segment_edit_button.setObjectName("secondaryButton")
        self.segment_edit_button.setToolTip(
            "Zeitbereiche der Quelle einzeln dem Master zuordnen, teilen oder deaktivieren"
        )
        self.segment_edit_button.setAccessibleName(f"Schnittbereiche für {Path(path).name} bearbeiten")
        self.segment_edit_button.setEnabled(False)
        self.segment_edit_button.clicked.connect(lambda: self.segmentEditRequested.emit(self.path))
        status_row.addWidget(self.segment_edit_button, 0)
        layout.addLayout(status_row)
        self.residual_plot = AlignmentResidualPlot(self)
        self.residual_plot.setVisible(False)
        layout.addWidget(self.residual_plot)

        self.offset_spin.valueChanged.connect(self._alignment_value_changed)
        self.scale_spin.valueChanged.connect(self._alignment_value_changed)
        self.offset_spin.editingFinished.connect(self._commit_alignment_edit)
        self.scale_spin.editingFinished.connect(self._commit_alignment_edit)
        self.audio_combo.currentIndexChanged.connect(lambda _v: self.trackChanged.emit())
        self.language_edit.textChanged.connect(lambda _v: self.trackChanged.emit())
        self.title_edit.textChanged.connect(lambda _v: self.trackChanged.emit())
        self._set_master(is_master)

    @property
    def audio_ordinal(self) -> int:
        return int(self.audio_combo.currentData() or 0)

    def is_included(self) -> bool:
        return self.include_audio.isChecked() and not self.master_radio.isChecked() and bool(self.info.audio)

    def _set_master(self, is_master: bool):
        self.master_radio.blockSignals(True)
        self.master_radio.setChecked(is_master)
        self.master_radio.blockSignals(False)
        self.include_audio.setEnabled(not is_master and bool(self.info.audio))
        if is_master:
            self.include_audio.setChecked(False)
        elif self.info.audio and not self.include_audio.isChecked():
            self.include_audio.setChecked(True)
        # The master keeps all original audio streams intact; stream editing applies
        # only to additional source files that are being imported.
        self.audio_combo.setEnabled(bool(self.info.audio) and not is_master)
        # For the master, the language/title fields relabel the selected default
        # audio stream in the output MKV; original input metadata is never changed.
        self.language_edit.setEnabled(bool(self.info.audio))
        self.title_edit.setEnabled(bool(self.info.audio))
        if is_master:
            self.audio_combo.setToolTip("Die Standard-Audiospur des Referenzvideos wird beibehalten; Sprache/Trackname unten gelten für diese Spur.")
        else:
            self.audio_combo.setToolTip("Wähle die Quell-Audiospur, die synchronisiert und hinzugefügt werden soll.")
        self.offset_spin.setEnabled(not is_master)
        self.scale_spin.setEnabled(not is_master)
        self.segment_edit_button.setEnabled(not is_master and self._last_alignment is not None)

    def _alignment_value_changed(self, *_args):
        self.manual_alignment = True
        self.reset_alignment_button.setEnabled(True)
        if self._last_alignment is not None:
            self.residual_plot.set_alignment(
                self._last_alignment, self.offset_spin.value(), self.scale_spin.value()
            )
            self.residual_plot.setVisible(True)
        self.trackChanged.emit()

    def _current_alignment_pair(self) -> tuple[float, float]:
        return (float(self.offset_spin.value()), float(self.scale_spin.value()))

    def _update_alignment_history_buttons(self):
        self.undo_alignment_button.setEnabled(bool(self._alignment_undo))
        self.redo_alignment_button.setEnabled(bool(self._alignment_redo))

    def _commit_alignment_edit(self):
        if self._history_applying:
            return
        current = self._current_alignment_pair()
        if any(abs(a-b) > 1e-9 for a, b in zip(current, self._last_committed_alignment)):
            self._alignment_undo.append(self._last_committed_alignment)
            del self._alignment_undo[:-100]
            self._alignment_redo.clear()
            self._last_committed_alignment = current
            self._update_alignment_history_buttons()

    def _apply_alignment_pair(self, pair: tuple[float, float], *, manual: bool = True):
        self._history_applying = True
        self.offset_spin.blockSignals(True)
        self.scale_spin.blockSignals(True)
        self.offset_spin.setValue(float(pair[0]))
        self.scale_spin.setValue(float(pair[1]))
        self.offset_spin.blockSignals(False)
        self.scale_spin.blockSignals(False)
        self._history_applying = False
        self._last_committed_alignment = self._current_alignment_pair()
        self.manual_alignment = manual
        self.reset_alignment_button.setEnabled(manual)
        if self._last_alignment is not None:
            self.residual_plot.set_alignment(
                self._last_alignment, self.offset_spin.value(), self.scale_spin.value()
            )
            self.residual_plot.setVisible(True)
        self._update_alignment_history_buttons()
        self.trackChanged.emit()

    def undo_alignment(self):
        self._commit_alignment_edit()
        if not self._alignment_undo:
            return
        current = self._current_alignment_pair()
        previous = self._alignment_undo.pop()
        self._alignment_redo.append(current)
        self._apply_alignment_pair(previous, manual=True)

    def redo_alignment(self):
        self._commit_alignment_edit()
        if not self._alignment_redo:
            return
        current = self._current_alignment_pair()
        following = self._alignment_redo.pop()
        self._alignment_undo.append(current)
        self._apply_alignment_pair(following, manual=True)

    def adopt_current_alignment(self):
        """Treat restored project values as the baseline, not as new user edits."""
        self._last_committed_alignment = self._current_alignment_pair()
        self._alignment_undo.clear()
        self._alignment_redo.clear()
        self._update_alignment_history_buttons()
        if self._last_alignment is not None:
            self.residual_plot.set_alignment(
                self._last_alignment, self.offset_spin.value(), self.scale_spin.value()
            )
            self.residual_plot.setVisible(True)

    def _reset_alignment(self):
        if self._last_alignment is not None and self._last_alignment.manual_segments:
            self.resetSegmentRequested.emit(self.path)
            return
        self._commit_alignment_edit()
        result = self._last_alignment
        automatic = (float(result.offset), float(result.slope)) if result is not None else (0.0, 1.0)
        current = self._current_alignment_pair()
        if any(abs(a-b) > 1e-9 for a, b in zip(current, automatic)):
            self._alignment_undo.append(current)
            del self._alignment_undo[:-100]
            self._alignment_redo.clear()
        self._apply_alignment_pair(automatic, manual=False)

    def set_alignment(self, result: AlignmentResult):
        self._last_alignment = result
        self.offset_spin.blockSignals(True)
        self.scale_spin.blockSignals(True)
        self.offset_spin.setValue(result.offset)
        self.scale_spin.setValue(result.slope)
        self.offset_spin.blockSignals(False)
        self.scale_spin.blockSignals(False)
        self.manual_alignment = False
        self._last_committed_alignment = self._current_alignment_pair()
        self._alignment_undo.clear()
        self._alignment_redo.clear()
        self.residual_plot.set_alignment(result, result.offset, result.slope)
        self.residual_plot.setVisible(True)
        self._update_alignment_history_buttons()
        status = (f"{len(result.segments)} Abschnitte manuell angepasst" if result.manual_segments else
                  f"{len(result.segments)} Abschnitte bestätigt" if result.segments
                  and result.safe_for_automatic_export else
                  "Gute Übereinstimmung" if result.safe_for_automatic_export else "Prüfung empfohlen")
        self.status_label.setText(
            f"{status} · Konfidenz {result.confidence:.0%} · {len(result.anchors)} Anker · RMS {result.rms_error:.2f}s"
        )
        self.offset_spin.setToolTip(
            "Manuelle Offsetkorrektur ersetzt die bestätigte Abschnittszuordnung durch einen einzigen Zeitversatz."
            if result.segments else "Zielzeit = Zeitmaßstab × Quellzeit + Offset"
        )
        self.scale_spin.setToolTip(
            "Manuelle Zeitmaßstabänderung ersetzt die bestätigte Abschnittszuordnung durch eine lineare Korrektur."
            if result.segments else "Zeitmaßstab; 1,0 bedeutet keine Driftkorrektur"
        )
        self.status_label.setObjectName("statusGood" if result.safe_for_automatic_export else "statusWarn")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.reset_alignment_button.setEnabled(self.manual_alignment or result.manual_segments)
        self.reset_alignment_button.setToolTip(
            "Manuelle Schnittbearbeitung entfernen und die ursprüngliche Automatik wiederherstellen."
            if result.manual_segments else
            "Manuelle Zeitkorrektur entfernen und die automatisch ermittelten Werte verwenden."
        )
        self.segment_edit_button.setEnabled(not self.master_radio.isChecked())

    def set_master_choice(self, path: str):
        self._set_master(self.path == path)


class RootLanguageDialog(QDialog):
    """Assign an explicit language and mux title to each imported source folder."""

    def __init__(self, roots: list[str], current: dict[str, dict[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sprachen und Tracknamen")
        self.resize(900, min(680, 260 + 46 * max(1, len(roots))))
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Ordne jedem Quellordner eine Sprache zu. Die Einstellung gilt für alle Videos "
            "unter diesem Ordner und wird beim Serien-Stapel für die Trackmetadaten verwendet. "
            "„Automatisch“ nutzt vorhandene Metadaten und Dateinamen."
        )
        intro.setWordWrap(True)
        intro.setObjectName("mutedText")
        layout.addWidget(intro)

        self.table = QTableWidget(len(roots), 3)
        self.table.setHorizontalHeaderLabels(["Quellordner", "Sprache", "Trackname im Player"])
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._rows = []

        from dubsync.core.scanner import infer_language
        for row_index, raw_root in enumerate(roots):
            root = str(Path(raw_root).expanduser().resolve())
            label = QLabel(root)
            label.setWordWrap(True)
            label.setToolTip(root)
            self.table.setCellWidget(row_index, 0, label)

            saved = current.get(root, {})
            detected = infer_language(root)
            default_code = saved.get("language", detected if detected != "und" else "auto")
            combo = QComboBox()
            for caption, code in LANGUAGE_CHOICES:
                combo.addItem(caption, code)
            index = combo.findData(default_code)
            combo.setCurrentIndex(index if index >= 0 else 0)

            title = QLineEdit()
            current_title = str(saved.get("title", "") or "")
            if not current_title and default_code not in ("auto", "und"):
                current_title = language_display_name(default_code)
            title.setText(current_title)
            title.setPlaceholderText("Automatisch / z. B. Deutsch")
            title.setToolTip("Dieser Titel wird in VLC/mpv als Name der Audiospur angezeigt.")
            title.setProperty("custom_title", bool(current_title and current_title != language_display_name(default_code)))
            title.textEdited.connect(lambda _text, edit=title: edit.setProperty("custom_title", True))

            def update_default_title(_index, combo=combo, edit=title):
                if not edit.property("custom_title"):
                    edit.setText(language_display_name(str(combo.currentData())) if combo.currentData() != "auto" else "")

            combo.currentIndexChanged.connect(update_default_title)
            self.table.setCellWidget(row_index, 1, combo)
            self.table.setCellWidget(row_index, 2, title)
            self._rows.append((root, combo, title))
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def rules(self) -> dict[str, dict[str, str]]:
        result = {}
        for root, combo, title in self._rows:
            language = str(combo.currentData() or "auto")
            track_title = title.text().strip()
            if language != "auto" or track_title:
                result[root] = {"language": language, "title": track_title}
        return result


class JobHistoryDialog(QDialog):
    """Read-only persistent job journal with selectable diagnostic details."""

    def __init__(self, entries: list[dict[str, Any]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("DubSync Studio · Job-Verlauf")
        self.resize(1000, 620)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(entries), 5)
        self.table.setHorizontalHeaderLabels(["Gestartet (UTC)", "Typ", "Status", "Aufgabe", "Zusammenfassung"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._entries = entries
        for row, entry in enumerate(entries):
            values = [entry.get("started_at", ""), entry.get("kind", ""),
                      entry.get("status", ""), entry.get("label", ""),
                      entry.get("summary", "")]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry.get("id", ""))
                self.table.setItem(row, col, item)
        self.table.itemSelectionChanged.connect(self._show_details)
        layout.addWidget(self.table, 2)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(180)
        self.details.setPlaceholderText("Wähle einen Job, um Quellen, Ausgaben und Fehlerdetails anzuzeigen.")
        layout.addWidget(self.details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        if entries:
            self.table.selectRow(0)

    def _show_details(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self._entries):
            self.details.clear()
            return
        entry = self._entries[row]
        details = {
            "Job-ID": entry.get("id"),
            "Status": entry.get("status"),
            "Start": entry.get("started_at"),
            "Ende": entry.get("finished_at"),
            "Quellordner": entry.get("source_roots", []),
            "Ausgabeordner": entry.get("output_directory", ""),
            "Ergebnisdetails": entry.get("details", {}),
            "Fehler": entry.get("error_text", ""),
        }
        self.details.setPlainText(json.dumps(details, ensure_ascii=False, indent=2, default=str))


class BatchQueueDialog(QDialog):
    """Explicit restart/retry controls; never execute persisted work on opening."""

    STATE_LABELS = {
        "queued": "Wartet", "running": "Läuft", "paused": "Pausiert",
        "interrupted": "Unterbrochen", "cancelled": "Abgebrochen",
        "failed": "Fehlgeschlagen", "needs_review": "Prüfung nötig",
        "completed": "Abgeschlossen",
    }
    STARTABLE = {"queued", "interrupted", "cancelled", "failed", "needs_review"}

    def __init__(self, entries: list[dict[str, Any]], *, busy: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DubSync Studio · Stapel-Warteschlange")
        self.resize(1040, 570)
        self.action: str | None = None
        self.job_id: str | None = None
        self._entries = entries
        layout = QVBoxLayout(self)
        intro = QLabel("Gespeicherte Aufträge bleiben nach einem Neustart erhalten. "
                       "Fortsetzen prüft Quellen und fertige MKVs erneut; es überschreibt nichts automatisch.")
        intro.setWordWrap(True)
        intro.setObjectName("mutedText")
        layout.addWidget(intro)
        self.table = QTableWidget(len(entries), 5)
        self.table.setAccessibleName("Gespeicherte Stapelaufträge")
        self.table.setHorizontalHeaderLabels(["Angelegt (UTC)", "Status", "Auftrag", "Fortschritt", "Letzte Meldung"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for col in (0, 1, 3):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for col in (2, 4):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        for row, entry in enumerate(entries):
            current, total = entry.get("progress_current", 0), entry.get("progress_total", 0)
            status = self.STATE_LABELS.get(entry.get("state", ""), str(entry.get("state", "")))
            values = [entry.get("created_at", ""), status, entry.get("label", ""),
                      f"{current}/{total}" if total else "—",
                      entry.get("error_text") or entry.get("progress_message", "")]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry["id"])
                item.setToolTip(str(value))
                self.table.setItem(row, col, item)
        layout.addWidget(self.table, 1)
        self.details = QLabel("Wähle einen Auftrag, um den Ressourcen- und Protokollstatus zu sehen.")
        self.details.setObjectName("mutedText")
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        actions = QHBoxLayout()
        self.start_button = QPushButton("Ausgewählten starten / fortsetzen")
        self.start_button.clicked.connect(lambda: self._finish("start"))
        self.retry_button = QPushButton("Probleme neu einplanen")
        self.retry_button.setObjectName("secondaryButton")
        self.retry_button.clicked.connect(lambda: self._finish("retry"))
        self.drain_button = QPushButton("Alle wartenden abarbeiten")
        self.drain_button.setObjectName("secondaryButton")
        self.drain_button.clicked.connect(lambda: self._finish("drain"))
        for button in (self.start_button, self.retry_button, self.drain_button):
            actions.addWidget(button)
        actions.addStretch(1)
        close = QPushButton("Schließen")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        layout.addLayout(actions)
        self._busy = busy
        self.table.itemSelectionChanged.connect(self._selection_changed)
        if entries:
            self.table.selectRow(0)
        self._selection_changed()

    def _selection_changed(self):
        row = self.table.currentRow()
        entry = self._entries[row] if 0 <= row < len(self._entries) else None
        state = entry.get("state") if entry else ""
        self.start_button.setEnabled(not self._busy and state in self.STARTABLE)
        self.retry_button.setEnabled(not self._busy and state in
                                     {"failed", "cancelled", "needs_review", "interrupted", "completed"}
                                     and bool(entry.get("journal_path")))
        self.drain_button.setEnabled(not self._busy and any(
            item.get("state") == "queued" for item in self._entries))
        self.details.setText(
            (f"{entry.get('resource_reason') or 'Ressourcen werden beim Start bestimmt.'} "
             f"{'Protokoll vorhanden.' if entry.get('journal_path') else 'Noch kein Protokoll erstellt.'} "
             "Ausgaben bleiben erhalten; neue Versuche überschreiben nichts ungefragt.")
            if entry else "Keine Aufträge. Scanne Medien und plane einen Serien-Stapel ein."
        )

    def _finish(self, action: str):
        self.action = action
        row = self.table.currentRow()
        self.job_id = self._entries[row]["id"] if 0 <= row < len(self._entries) else None
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._logger = configure_logging()
        try:
            self.history = JobHistory()
            recovered_jobs = self.history.recover_interrupted()
            if recovered_jobs:
                self._logger.warning("%d unterbrochene Jobs beim Start markiert.", recovered_jobs)
        except Exception:
            self._logger.exception("Persistenter Job-Verlauf konnte nicht initialisiert werden.")
            self.history = None
        try:
            self.batch_queue = BatchJobQueue()
            count = self.batch_queue.recover_interrupted()
            if count:
                self._logger.warning("%d Stapel können ausdrücklich fortgesetzt werden.", count)
        except Exception:
            self._logger.exception("Persistente Warteschlange konnte nicht initialisiert werden.")
            self.batch_queue = None
        self._active_queue_job_id: str | None = None
        self._queue_drain_active = False
        self._last_queue_room_refresh = 0.0
        self._active_history_job_id: str | None = None
        self.current_project_path: str | None = None
        self.project_dirty = False
        self._loading_project = False
        self._project_created_at: str | None = None
        self.setWindowTitle(f"{APP_TITLE} · {APP_VERSION}")
        self.setWindowIcon(QIcon(str(APP_LOGO)))
        self.setAcceptDrops(True)
        self.resize(1440, 900)
        self.setMinimumSize(980, 680)
        # Every launch is a fresh working session. Never restore a previous
        # library, track/master assignments or an implicitly remembered project.
        # Explicitly opened .dubsync project files still work when requested.
        self.settings = persistent_preferences(load_settings())
        self.roots: list[str] = []
        self.root_languages: dict[str, dict[str, str]] = {}
        self.track_overrides: dict[str, dict[str, Any]] = {}
        self.master_overrides: dict[str, str] = {}
        self.manual_segment_overrides: dict[tuple[str, str], dict[str, Any]] = {}
        self._track_override_timer = QTimer(self)
        self._track_override_timer.setSingleShot(True)
        self._track_override_timer.setInterval(450)
        self._track_override_timer.timeout.connect(self._persist_track_overrides)
        self.output_directory = str(self.settings.get("output_directory", Path.home() / "Videos" / "DubSync Output"))
        self.organize_output = bool(self.settings.get("organize_output", True))
        # Old releases silently skipped existing outputs, which made a rerun appear
        # to start at E04/E05. Default to an explicit fresh rebuild instead.
        self.skip_existing = False
        raw_performance = self.settings.get("performance", {})
        if isinstance(raw_performance, dict) and "audio_bitrate_kbps" not in raw_performance:
            # 0.1.3 stored a fixed "balanced" default. On upgrade, replace that
            # untouched default with the new system-aware recommendation, but keep
            # explicitly chosen fast/quality/custom settings.
            migrated = dict(raw_performance)
            old_preset = str(migrated.get("preset", "balanced"))
            if old_preset == "balanced":
                refreshed = default_performance_settings()
                if migrated.get("cache_enabled") is False:
                    refreshed["cache_enabled"] = False
                old_hw = str(migrated.get("hardware_accel", "auto"))
                if old_hw not in {"auto", "none"}:
                    refreshed["hardware_accel"] = old_hw
                raw_performance = refreshed
            else:
                migrated["audio_bitrate_kbps"] = 128
                migrated["auto_system_profile"] = False
                raw_performance = migrated
            self.settings["performance"] = dict(raw_performance)
        self.performance_settings = normalize_performance_settings(raw_performance)
        self.performance_settings["cache_enabled"] = False
        self.settings["performance"] = dict(self.performance_settings)
        self.groups: list[EpisodeGroup] = []
        self.media_cache: dict[str, MediaInfo] = {}
        self.alignments: dict[tuple[str, str], AlignmentResult] = {}
        self.automatic_alignments: dict[tuple[str, str], AlignmentResult] = {}
        # A private temporary database prevents even accidental reuse of an old
        # on-disk alignment cache. It disappears when the application exits.
        self._session_cache_dir = tempfile.TemporaryDirectory(prefix="dubsync-session-")
        self.cache = AlignmentCache(Path(self._session_cache_dir.name) / "alignments.sqlite3")
        self.source_rows: dict[str, SourceRow] = {}
        self.selected_group: EpisodeGroup | None = None
        self.selected_master: str | None = None
        self.worker: TaskWorker | None = None
        self._pending_success: Callable | None = None
        self._current_job_is_alignment = False
        self._shown_group_key: str | None = None
        self._job_started_at = 0.0
        self._progress_phase_key = ""
        self._progress_phase_started_at = 0.0
        self._progress_phase_start_fraction = 0.0
        self._progress_last_fraction: float | None = None
        self._prewarm_worker: TaskWorker | None = None
        self._prewarming_paths: set[str] = set()
        self._prewarm_refresh_pending = False
        self._preview_directory = Path(tempfile.mkdtemp(prefix="dubsync-studio-preview-"))
        self._preview_dialog: SyncPreviewDialog | None = None
        try:
            os.chmod(self._preview_directory, 0o700)
        except OSError:
            pass

        self._build_ui()
        self._refresh_roots()
        self._update_tool_status()
        self._apply_performance_settings(log_change=False)
        self._save_settings()
        self.statusBar().showMessage(
            "Neue Sitzung · Medienordner hinzufügen und Bibliothek frisch scannen", 12000
        )

    def _build_ui(self):
        self._build_menus()
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 14, 20, 10)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(14)
        logo = QLabel()
        logo.setObjectName("brandLogo")
        logo.setPixmap(QIcon(str(APP_LOGO)).pixmap(QSize(34, 34)))
        logo.setFixedSize(38, 38)
        logo.setAccessibleName("DubSync Studio Logo")
        header.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
        brand = QVBoxLayout()
        brand.setSpacing(3)
        title = QLabel("DubSync Studio")
        title.setObjectName("brandTitle")
        title.setTextFormat(Qt.TextFormat.RichText)
        subtitle = QLabel("BILD BEHALTEN. SPRACHEN VERBINDEN.")
        subtitle.setObjectName("mutedText")
        self.brand_subtitle = subtitle
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch(1)
        self.tool_badge = QLabel("Prüfe Medienwerkzeuge …")
        self.tool_badge.setObjectName("statusChip")
        self.tool_badge.setAccessibleName("Status der Medienwerkzeuge")
        header.addWidget(self.tool_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.release_badge = QLabel("2.0 · BETA")
        self.release_badge.setObjectName("releaseBadge")
        self.release_badge.setToolTip("Vorabversion: automatische Regressionen geprüft; NAS und reale GPU-/Desktop-Systeme bleiben Release-Gates.")
        header.addWidget(self.release_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.session_badge = QLabel("NEUE SITZUNG")
        self.session_badge.setObjectName("statusChip")
        self.session_badge.setToolTip(
            "Bei jedem Programmstart werden Quellen, Episodengruppen und "
            "Zeitkorrekturen neu begonnen. Nur explizit geöffnete Projekte "
            "laden gespeicherte Zuordnungen."
        )
        header.addWidget(self.session_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(header)

        # Workflow controls are grouped into compact panels rather than one overcrowded row.
        source_strip = QFrame()
        source_strip.setObjectName("workflowStrip")
        source_strip.setAccessibleName("Arbeitsablauf Schritt 1: Quellen auswählen")
        source_bar = QHBoxLayout(source_strip)
        source_bar.setContentsMargins(12, 8, 12, 8)
        source_bar.setSpacing(8)
        source_step = QLabel("QUELLEN")
        source_step.setObjectName("workflowStep")
        source_step.setMinimumWidth(96)
        source_bar.addWidget(source_step)

        self.add_folder_button = QPushButton("Ordner hinzufügen")
        self.add_folder_button.setObjectName("primaryButton")
        self.add_folder_button.clicked.connect(self.add_folder)
        self.add_folder_button.setAccessibleName("Lokalen oder eingehängten Medienordner hinzufügen")
        source_bar.addWidget(self.add_folder_button)
        self.network_button = QPushButton("NAS / Netzwerk …")
        self.network_button.setObjectName("secondaryButton")
        self.network_button.setToolTip("SMB-/SFTP-/WebDAV-Freigabe im Dateimanager verbinden oder einen vorhandenen NAS-Mount auswählen.")
        self.network_button.setAccessibleName("NAS- oder Netzwerkquelle hinzufügen")
        self.network_button.clicked.connect(self.add_network_source)
        source_bar.addWidget(self.network_button)
        self.remove_folder_button = QPushButton("Entfernen")
        self.remove_folder_button.setToolTip("Ausgewählte Quelle aus dem Projekt entfernen; Dateien bleiben unverändert.")
        self.remove_folder_button.clicked.connect(self.remove_folder)
        source_bar.addWidget(self.remove_folder_button)
        source_bar.addStretch(1)
        self.scan_button = QPushButton("Bibliothek scannen")
        self.scan_button.setObjectName("secondaryButton")
        self.scan_button.setToolTip("F5 · Ordner nach Episoden und Sprachfassungen durchsuchen.")
        self.scan_button.clicked.connect(self.scan_library)
        self.scan_button.setAccessibleName("Bibliothek scannen")
        source_bar.addWidget(self.scan_button)
        root.addWidget(source_strip)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        analysis_strip = QFrame()
        analysis_strip.setObjectName("workflowStrip")
        analysis_bar = QHBoxLayout(analysis_strip)
        analysis_bar.setContentsMargins(12, 8, 12, 8)
        analysis_bar.setSpacing(8)
        analysis_step = QLabel("ABGLEICH")
        analysis_step.setObjectName("workflowStep")
        analysis_bar.addWidget(analysis_step)
        self.analyze_button = QPushButton("Analyse starten")
        self.analyze_button.setObjectName("secondaryButton")
        self.analyze_button.clicked.connect(self.analyze_selected)
        self.analyze_button.setAccessibleName("Synchronisationsanalyse für ausgewählte Episode starten")
        self.analyze_button.setToolTip("Ermittelt Bildanker, Startversatz, Drift und Konfidenz.")
        analysis_bar.addWidget(self.analyze_button)
        self.diagnostic_button = QPushButton("Diagnoseberichte")
        self.diagnostic_button.setToolTip("Ctrl+D · Bildabgleiche verstehen und Berichte kopieren")
        self.diagnostic_button.setAccessibleName("Bildabgleich-Diagnosen öffnen")
        self.diagnostic_button.clicked.connect(self._show_alignment_diagnostic)
        analysis_bar.addWidget(self.diagnostic_button)
        self.batch_diagnostic_button = QPushButton("Stapelprotokoll")
        self.batch_diagnostic_button.setToolTip("Gesamten Stapellauf mit ALLEN Episoden und Analyseversuchen anzeigen · Strg+Umschalt+D")
        self.batch_diagnostic_button.clicked.connect(self._show_batch_diagnostic)
        analysis_bar.addWidget(self.batch_diagnostic_button)
        self.preview_button = QPushButton("Hörprobe")
        self.preview_button.setToolTip("Synchronisierte Audiospur an frühen, mittleren oder späten Zeitpunkten anhören.")
        self.preview_button.setAccessibleName("Synchronisierte Hörprobe öffnen")
        self.preview_button.clicked.connect(self.preview_sync_audio)
        self.preview_button.setEnabled(False)
        analysis_bar.addWidget(self.preview_button)
        analysis_bar.addStretch(1)
        action_row.addWidget(analysis_strip, 1)

        export_strip = QFrame()
        export_strip.setObjectName("workflowStrip")
        export_bar = QHBoxLayout(export_strip)
        export_bar.setContentsMargins(12, 8, 12, 8)
        export_bar.setSpacing(8)
        export_step = QLabel("EPISODE")
        export_step.setObjectName("workflowStep")
        export_bar.addWidget(export_step)
        self.export_button = QPushButton("MKV erstellen")
        self.export_button.setObjectName("primaryButton")
        self.export_button.setToolTip("Nur die gewählte Episode als mehrsprachige MKV erstellen.")
        self.export_button.clicked.connect(self.export_selected)
        export_bar.addWidget(self.export_button)
        self.batch_button = QPushButton("Serien-Stapel …")
        self.batch_button.setToolTip("Mehrere Episoden verarbeiten; unsichere Folgen werden zur Prüfung markiert.")
        self.batch_button.clicked.connect(self.export_batch)
        export_bar.addWidget(self.batch_button)
        action_row.addWidget(export_strip, 1)
        root.addLayout(action_row)

        # An in-context explanation prevents a cancelled job being confused with
        # an unsafe alignment. The details button opens the exact support reports.
        self.insight_panel = QFrame()
        self.insight_panel.setObjectName("analysisInsight")
        insight_layout = QHBoxLayout(self.insight_panel)
        insight_layout.setContentsMargins(14, 10, 12, 10)
        insight_layout.setSpacing(12)
        insight_content = QVBoxLayout()
        insight_content.setSpacing(3)
        self.insight_title = QLabel("")
        self.insight_title.setObjectName("insightTitle")
        self.insight_text = QLabel("")
        self.insight_text.setWordWrap(True)
        self.insight_text.setObjectName("insightText")
        insight_content.addWidget(self.insight_title)
        insight_content.addWidget(self.insight_text)
        insight_layout.addLayout(insight_content, 1)
        insight_reports = QPushButton("Bericht ansehen")
        insight_reports.setToolTip("Detaillierte Diagnose dieser und vorheriger Analysen")
        insight_reports.clicked.connect(self._show_alignment_diagnostic)
        insight_layout.addWidget(insight_reports)
        insight_close = QPushButton("×")
        insight_close.setObjectName("insightDismiss")
        insight_close.setAccessibleName("Hinweis schließen")
        insight_close.clicked.connect(self.insight_panel.hide)
        insight_layout.addWidget(insight_close)
        self.insight_panel.hide()
        root.addWidget(self.insight_panel)

        output_panel = QFrame()
        output_panel.setObjectName("outputPanel")
        output_panel.setAccessibleName("Ausgabe und Stapeloptionen")
        output_layout = QVBoxLayout(output_panel)
        output_layout.setContentsMargins(12, 9, 12, 9)
        output_layout.setSpacing(7)
        output_heading_row = QHBoxLayout()
        output_heading = QLabel("AUSGABE-ZIEL")
        output_heading.setObjectName("outputHeading")
        output_heading_row.addWidget(output_heading)
        output_heading_row.addStretch(1)
        output_heading_row.addWidget(QLabel("Lokaler Ordner oder beschreibbarer NAS-Mount"))
        output_layout.addLayout(output_heading_row)

        output_path_row = QHBoxLayout()
        output_path_row.addWidget(QLabel("Ordner"))
        self.output_dir_edit = QLineEdit(self.output_directory)
        self.output_dir_edit.setPlaceholderText("Zielordner für Serie / Staffel / Episoden")
        self.output_dir_edit.setToolTip("Fertige MKVs werden in dieser Zielstruktur organisiert.")
        self.output_dir_edit.setAccessibleName("Ausgabeordner für fertige MKV-Dateien")
        self.output_dir_edit.editingFinished.connect(self._save_settings)
        self.output_dir_edit.textChanged.connect(self._update_output_preview)
        output_path_row.addWidget(self.output_dir_edit, 1)
        self.output_browse_button = QPushButton("Durchsuchen …")
        self.output_browse_button.clicked.connect(self.browse_output_directory)
        output_path_row.addWidget(self.output_browse_button)
        output_layout.addLayout(output_path_row)

        output_options = QHBoxLayout()
        self.organize_output_check = QCheckBox("Serie / Staffel organisieren")
        self.organize_output_check.setChecked(self.organize_output)
        self.organize_output_check.setToolTip("Erstellt Unterordner wie Serienname/Staffel 01.")
        self.organize_output_check.toggled.connect(self._output_options_changed)
        output_options.addWidget(self.organize_output_check)
        self.skip_existing_check = QCheckBox("Vorhandene Ausgaben überspringen")
        self.skip_existing_check.setChecked(self.skip_existing)
        self.skip_existing_check.setToolTip("Ausgeschaltet: DubSync berechnet die Episoden neu und fragt vor dem Ersetzen vorhandener MKVs nach.")
        self.skip_existing_check.toggled.connect(self._output_options_changed)
        output_options.addWidget(self.skip_existing_check)
        self._resume_journal_path: str | None = None
        self.resume_batch_button = QPushButton("Stapel fortsetzen …")
        self.resume_batch_button.setObjectName("secondaryButton")
        self.resume_batch_button.setToolTip(
            "Ein früheres Stapelprotokoll auswählen. Nur erneut verifizierte Exporte werden übersprungen."
        )
        self.resume_batch_button.clicked.connect(self._choose_resume_journal)
        output_options.addWidget(self.resume_batch_button)
        output_options.addStretch(1)
        self.root_languages_button = QPushButton("Sprachen / Tracknamen …")
        self.root_languages_button.setToolTip("Je Quellordner eine Sprache und einen sichtbaren MKV-Tracknamen festlegen.")
        self.root_languages_button.clicked.connect(self.configure_root_languages)
        output_options.addWidget(self.root_languages_button)
        output_layout.addLayout(output_options)

        self.output_preview_label = QLabel("Ausgabepfad-Vorschau erscheint nach dem Bibliotheksscan.")
        self.output_preview_label.setObjectName("pathPreview")
        self.output_preview_label.setWordWrap(True)
        self.output_preview_label.setAccessibleName("Vorschau des geplanten Ausgabepfades")
        output_layout.addWidget(self.output_preview_label)
        self.resume_batch_hint = QLabel("Fortsetzen: kein Stapelprotokoll ausgewählt")
        self.resume_batch_hint.setObjectName("statusMuted")
        self.resume_batch_hint.setWordWrap(True)
        output_layout.addWidget(self.resume_batch_hint)
        root.addWidget(output_panel)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QFrame()
        left.setObjectName("panel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)
        left_layout.addWidget(self._section_title("Episoden", "Fassungen rechts abgleichen."))
        self.root_hint = QLabel("Ordner oder Videodateien hier hineinziehen · NAS über die Quellenleiste hinzufügen")
        self.root_hint.setObjectName("dropHint")
        self.root_hint.setWordWrap(True)
        self.root_hint.setAccessibleName("Hilfe zum Hinzufügen von Medienquellen")
        left_layout.addWidget(self.root_hint)
        self.root_list = SourceDropList()
        self.root_list.setMaximumHeight(118)
        self.root_list.setObjectName("rootList")
        self.root_list.setAccessibleName("Quellordner und Netzwerklaufwerke")
        self.root_list.setAccessibleDescription("Liste aller lokalen oder eingehängten NAS-Quellen. Ziehe Ordner oder Videodateien hier hinein, um Quellen hinzuzufügen.")
        self.root_list.pathsDropped.connect(self._handle_dropped_paths)
        self.root_list.itemDoubleClicked.connect(self._open_source_location)
        self.root_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.root_list.customContextMenuRequested.connect(self._source_context_menu)
        left_layout.addWidget(self.root_list)
        self.episode_count = QLabel("Noch nicht gescannt")
        self.episode_count.setObjectName("mutedText")
        left_layout.addWidget(self.episode_count)

        filter_row = QHBoxLayout()
        self.episode_search = QLineEdit()
        self.episode_search.setPlaceholderText("Folge, Serie oder Dateiname suchen …")
        self.episode_search.setAccessibleName("Serienbibliothek durchsuchen")
        self.episode_search.setClearButtonEnabled(True)
        self.episode_search.setToolTip("Filtert die Bibliothek ohne einen neuen Scan auszulösen.")
        self.episode_filter_timer = QTimer(self)
        self.episode_filter_timer.setSingleShot(True)
        self.episode_filter_timer.setInterval(120)
        self.episode_filter_timer.timeout.connect(self._filter_episodes)
        self.episode_search.textChanged.connect(lambda _text: self.episode_filter_timer.start())
        self.episode_search.textChanged.connect(self._search_cleared)
        filter_row.addWidget(self.episode_search, 1)
        self.episode_filter = QComboBox()
        self.episode_filter.addItem("Alle", "all")
        self.episode_filter.addItem("Mehrsprachig", "multi")
        self.episode_filter.addItem("Prüfen", "review")
        self.episode_filter.setToolTip("Zeigt nur passende Episodengruppen an.")
        self.episode_filter.currentIndexChanged.connect(self._filter_episodes)
        filter_row.addWidget(self.episode_filter)
        left_layout.addLayout(filter_row)

        self.episode_table = QTableWidget(0, 4)
        self.episode_table.setHorizontalHeaderLabels(["Episode / Gruppe", "Dateien", "Sprachen", "Status"])
        self.episode_table.setAccessibleName("Erkannte Serien und Episoden")
        self.episode_table.setAccessibleDescription("Wähle eine Episode, um Bildmaster, Audiospuren und Synchronisationsqualität zu konfigurieren.")
        self.episode_table.verticalHeader().setVisible(False)
        self.episode_table.verticalHeader().setDefaultSectionSize(44)
        self.episode_table.horizontalHeader().setMinimumHeight(38)
        self.episode_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.episode_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.episode_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.episode_table.setShowGrid(False)
        self.episode_table.setAlternatingRowColors(False)
        self.episode_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.episode_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.episode_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.episode_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.episode_table.itemSelectionChanged.connect(self._on_episode_selected)
        left_layout.addWidget(self.episode_table, 1)
        splitter.addWidget(left)

        right = QFrame()
        right.setObjectName("panel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(10)
        self.detail_heading = QLabel("Episode einrichten")
        self.detail_heading.setObjectName("sectionTitle")
        right_layout.addWidget(self.detail_heading)
        self.detail_description = QLabel("Wähle links eine Gruppe aus. Bestimme anschließend das Referenzvideo und die hinzuzufügenden Audiospuren.")
        self.detail_description.setObjectName("mutedText")
        self.detail_description.setWordWrap(True)
        self.detail_description.setAccessibleName("Informationen zur ausgewählten Episode")
        right_layout.addWidget(self.detail_description)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_content = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_content)
        self.detail_layout.setContentsMargins(2, 2, 2, 2)
        self.detail_layout.setSpacing(10)
        self.detail_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.detail_empty = QLabel("Dein Schnittplatz ist bereit.\n\nFüge die Ordner deiner Sprachfassungen hinzu\nund starte den Bibliotheksscan.\n\nHier prüfst du anschließend Bildmaster, Tonspuren\nund den ermittelten Zeitversatz.")
        self.detail_empty.setObjectName("emptyState")
        self.detail_empty.setWordWrap(True)
        self.detail_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_layout.addStretch(1)
        self.detail_layout.addWidget(self.detail_empty)
        self.detail_layout.addStretch(1)
        self.detail_scroll.setWidget(self.detail_content)
        right_layout.addWidget(self.detail_scroll, 1)
        splitter.addWidget(right)
        splitter.setSizes([610, 740])
        root.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFixedWidth(180)
        bottom.addWidget(self.progress)
        self.activity_label = QLabel("Bereit")
        self.activity_label.setObjectName("mutedText")
        self.activity_label.setMinimumWidth(0)
        self.activity_label.setWordWrap(True)
        bottom.addWidget(self.activity_label, 1)
        self.progress_plot = ProgressHistoryPlot()  # retained for compatibility; intentionally not shown
        self.pause_button = QPushButton("Pause")
        self.pause_button.setEnabled(False)
        self.pause_button.setToolTip("Verarbeitung am nächsten sicheren Fortschrittspunkt anhalten")
        self.pause_button.clicked.connect(self.toggle_pause_current_job)
        bottom.addWidget(self.pause_button)
        self.log_toggle_button = QPushButton("Protokoll")
        self.log_toggle_button.setCheckable(True)
        self.log_toggle_button.setChecked(False)
        self.log_toggle_button.setToolTip("Technische Details und übersprungene Episoden ein-/ausblenden")
        self.log_toggle_button.toggled.connect(self._toggle_log_view)
        bottom.addWidget(self.log_toggle_button)
        self.cancel_button = QPushButton("Abbrechen")
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setToolTip("Verarbeitung bewusst stoppen; im Stapel erfolgt eine Sicherheitsrückfrage.")
        # Never make cancellation the implicit Return/Escape action in dialogs.
        # Keyboard users can still focus it explicitly with Tab.
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setDefault(False)
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_current_job)
        bottom.addWidget(self.cancel_button)
        root.addLayout(bottom)
        self.progress_detail = QLabel("Keine Verarbeitung aktiv")
        self.progress_detail.setObjectName("progressDetail")
        self.progress_detail.setWordWrap(True)
        self.progress_detail.setAccessibleName("Aktueller Verarbeitungsschritt")
        root.addWidget(self.progress_detail)

        self.log_view = QTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(3000)
        self.log_view.setMaximumHeight(105)
        self.log_view.setPlaceholderText("Analyseberichte, Warnungen und übersprungene Episoden erscheinen hier.")
        self.log_view.hide()
        root.addWidget(self.log_view)

        self.setStatusBar(QStatusBar())
        self._build_workspace_shell(
            root, source_strip, action_row, output_panel, splitter, bottom
        )
        self.setStyleSheet(self._stylesheet())
        self._set_controls_enabled(True)
        self._refresh_workflow_summary()

    def _build_workspace_shell(self, root, source_strip, action_row, output_panel, splitter, bottom):
        """Rehome existing, battle-tested controls into a clean three-screen workflow.

        Widgets retain their instances/signals so the episode identity rescue,
        export safeguards and batch report code are not rewritten by the UI.
        """
        for widget in (source_strip, self.insight_panel, output_panel, splitter,
                       self.progress_detail, self.log_view):
            root.removeWidget(widget)
        root.removeItem(action_row)
        root.removeItem(bottom)

        self._page_stack = QStackedWidget()
        self._page_stack.setObjectName("workspacePages")
        shell = QVBoxLayout()
        shell.setSpacing(12)
        navigation = QFrame()
        navigation.setObjectName("navigation")
        nav = QHBoxLayout(navigation)
        nav.setContentsMargins(4, 4, 12, 4)
        nav.setSpacing(4)
        self._nav_buttons = []
        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        for index, label in enumerate(("Studio", "Stapelzentrale", "Aktivität")):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setMinimumWidth(130)
            button.setMinimumHeight(34)
            button.setAccessibleName(label)
            button.setShortcut(f"Alt+{index + 1}")
            button.setToolTip(f"{label} · Alt+{index + 1}")
            self.navigation_group.addButton(button, index)
            button.clicked.connect(lambda _checked=False, page=index: self._navigate_to(page))
            nav.addWidget(button)
            self._nav_buttons.append(button)
        nav.addStretch(1)
        self.sidebar_state = QLabel("Neue Sitzung")
        self.sidebar_state.setObjectName("mutedText")
        self.sidebar_state.setWordWrap(False)
        nav.addWidget(self.sidebar_state)
        shell.addWidget(navigation)

        # Sources span the workspace; episode list and inspector get the full width.
        left_layout = splitter.widget(0).layout()
        left_layout.removeWidget(self.root_hint)
        left_layout.removeWidget(self.root_list)
        self.root_hint.setText("Medienordner hier ablegen · oder Ordner / NAS hinzufügen")
        self.root_list.setMaximumHeight(72)
        self.root_list.setMinimumHeight(56)
        source_panel = QFrame()
        self.sources_panel = source_panel
        source_panel.setObjectName("sourcePanel")
        source_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        source_layout = QVBoxLayout(source_panel)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(0)
        source_strip.setFixedHeight(54)
        source_layout.addWidget(source_strip)
        source_layout.addWidget(self.root_hint)
        source_layout.addWidget(self.root_list)
        self.sources_toggle = QPushButton("Quellen anzeigen")
        self.sources_toggle.setCheckable(True)
        self.sources_toggle.setChecked(True)
        self.sources_toggle.setToolTip("Quellenliste ein- oder ausblenden; Dateien bleiben unverändert")
        self.sources_toggle.toggled.connect(self._toggle_sources)
        source_strip.layout().insertWidget(1, self.sources_toggle)
        splitter.setHandleWidth(8)
        splitter.widget(0).setMinimumWidth(232)
        splitter.widget(1).setMinimumWidth(300)

        self.library_empty = QLabel("Noch keine Episoden\nFüge Quellen hinzu und wähle Bibliothek scannen.")
        self.library_empty.setObjectName("emptyState")
        self.library_empty.setWordWrap(True)
        self.library_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.episode_surface = QStackedWidget()
        left_layout.removeWidget(self.episode_table)
        self.episode_surface.addWidget(self.library_empty)
        self.episode_surface.addWidget(self.episode_table)
        left_layout.addWidget(self.episode_surface, 1)

        # The domain engine and the existing track editor stay authoritative.
        # This is a read-only, on-demand visual inspection surface between them.
        self.review_panel = StudioReviewPanel()
        self.review_panel.audioRequested.connect(self.preview_sync_audio)
        self.review_panel.sourceChanged.connect(self._review_source_changed)
        review_scroll = QScrollArea()
        review_scroll.setObjectName("reviewViewport")
        review_scroll.setFrameShape(QFrame.Shape.NoFrame)
        review_scroll.setWidgetResizable(True)
        review_scroll.setWidget(self.review_panel)
        review_scroll.setMinimumWidth(290)
        splitter.insertWidget(1, review_scroll)
        splitter.setSizes([310, 435, 515])

        # Page 1: real three-pane review desk, one episode at a time.
        library = QWidget()
        lib = QVBoxLayout(library)
        lib.setContentsMargins(4, 2, 3, 2)
        lib.setSpacing(11)
        self.library_heading = self._page_heading(
            "Studio", "Episode wählen · Bildanker prüfen · Sprachspuren konfigurieren."
        )
        lib.addWidget(self.library_heading)
        lib.addWidget(source_panel)
        lib.addWidget(splitter, 1)
        for button in (self.diagnostic_button, self.batch_diagnostic_button):
            button.parentWidget().layout().removeWidget(button)
            button.hide()
        lib.addLayout(action_row)
        continue_row = QHBoxLayout()
        self.library_status = QLabel("Noch keine Quellen hinzugefügt")
        self.library_status.setObjectName("mutedText")
        self.library_status.setWordWrap(True)
        continue_row.addWidget(self.library_status, 1)
        self.to_export_button = QPushButton("Stapel vorbereiten →")
        self.to_export_button.setObjectName("primaryButton")
        self.to_export_button.clicked.connect(lambda: self._navigate_to(1))
        self.export_button.parentWidget().layout().addWidget(self.to_export_button)
        lib.addLayout(continue_row)
        self.episode_surface.setMinimumHeight(110)
        self._page_stack.addWidget(library)

        # Page 2: batch setup and persistent control room.
        export_page = QWidget()
        ex = QVBoxLayout(export_page)
        ex.setContentsMargins(4, 2, 3, 4)
        ex.setSpacing(14)
        ex.addWidget(self._page_heading(
            "Stapelzentrale", "Aufträge kontrollieren, fortsetzen oder prüfen · unten Ziel und Leistungsprofil festlegen."
        ))
        summary_card = QFrame()
        summary_card.setObjectName("heroCard")
        sc = QVBoxLayout(summary_card)
        sc.setContentsMargins(15, 10, 15, 10)
        sc.setSpacing(5)
        sc.addWidget(self._sidebar_caption("DEIN STAPEL"))
        self.export_overview_label = QLabel("Noch keine Episoden erkannt")
        self.export_overview_label.setObjectName("heroNumber")
        sc.addWidget(self.export_overview_label)
        self.export_overview_detail = QLabel("Bitte zuerst zwei Medienordner hinzufügen und scannen.")
        self.export_overview_detail.setWordWrap(True)
        self.export_overview_detail.setObjectName("mutedText")
        sc.addWidget(self.export_overview_detail)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        export_bar = self.export_button.parentWidget().layout()  # created in original export strip
        export_bar.removeWidget(self.batch_button)
        self.batch_button.setParent(None)
        self.batch_button.setText("Gesamten Stapel starten  →")
        self.batch_button.setObjectName("primaryButton")
        self.batch_button.setMinimumHeight(44)
        self.queue_button = QPushButton("Queue-Details …")
        self.queue_button.setObjectName("secondaryButton")
        self.queue_button.setMinimumHeight(44)
        self.queue_button.setToolTip("Gespeicherte Serien-Stapel starten, nach Neustart fortsetzen oder problematische Folgen erneut einplanen.")
        self.queue_button.clicked.connect(self._show_batch_queue)
        button_row.addWidget(self.queue_button)
        button_row.addWidget(self.batch_button)
        sc.addLayout(button_row)
        ex.addWidget(summary_card)
        ex.addWidget(output_panel)
        perf_card = QFrame()
        perf_card.setObjectName("outputPanel")
        perf = QVBoxLayout(perf_card)
        perf.setContentsMargins(18, 15, 18, 15)
        perf.setSpacing(9)
        perf.addWidget(self._sidebar_caption("LEISTUNG · RESSOURCEN"))
        profile_line = QHBoxLayout()
        profile_line.addWidget(QLabel("Profil"))
        self.profile_quick_combo = QComboBox()
        self.profile_quick_combo.setAccessibleName("Leistungsprofil auswählen")
        for label, value in ((f"Auto (Empfehlung: {PROFILE_LABELS[recommended_profile()]})", "system"),
                             ("Leicht", "fast"), ("Mittel", "balanced"),
                             ("Stark", "quality"), ("Benutzerdefiniert", "custom")):
            self.profile_quick_combo.addItem(label, value)
        index = self.profile_quick_combo.findData(self.performance_settings["preset"])
        self.profile_quick_combo.setCurrentIndex(max(0, index))
        self.profile_quick_combo.currentIndexChanged.connect(self._quick_profile_changed)
        profile_line.addWidget(self.profile_quick_combo, 1)
        fine = QPushButton("Anpassen …")
        fine.clicked.connect(self._open_settings)
        profile_line.addWidget(fine)
        perf.addLayout(profile_line)
        self.profile_quick_summary = QLabel("")
        self.profile_quick_summary.setWordWrap(True)
        self.profile_quick_summary.setObjectName("mutedText")
        perf.addWidget(self.profile_quick_summary)
        self.profile_tradeoff = QLabel("")
        self.profile_tradeoff.setWordWrap(True)
        self.profile_tradeoff.setObjectName("pathPreview")
        perf.addWidget(self.profile_tradeoff)
        ex.addWidget(perf_card)
        self.queue_room = QueueControlRoom()
        self.queue_room.startRequested.connect(self._start_inline_queued_batch)
        self.queue_room.retryRequested.connect(self._retry_inline_queue)
        self.queue_room.drainRequested.connect(self._drain_inline_queue)
        self.queue_room.refreshRequested.connect(self._refresh_inline_queue)
        # Operations-first: persisted jobs are visible before the long settings
        # form; setup stays below in the same independently scrolling page.
        ex.insertWidget(1, self.queue_room, 1)
        export_scroll = QScrollArea()
        export_scroll.setObjectName("pageScroll")
        export_scroll.setWidgetResizable(True)
        export_scroll.setFrameShape(QFrame.Shape.NoFrame)
        export_scroll.setWidget(export_page)
        self._page_stack.addWidget(export_scroll)

        # Page 3: readable execution log, full batch reports and history.
        activity = QWidget()
        act = QVBoxLayout(activity)
        act.setContentsMargins(4, 2, 3, 4)
        act.setSpacing(12)
        act.addWidget(self._page_heading(
            "Aktivität & Diagnose", "Jede Episode, jeder Skip-Grund und alle Analyseversuche bleiben nachvollziehbar."
        ))
        report_row = QHBoxLayout()
        self.batch_diagnostic_button.setText("Stapel-Gesamtprotokoll öffnen")
        self.batch_diagnostic_button.setObjectName("primaryButton")
        self.batch_diagnostic_button.show()
        report_row.addWidget(self.batch_diagnostic_button)
        self.diagnostic_button.setText("Bildabgleich-Diagnose")
        self.diagnostic_button.show()
        report_row.addWidget(self.diagnostic_button)
        history = QPushButton("Job-Verlauf")
        history.clicked.connect(self._show_job_history)
        report_row.addWidget(history)
        report_row.addStretch(1)
        act.addLayout(report_row)
        self.activity_tip = QLabel(
            "Hier erscheint der technische Verlauf. Für Hilfe zum Überspringen einer Folge "
            "öffne das vollständige Stapelprotokoll – nicht nur die letzte Einzelanalyse."
        )
        self.activity_tip.setObjectName("mutedText")
        self.activity_tip.setWordWrap(True)
        act.addWidget(self.activity_tip)
        self.log_view.setMaximumHeight(16777215)
        self.log_view.setVisible(True)
        act.addWidget(self.log_view, 1)
        self._page_stack.addWidget(activity)
        shell.addWidget(self._page_stack, 1)
        root.addLayout(shell, 1)
        root.addWidget(self.insight_panel)

        # Always-visible, small job dock – cancel cannot accidentally be default.
        dock = QFrame()
        dock.setObjectName("jobDock")
        job = QVBoxLayout(dock)
        job.setContentsMargins(13, 8, 13, 8)
        job.setSpacing(3)
        job.addLayout(bottom)
        job.addWidget(self.progress_detail)
        root.addWidget(dock)
        self.log_toggle_button.setCheckable(False)
        try:
            self.log_toggle_button.toggled.disconnect(self._toggle_log_view)
        except (TypeError, RuntimeError):
            pass
        self.log_toggle_button.setText("Aktivität anzeigen")
        self.log_toggle_button.clicked.connect(lambda: self._navigate_to(2))
        self._navigate_to(0)
        self._update_quick_profile_summary()
        self._refresh_inline_queue()

    def _toggle_sources(self, expanded: bool):
        self.root_list.setVisible(expanded and bool(self.roots))
        self.sources_panel.setFixedHeight(126 if expanded and self.roots else 54 if self.roots else 88)
        self.sources_toggle.setText(f"{len(self.roots)} Quellen {'▾' if expanded else '▸'}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "library_heading"):
            compact = self.height() < 950
            self.library_heading.setVisible(not compact)
            self.brand_subtitle.setVisible(not compact)

    @staticmethod
    def _sidebar_caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("eyebrow")
        return label

    @staticmethod
    def _page_heading(title: str, subtitle: str) -> QWidget:
        frame = QFrame()
        frame.setObjectName("pageHeader")
        content = QVBoxLayout(frame)
        content.setContentsMargins(10, 4, 8, 6)
        content.setSpacing(2)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        content.addWidget(heading)
        description = QLabel(subtitle)
        description.setWordWrap(True)
        description.setObjectName("mutedText")
        content.addWidget(description)
        return frame

    def _navigate_to(self, page: int):
        self._page_stack.setCurrentIndex(page)
        for index, button in enumerate(self._nav_buttons):
            button.setChecked(index == page)
        self.statusBar().showMessage(
            ("Quellen · Framevergleich · Spuren", "Stapel und Ausgabe", "Verarbeitung und Diagnose")[page], 2500
        ) if self.statusBar() is not None else None
        if page == 1 and hasattr(self, "queue_room"):
            self._refresh_inline_queue()

    def _review_source_changed(self, _path: str):
        self.review_panel.refresh(self._review_alignments())

    def _focus_episode_search(self):
        self._navigate_to(0)
        self.episode_search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.episode_search.selectAll()

    def _review_alignments(self) -> dict[tuple[str, str], AlignmentResult]:
        """Show manual timing in the review without modifying export evidence."""
        mapped = dict(self.alignments)
        master = self.selected_master
        if master:
            for path, row in self.source_rows.items():
                key = (master, path)
                original = mapped.get(key)
                if original is not None and row.manual_alignment:
                    mapped[key] = replace(original, slope=row.scale_spin.value(),
                                          offset=row.offset_spin.value(), segments=[],
                                          manual_segments=True)
        return mapped

    def _refresh_inline_queue(self):
        if not hasattr(self, "queue_room"):
            return
        if self.batch_queue is None:
            self.queue_room.set_entries([])
            self.queue_room.detail_label.setText("Warteschlange nicht verfügbar; Details im Systemprotokoll.")
            return
        try:
            entries = self.batch_queue.recent(100)
        except (QueueError, OSError, ValueError) as exc:
            self.queue_room.set_entries([])
            self.queue_room.detail_label.setText(f"Warteschlange kann nicht geladen werden: {exc}")
            return
        self.queue_room.set_entries(entries, busy=self.worker is not None)

    def _start_inline_queued_batch(self, job_id: str):
        self._queue_drain_active = False
        if self._start_queued_batch(job_id):
            self._refresh_inline_queue()

    def _retry_inline_queue(self, job_id: str):
        if self.worker is not None or self.batch_queue is None:
            return
        try:
            created = self.batch_queue.retry_problematic(job_id)
            self._log(f"Neuer Prüfauftrag: {created[:8]} · nur problematische Episoden")
            self.statusBar().showMessage("Problematische Episoden neu eingeplant · manuell starten", 6000)
            self._refresh_inline_queue()
        except (QueueError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Wiederaufnahme nicht möglich", str(exc))

    def _drain_inline_queue(self):
        if self.worker is None:
            self._queue_drain_active = True
            self._run_next_queued_batch()
            self._refresh_inline_queue()

    def _refresh_workflow_summary(self):
        if not hasattr(self, "export_overview_label"):
            return
        count = sum(len(group.files) >= 2 for group in self.groups)
        missing = sum(len(group.files) < 2 for group in self.groups)
        self.export_overview_label.setText(f"{count} Episoden im Stapel" if count else "Noch keine Episoden im Stapel")
        self.export_overview_detail.setText(
            f"{len(self.groups)} Gruppen erkannt · {count} mit mindestens zwei Videodateien"
            + (f" · {missing} nur mit einer Fassung" if missing else "")
            if self.groups else "Füge deine Quellordner hinzu und scanne die Bibliothek."
        )
        self.library_status.setText(
            f"{count} Gruppen mit mehreren Fassungen · Synchronisation wird beim Export geprüft"
        )
        self.sidebar_state.setText(
            f"{len(self.roots)} Quellen · {len(self.groups)} Episoden · {PROFILE_LABELS.get(self.performance_settings['preset'], 'Auto')}"
        )
        self.to_export_button.setEnabled(count > 0 and self.worker is None)
        self.root_list.setVisible(bool(self.roots) and self.sources_toggle.isChecked())
        self.sources_toggle.setVisible(bool(self.roots))
        self.sources_toggle.setText(f"{len(self.roots)} Quellen {'▾' if self.sources_toggle.isChecked() else '▸'}")
        self.root_hint.setVisible(not self.roots)
        self.sources_panel.setFixedHeight(126 if self.roots and self.sources_toggle.isChecked() else 54 if self.roots else 88)

    def _quick_profile_changed(self, _index: int):
        if self.worker is not None:
            return
        preset = str(self.profile_quick_combo.currentData() or "system")
        if preset == "custom":
            self._open_settings()
            self._update_quick_profile_summary()
            return
        # Resource presets must preserve explicit frame density, audio bitrate
        # and cache selection, so changing load cannot change match results.
        settings = {
            **self.performance_settings,
            "preset": preset, "auto_system_profile": preset == "system",
            "hardware_accel": "auto",
        }
        changed_decoder = self.performance_settings["hardware_accel"] != "auto"
        self.performance_settings = normalize_performance_settings(settings)
        if changed_decoder:
            self.alignments.clear()
        self._apply_performance_settings()
        self._save_settings()
        self._refresh_track_actions()
        self._refresh_workflow_summary()

    def _update_quick_profile_summary(self):
        if not hasattr(self, "profile_quick_combo"):
            return
        cfg = self.performance_settings
        index = self.profile_quick_combo.findData(cfg["preset"])
        if index >= 0:
            self.profile_quick_combo.blockSignals(True)
            self.profile_quick_combo.setCurrentIndex(index)
            self.profile_quick_combo.blockSignals(False)
        specs = system_specs()
        self.profile_quick_summary.setText(
            f"{specs['cpu_model']}\n"
            f"{specs['physical_cores']} Kerne / {specs['logical_cpus']} Threads verfügbar · "
            f"{specs['ram_gib']:.1f} GiB RAM · "
            f"Profil nutzt {cfg['cpu_threads']} CPU-Threads, "
            f"{cfg['probe_workers']} Metadaten-Worker, "
            f"bis zu {cfg['sampling_workers']} Video-Decoder.\n"
            f"GPU-Auto: {auto_decoder_summary()}"
        )
        desc = {
            "fast": "LEICHT · Kleine Ressourcenreserve, geeignet während du andere Programme benutzt.",
            "balanced": "MITTEL · Nutzt etwa die Hälfte der verfügbaren CPU-Threads. Für den Alltag.",
            "quality": "STARK · Nutzt nahezu alle CPU-Threads und mehr lokale Parallelität. Die Analyse bleibt identisch.",
            "system": f"AUTO · Empfohlen: {PROFILE_LABELS[recommended_profile(specs)]}. Berechnet die Ressourcenwerte für dieses Gerät neu.",
            "custom": "EIGENE EINSTELLUNGEN · Manuell angepasst."
        }
        self.profile_tradeoff.setText(desc.get(cfg["preset"], desc["system"]))

    def _build_menus(self):
        project = self.menuBar().addMenu("Projekt")
        open_project_action = QAction("Projekt öffnen…", self)
        open_project_action.setShortcut("Ctrl+Shift+O")
        open_project_action.triggered.connect(self._open_project)
        project.addAction(open_project_action)
        save_project_action = QAction("Projekt speichern", self)
        save_project_action.setShortcut("Ctrl+S")
        save_project_action.triggered.connect(self._save_project)
        project.addAction(save_project_action)
        save_as_action = QAction("Projekt speichern unter…", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self._save_project_as)
        project.addAction(save_as_action)
        project.addSeparator()
        history_action = QAction("Job-Verlauf…", self)
        history_action.triggered.connect(self._show_job_history)
        project.addAction(history_action)
        queue_action = QAction("Stapel-Warteschlange…", self)
        queue_action.setShortcut("Ctrl+Shift+J")
        queue_action.triggered.connect(self._show_batch_queue)
        project.addAction(queue_action)
        add_action = QAction("Medienordner hinzufügen…", self)
        add_action.setShortcut("Ctrl+O")
        add_action.triggered.connect(self.add_folder)
        project.addAction(add_action)
        remove_action = QAction("Ausgewählten Ordner entfernen", self)
        remove_action.triggered.connect(self.remove_folder)
        project.addAction(remove_action)
        project.addSeparator()
        scan_action = QAction("Bibliothek scannen", self)
        scan_action.setShortcut("F5")
        scan_action.triggered.connect(self.scan_library)
        project.addAction(scan_action)
        batch_action = QAction("Serien-Stapel starten…", self)
        batch_action.setShortcut("Ctrl+Shift+E")
        batch_action.triggered.connect(self.export_batch)
        project.addAction(batch_action)
        report_action = QAction("Bibliotheksbericht exportieren…", self)
        report_action.triggered.connect(self._save_library_report)
        project.addAction(report_action)
        project.addSeparator()
        exit_action = QAction("Beenden", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        project.addAction(exit_action)

        edit_menu = self.menuBar().addMenu("Bearbeiten")
        search_action = QAction("Episoden durchsuchen", self)
        search_action.setShortcut("Ctrl+F")
        search_action.triggered.connect(self._focus_episode_search)
        edit_menu.addAction(search_action)
        edit_menu.addSeparator()
        undo_action = QAction("Zeitkorrektur rückgängig", self)
        undo_action.setShortcut("Ctrl+Alt+Z")
        undo_action.triggered.connect(self._undo_selected_alignment)
        edit_menu.addAction(undo_action)
        redo_action = QAction("Zeitkorrektur wiederholen", self)
        redo_action.setShortcut("Ctrl+Alt+Y")
        redo_action.triggered.connect(self._redo_selected_alignment)
        edit_menu.addAction(redo_action)

        settings_menu = self.menuBar().addMenu("Einstellungen")
        contrast_action = QAction("Hoher Kontrast", self)
        contrast_action.setCheckable(True)
        contrast_action.setChecked(bool(self.settings.get("high_contrast", False)))
        contrast_action.setToolTip("Erhöht Kontrast und Konturen für bessere Lesbarkeit.")
        contrast_action.toggled.connect(self._toggle_high_contrast)
        settings_menu.addAction(contrast_action)
        settings_menu.addSeparator()
        performance_action = QAction("Leistung & Analyse…", self)
        performance_action.triggered.connect(self._open_settings)
        settings_menu.addAction(performance_action)
        diagnostics_action = QAction("Systemdiagnose…", self)
        diagnostics_action.triggered.connect(self._show_diagnostics)
        settings_menu.addAction(diagnostics_action)
        settings_menu.addSeparator()
        clear_cache_action = QAction("Analyse-Cache leeren…", self)
        clear_cache_action.triggered.connect(self._clear_alignment_cache)
        settings_menu.addAction(clear_cache_action)
        settings_menu.addSeparator()
        alignment_log_action = QAction("Bildabgleich-Diagnoseberichte…", self)
        alignment_log_action.setShortcut("Ctrl+D")
        alignment_log_action.triggered.connect(self._show_alignment_diagnostic)
        settings_menu.addAction(alignment_log_action)
        batch_log_action = QAction("Stapel-Gesamtprotokolle…", self)
        batch_log_action.setShortcut("Ctrl+Shift+D")
        batch_log_action.triggered.connect(self._show_batch_diagnostic)
        settings_menu.addAction(batch_log_action)
        logs_action = QAction("Protokolldatei öffnen…", self)
        logs_action.triggered.connect(self._open_log_file)
        settings_menu.addAction(logs_action)

        help_menu = self.menuBar().addMenu("Hilfe")
        about_action = QAction("Über DubSync Studio…", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _apply_performance_settings(self, log_change: bool = True):
        self.performance_settings = normalize_performance_settings(self.performance_settings)
        self.settings["performance"] = dict(self.performance_settings)
        self._update_quick_profile_summary()
        effective = apply_cpu_thread_limit(self.performance_settings["cpu_threads"])
        if hasattr(self, "tool_badge"):
            self.tool_badge.setToolTip(
                self.tool_badge.toolTip().split("\n\nProfil:", 1)[0]
                + f"\n\nProfil: {self.performance_settings['preset']}"
                + f"\nOpenCV-CPU-Threads: {effective}"
                + f"\nMetadaten-Worker: {self.performance_settings.get('probe_workers', 2)}"
                + f"\nLokale Videodecoder (max.): {self.performance_settings['sampling_workers']}"
                + f"\nVideo-Decoding: {self.performance_settings['hardware_accel']}"
                + f"\nZusatz-Audio: AAC {self.performance_settings['audio_bitrate_kbps']} kbit/s"
            )
        if log_change and hasattr(self, "log_view"):
            self._log(
                f"Leistungseinstellungen aktiv: {self.performance_settings['preset']} · "
                f"{effective} CPU-Threads · {self.performance_settings['max_samples']} Samples · "
                f"max. {self.performance_settings['sampling_workers']} Video-Decoder · "
                f"Decoder {self.performance_settings['hardware_accel']} · "
                f"Zusatz-Audio {self.performance_settings['audio_bitrate_kbps']} kbit/s"
            )

    def _open_settings(self):
        if self.worker is not None:
            QMessageBox.information(self, "Aufgabe läuft", "Ändere die Leistungsoptionen erst, wenn der aktuelle Job beendet ist.")
            return
        dialog = PerformanceSettingsDialog(self.performance_settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = normalize_performance_settings(dialog.values())
        changed_analysis = any(
            updated.get(key) != self.performance_settings.get(key)
            for key in ("sample_interval", "max_samples", "hardware_accel")
        )
        self.performance_settings = updated
        self.settings["performance"] = dict(updated)
        self._save_settings()
        self._apply_performance_settings()
        self._refresh_workflow_summary()
        if changed_analysis:
            self.alignments.clear()
            for path, row in self.source_rows.items():
                if path != self.selected_master:
                    row.status_label.setText("Analyseprofil geändert · bitte neu analysieren")
                    row.status_label.setObjectName("statusWarn")
                    row.status_label.style().unpolish(row.status_label)
                    row.status_label.style().polish(row.status_label)
        self._refresh_track_actions()
        QMessageBox.information(
            self, "Einstellungen gespeichert",
            "Die Einstellungen gelten für neue Analysen. Eine nicht verfügbare Hardware-Decodierung "
            "fällt automatisch auf CPU-Decoding zurück. Bereits exportierte Dateien werden nicht verändert."
        )

    def _show_diagnostics(self):
        lines = [
            f"DubSync Studio: {APP_VERSION}",
            f"Python: {sys.version.split()[0]}",
            f"Quellordner: {len(self.roots)}",
            f"Episodengruppen im aktuellen Scan: {len(self.groups)}",
            f"Medienmetadaten im Arbeitsspeicher: {len(self.media_cache)}",
            f"Alignment-Cache-Einträge: {self.cache.size}",
            f"Job-Verlauf: {self.history.path if self.history else 'nicht verfügbar'}",
            f"Logdatei: {default_log_path()}",
            f"Bildabgleich-Diagnosen: {diagnostics_directory()}",
            f"Letzter Bericht: {latest_diagnostic() or 'Noch keine frische Analyse'}",
            f"Stapel-Gesamtprotokolle: {batch_diagnostics_directory()}",
            f"Projekt: {self.current_project_path or 'nicht gespeichert'}",
            "",
            performance_diagnostics(self.performance_settings),
        ]
        try:
            process = subprocess.run(
                ["ffmpeg", "-hide_banner", "-hwaccels"], capture_output=True,
                text=True, timeout=5, check=False,
            )
            if process.returncode == 0:
                lines.extend(["", "FFmpeg-Hardwarebeschleunigung:", process.stdout.strip()])
        except (OSError, subprocess.TimeoutExpired):
            lines.extend(["", "FFmpeg-Hardwarebeschleunigung: nicht abfragbar"])
        dialog = QDialog(self)
        dialog.setWindowTitle("Systemdiagnose")
        dialog.resize(680, 560)
        box = QVBoxLayout(dialog)
        details = QTextEdit()
        details.setReadOnly(True)
        details.setPlainText("\n".join(lines))
        box.addWidget(details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        box.addWidget(buttons)
        dialog.exec()

    def _clear_alignment_cache(self):
        if self.worker is not None:
            QMessageBox.information(self, "Aufgabe läuft", "Der Cache kann erst nach Abschluss der aktuellen Aufgabe geleert werden.")
            return
        answer = QMessageBox.question(
            self, "Analyse-Cache leeren",
            "Gespeicherte Synchronisationsanalysen löschen? Die Originalvideos und fertigen Exporte bleiben unverändert.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.cache.clear()
        self.alignments.clear()
        for path, row in self.source_rows.items():
            if path != self.selected_master:
                row.status_label.setText("Cache geleert · Analyse erforderlich")
                row.status_label.setObjectName("statusMuted")
                row.status_label.style().unpolish(row.status_label)
                row.status_label.style().polish(row.status_label)
        self._refresh_track_actions()
        self._log("Analyse-Cache geleert. Quellen und Exporte wurden nicht geändert.")
        self.statusBar().showMessage("Analyse-Cache geleert", 5000)

    def _save_library_report(self):
        if not self.groups:
            QMessageBox.information(self, "Keine Bibliothek", "Scanne zuerst deine Medienordner.")
            return
        default_path = Path(self.output_dir_edit.text().strip() or (Path.home() / "Videos")) / "DubSync-Bibliotheksbericht.json"
        output, selected_filter = QFileDialog.getSaveFileName(
            self, "Bibliotheksbericht speichern", str(default_path),
            "JSON-Bericht (*.json);;CSV-Tabelle (*.csv)",
        )
        if not output:
            return
        target = Path(output).expanduser()
        if not target.suffix:
            target = target.with_suffix(".csv" if "CSV" in selected_filter else ".json")
        entries = []
        for index, group in enumerate(self.groups):
            status_item = self.episode_table.item(index, 3)
            language_item = self.episode_table.item(index, 2)
            target_path = None
            try:
                if self.output_dir_edit.text().strip():
                    target_path = str(output_path_for_group(
                        self.output_dir_edit.text().strip(), group,
                        organize_by_series=self.organize_output_check.isChecked(),
                    ))
            except (OSError, RuntimeError, ValueError):
                target_path = None
            entries.append({
                "episode": group.display_name,
                "key": group.key,
                "series": group.series_name,
                "season": group.season_number,
                "episode_number": group.episode_number,
                "files_count": len(group.files),
                "languages": language_item.text() if language_item else "",
                "status": status_item.text() if status_item else "",
                "files": list(group.files),
                "planned_output": target_path,
                "alignments": [
                    {
                        "master": Path(master_path).name,
                        "source": Path(source_path).name,
                        "confidence": result.confidence,
                        "offset_seconds": result.offset,
                        "scale": result.slope,
                        "safe": result.safe_for_automatic_export,
                    }
                    for (master_path, source_path), result in self.alignments.items()
                    if master_path in group.files and source_path in group.files
                ],
            })
        payload = {
            "app": APP_TITLE,
            "version": APP_VERSION,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "roots": list(self.roots),
            "output_directory": self.output_dir_edit.text().strip(),
            "performance_settings": dict(self.performance_settings),
            "episode_count": len(self.groups),
            "file_count": sum(len(group.files) for group in self.groups),
            "episodes": entries,
        }
        if target.exists():
            answer = QMessageBox.question(
                self, "Bericht ersetzen?", f"Die Datei existiert bereits:\n{target}\n\nErsetzen?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.suffix.lower() == ".csv":
                import csv
                import io
                buffer = io.StringIO(newline="")
                fields = ["episode", "series", "season", "episode_number", "files_count", "languages", "status", "planned_output", "files"]
                writer = csv.DictWriter(buffer, fieldnames=fields)
                writer.writeheader()
                for entry in entries:
                    row = dict(entry)
                    row["files"] = " | ".join(row["files"])
                    writer.writerow({key: row.get(key, "") for key in fields})
                target.write_text(buffer.getvalue(), encoding="utf-8-sig")
            else:
                target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Bericht konnte nicht gespeichert werden", str(exc))
            return
        self._log(f"Bibliotheksbericht geschrieben: {target}")
        self.statusBar().showMessage(f"Bericht gespeichert: {target}", 8000)
        QMessageBox.information(self, "Bericht gespeichert", f"Bibliotheksbericht gespeichert:\n\n{target}")

    def _show_about(self):
        QMessageBox.about(
            self, "Über DubSync Studio",
            f"<h2>DubSync Studio {APP_VERSION}</h2>"
            "Lokale Analyse und Zusammenführung mehrsprachiger Videofassungen.<br><br>"
            "GPU-Decoding ist optional und treiberabhängig. Bildabgleich und Zeitachsenfit laufen auf der CPU. "
            "Unsichere Zeitabbildungen sollten vor dem Export geprüft werden."
        )

    def _search_cleared(self, text: str):
        if not text:
            self.episode_filter_timer.stop()
            self._filter_episodes()

    def _filter_episodes(self, *_args):
        if not hasattr(self, "episode_table"):
            return
        query = self.episode_search.text().strip().casefold() if hasattr(self, "episode_search") else ""
        filter_mode = self.episode_filter.currentData() if hasattr(self, "episode_filter") else "all"
        visible = 0
        for index, group in enumerate(self.groups):
            if index >= self.episode_table.rowCount():
                continue
            parts = [group.key, group.display_name, *group.files]
            for col in (2, 3):
                item = self.episode_table.item(index, col)
                if item:
                    parts.append(item.text())
            matches_text = not query or any(query in str(part).casefold() for part in parts)
            matches_mode = (
                filter_mode == "all"
                or (filter_mode == "multi" and len(group.files) >= 2)
                or (filter_mode == "review" and len(group.files) < 2)
            )
            shown = matches_text and matches_mode
            self.episode_table.setRowHidden(index, not shown)
            if shown:
                visible += 1
        if hasattr(self, "episode_surface"):
            self.episode_surface.setCurrentIndex(1 if visible else 0)
            self.library_empty.setText(
                "Keine passenden Episoden\nSuchbegriff löschen oder Filter auf Alle setzen."
                if self.groups else "Noch keine Episoden\nFüge Quellen hinzu und wähle Bibliothek scannen."
            )
        if self.groups:
            self.episode_count.setText(
                f"{visible}/{len(self.groups)} Episoden angezeigt · "
                f"{sum(g.count for g in self.groups)} Dateien gescannt"
            )

    def _section_title(self, title: str, subtitle: str):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        sub = QLabel(subtitle)
        sub.setObjectName("mutedText")
        sub.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(sub)
        box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        return box

    def _stylesheet(self) -> str:
        return studio_stylesheet(self.settings.get("high_contrast", False))

    def _update_tool_status(self):
        try:
            ffmpeg, ffprobe = require_media_tools()
            result = f"FFmpeg bereit · {Path(ffmpeg).name} / {Path(ffprobe).name}"
            self.tool_badge.setText(result)
            self.tool_badge.setToolTip(ffmpeg + "\n" + ffprobe)
        except MediaToolError as exc:
            self.tool_badge.setText("FFmpeg fehlt")
            self.tool_badge.setToolTip(str(exc))

    def _refresh_roots(self):
        self.root_list.clear()
        if hasattr(self, "root_hint"):
            self.root_hint.setText(
                "Ordner oder Videodateien hier hineinziehen · NAS über die Quellenleiste hinzufügen"
                if not self.roots else
                "Doppelklick öffnet eine Quelle · Rechtsklick zeigt weitere Aktionen"
            )
        for root in self.roots:
            resolved = os.path.abspath(os.path.expanduser(root))
            rule = self.root_languages.get(resolved, {})
            language = normalize_language_code(rule.get("language", "auto"))
            title = str(rule.get("title", "") or "").strip()
            suffix = title or language_display_name(language)
            mount = network_mount_for_path(resolved)
            source_kind = f"NAS · {network_mount_type_label(mount['filesystem'])}" if mount else "Lokaler Ordner"
            track_suffix = f" · {suffix}" if ((suffix and language != "auto") or title) else ""
            item = QListWidgetItem(f"{source_kind}  ·  {root}{track_suffix}")
            item.setToolTip(f"{root}\nQuelle: {source_kind}\nTrackname: {suffix or 'automatisch erkennen'}")
            item.setData(Qt.ItemDataRole.UserRole, resolved)
            self.root_list.addItem(item)
        self._refresh_workflow_summary()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self._handle_dropped_paths(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    def _handle_dropped_paths(self, paths: list[str]):
        plan = classify_dropped_paths(paths)
        if len(paths) == 1 and len(plan["projects"]) == 1 and not plan["roots"]:
            self._open_project_file(plan["projects"][0])
            return
        if plan["projects"]:
            self._log("Projektdateien in gemischtem Drop ignoriert: bitte eine .dubsync-Datei allein ablegen.")

        existing = {os.path.abspath(os.path.expanduser(path)) for path in self.roots}
        added: list[str] = []
        ignored = list(plan["ignored"])
        for candidate in plan["roots"]:
            candidate = os.path.abspath(os.path.expanduser(candidate))
            if candidate in existing:
                continue
            # Mount-table lookup does not stat the remote directory. On a network mount,
            # defer actual access to the scanner's background worker.
            mount = network_mount_for_path(candidate)
            if mount or Path(candidate).is_dir():
                self.roots.append(candidate)
                existing.add(candidate)
                added.append(candidate)
            else:
                ignored.append(candidate)

        if added:
            self._refresh_roots()
            self._save_settings()
            self._mark_project_dirty()
            self._log(f"Drag-and-drop: {len(added)} Quelle(n) hinzugefügt.")
            self.statusBar().showMessage(
                f"{len(added)} Quelle(n) hinzugefügt · F5 startet den Bibliotheksscan", 8000
            )
        if ignored:
            short = "\n".join(ignored[:8])
            QMessageBox.information(
                self, "Einige Einträge wurden übersprungen",
                "Ziehe einen Ordner, eine Videodatei oder genau eine .dubsync-Projektdatei hinein.\n\n" + short
            )
        if not added and not ignored and not plan["projects"]:
            self.statusBar().showMessage("Alle abgelegten Quellen sind bereits hinzugefügt.", 5000)

    def _open_source_location(self, item):
        path = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not path:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _source_context_menu(self, position):
        item = self.root_list.itemAt(position)
        if item is None:
            return
        self.root_list.setCurrentItem(item)
        path = str(item.data(Qt.ItemDataRole.UserRole) or "")
        menu = QMenu(self)
        open_action = menu.addAction("Im Dateimanager öffnen")
        copy_action = menu.addAction("Pfad kopieren")
        menu.addSeparator()
        remove_action = menu.addAction("Quelle aus Projekt entfernen")
        chosen = menu.exec(self.root_list.mapToGlobal(position))
        if chosen == open_action:
            self._open_source_location(item)
        elif chosen == copy_action:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(path)
            self.statusBar().showMessage("Quellpfad kopiert", 3000)
        elif chosen == remove_action:
            self.remove_folder()

    def _toggle_high_contrast(self, enabled: bool):
        self.settings["high_contrast"] = bool(enabled)
        self.setStyleSheet(self._stylesheet())
        self._save_settings()
        self.statusBar().showMessage("Hoher Kontrast aktiviert" if enabled else "Standardkontrast aktiviert", 4000)

    def _add_source_root(self, directory: str):
        directory = os.path.abspath(os.path.expanduser(directory))
        existing = {os.path.abspath(os.path.expanduser(root)) for root in self.roots}
        if directory in existing:
            QMessageBox.information(self, "Quelle bereits vorhanden", f"Dieser Quellordner ist bereits eingetragen:\n{directory}")
            return
        # Do not synchronously stat a network mount on the GUI thread: hard-mounted NFS/SMB can stall while the NAS wakes up.
        mount = network_mount_for_path(directory)
        if not mount and not Path(directory).is_dir():
            QMessageBox.warning(self, "Quelle nicht verfügbar", "Der Ordner ist nicht erreichbar. Prüfe bitte die NAS-Verbindung oder den Mount-Punkt.")
            return
        self.roots.append(directory)
        self._refresh_roots()
        self._save_settings()
        mount = network_mount_for_path(directory)
        label = f"NAS-/Netzwerkquelle hinzugefügt ({mount['filesystem']})" if mount else "Quellordner hinzugefügt"
        self._log(f"{label}: {directory}")
        self.statusBar().showMessage(f"Quelle hinzugefügt: {directory}", 6000)

    def add_network_source(self):
        choice = QMessageBox(self)
        choice.setWindowTitle("NAS / Netzwerkquelle")
        choice.setText(
            "Du kannst eine SMB-/SFTP-/WebDAV-Freigabe im Dateimanager verbinden oder einen bereits eingehängten NAS-Ordner auswählen.\n\n"
            "NAS-Zugänge mit Passwort werden bewusst vom Desktop-Dateimanager verwaltet; DubSync speichert keine Netzwerk-Anmeldedaten."
        )
        connect_button = choice.addButton("Freigabe verbinden …", QMessageBox.ButtonRole.AcceptRole)
        mounted_button = choice.addButton("Eingehängten Ordner wählen …", QMessageBox.ButtonRole.ActionRole)
        choice.addButton(QMessageBox.StandardButton.Cancel)
        choice.exec()
        clicked = choice.clickedButton()
        if clicked == mounted_button:
            self._browse_network_mount()
            return
        if clicked != connect_button:
            return

        uri, accepted = QInputDialog.getText(
            self, "NAS-Adresse", "Freigabe-URI (z. B. smb://synology.local/Medien/Serien):",
            QLineEdit.EchoMode.Normal, "smb://"
        )
        if not accepted or not uri.strip():
            return
        uri = uri.strip()
        try:
            parsed = urlsplit(uri)
        except ValueError:
            parsed = None
        if (parsed is None or parsed.scheme.lower() not in {"smb", "sftp", "dav", "davs"}
                or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
            QMessageBox.warning(
                self, "Ungültige NAS-Adresse",
                "Verwende eine Adresse wie smb://nas.local/Medien. Benutzername und Passwort gehören nicht in die URL; authentifiziere dich im Dateimanager."
            )
            return
        opened = QDesktopServices.openUrl(QUrl(uri))
        if not opened:
            manager = shutil.which("thunar") or shutil.which("xdg-open")
            if manager:
                try:
                    subprocess.Popen([manager, uri], start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    opened = True
                except OSError:
                    opened = False
        if not opened:
            QMessageBox.warning(self, "Dateimanager nicht verfügbar", "Die Netzwerkadresse konnte nicht geöffnet werden. Wähle den NAS-Mount manuell über /mnt oder /run/user/<UID>/gvfs aus.")
            return
        next_step = QMessageBox(self)
        next_step.setWindowTitle("NAS verbinden")
        next_step.setText(
            "Die Adresse wurde im Dateimanager geöffnet. Melde dich dort bei Bedarf an und öffne die Freigabe.\n\n"
            "Kehre anschließend hierher zurück und wähle den eingehängten Serien-/Staffelordner. Der Pfad wird lokal als Quelle gespeichert; Zugangsdaten bleiben beim Desktop."
        )
        choose_button = next_step.addButton("Eingehängten Ordner auswählen", QMessageBox.ButtonRole.AcceptRole)
        next_step.addButton(QMessageBox.StandardButton.Cancel)
        next_step.exec()
        if next_step.clickedButton() == choose_button:
            self._browse_network_mount()

    def _browse_network_mount(self):
        candidates = network_mount_candidates()
        network_candidates = [path for path in candidates if network_mount_for_path(path)]
        if network_candidates:
            start = str(network_candidates[0])
        else:
            uid = os.getuid() if hasattr(os, "getuid") else -1
            gvfs = Path(f"/run/user/{uid}/gvfs") if uid >= 0 else Path("/mnt")
            start = str(gvfs if gvfs.is_dir() else Path("/mnt") if Path("/mnt").is_dir() else Path.home())
        directory = QFileDialog.getExistingDirectory(
            self, "Eingehängten NAS-/Netzwerkordner auswählen", start
        )
        if directory:
            self._add_source_root(directory)

    def add_folder(self):
        videos = Path.home() / "Videos"
        start = str(videos if videos.is_dir() else Path.home())
        directory = QFileDialog.getExistingDirectory(self, "Medienordner hinzufügen", start)
        if directory:
            self._add_source_root(directory)

    def remove_folder(self):
        row = self.root_list.currentRow()
        if row >= 0:
            del self.roots[row]
            self._refresh_roots()
            self._save_settings()

    def _set_window_project_title(self):
        if self.current_project_path:
            name = Path(self.current_project_path).name
            marker = " *" if self.project_dirty else ""
            self.setWindowTitle(f"{APP_TITLE} · {APP_VERSION} — {name}{marker}")
        else:
            self.setWindowTitle(f"{APP_TITLE} · {APP_VERSION}")

    def _mark_project_dirty(self):
        if self.current_project_path and not self._loading_project:
            self.project_dirty = True
            self._set_window_project_title()

    def _collect_project_payload(self) -> dict[str, Any]:
        output = self.output_dir_edit.text().strip() if hasattr(self, "output_dir_edit") else self.output_directory
        organize = self.organize_output_check.isChecked() if hasattr(self, "organize_output_check") else self.organize_output
        skip = self.skip_existing_check.isChecked() if hasattr(self, "skip_existing_check") else self.skip_existing
        return {
            "roots": list(self.roots),
            "root_languages": self.root_languages,
            "output_directory": output or str(Path.home() / "Videos" / "DubSync Output"),
            "organize_output": bool(organize),
            "skip_existing": bool(skip),
            "performance": dict(self.performance_settings),
            "track_overrides": self.track_overrides,
            "master_overrides": self.master_overrides,
            "manual_segments": list(self.manual_segment_overrides.values()),
        }

    def _save_project(self) -> bool:
        if not self.current_project_path:
            return self._save_project_as()
        return self._write_project(Path(self.current_project_path))

    def _save_project_as(self) -> bool:
        default = self.current_project_path or str(Path.home() / "Videos" / "DubSync-Projekt.dubsync")
        filename, _ = QFileDialog.getSaveFileName(
            self, "DubSync-Projekt speichern", default, "DubSync-Projekt (*.dubsync)"
        )
        if not filename:
            return False
        target = Path(filename).expanduser()
        if target.suffix.lower() != ".dubsync":
            target = target.with_suffix(".dubsync")
        if target.exists() and (not self.current_project_path or target.resolve() != Path(self.current_project_path).resolve()):
            answer = QMessageBox.question(
                self, "Projektdatei ersetzen?", f"{target}\n\nBestehende Projektdatei ersetzen?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        return self._write_project(target)

    def _write_project(self, target: Path) -> bool:
        try:
            target = save_project_file(target, self._collect_project_payload(), created_at=self._project_created_at)
        except ProjectFileError as exc:
            QMessageBox.critical(self, "Projekt konnte nicht gespeichert werden", str(exc))
            self._logger.exception("Projekt speichern fehlgeschlagen")
            return False
        self.current_project_path = str(target)
        if not self._project_created_at:
            self._project_created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.project_dirty = False
        self._save_settings()
        self.project_dirty = False
        self._set_window_project_title()
        self._logger.info("Projekt gespeichert: %s", target)
        self.statusBar().showMessage(f"Projekt gespeichert: {target}", 7000)
        return True

    def _confirm_replace_project(self) -> bool:
        if not self.project_dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Ungespeicherte Projektänderungen")
        box.setText("Das aktuelle Projekt wurde verändert. Änderungen vor dem Wechsel speichern?")
        save_button = box.addButton("Speichern", QMessageBox.ButtonRole.AcceptRole)
        discard_button = box.addButton("Verwerfen", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save_button:
            return self._save_project()
        if clicked == discard_button:
            self.project_dirty = False
            self._set_window_project_title()
            return True
        return False

    def _open_project(self):
        if self.worker is not None:
            QMessageBox.information(self, "Aufgabe läuft", "Öffne ein anderes Projekt erst, wenn die aktuelle Aufgabe beendet ist.")
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "DubSync-Projekt öffnen", str(Path.home() / "Videos"),
            "DubSync-Projekt (*.dubsync);;JSON-Datei (*.json)"
        )
        if not filename:
            return
        self._open_project_file(filename)

    def _open_project_file(self, filename: str):
        if self.worker is not None:
            QMessageBox.information(self, "Aufgabe läuft", "Öffne ein anderes Projekt erst, wenn die aktuelle Aufgabe beendet ist.")
            return
        if not self._confirm_replace_project():
            return
        try:
            envelope = load_project_file(filename)
        except ProjectFileError as exc:
            QMessageBox.critical(self, "Projekt konnte nicht geöffnet werden", str(exc))
            self._logger.exception("Projekt öffnen fehlgeschlagen: %s", filename)
            return

        data = envelope["project"]
        if self._track_override_timer.isActive():
            self._track_override_timer.stop()
        self._loading_project = True
        try:
            self.roots = list(data["roots"])
            self.root_languages = {
                str(Path(root).expanduser().resolve()): dict(rule)
                for root, rule in data["root_languages"].items()
            }
            self.output_directory = data["output_directory"]
            self.organize_output = bool(data["organize_output"])
            # Older project files may carry skip-existing/cache choices that caused
            # stale exports to be silently reused. Fresh rebuild is now the safe default.
            self.skip_existing = False
            self.performance_settings = normalize_performance_settings(data["performance"])
            self.performance_settings["cache_enabled"] = False
            self.track_overrides = dict(data.get("track_overrides", {}))
            self.master_overrides = dict(data.get("master_overrides", {}))
            self.manual_segment_overrides = {
                (record["master"], record["source"]): record
                for record in data.get("manual_segments", [])
            }
            if self._track_override_timer.isActive():
                self._track_override_timer.stop()
            self.media_cache.clear()
            self.alignments.clear()
            self.automatic_alignments.clear()
            self.groups.clear()
            self.selected_group = None
            self.selected_master = None
            self.source_rows.clear()
            self.episode_table.setRowCount(0)
            self.episode_count.setText("Projekt geladen · Bibliothek noch scannen")
            self._clear_detail("Projekt geladen. Scanne die Quellordner, um Episoden zu laden.")
            self.output_dir_edit.setText(self.output_directory)
            self.organize_output_check.setChecked(self.organize_output)
            self.skip_existing_check.setChecked(False)
            self._clear_resume_journal()
            self._refresh_roots()
            self._apply_performance_settings(log_change=False)
            self._save_settings()
        finally:
            self._loading_project = False

        self.current_project_path = envelope["path"]
        self._project_created_at = envelope["created_at"]
        self.project_dirty = False
        self._loading_project = True
        try:
            self._save_settings()
        finally:
            self._loading_project = False
        self.project_dirty = False
        self._set_window_project_title()
        self._update_output_preview()
        self._log(f"Projekt geöffnet: {self.current_project_path}")
        missing = envelope.get("missing_roots", [])
        if missing:
            QMessageBox.warning(
                self, "Quellordner nicht gefunden",
                "Das Projekt wurde geladen, aber diese Quellordner sind nicht erreichbar:\n\n"
                + "\n".join(missing[:20])
                + "\n\nKorrigiere die Pfade oder entferne die fehlenden Ordner vor dem Scan."
            )
        self.statusBar().showMessage("Projekt geladen · Bibliothek scannen, um fortzufahren", 8000)

    def _show_job_history(self):
        if self.history is None:
            QMessageBox.warning(self, "Job-Verlauf nicht verfügbar", "Die lokale Job-Datenbank konnte nicht geöffnet werden. Prüfe die Systemdiagnose.")
            return
        try:
            entries = self.history.recent(300)
        except Exception as exc:
            self._logger.exception("Job-Verlauf konnte nicht gelesen werden")
            QMessageBox.critical(self, "Job-Verlauf fehlgeschlagen", str(exc))
            return
        if not entries:
            QMessageBox.information(self, "Job-Verlauf", "Es wurden noch keine Aufgaben protokolliert.")
            return
        JobHistoryDialog(entries, self).exec()

    def _show_batch_queue(self):
        if self.batch_queue is None:
            QMessageBox.warning(self, "Warteschlange nicht verfügbar",
                                "Die lokale Warteschlange konnte nicht geöffnet werden. Prüfe das Diagnoseprotokoll.")
            return
        try:
            while True:
                dialog = BatchQueueDialog(self.batch_queue.recent(),
                                          busy=self.worker is not None, parent=self)
                if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.action:
                    return
                action, selected = dialog.action, dialog.job_id
                if action == "retry" and selected:
                    created = self.batch_queue.retry_problematic(selected)
                    self._log(f"Problematische Episoden als neuen Auftrag eingeplant ({created[:8]}).")
                    continue
                if action == "drain":
                    self._queue_drain_active = True
                    self._run_next_queued_batch()
                    return
                if action == "start" and selected:
                    self._queue_drain_active = False
                    self._start_queued_batch(selected)
                    return
        except (QueueError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Warteschlange", str(exc))
        finally:
            self._refresh_inline_queue()

    def _start_queued_batch(self, job_id: str, *, authorized_for_current_session: bool = False) -> bool:
        if self.worker is not None or self.batch_queue is None:
            self.statusBar().showMessage("Eine Aufgabe läuft bereits.", 5000)
            return False
        try:
            entry = self.batch_queue.get(job_id)
            if entry["state"] not in BatchQueueDialog.STARTABLE:
                raise QueueError("Dieser Auftrag kann aktuell nicht gestartet werden.")
        except QueueError as exc:
            QMessageBox.warning(self, "Warteschlange", str(exc))
            return False
        if (not authorized_for_current_session and not entry["journal_path"]
                and entry["spec"]["options"].get("overwrite_existing")):
            answer = QMessageBox.warning(
                self, "Überschreiben erneut bestätigen",
                "Dieser gespeicherte Auftrag wurde mit Überschreibberechtigung angelegt. "
                "Vorhandene Zieldateien könnten ersetzt werden. Quelldateien bleiben unverändert.\n\n"
                "Diesen Auftrag jetzt mit Überschreibberechtigung starten?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        if self._prewarm_worker is not None and self._prewarm_worker.isRunning():
            self._prewarm_worker.cancel(reason="batch_priority")
        self._active_queue_job_id = job_id
        self._log(f"Gespeicherten Stapel starten: {entry['label']} ({job_id[:8]}).")
        self._run_task(
            f"Serien-Stapel: {entry['label']}",
            lambda report, cancel: self.batch_queue.run(
                job_id, cache=self.cache, cancel_event=cancel,
                progress=lambda current, total, message: report(message, current, total),
            ),
            self._batch_done,
        )
        return True

    def _run_next_queued_batch(self):
        if not self._queue_drain_active or self.batch_queue is None or self.worker is not None:
            return
        try:
            waiting = [entry for entry in reversed(self.batch_queue.recent(200))
                       if entry["state"] == "queued"]
        except (QueueError, OSError, ValueError) as exc:
            self._queue_drain_active = False
            QMessageBox.warning(self, "Warteschlange", str(exc))
            return
        if waiting:
            if not self._start_queued_batch(waiting[0]["id"]):
                self._queue_drain_active = False
        else:
            self._queue_drain_active = False
            self.statusBar().showMessage("Warteschlange abgearbeitet", 5000)

    def _finish_queued_task(self, *, interrupted: bool = False):
        was_queued = self._active_queue_job_id is not None
        self._active_queue_job_id = None
        if interrupted:
            self._queue_drain_active = False
        if was_queued and self._queue_drain_active:
            QTimer.singleShot(0, self._run_next_queued_batch)
        self._refresh_inline_queue()

    def _show_batch_diagnostic(self):
        """Explore complete batch runs, not just the last cancelled alignment."""
        reports = []
        for path in recent_batch_diagnostics():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                reports.append((path, payload))
            except (OSError, ValueError, TypeError):
                continue
        if not reports:
            QMessageBox.information(
                self, "Stapel-Gesamtprotokoll",
                "Es gibt noch keinen Stapelbericht. Starte Bibliotheken scannen → Serien-Stapel. "
                "Ein Bericht wird bereits während des Stapels gespeichert.\n\n"
                f"Speicherort: {batch_diagnostics_directory()}",
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Stapel-Gesamtprotokoll · alle Folgen und Abgleichversuche")
        dialog.resize(1030, 760)
        layout = QVBoxLayout(dialog)
        heading = QLabel("Alle Episoden · alle Ergebnisse · alle Analyseversuche")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        caption = QLabel("Wähle einen Stapellauf. Die Folge E02 bleibt sichtbar, auch wenn E03 später abgebrochen wurde.")
        caption.setWordWrap(True)
        layout.addWidget(caption)
        picker = QComboBox(dialog)
        for path, payload in reports:
            total = len(payload.get("episodes", []))
            skipped = (payload.get("summary") or {}).get("skipped", 0)
            label = f"{payload.get('started_utc', '')[:19]} · {total} Folgen · {skipped} übersprungen · {payload.get('status', '?')}"
            picker.addItem(label, str(path))
        layout.addWidget(picker)
        status_line = QLabel(dialog)
        status_line.setObjectName("statusChip")
        layout.addWidget(status_line)
        table = QTableWidget(dialog)
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Schritt", "Episode", "Status", "Versuche", "Grund"])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        table.setMinimumHeight(190)
        layout.addWidget(table, 1)
        tabs = QTabWidget(dialog)
        episode_text = QTextEdit(dialog)
        episode_text.setReadOnly(True)
        episode_text.setObjectName("logView")
        summary_text = QTextEdit(dialog)
        summary_text.setReadOnly(True)
        summary_text.setObjectName("logView")
        raw_json = QTextEdit(dialog)
        raw_json.setReadOnly(True)
        raw_json.setObjectName("logView")
        raw_json.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        tabs.addTab(episode_text, "Ausgewählte Folge")
        tabs.addTab(summary_text, "Alle Folgen als Text")
        tabs.addTab(raw_json, "Vollständiger JSON-Bericht")
        layout.addWidget(tabs, 2)
        warning = QLabel("Die Berichte enthalten Dateinamen, Zeitangaben und Fehlerdetails. Vor dem Teilen private Angaben prüfen.")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        def current():
            return reports[picker.currentIndex()]

        def render_episode():
            row = table.currentRow()
            entries = current()[1].get("episodes", [])
            if not (0 <= row < len(entries)):
                episode_text.clear()
                return
            entry = entries[row]
            detail = [f"{entry.get('episode') or entry.get('display_name')} · {entry.get('status')}",
                      f"Grund: {entry.get('reason') or 'Noch kein Abschluss'}", "",
                      "Alle bisherigen Analyseversuche dieser Episode:"]
            for attempt in entry.get("attempts", []):
                detail.extend(["", f"Versuch {attempt.get('attempt')} · {attempt.get('max_samples')} Samples · {attempt.get('status')}",
                               json.dumps(attempt, ensure_ascii=False, indent=2)])
            if not entry.get("attempts"):
                detail.append("Keine Bildanalyse gestartet (z. B. wegen vorhandener Ausgabe oder fehlender Quelle).")
            episode_text.setPlainText("\n".join(detail))
            episode_text.verticalScrollBar().setValue(0)

        def render_run(_index=0):
            payload = current()[1]
            summary = payload.get("summary") or {}
            status_line.setText(
                f"{payload.get('status')} · " + " · ".join(f"{summary.get(k, 0)} {k}" for k in
                    ("exported", "skipped", "failed", "cancelled", "not_started"))
            )
            entries = payload.get("episodes", [])
            table.setRowCount(len(entries))
            for r, entry in enumerate(entries):
                values = [f"{entry.get('position')}/{entry.get('total')}",
                          entry.get("episode") or entry.get("display_name"),
                          {"exported": "Exportiert", "skipped": "Übersprungen",
                           "failed": "Fehler", "cancelled": "Abgebrochen", "pending": "Nicht gestartet",
                           "running": "In Arbeit"}.get(entry.get("status"), entry.get("status")),
                          str(len(entry.get("attempts", []))), entry.get("reason") or ""]
                for col, val in enumerate(values):
                    table.setItem(r, col, QTableWidgetItem(str(val)))
            summary_text.setPlainText(summarize_batch(payload))
            raw_json.setPlainText(json.dumps(payload, indent=2, ensure_ascii=False))
            if entries:
                table.selectRow(0)
            else:
                episode_text.clear()

        picker.currentIndexChanged.connect(render_run)
        table.itemSelectionChanged.connect(render_episode)
        controls = QHBoxLayout()
        copy_all = QPushButton("Gesamtes Stapel-JSON kopieren")
        copy_all.setObjectName("primaryButton")
        copy_all.clicked.connect(lambda: QApplication.clipboard().setText(
            json.dumps(current()[1], ensure_ascii=False, indent=2)))
        controls.addWidget(copy_all)
        copy_text = QPushButton("Gesamtauswertung kopieren")
        copy_text.clicked.connect(lambda: QApplication.clipboard().setText(summarize_batch(current()[1])))
        controls.addWidget(copy_text)

        def save_as():
            original, payload = current()
            filename, _ = QFileDialog.getSaveFileName(dialog, "Gesamtes Stapelprotokoll speichern",
                                                     original.name, "JSON (*.json)")
            if filename:
                try:
                    Path(filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                except OSError as exc:
                    QMessageBox.warning(dialog, "Speichern fehlgeschlagen", str(exc))
        save_button = QPushButton("JSON speichern…")
        save_button.clicked.connect(save_as)
        controls.addWidget(save_button)
        open_folder = QPushButton("Berichtsordner öffnen")
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(current()[0].parent))))
        controls.addWidget(open_folder)
        controls.addStretch(1)
        close_button = QPushButton("Schließen")
        close_button.clicked.connect(dialog.accept)
        controls.addWidget(close_button)
        layout.addLayout(controls)
        render_run()
        dialog.exec()

    def _show_alignment_diagnostic(self):
        """Select a run, explain its status, then show the raw support JSON."""
        reports = []
        for path in recent_diagnostics():
            try:
                raw = path.read_text(encoding="utf-8")
                payload = json.loads(raw)
                source = payload.get("files", {}).get("source", {}).get("filename", "?")
                status = payload.get("decision", {}).get("status", "?")
                status_label = {"safe": "Sicher", "unsafe": "Unsicher", "cancelled": "Abgebrochen",
                                "error": "Fehler"}.get(status, "Unbekannt")
                label = f"{status_label} · {source} · {str(payload.get('started_utc', '?'))[:19]}"
                reports.append((label, path, raw, payload))
            except (OSError, ValueError, TypeError):
                continue
        if not reports:
            QMessageBox.information(
                self, "Bildabgleich-Diagnose",
                "Noch kein Bericht vorhanden. Starte eine neue Analyse ohne Cache. "
                f"Die Berichte findest du danach unter:\n{diagnostics_directory()}",
            )
            return
        window = QDialog(self)
        window.setWindowTitle("Bildabgleich verstehen · DubSync Diagnose")
        window.resize(860, 650)
        layout = QVBoxLayout(window)
        layout.setSpacing(10)
        heading = QLabel("Analyseberichte")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        description = QLabel("Wähle die richtige Episode aus. Eine abgebrochene Analyse wurde nicht als unsicher bewertet.")
        description.setWordWrap(True)
        description.setObjectName("mutedText")
        layout.addWidget(description)
        picker = QComboBox(window)
        picker.setAccessibleName("Analysebericht nach Status und Dateiname auswählen")
        for label, path, _raw, _payload in reports:
            picker.addItem(label, str(path))
        layout.addWidget(picker)
        summary = QLabel(window)
        summary.setObjectName("statusChip")
        layout.addWidget(summary)
        tabs = QTabWidget(window)
        explanation = QTextEdit(window)
        explanation.setReadOnly(True)
        explanation.setObjectName("logView")
        raw_json = QTextEdit(window)
        raw_json.setReadOnly(True)
        raw_json.setObjectName("logView")
        raw_json.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        tabs.addTab(explanation, "Verständliche Auswertung")
        tabs.addTab(raw_json, "JSON · technische Details")
        layout.addWidget(tabs, 1)
        privacy = QLabel("Der Bericht enthält Dateinamen und technische Messwerte. Vor dem Teilen auf private Angaben prüfen.")
        privacy.setWordWrap(True)
        privacy.setObjectName("mutedText")
        layout.addWidget(privacy)

        def selected():
            return reports[picker.currentIndex()]

        def render(_index=0):
            _label, _path, _raw, payload = selected()
            decision = payload.get("decision", {})
            status = decision.get("status", "unknown")
            status_text = {"safe": "Synchronisation sicher", "unsafe": "Synchronisation unsicher",
                           "cancelled": "Analyse abgebrochen", "error": "Technischer Fehler"}.get(status, "Unbekannt")
            summary.setText(f"{status_text}  ·  {payload.get('elapsed_seconds', '?')} s  ·  "
                            f"{decision.get('method') or decision.get('last_completed_stage') or 'noch kein Bildabgleich'}")
            explanation.setPlainText(summarize_diagnostic(payload))
            raw_json.setPlainText(json.dumps(payload, indent=2, ensure_ascii=False))
            explanation.verticalScrollBar().setValue(0)
            raw_json.verticalScrollBar().setValue(0)

        picker.currentIndexChanged.connect(render)
        actions = QHBoxLayout()
        copy_summary = QPushButton("Zusammenfassung kopieren")
        copy_summary.clicked.connect(lambda: QApplication.clipboard().setText(summarize_diagnostic(selected()[3])))
        actions.addWidget(copy_summary)
        copy_json = QPushButton("JSON für Fehlersuche kopieren")
        copy_json.setObjectName("primaryButton")
        copy_json.setToolTip("Vollständige Diagnose in die Zwischenablage kopieren")
        copy_json.clicked.connect(lambda: QApplication.clipboard().setText(selected()[2]))
        actions.addWidget(copy_json)
        folder_button = QPushButton("Berichtsordner öffnen")
        folder_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(selected()[1].parent))))
        actions.addWidget(folder_button)
        actions.addStretch(1)
        close_button = QPushButton("Schließen")
        close_button.clicked.connect(window.accept)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        render()
        window.exec()

    def _open_log_file(self):
        path = default_log_path()
        if not path.exists():
            QMessageBox.information(self, "Protokoll", f"Die Logdatei wird beim nächsten Protokolleintrag erstellt:\n{path}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.information(self, "Protokolldatei", str(path))

    def _save_settings(self):
        """Save preferences only; active libraries belong to this launch."""
        self.settings["performance"] = dict(self.performance_settings)
        if hasattr(self, "output_dir_edit"):
            self.output_directory = self.output_dir_edit.text().strip()
            self.settings["output_directory"] = self.output_directory
        if hasattr(self, "organize_output_check"):
            self.organize_output = self.organize_output_check.isChecked()
            self.settings["organize_output"] = self.organize_output
        # Intentionally do not persist roots, root_languages, track_overrides,
        # master_overrides, last_project_path or skip_existing. Old values from
        # earlier releases are stripped on the first 1.5.11 launch, not reused.
        self.settings = persistent_preferences(self.settings)
        save_settings(self.settings)
        self._mark_project_dirty()

    def _output_options_changed(self, *_args):
        self._save_settings()
        self._update_output_preview()

    def browse_output_directory(self):
        start = self.output_dir_edit.text().strip() or str(Path.home() / "Videos")
        directory = QFileDialog.getExistingDirectory(self, "Ausgabeordner wählen", start)
        if directory:
            self.output_dir_edit.setText(directory)
            self._save_settings()
            self._update_output_preview()

    def configure_root_languages(self):
        if not self.roots:
            QMessageBox.information(self, "Keine Quellordner", "Füge zuerst die Ordner mit den Sprachfassungen hinzu.")
            return
        dialog = RootLanguageDialog(self.roots, self.root_languages, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.root_languages = dialog.rules()
            self._save_settings()
            self._refresh_roots()
            self._update_output_preview()
            self._log(f"Sprach-/Tracknamenzuordnung gespeichert: {len(self.root_languages)} Ordnerregeln.")
            # Update the visible language column without throwing away the current
            # FFprobe and alignment caches or launching needless background probes.
            for row_index, group in enumerate(self.groups):
                labels = set()
                for path in group.files:
                    code, title = resolve_track_label(
                        path, roots=self.roots, root_languages=self.root_languages,
                        inferred_language=infer_language(path),
                    )
                    label = language_display_name(code) or (title if title != Path(path).stem else "")
                    if label and label.casefold() not in {"und", "auto"}:
                        labels.add(label)
                item = self.episode_table.item(row_index, 2)
                if item:
                    item.setText(", ".join(sorted(labels)) if labels else "Zuordnung offen")
            if self.selected_group:
                for path, row in self.source_rows.items():
                    if not row.info.audio:
                        continue
                    stream = row.info.audio[row.audio_ordinal]
                    language, title = resolve_track_label(
                        path, stream.language, stream.title,
                        roots=self.roots, root_languages=self.root_languages,
                        inferred_language=infer_language(path),
                    )
                    row.language_edit.setText(language if language != "und" else "")
                    row.title_edit.setText(title)

    def _update_output_preview(self, *_args):
        if not hasattr(self, "output_preview_label"):
            return
        output = self.output_dir_edit.text().strip() if hasattr(self, "output_dir_edit") else self.output_directory
        group = next((item for item in self.groups if len(item.files) >= 2), None)
        organize = self.organize_output_check.isChecked() if hasattr(self, "organize_output_check") else self.organize_output
        if not output:
            self.output_preview_label.setText("Bitte einen Ausgabeordner angeben.")
        elif group:
            try:
                target = output_path_for_group(output, group, organize_by_series=organize)
                self.output_preview_label.setText(f"Beispiel-Ausgabe: {target}")
            except (OSError, RuntimeError, ValueError) as exc:
                self.output_preview_label.setText(f"Ungültiger Ausgabepfad: {exc}")
        elif organize:
            self.output_preview_label.setText(f"Ausgabe wird nach Serie und Staffel organisiert: {output}/Serienname/Staffel 01/…")
        else:
            self.output_preview_label.setText(f"Alle fertigen Episoden kommen direkt nach: {output}")

    def _start_metadata_prewarm(self):
        if not self.performance_settings.get("prewarm_metadata", True) or not self.groups:
            return
        if self._prewarm_worker is not None and self._prewarm_worker.isRunning():
            return
        excluded = set(self.selected_group.files) if self.selected_group else set()
        paths = list(dict.fromkeys(
            path for group in self.groups for path in group.files
            if path not in excluded and path not in self.media_cache
        ))
        if not paths:
            return
        # Prewarm a bounded number of upcoming files. The UI remains available and
        # a mounted NAS gets at most two metadata reads in parallel.
        is_network = any(is_network_path(path) for path in paths[:8])
        limit = 12 if is_network else 24
        paths = paths[:limit]
        workers = min(
            int(self.performance_settings.get("probe_workers", 3)),
            2 if is_network else 4, len(paths),
        )
        self._prewarming_paths = set(paths)
        self._logger.info("Metadaten-Cache vorwärmen: %d Dateien, %d Worker, Netzwerk=%s", len(paths), workers, is_network)
        worker = TaskWorker(lambda report, cancel: self._probe_paths(paths, report, cancel), self)
        self._prewarm_worker = worker
        worker.progress.connect(self._metadata_prewarm_progress)
        worker.finished.connect(self._metadata_prewarm_done)
        worker.start()

    def _metadata_prewarm_progress(self, message: str, current: int, total: int):
        if total > 0:
            self.statusBar().showMessage(f"Vorwärmen · {current}/{total} · {message}")
        else:
            self.statusBar().showMessage(message, 3500)

    def _metadata_prewarm_done(self):
        worker = self._prewarm_worker
        self._prewarm_worker = None
        self._prewarming_paths.clear()
        if worker is None:
            return
        if worker.error_text:
            self._logger.warning("Metadaten-Vorwärmen abgebrochen/fehlgeschlagen: %s", worker.error_text.splitlines()[0])
        elif isinstance(worker.result_value, list):
            for info in worker.result_value:
                self.media_cache[info.path] = info
            self._logger.info("Metadaten-Vorwärmen abgeschlossen: %d Dateien", len(worker.result_value))
            self.statusBar().showMessage(f"Metadaten-Cache vorbereitet · {len(worker.result_value)} Dateien", 5000)
        worker.deleteLater()
        if self.selected_group:
            if self.worker is not None:
                self._prewarm_refresh_pending = True
            else:
                missing = [path for path in self.selected_group.files if path not in self.media_cache]
                if missing:
                    self._on_episode_selected()
                else:
                    self._render_group(self.selected_group)

    def _refresh_after_prewarm(self):
        self._prewarm_refresh_pending = False
        if not self.selected_group or self.worker is not None:
            return
        missing = [path for path in self.selected_group.files if path not in self.media_cache]
        if missing:
            self._on_episode_selected()
        else:
            self._render_group(self.selected_group)

    def scan_library(self):
        if not self.roots:
            QMessageBox.information(self, "Ordner auswählen", "Füge zuerst mindestens einen Medienordner hinzu.")
            return
        roots = list(self.roots)
        self._run_task(
            "Scanne Videodateien …",
            lambda report, cancel: scan_directories(
                roots, progress=lambda done, total, name: report(f"Scanne {name}", done, total),
                cancel_event=cancel,
            ),
            self._scan_done,
        )

    def _scan_done(self, groups):
        self.groups = groups
        self.media_cache.clear()
        self.alignments.clear()
        self.episode_table.setRowCount(0)
        for group in groups:
            row = self.episode_table.rowCount()
            self.episode_table.insertRow(row)
            title = QTableWidgetItem(group.display_name)
            title.setData(Qt.ItemDataRole.UserRole, group.key)
            self.episode_table.setItem(row, 0, title)
            self.episode_table.setItem(row, 1, QTableWidgetItem(str(group.count)))
            languages = set()
            for path in group.files:
                code, title = resolve_track_label(
                    path, roots=self.roots, root_languages=self.root_languages,
                    inferred_language=infer_language(path),
                )
                label = language_display_name(code) or (title if title != Path(path).stem else code.upper())
                if label and label != "UND":
                    languages.add(label)
            self.episode_table.setItem(row, 2, QTableWidgetItem(", ".join(sorted(languages)) if languages else "Zuordnung offen"))
            status = "Abgleich offen" if len(group.files) > 1 else "Fassung fehlt"
            self.episode_table.setItem(row, 3, QTableWidgetItem(status))
        self.episode_search.clear()
        self.episode_filter.setCurrentIndex(0)
        self.episode_count.setText(f"{len(groups)} Gruppen · {sum(g.count for g in groups)} Videodateien")
        self._filter_episodes()
        self.sources_toggle.setChecked(False)
        self._log(f"Scan abgeschlossen: {len(groups)} Episodengruppen aus {sum(g.count for g in groups)} Dateien.")
        if groups:
            self.episode_table.selectRow(0)
            self._start_metadata_prewarm()
        else:
            self._clear_detail("Keine Videodateien gefunden")
        self._update_output_preview()
        self._refresh_workflow_summary()

    def _on_episode_selected(self):
        row = self.episode_table.currentRow()
        if row < 0 or row >= len(self.groups):
            self.review_panel.reset()
            return
        # Table rows and groups use the same deterministic sorted order.
        group = self.groups[row]
        self.selected_group = group
        self.selected_master = group.files[0] if group.files else None
        self._show_probe_loading(group)
        paths = list(group.files)
        missing = [path for path in paths if path not in self.media_cache]
        if not missing:
            self._render_group(group)
            return
        if any(path in self._prewarming_paths for path in missing):
            self._show_probe_loading(group)
            self.statusBar().showMessage("Metadaten werden bereits im Hintergrund vorgeladen …", 5000)
            return

        group_key = group.key
        self._run_task(
            f"Analysiere Medienstreams für {group.display_name} …",
            lambda report, cancel: self._probe_paths(missing, report, cancel),
            lambda infos: self._probes_done(group_key, infos),
            update_busy=False,
        )

    def _probe_paths(self, paths: list[str], report, cancel_event):
        # Local SSDs can sustain several concurrent FFprobe jobs; network mounts
        # use a stricter limit to avoid hammering sleeping NAS disks or saturating SMB.
        is_network = any(is_network_path(path) for path in paths)
        requested = int(self.performance_settings.get("probe_workers", 3))
        workers = min(requested, 2 if is_network else 6, max(1, len(paths)))
        report(
            f"Metadatenprüfung · {workers} parallele FFprobe-Worker"
            + (" · Netzwerkquelle, begrenzte Parallelität" if is_network else ""),
            0, len(paths),
        )
        if self.performance_settings.get("cache_enabled", True) and paths:
            try:
                fingerprint_many(
                    paths, max_workers=min(workers, 2 if is_network else 4),
                    progress=lambda done, total, name: report(f"Schnellfingerprint · {name}", done, total),
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                if cancel_event.is_set():
                    raise RuntimeError("Medienprüfung abgebrochen.") from exc
                # Fingerprinting is an optimization only; never block FFprobe on a
                # NAS permission issue, unsupported file, or a temporary read error.
                self._logger.warning("Schnellfingerprint übersprungen: %s", exc)
                report("Schnellfingerprint nicht verfügbar · FFprobe wird fortgesetzt", 0, len(paths))
        return probe_many(
            paths, max_workers=workers,
            progress=lambda done, total, name: report(f"Metadaten · {name}", done, total),
            cancel_event=cancel_event,
        )

    def _probes_done(self, group_key: str, infos: list[MediaInfo]):
        for info in infos:
            self.media_cache[info.path] = info
        group = next((item for item in self.groups if item.key == group_key), None)
        if group and self.selected_group and self.selected_group.key == group_key:
            self._render_group(group)

    def _show_probe_loading(self, group: EpisodeGroup):
        self.review_panel.reset(f"{group.display_name} · Metadaten werden geladen")
        self.detail_heading.setText(group.display_name)
        self.detail_description.setText(f"{group.count} Dateien · Metadaten und Audiospuren werden geladen …")
        self._clear_detail("Analysiere Dateiformate und Audiospuren …")

    def _clear_detail(self, message: str):
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        label = QLabel(message)
        label.setObjectName("mutedText")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_layout.addWidget(label)
        self.detail_layout.addStretch(1)
        self.source_rows.clear()

    def _render_group(self, group: EpisodeGroup):
        # Automatically use the smallest video file as picture master to minimize
        # output size. The radio buttons still allow a manual override for this episode.
        known_videos = [
            self.media_cache[path] for path in group.files
            if path in self.media_cache and self.media_cache[path].video
        ]
        if known_videos:
            try:
                self.selected_master = choose_smallest_video(known_videos).path
            except ValueError:
                self.selected_master = group.files[0] if group.files else None
        manual_master = self.master_overrides.get(group.key)
        if manual_master in group.files and manual_master in self.media_cache:
            if self.media_cache[manual_master].video:
                self.selected_master = manual_master
        self._clear_detail("")
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.source_rows = {}
        self.detail_heading.setText(group.display_name)
        total_duration = max((self.media_cache[p].duration for p in group.files if p in self.media_cache), default=0)
        master_info = self.media_cache.get(self.selected_master or "")
        if master_info:
            added_count = sum(
                1 for path in group.files
                if path != self.selected_master
                and path in self.media_cache and bool(self.media_cache[path].audio)
            )
            bitrate = int(self.performance_settings.get("audio_bitrate_kbps", 128))
            estimated_bytes = estimate_output_size_bytes(
                master_info.size_bytes, master_info.duration, added_count, bitrate
            )
            master_label = "Manuell gewähltes Bildmaster" if manual_master == self.selected_master else "Automatisches Bildmaster (kleinste Datei)"
            master_note = (
                f"{master_label}: „{Path(self.selected_master).name}“ "
                f"({master_info.size_bytes / (1024 * 1024):.1f} MiB). "
                f"Grobe Zielgröße: {estimated_bytes / (1024 * 1024):.1f} MiB bei AAC {bitrate} kbit/s. "
            )
            other_durations = [
                self.media_cache[path].duration for path in group.files
                if path in self.media_cache and self.media_cache[path].duration > 0
            ]
            if other_durations and max(other_durations) - master_info.duration > 2.0:
                master_note += (
                    "Achtung: Andere Fassungen sind länger; das resultierende Bild übernimmt "
                    "die Laufzeit dieser kleinsten Datei. "
                )
            if other_durations and max(other_durations) - min(other_durations) >= 30.0:
                difference = max(other_durations) - min(other_durations)
                master_note += (
                    f"Deutliche Laufzeitabweichung ({difference:.1f} s): eventuell andere "
                    "Intros, Abspänne oder Schnittfassung. Eine sichere Audiozuordnung "
                    "ist damit nicht garantiert. "
                )
        else:
            master_note = "Das kleinste Bildmaster wird nach dem Einlesen der Metadaten gewählt. "
        self.detail_description.setText(
            f"{len(group.files)} Fassungen · längste Laufzeit {int(total_duration // 60):02d}:{int(total_duration % 60):02d}. "
            + master_note + "Du kannst das Referenzvideo unten manuell ändern. "
            "Zusätzliche Audiospuren werden auf dessen Zeitachse ausgerichtet."
        )

        auto_master_button = QPushButton("Automatisch die kleinste Datei als Bildmaster wählen")
        auto_master_button.setObjectName("secondaryButton")
        auto_master_button.setToolTip("Entfernt die manuelle Bildmaster-Auswahl für diese Episode.")
        auto_master_button.clicked.connect(lambda _checked=False, key=group.key: self._reset_master_override(key))
        self.detail_layout.addWidget(auto_master_button)

        radios = QButtonGroup(self)
        radios.setExclusive(True)
        for index, path in enumerate(group.files):
            info = self.media_cache.get(path)
            if not info:
                continue
            path_key = str(Path(path).expanduser().resolve())
            override = self.track_overrides.get(path_key, {})
            row = SourceRow(path, info, is_master=(path == self.selected_master))
            if info.audio:
                wanted_ordinal = override.get("audio_ordinal")
                if isinstance(wanted_ordinal, int):
                    ordinal_index = row.audio_combo.findData(wanted_ordinal)
                    if ordinal_index >= 0:
                        row.audio_combo.setCurrentIndex(ordinal_index)
                ordinal = row.audio_ordinal
                stream = info.audio[ordinal]
                language, track_title = resolve_track_label(
                    path, stream.language, stream.title,
                    roots=self.roots, root_languages=self.root_languages,
                    inferred_language=infer_language(path),
                )
                row.language_edit.setText(
                    str(override.get("language", language if language != "und" else ""))
                )
                row.title_edit.setText(str(override.get("title", track_title)))
                if not row.master_radio.isChecked() and isinstance(override.get("include"), bool):
                    row.include_audio.setChecked(override["include"])
            radios.addButton(row.master_radio)
            row.masterToggled.connect(self._master_changed)
            row.segmentEditRequested.connect(self._edit_segments)
            row.resetSegmentRequested.connect(self._reset_manual_segments)
            self.detail_layout.addWidget(row)
            self.source_rows[path] = row
            key = (self.selected_master or "", path)
            cached = self.alignments.get(key)
            if cached is None and self.performance_settings.get("cache_enabled", True):
                cached = self.cache.get(
                    path, self.selected_master or path,
                    interval=self.performance_settings["sample_interval"],
                    max_samples=self.performance_settings["max_samples"],
                    hardware_accel=self.performance_settings["hardware_accel"],
                )
            if cached and path != self.selected_master:
                if not cached.manual_segments:
                    self.automatic_alignments[key] = cached
                    cached = self._restore_manual_segments(cached)
                self.alignments[key] = cached
                row.set_alignment(cached)
            # Saved manual corrections survive cached or newly recomputed analysis.
            if override.get("manual_alignment") and ("offset" in override or "scale" in override):
                row.manual_alignment = True
                row.offset_spin.blockSignals(True)
                row.scale_spin.blockSignals(True)
                if "offset" in override:
                    row.offset_spin.setValue(float(override["offset"]))
                if "scale" in override:
                    row.scale_spin.setValue(float(override["scale"]))
                row.offset_spin.blockSignals(False)
                row.scale_spin.blockSignals(False)
                row.reset_alignment_button.setEnabled(True)
                if cached and ("offset" in override or "scale" in override):
                    row.status_label.setText("Gespeicherte manuelle Korrektur · wird vor Export geprüft")
                    row.status_label.setObjectName("statusWarn")
                    row.status_label.style().unpolish(row.status_label)
                    row.status_label.style().polish(row.status_label)
            row.adopt_current_alignment()
            row.trackChanged.connect(lambda _unused=None, source_path=path: self._on_track_changed(source_path))

        self.detail_layout.addStretch(1)
        self._refresh_track_actions()
        self.review_panel.show_group(group, self.selected_master, self.media_cache,
                                     self._review_alignments())

    def _on_track_changed(self, path: str):
        row = self.source_rows.get(path)
        if row is None:
            self._refresh_track_actions()
            return
        saved_override = {
            "audio_ordinal": row.audio_ordinal,
            "language": row.language_edit.text().strip(),
            "title": row.title_edit.text().strip(),
            "include": row.include_audio.isChecked(),
            "manual_alignment": bool(row.manual_alignment),
        }
        if row.manual_alignment:
            saved_override["offset"] = row.offset_spin.value()
            saved_override["scale"] = row.scale_spin.value()
        self.track_overrides[str(Path(path).expanduser().resolve())] = saved_override
        self._mark_project_dirty()
        self._track_override_timer.start()
        self._refresh_track_actions()
        self.review_panel.refresh(self._review_alignments())

    def _persist_track_overrides(self):
        self._save_settings()

    def _reset_master_override(self, group_key: str):
        group = next((item for item in self.groups if item.key == group_key), None)
        if group is None:
            return
        self.master_overrides.pop(group_key, None)
        self._save_settings()
        self.selected_group = group
        self.selected_master = None
        self._render_group(group)
        self._log(f"Bildmaster auf automatische Auswahl zurückgesetzt: {group.display_name}")

    def _master_changed(self, path: str):
        if not self.selected_group:
            return
        self.selected_master = path
        self.master_overrides[self.selected_group.key] = path
        self._save_settings()
        info = self.media_cache.get(path)
        if info:
            total_duration = max(
                (self.media_cache[p].duration for p in self.selected_group.files if p in self.media_cache),
                default=0,
            )
            added_count = sum(
                1 for item_path in self.selected_group.files
                if item_path != path and item_path in self.media_cache
                and bool(self.media_cache[item_path].audio)
            )
            bitrate = int(self.performance_settings.get("audio_bitrate_kbps", 128))
            estimate = estimate_output_size_bytes(info.size_bytes, info.duration, added_count, bitrate)
            note = (
                f"Manuell ausgewähltes Bildmaster: {Path(path).name} ({info.size_bytes / (1024 * 1024):.1f} MiB). "
                f"Grobe Zielgröße: {estimate / (1024 * 1024):.1f} MiB bei AAC {bitrate} kbit/s. "
            )
            other_durations = [
                self.media_cache[p].duration for p in self.selected_group.files
                if p in self.media_cache and self.media_cache[p].duration > 0
            ]
            if other_durations and max(other_durations) - info.duration > 2.0:
                note += "Achtung: Andere Fassungen sind länger; das Ergebnis übernimmt diese Laufzeit. "
            self.detail_description.setText(
                f"{len(self.selected_group.files)} Fassungen · längste Laufzeit "
                f"{int(total_duration // 60):02d}:{int(total_duration % 60):02d}. "
                + note + "Zusätzliche Audiospuren werden auf diese Zeitachse ausgerichtet."
            )
        for row in self.source_rows.values():
            row.set_master_choice(path)
            if row.path != path:
                key = (path, row.path)
                cached = self.alignments.get(key)
                if cached is None and self.performance_settings.get("cache_enabled", True):
                    cached = self.cache.get(
                        row.path, path,
                        interval=self.performance_settings["sample_interval"],
                        max_samples=self.performance_settings["max_samples"],
                        hardware_accel=self.performance_settings["hardware_accel"],
                    )
                if cached:
                    if not cached.manual_segments:
                        self.automatic_alignments[key] = cached
                        cached = self._restore_manual_segments(cached)
                    self.alignments[key] = cached
                    row.set_alignment(cached)
                else:
                    row.status_label.setText("Noch nicht mit diesem Referenzvideo analysiert")
                    row.status_label.setObjectName("statusMuted")
                    row.status_label.style().unpolish(row.status_label)
                    row.status_label.style().polish(row.status_label)
        self._refresh_track_actions()
        self.review_panel.show_group(self.selected_group, self.selected_master,
                                     self.media_cache, self._review_alignments())

    def _active_track_rows(self) -> list[SourceRow]:
        return [row for path, row in self.source_rows.items() if path != self.selected_master and row.is_included()]

    def _refresh_track_actions(self):
        if not self.selected_group:
            self.analyze_button.setEnabled(False)
            self.export_button.setEnabled(False)
            self.preview_button.setEnabled(False)
            return
        tracks = self._active_track_rows()
        missing_audio = [row for row in tracks if not row.info.audio]
        self.analyze_button.setEnabled(bool(tracks) and self.worker is None)
        all_aligned = bool(tracks) and all((self.selected_master, row.path) in self.alignments for row in tracks)
        self.export_button.setEnabled(all_aligned and not missing_audio and self.worker is None)
        self.preview_button.setEnabled(all_aligned and self.worker is None and any(row.info.audio for row in self.source_rows.values()))

    def _selected_alignment_row(self) -> SourceRow | None:
        focused = self.focusWidget()
        if focused is not None:
            for row in self.source_rows.values():
                if row is focused or row.isAncestorOf(focused):
                    return row
        for row in self.source_rows.values():
            if row.path != self.selected_master and row.is_included():
                return row
        return None

    @staticmethod
    def _media_pair_signature(master: str, source: str) -> dict[str, int] | None:
        try:
            source_stat = Path(source).stat()
            master_stat = Path(master).stat()
        except OSError:
            return None
        return {
            "source_size": source_stat.st_size,
            "source_mtime_ns": source_stat.st_mtime_ns,
            "master_size": master_stat.st_size,
            "master_mtime_ns": master_stat.st_mtime_ns,
        }

    def _restore_manual_segments(self, original: AlignmentResult) -> AlignmentResult:
        """Never reuse an edit for files whose identity or duration has changed."""
        key = (original.target_path, original.source_path)
        record = self.manual_segment_overrides.get(key)
        if record is None:
            return original
        signature = self._media_pair_signature(*key)
        if signature is None or any(record.get(name) != value for name, value in signature.items()):
            self._log(f"Manuelle Schnittbereiche verworfen (Datei verändert): {Path(key[1]).name}")
            self.manual_segment_overrides.pop(key, None)
            self._mark_project_dirty()
            return original
        try:
            segments = [TimelineSegment(
                source_start=record_item["source_start"],
                source_end=record_item["source_end"],
                target_start=record_item["target_start"],
                target_end=record_item["target_end"],
                anchor_count=0, median_hamming=99.0, rms_error=99.0,
                distinct_hashes=0, enabled=record_item["enabled"],
            ) for record_item in record["segments"]]
            problem = validate_timeline_layout(segments, original.source_duration,
                                               original.target_duration)
            if problem:
                raise ValueError(problem)
        except (KeyError, ValueError, TypeError) as exc:
            self._log(f"Manuelle Schnittbereiche nicht übernommen: {exc}")
            self.manual_segment_overrides.pop(key, None)
            self._mark_project_dirty()
            return original
        return replace(original, segments=segments, manual_segments=True, warnings=[
            *original.warnings,
            "Manuelle Abschnittszuordnung aus dem Projekt geladen; Exportfreigabe erforderlich.",
        ])

    def _reset_manual_segments(self, path: str):
        if not self.selected_master:
            return
        key = (self.selected_master, path)
        original = self.automatic_alignments.get(key)
        row = self.source_rows.get(path)
        if original is None or row is None:
            QMessageBox.information(self, "Automatische Zuordnung fehlt",
                                    "Analysiere diese Quelle erneut, um die ursprünglichen Abschnitte wiederherzustellen.")
            return
        self.manual_segment_overrides.pop(key, None)
        self.alignments[key] = original
        row.set_alignment(original)
        self._on_track_changed(path)
        self._log(f"Automatische Schnittbereiche wiederhergestellt: {Path(path).name}")

    def _edit_segments(self, path: str):
        """Manual sections stay unsafe until the user approves the export."""
        if self.worker is not None or not self.selected_master or path == self.selected_master:
            return
        row = self.source_rows.get(path)
        original = self.alignments.get((self.selected_master, path))
        if row is None or original is None:
            QMessageBox.information(self, "Analyse erforderlich",
                                    "Analysiere die Quelle zuerst gegen das aktuelle Bildmaster.")
            return
        baseline = original
        if row.manual_alignment:
            baseline = replace(original, slope=row.scale_spin.value(),
                               offset=row.offset_spin.value(), segments=[], manual_segments=True)
        dialog = SegmentEditorDialog(baseline, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = replace(
            baseline, segments=list(dialog.segments), manual_segments=True,
            warnings=list(dict.fromkeys([
                *baseline.warnings,
                "Schnittbereiche wurden manuell verändert; Export erfordert ausdrückliche Freigabe.",
            ])),
        )
        self.alignments[(self.selected_master, path)] = updated
        signature = self._media_pair_signature(self.selected_master, path)
        if signature is not None:
            self.manual_segment_overrides[(self.selected_master, path)] = {
                "master": self.selected_master,
                "source": path,
                **signature,
                "segments": [
                    {name: getattr(item, name) for name in (
                        "source_start", "source_end", "target_start", "target_end", "enabled"
                    )} for item in updated.segments
                ],
            }
        row.set_alignment(updated)
        self._on_track_changed(path)
        self._log(f"Manuelle Schnittbereiche übernommen: {Path(path).name} · "
                  f"{len(updated.segments)} Abschnitte, "
                  f"{sum(item.enabled for item in updated.segments)} aktiv")

    def _undo_selected_alignment(self):
        row = self._selected_alignment_row()
        if row is None:
            self.statusBar().showMessage("Wähle eine Audiospur aus, um Zeitkorrekturen rückgängig zu machen.", 5000)
            return
        row.undo_alignment()

    def _redo_selected_alignment(self):
        row = self._selected_alignment_row()
        if row is None:
            self.statusBar().showMessage("Keine Audiospur für Wiederholen ausgewählt.", 5000)
            return
        row.redo_alignment()

    def preview_sync_audio(self):
        if not self.selected_group or not self.selected_master:
            QMessageBox.information(self, "Keine Episode ausgewählt", "Wähle zuerst eine Episode aus der Bibliothek.")
            return
        master_info = self.media_cache.get(self.selected_master)
        if master_info is None:
            QMessageBox.information(self, "Metadaten fehlen", "Die Medienmetadaten werden noch geladen. Bitte erneut versuchen, sobald die Episode bereit ist.")
            return
        tracks: list[dict[str, Any]] = []
        for path, row in self.source_rows.items():
            if not row.info.audio:
                continue
            is_master = path == self.selected_master
            if not is_master and not row.is_included():
                continue
            alignment = self.alignments.get((self.selected_master, path)) if not is_master else None
            if not is_master and alignment is None:
                continue
            stream = row.info.audio[row.audio_ordinal]
            title = row.title_edit.text().strip() or language_display_name(row.language_edit.text()) or Path(path).stem
            tracks.append({
                "path": path,
                "stream_index": stream.index,
                "label": f"{title} · {Path(path).name}" + (" (Master)" if is_master else ""),
                "offset": 0.0 if is_master else float(row.offset_spin.value()),
                "scale": 1.0 if is_master else float(row.scale_spin.value()),
                "segments": (list(alignment.segments) if alignment and not row.manual_alignment
                             else []),
                "duration": float(row.info.duration),
                "master": is_master,
                "language": row.language_edit.text().strip() or "und",
            })
        if len(tracks) < 2:
            QMessageBox.information(
                self, "Noch keine synchronisierte Zusatzspur",
                "Führe zuerst die Synchronisationsanalyse aus. Danach kannst du Hörproben der Master- und Zusatzspuren an frühen, mittleren und späten Stellen vergleichen."
            )
            return
        dialog = SyncPreviewDialog(tracks, master_info.duration, self)
        dialog.preview_requested.connect(self._generate_preview_clip)
        dialog.exec()

    def _generate_preview_clip(self, request: dict[str, Any]):
        if self.worker is not None:
            QMessageBox.information(self, "Aufgabe läuft", "Warte bitte, bis die aktuelle Aufgabe abgeschlossen ist, bevor du eine weitere Hörprobe erstellst.")
            return
        sender = self.sender()
        path = str(request.get("path", ""))
        stream_index = int(request.get("stream_index", -1))
        target_time = float(request.get("target_time", 0.0))
        scale = float(request.get("scale", 1.0))
        offset = float(request.get("offset", 0.0))
        duration = max(3.0, min(15.0, float(request.get("sample_duration", 12.0))))
        if not path or stream_index < 0 or not (0.5 <= scale <= 2.0):
            QMessageBox.warning(self, "Ungültige Hörprobe", "Die ausgewählte Audiospur oder Zeitkorrektur ist ungültig.")
            return
        segments = request.get("segments") or []
        if segments and not request.get("master"):
            piece = next((item for item in segments
                          if item.enabled and item.target_start <= target_time < item.target_end), None)
            if piece is None:
                QMessageBox.information(self, "Keine Audioabdeckung",
                                        "Dieser Abschnitt des Bildmasters hat keine bestätigte passende Audioszene. "
                                        "Wähle einen Zeitpunkt innerhalb eines bestätigten Abschnitts.")
                return
            source_time = piece.source_time_for_target(target_time)
            if source_time is None:
                return
            duration = min(duration, piece.source_end - source_time)
        else:
            source_time = target_time if request.get("master") else (target_time - offset) / scale
        source_duration = float(request.get("duration", 0.0))
        if source_time < 0 or source_time + 0.3 >= source_duration or duration < 0.3:
            QMessageBox.information(
                self, "Keine Audioabdeckung",
                f"Die Referenzzeit {target_time:.2f} s liegt für diese Spur außerhalb der verfügbaren Quellzeit (berechnet: {source_time:.2f} s). Wähle einen anderen Zeitpunkt."
            )
            return
        self._preview_dialog = sender if isinstance(sender, SyncPreviewDialog) else None
        output = self._preview_directory / f"preview-{uuid.uuid4().hex[:12]}.wav"
        self._log(
            f"Hörprobe: {request.get('label', Path(path).name)} · Referenz {target_time:.2f}s · Quelle {source_time:.2f}s"
        )

        def extract(report, cancel_event):
            if cancel_event.is_set():
                raise RuntimeError("Hörprobe abgebrochen.")
            ffmpeg, _ffprobe = require_media_tools()
            command = [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{source_time:.4f}", "-i", path,
                "-map", f"0:{stream_index}", "-t", f"{duration:.2f}",
                "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(output),
            ]
            report("Hörprobe aus der synchronisierten Quellzeit extrahieren …", 0, 1)
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("FFmpeg-Zeitlimit beim Erzeugen der Hörprobe.") from exc
            if cancel_event.is_set():
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    pass
                raise RuntimeError("Hörprobe abgebrochen.")
            if result.returncode != 0 or not output.is_file() or output.stat().st_size < 44:
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    pass
                raise RuntimeError("FFmpeg konnte die Hörprobe nicht erzeugen: " + (result.stderr.strip()[-900:] or "leere WAV-Datei"))
            report("Hörprobe bereit", 1, 1)
            return str(output)

        self._set_preview_dialog_busy(True, "Hörprobe wird erzeugt …")
        self._run_task("Erzeuge synchronisierte Hörprobe …", extract, self._preview_clip_ready)

    def _set_preview_dialog_busy(self, busy: bool, message: str | None = None):
        dialog = self._preview_dialog
        if dialog is None:
            return
        try:
            dialog.play_button.setEnabled(not busy)
            if message:
                dialog.status.setText(message)
            if not busy:
                self._preview_dialog = None
        except RuntimeError:
            # The dialog may have been closed while a short extraction was running.
            self._preview_dialog = None

    def _preview_clip_ready(self, path: str):
        self._set_preview_dialog_busy(False, "Hörprobe bereit. Du kannst einen anderen Zeitpunkt wählen und erneut vergleichen.")
        player = shutil.which("ffplay")
        if player:
            try:
                subprocess.Popen(
                    [player, "-nodisp", "-autoexit", "-loglevel", "error", path],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True,
                )
                self.statusBar().showMessage("Hörprobe wird mit ffplay abgespielt.", 5000)
                return
            except OSError:
                pass
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.information(self, "Hörprobe bereit", f"Öffne die WAV-Datei in deinem Player:\n{path}")
        else:
            self.statusBar().showMessage("Hörprobe an den Standard-Audioplayer übergeben.", 5000)

    def analyze_selected(self):
        if not self.selected_group or not self.selected_master:
            return
        tracks = self._active_track_rows()
        if not tracks:
            QMessageBox.information(self, "Keine zusätzlichen Audios", "Wähle mindestens eine zusätzliche Audiospur aus.")
            return
        master = self.selected_master
        sources = [row.path for row in tracks]
        probed_snapshot = {str(Path(k).resolve()): v for k, v in self.media_cache.items()}
        self._run_task(
            f"Analysiere Synchronisation für {self.selected_group.display_name} …",
            lambda report, cancel: self._analyze_sources(
                master, sources, report, cancel, dict(self.performance_settings), probed_snapshot
            ),
            self._analysis_done,
        )

    def _analyze_sources(self, master: str, sources: list[str], report, cancel_event,
                         options: dict[str, Any] | None = None,
                         probed_snapshot: dict[str, MediaInfo] | None = None):
        options = normalize_performance_settings(options or self.performance_settings)
        use_cache = bool(options["cache_enabled"])
        probed_snapshot = probed_snapshot or {}
        master_info = probed_snapshot.get(str(Path(master).resolve()))
        results: list[AlignmentResult] = []
        for index, source in enumerate(sources, 1):
            if cancel_event.is_set():
                raise RuntimeError("Analyse abgebrochen")
            report(f"({index}/{len(sources)}) {Path(source).name}: prüfe Cache …", 0, 0)
            cached = self.cache.get(
                source, master, interval=options["sample_interval"],
                max_samples=options["max_samples"], hardware_accel=options["hardware_accel"],
            ) if use_cache else None
            if cached:
                result = cached
                report(f"Cache verwendet: {Path(source).name}", index, len(sources))
            else:
                def subprogress(phase, current, total):
                    report(f"{Path(source).name} · {phase}", current, total)
                result = align_videos(
                    source, master, interval=options["sample_interval"],
                    progress=subprogress, cancel_event=cancel_event,
                    max_samples=options["max_samples"],
                    hardware_accel=options["hardware_accel"],
                    frame_cache_enabled=bool(options["frame_cache_enabled"]),
                    sampling_workers=int(options["sampling_workers"]),
                    source_info=probed_snapshot.get(str(Path(source).resolve())),
                    target_info=master_info,
                )
                if use_cache:
                    self.cache.put(result)
            results.append(result)
        return results

    def _analysis_done(self, results: list[AlignmentResult]):
        for result in results:
            self.automatic_alignments[(result.target_path, result.source_path)] = result
            result = self._restore_manual_segments(result)
            self.alignments[(result.target_path, result.source_path)] = result
            row = self.source_rows.get(result.source_path)
            if row:
                row.set_alignment(result)
                override = self.track_overrides.get(str(Path(result.source_path).expanduser().resolve()), {})
                if override.get("manual_alignment") and ("offset" in override or "scale" in override):
                    row.manual_alignment = True
                    row.offset_spin.blockSignals(True)
                    row.scale_spin.blockSignals(True)
                    if "offset" in override:
                        row.offset_spin.setValue(float(override["offset"]))
                    if "scale" in override:
                        row.scale_spin.setValue(float(override["scale"]))
                    row.offset_spin.blockSignals(False)
                    row.scale_spin.blockSignals(False)
                    row.reset_alignment_button.setEnabled(True)
                    row.status_label.setText("Gespeicherte manuelle Korrektur · wird vor Export geprüft")
                    row.status_label.setObjectName("statusWarn")
                    row.status_label.style().unpolish(row.status_label)
                    row.status_label.style().polish(row.status_label)
                row.adopt_current_alignment()
            self._log(self._format_alignment(result))
            for warning in result.warnings:
                self._log("WARNUNG: " + warning)
        unsafe = [r for r in results if not r.safe_for_automatic_export]
        if unsafe:
            first = unsafe[0]
            self._set_analysis_insight(
                f"{len(unsafe)} Synchronisation(en) unsicher – kein automatischer Export",
                f"{Path(first.source_path).name}: " +
                (" · ".join(first.warnings[:2]) if first.warnings else
                 "Die Bildanker erfüllen die Sicherheitsgrenzen nicht.") +
                " Öffne den Diagnosebericht für die konkreten Messwerte.", "unsafe")
        elif results:
            self._set_analysis_insight(
                "Synchronisation analysiert", f"{len(results)} Audiospur(en) erfolgreich geprüft. "
                "Kontrolliere den Versatz mit der Hörprobe vor dem Export.", "safe")
        self._refresh_track_actions()
        if self.selected_group:
            self.review_panel.refresh(self._review_alignments())

    @staticmethod
    def _format_alignment(result: AlignmentResult) -> str:
        return (
            f"{Path(result.source_path).name}: Zeitmaßstab {result.slope:.7f}, "
            f"Offset {result.offset:+.3f}s · Konfidenz {result.confidence:.0%} · "
            f"{len(result.anchors)} Anker · Inlier {result.inlier_ratio:.0%} · RMS {result.rms_error:.3f}s"
        )

    def export_batch(self):
        if not self.groups:
            QMessageBox.information(self, "Keine Bibliothek", "Scanne zunächst mindestens einen Medienordner.")
            return
        candidates = [group for group in self.groups if len(group.files) >= 2]
        if not candidates:
            QMessageBox.information(self, "Keine mehrsprachigen Gruppen", "Es wurden keine Episodengruppen mit mindestens zwei Dateien gefunden.")
            return
        output_dir = self.output_dir_edit.text().strip()
        if not output_dir:
            QMessageBox.information(self, "Ausgabeordner fehlt", "Trage oben einen Ausgabeordner ein oder wähle ihn über Durchsuchen aus.")
            self.output_dir_edit.setFocus()
            return
        output_path = Path(output_dir).expanduser()
        if output_path.exists() and not output_path.is_dir():
            QMessageBox.warning(self, "Ungültiger Ausgabeordner", "Der gewählte Ausgabepfad ist eine Datei und kein Ordner.")
            return
        try:
            preview = output_path_for_group(
                str(output_path), candidates[0],
                organize_by_series=self.organize_output_check.isChecked(),
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Ungültiger Ausgabepfad", str(exc))
            return

        skip_existing = self.skip_existing_check.isChecked()
        overwrite_existing = False
        if not skip_existing:
            answer = QMessageBox.warning(
                self, "Vorhandene Ausgaben überschreiben?",
                "Die Option zum Überspringen ist deaktiviert. Bereits vorhandene Zieldateien "
                "können nach erfolgreichem Export ersetzt werden. Quelldateien werden niemals überschrieben.\n\nFortfahren?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            overwrite_existing = True

        root_rules = dict(self.root_languages)
        roots = list(self.roots)
        organize = self.organize_output_check.isChecked()
        # Keep verified single-episode results visible if the confirmation is
        # cancelled. The batch aligner still deliberately recalculates each pair.
        mapping_preview = "\n".join(
            f"  • {group.display_name}: " + " | ".join(Path(path).name for path in group.files[:3])
            for group in candidates[:5]
        )
        if len(candidates) > 5:
            mapping_preview += f"\n  … weitere {len(candidates) - 5} Gruppen"
        answer = QMessageBox.question(
            self, "Serien-Stapel einplanen",
            f"Es werden {len(candidates)} Episodengruppen automatisch verarbeitet.\n\n"
            "Die Stapelposition (z. B. 1/13) ist NICHT die Episodennummer.\n"
            "Vorschau der Dateizuordnung:\n" + mapping_preview + "\n\n"
            "Nur zuverlässig ausgerichtete Gruppen werden exportiert. Episoden mit manuellen "
            "Schnittbereichen werden übersprungen und müssen einzeln bestätigt werden.\n\n"
            f"Beispiel-Ausgabe:\n{preview}\n\n"
            f"Ordnerstruktur: {'Serie / Staffel' if organize else 'ein Ausgabeordner'}\n"
            f"Tracknamen-Regeln: {len(root_rules)}\n"
            "Bildmaster: automatisch kleinste Videodatei je Episode (Laufzeit/Auflösung folgen dieser Datei)\n"
            f"Zusatz-Audio: AAC {self.performance_settings['audio_bitrate_kbps']} kbit/s\n"
            f"Bereits fertig überspringen: {'Ja' if skip_existing else 'Nein'}\n"
            f"Sichere Fortsetzung: {Path(self._resume_journal_path).name if self._resume_journal_path else 'Nein'}\n\n"
            "Ein Stapelprotokoll verifiziert nur bereits abgeschlossene MKVs. "
            "Ungeprüfte Dateien werden niemals stillschweigend ersetzt.\n\n"
            "Ja: einplanen und jetzt starten. Nein: nur dauerhaft einplanen.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return
        if self.batch_queue is None:
            QMessageBox.critical(self, "Warteschlange nicht verfügbar",
                                 "Der Serien-Stapel kann nicht dauerhaft gespeichert werden. "
                                 "Prüfe die Diagnose, bevor du erneut startest.")
            return
        self._save_settings()
        # Immediately after a scan, metadata prewarming can still be querying the
        # same NAS. Give the actual batch job priority to prevent competing reads.
        if self._prewarm_worker is not None and self._prewarm_worker.isRunning():
            self._log("Metadaten-Vorwärmen für Serien-Stapel angehalten (NAS-Zugriffe priorisieren).")
            self._prewarm_worker.cancel(reason="batch_priority")
        groups = list(candidates)
        self._log("Stapel-Zuordnung (Dateinamen, nicht Verarbeitungsreihenfolge):")
        for position, group in enumerate(groups, 1):
            self._log(
                f"  Stapel {position}/{len(groups)} | {group.display_name} "
                f"| Staffel={group.season_number} Folge={group.episode_number} "
                f"| {', '.join(Path(path).name for path in group.files)}"
            )
        options = {
            "roots": roots,
            "root_languages": root_rules,
            "organize_by_series": organize,
            "skip_existing": skip_existing,
            "overwrite_existing": overwrite_existing,
            "track_overrides": dict(self.track_overrides),
            "master_overrides": dict(self.master_overrides),
            "manual_section_sources": sorted({source for _, source in self.manual_segment_overrides}),
            "alignment_options": {
                "interval": self.performance_settings["sample_interval"],
                "max_samples": self.performance_settings["max_samples"],
                "hardware_accel": self.performance_settings["hardware_accel"],
                "cache_enabled": False,
                "frame_cache_enabled": self.performance_settings["frame_cache_enabled"],
                "sampling_workers": self.performance_settings["sampling_workers"],
                "audio_bitrate_kbps": self.performance_settings["audio_bitrate_kbps"],
            },
        }
        try:
            job_id = self.batch_queue.enqueue(
                groups, str(output_path), label=f"{len(groups)} Episoden · {groups[0].series_name or 'Serie'}",
                options=options, initial_resume_journal=self._resume_journal_path,
            )
        except (QueueError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Stapel konnte nicht gespeichert werden", str(exc))
            return
        self._log(f"Serien-Stapel dauerhaft eingeplant ({job_id[:8]}).")
        self._clear_resume_journal()
        if answer == QMessageBox.StandardButton.Yes:
            self._queue_drain_active = False
            self._start_queued_batch(job_id, authorized_for_current_session=True)
        else:
            self.statusBar().showMessage("Stapel eingeplant · Warteschlange über Strg+Umschalt+J öffnen", 7000)

    def _choose_resume_journal(self):
        """Selection is explicit and report compatibility is checked by the core."""
        initial_directory = str(batch_diagnostics_directory())
        selected, _ = QFileDialog.getOpenFileName(
            self, "Früheres Stapelprotokoll zum sicheren Fortsetzen wählen",
            initial_directory, "Stapelprotokoll (batch-*.json);;JSON (*.json)",
        )
        if selected:
            self._resume_journal_path = selected
            self.resume_batch_hint.setText(
                f"Fortsetzen mit {Path(selected).name}: Nur identische Quellen, "
                "Einstellungen und intakte MKVs werden als fertig anerkannt."
            )
            self.resume_batch_button.setText("Protokoll ändern …")

    def _clear_resume_journal(self):
        self._resume_journal_path = None
        self.resume_batch_hint.setText("Fortsetzen: kein Stapelprotokoll ausgewählt")
        self.resume_batch_button.setText("Stapel fortsetzen …")

    def _batch_done(self, result: dict[str, list[str]]):
        self._clear_resume_journal()
        stopped = bool(result.get("cancelled"))
        self._log(
            f"Serien-Stapel {'abgebrochen' if stopped else 'beendet'}: {len(result['exported'])} exportiert, "
            f"{len(result['skipped'])} übersprungen, {len(result['failed'])} fehlgeschlagen."
        )
        for path in result["exported"]:
            self._log("EXPORT: " + path)
        for message in result["skipped"]:
            self._log("ÜBERSPRUNGEN: " + message)
        for message in result["failed"]:
            self._log("FEHLER: " + message)
        for message in result.get("warnings", []):
            self._log("WARNUNG: " + message)
        for message in result.get("cancelled", []):
            self._log("ABBRUCH: " + message)
        # Keep the completion dialogue concise. The Activity screen and the
        # persistent batch report contain ALL per-episode details without
        # hiding them inside an oversized modal window.
        summary = (
            ("Der Stapel wurde gestoppt. Bereits fertiggestellte Dateien bleiben erhalten.\n\n"
             if stopped else "")
            + f"Exportiert: {len(result['exported'])}\n"
            + f"Übersprungen: {len(result['skipped'])}\n"
            + f"Fehlgeschlagen: {len(result['failed'])}"
        )
        if result.get("skipped") or result.get("failed"):
            summary += "\n\nAlle Gründe stehen in der Aktivitätsansicht und im Stapel-Gesamtprotokoll."
        report_path = result.get("batch_report_path")
        if report_path:
            summary += "\n\nVollständiger Stapelbericht gespeichert."
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Serien-Stapel abgebrochen" if stopped else "Serien-Stapel abgeschlossen")
        dialog.setText(summary)
        open_report = dialog.addButton("Stapel-Gesamtprotokoll öffnen", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()
        if dialog.clickedButton() == open_report:
            self._show_batch_diagnostic()

    def export_selected(self):
        if not self.selected_group or not self.selected_master:
            return
        rows = self._active_track_rows()
        if not rows:
            QMessageBox.information(self, "Keine zusätzlichen Audios", "Wähle mindestens eine zusätzliche Audiospur aus.")
            return
        missing = [row for row in rows if (self.selected_master, row.path) not in self.alignments]
        if missing:
            QMessageBox.warning(self, "Analyse erforderlich", "Analysiere zuerst alle ausgewählten Audiospuren für das aktuelle Referenzvideo.")
            return

        tracks: list[TrackChoice] = []
        for row in rows:
            result = self.alignments[(self.selected_master, row.path)]
            # Never reinterpret confirmed piecewise cuts as a global affine fit
            # merely because the spin boxes round their display values.
            if row.manual_alignment:
                chosen_offset = row.offset_spin.value()
                chosen_slope = row.scale_spin.value()
                residuals = [a.target_time - (chosen_slope * a.source_time + chosen_offset)
                             for a in result.anchors]
                threshold = max(0.75, result.sample_interval * 1.20)
                inliers = [abs(value) <= threshold for value in residuals]
                if residuals:
                    rms = (sum(value * value for value in residuals) / len(residuals)) ** 0.5
                    inlier_ratio = sum(inliers) / len(inliers)
                    result = replace(result, offset=chosen_offset, slope=chosen_slope,
                                     rms_error=rms, inlier_ratio=inlier_ratio,
                                     segments=[], manual_segments=False)
                else:
                    result = replace(result, offset=chosen_offset, slope=chosen_slope,
                                     segments=[], manual_segments=False)
            tracks.append(TrackChoice(
                path=row.path, audio_ordinal=row.audio_ordinal,
                language=row.language_edit.text().strip() or "und",
                title=row.title_edit.text().strip() or Path(row.path).stem,
                include=True, alignment=result,
            ))

        low = [track for track in tracks if not track.alignment.safe_for_automatic_export]
        allow_low = False
        if low:
            details = "\n".join(
                f"• {Path(track.path).name}: " +
                ("manuell bearbeitete Schnittbereiche; nicht visuell neu verifiziert"
                 if track.alignment.manual_segments else " / ".join(track.alignment.warnings[:2]))
                for track in low
            )
            reply = QMessageBox.warning(
                self, "Synchronisation prüfen",
                "Mindestens eine Audiospur hat eine manuell bearbeitete oder unsichere Zuordnung.\n\n"
                + details + "\n\nTrotzdem exportieren? Die Ausgabe kann in diesen Bereichen unsynchron sein.",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            allow_low = True

        master_audio_metadata = {}
        master_row = self.source_rows.get(self.selected_master)
        if master_row and master_row.info.audio:
            master_audio_metadata[master_row.audio_ordinal] = (
                master_row.language_edit.text().strip() or "und",
                master_row.title_edit.text().strip() or default_track_title(
                    master_row.language_edit.text(), "", master_row.path
                ),
            )

        default_name = Path(self.selected_master).with_name(Path(self.selected_master).stem + ".multilingual.mkv")
        output, _ = QFileDialog.getSaveFileName(
            self, "Mehrsprachige MKV speichern", str(default_name), "Matroska Video (*.mkv)"
        )
        if not output:
            return
        if not output.lower().endswith(".mkv"):
            output += ".mkv"
        overwrite = False
        if Path(output).exists():
            reply = QMessageBox.question(
                self, "Datei existiert", f"{output}\n\nDatei nach erfolgreichem Zwischenausgang ersetzen?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            overwrite = True

        master = self.selected_master
        self._run_task(
            "Erstelle und prüfe mehrsprachige MKV …",
            lambda report, cancel: export_multilingual(
                master, tracks, output, allow_low_confidence=allow_low,
                overwrite=overwrite, master_audio_metadata=master_audio_metadata,
                progress=lambda percent, message: report(message, percent, 100),
                cancel_event=cancel,
                audio_bitrate_kbps=self.performance_settings["audio_bitrate_kbps"],
            ),
            self._export_done,
        )

    def _export_done(self, path: str):
        self._log(f"EXPORT ABGESCHLOSSEN UND GEPRÜFT: {path}")
        QMessageBox.information(self, "Export abgeschlossen", f"Die Datei wurde geprüft und gespeichert:\n\n{path}")

    @staticmethod
    def _progress_phase(message: str) -> str:
        text = message.casefold()
        phases = (
            (("scanne", "bibliotheksscan"), "Bibliotheksscan"),
            (("metadaten", "ffprobe", "medienstreams", "prüfe datei"), "Metadatenprüfung"),
            (("cache", "bereits fertig"), "Cacheprüfung"),
            (("schnellfingerprint", "fingerprint"), "Datei-Fingerprints"),
            (("mkv", "mux", "export", "audiospur codieren"), "Export"),
            (("stapel", "episode", "gruppe", "kompaktes mastervideo", "überspringe", "prüfe "), "Serienstapel"),
            (("quellvideo", "referenzvideo", "frame", "bildanker", "sample"), "Bildabgleich"),
            (("hörprobe",), "Hörprobe"),
        )
        for needles, label in phases:
            if any(needle in text for needle in needles):
                return label
        return message.split("·", 1)[-1].strip()[:60] or "Verarbeitung"

    @staticmethod
    def _format_eta(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds}s"
        minutes, sec = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}:{sec:02d} min"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d} h"

    def _run_task(self, label: str, task: Callable, on_success: Callable, update_busy: bool = True):
        if self.worker is not None:
            QMessageBox.information(self, "Aufgabe läuft", "Bitte warte, bis der aktuelle Job beendet ist.")
            return
        if self.history is not None:
            try:
                lower = label.casefold()
                kind = ("scan" if "scanne" in lower or "bibliothek" in lower else
                        "stapel-export" if "stapel" in lower or "serien" in lower else
                        "export" if "export" in lower or "mkv" in lower else
                        "synchronisation" if "synchron" in lower or "bildanker" in lower else
                        "medienanalyse")
                output_directory = self.output_dir_edit.text().strip() if hasattr(self, "output_dir_edit") else ""
                self._active_history_job_id = self.history.start(kind, label, list(self.roots), output_directory)
            except Exception:
                self._logger.exception("Job konnte nicht in der Historie gestartet werden")
                self._active_history_job_id = None
        else:
            self._active_history_job_id = None
        self._logger.info("Job gestartet: %s", label)
        self._current_job_is_alignment = ("synchron" in label.casefold() or "bildanker" in label.casefold())
        self._current_job_is_batch = ("stapel" in label.casefold())
        self._job_started_at = time.monotonic()
        self._progress_phase_key = ""
        self._progress_phase_started_at = self._job_started_at
        self._progress_phase_start_fraction = 0.0
        self._progress_last_fraction = None
        self.activity_label.setText(label)
        self.progress_detail.setText("Start · " + label)
        self.insight_panel.hide()
        self.progress.setValue(0)
        self.progress_plot.reset()
        self.pause_button.setText("Pause")
        self.pause_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Stapel stoppen…" if self._current_job_is_batch else "Abbrechen")
        self._pending_success = on_success
        self._set_controls_enabled(False)
        if self._current_job_is_batch:
            self._navigate_to(2)
        worker = TaskWorker(task, self)
        self.worker = worker
        worker.progress.connect(self._worker_progress)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def toggle_pause_current_job(self):
        worker = self.worker
        if worker is None:
            return
        if worker.pause_event.is_set():
            worker.resume()
            if self._active_queue_job_id and self.batch_queue is not None:
                try:
                    self.batch_queue.pause(self._active_queue_job_id, False)
                except Exception:
                    self._logger.exception("Pausenstatus konnte nicht gespeichert werden")
            self.pause_button.setText("Pause")
            self.activity_label.setText("Wird fortgesetzt …")
            self.statusBar().showMessage("Verarbeitung fortgesetzt", 3000)
        else:
            worker.pause()
            if self._active_queue_job_id and self.batch_queue is not None:
                try:
                    self.batch_queue.pause(self._active_queue_job_id, True)
                except Exception:
                    self._logger.exception("Pausenstatus konnte nicht gespeichert werden")
            self.pause_button.setText("Fortsetzen")
            self.activity_label.setText("Pausiert · Abbrechen bleibt jederzeit möglich")
            self.statusBar().showMessage("Pause angefordert · wird am nächsten sicheren Punkt wirksam", 5000)

    def _worker_progress(self, message: str, current: int, total: int):
        now = time.monotonic()
        phase = self._progress_phase(message)
        fraction = (max(0.0, min(1.0, current / total)) if total > 0 else None)
        reset_phase = phase != self._progress_phase_key
        if fraction is not None and self._progress_last_fraction is not None:
            if fraction + 0.05 < self._progress_last_fraction:
                reset_phase = True
        if reset_phase:
            self._progress_phase_key = phase
            self._progress_phase_started_at = now
            self._progress_phase_start_fraction = fraction or 0.0
            self._progress_last_fraction = fraction
        eta_suffix = ""
        if fraction is not None:
            if self._progress_last_fraction is None or fraction >= self._progress_last_fraction:
                self._progress_last_fraction = fraction
            delta = fraction - self._progress_phase_start_fraction
            elapsed = now - self._progress_phase_started_at
            if delta > 0.025 and elapsed >= 1.0 and fraction < 0.995:
                remaining = elapsed * max(0.0, 1.0 - fraction) / delta
                if remaining >= 2:
                    eta_suffix = f" · Restzeit ~{self._format_eta(remaining)}"
            self.progress.setValue(max(0, min(100, int(100 * fraction))))
        self.activity_label.setText(message + eta_suffix)
        self.progress_detail.setText(f"{phase} · " + (f"{current}/{total} " if total > 0 else "") + message)
        if fraction is not None:
            self.progress_plot.add_sample(fraction)
        self.statusBar().showMessage(message + eta_suffix, 7000)
        # Show the exact per-episode reason immediately, even if the user
        # stops a later episode before the final batch summary appears.
        if message.startswith("ÜBERSPRUNGEN:"):
            self._log(message)
        if self._active_queue_job_id and now - self._last_queue_room_refresh >= 2.5:
            self._last_queue_room_refresh = now
            self._refresh_inline_queue()

    def _set_analysis_insight(self, title: str, description: str, status: str = "info") -> None:
        self.insight_title.setText(title)
        self.insight_text.setText(description)
        self.insight_panel.setProperty("state", status)
        self.insight_panel.style().unpolish(self.insight_panel)
        self.insight_panel.style().polish(self.insight_panel)
        self.insight_panel.show()

    def _worker_failed(self, details: str):
        self._set_preview_dialog_busy(False, "Hörprobe fehlgeschlagen. Details stehen im Fehlerdialog und Diagnoseprotokoll.")
        self._logger.error("Hintergrundaufgabe fehlgeschlagen: %s", details)
        self._log("FEHLER: " + details.splitlines()[0])
        self.activity_label.setText("Aufgabe fehlgeschlagen")
        self.progress_detail.setText("Fehlgeschlagen · Details in der Logdatei und Diagnose")
        if self._current_job_is_alignment:
            self._set_analysis_insight("Technischer Fehler", details.splitlines()[0][:350], "error")
        QMessageBox.critical(self, "DubSync Studio – Fehler", details)

    def _worker_finished(self):
        # Erst den alten Worker freigeben und UI zurücksetzen, dann das Resultat
        # anwenden. Ein Scan darf in seiner Erfolgsmeldung sofort den Probe-Worker starten.
        worker = self.worker
        self.worker = None
        callback = self._pending_success
        self._pending_success = None
        error_text = worker.error_text if worker else ""
        result_value = worker.result_value if worker else None
        job_id = self._active_history_job_id
        self._active_history_job_id = None
        if job_id and self.history is not None:
            try:
                if worker and worker.cancelled:
                    self.history.finish(job_id, "abgebrochen", "Vom Benutzer abgebrochen")
                elif error_text:
                    status = "abgebrochen" if "abgebrochen" in error_text.casefold() or "cancel" in error_text.casefold() else "fehlgeschlagen"
                    summary = error_text.splitlines()[0][:500]
                    self.history.finish(job_id, status, summary, error_text=error_text)
                else:
                    details: Any = {}
                    status = "erfolgreich"
                    if isinstance(result_value, dict):
                        details = {key: list(value[:100]) if isinstance(value, list) else value
                                   for key, value in result_value.items()}
                        exported = len(result_value.get("exported", []))
                        skipped = len(result_value.get("skipped", []))
                        failed = len(result_value.get("failed", []))
                        summary = f"{exported} exportiert · {skipped} übersprungen · {failed} fehlgeschlagen"
                        if failed or result_value.get("warnings"):
                            status = "mit Warnungen"
                    elif isinstance(result_value, list):
                        details = {"count": len(result_value)}
                        summary = f"{len(result_value)} Elemente verarbeitet"
                    elif isinstance(result_value, str):
                        details = {"output": result_value}
                        summary = result_value
                    else:
                        summary = "Aufgabe erfolgreich abgeschlossen"
                    self.history.finish(job_id, status, summary, details=details)
            except Exception:
                self._logger.exception("Job konnte nicht abgeschlossen in die Historie geschrieben werden")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Abbrechen")
        self.pause_button.setEnabled(False)
        self.pause_button.setText("Pause")
        self._set_controls_enabled(True)
        self._refresh_workflow_summary()
        # Keep the last result visible until the next job. Resetting to 0 on
        # completion made successful analyses look as if nothing had happened.
        if not error_text and not (worker and worker.cancelled):
            self.progress.setValue(100)
        if worker:
            worker.deleteLater()
        if error_text:
            self._worker_failed(error_text)
            self._finish_queued_task(interrupted=True)
            if getattr(self, "_prewarm_refresh_pending", False):
                QTimer.singleShot(0, self._refresh_after_prewarm)
            return
        if worker and worker.cancelled:
            reason = getattr(worker.cancel_event, "reason", None)
            explanation = ("Du hast die Verarbeitung über ‚Abbrechen‘ beendet."
                           if reason in ("user_button", "user_button_confirmed") else
                           "Die Verarbeitung wurde beim Schließen des Fensters gestoppt."
                           if reason == "window_close" else
                           "Die Verarbeitung wurde beendet; der Bildabgleich ist nicht als unsicher bewertet worden.")
            if self._current_job_is_alignment:
                self._set_analysis_insight("Analyse abgebrochen – kein Synchronisationsurteil", explanation +
                                           " Der Diagnosebericht enthält die bis zum Abbruch gelesenen Frames.", "cancelled")
                self.progress_detail.setText("Abgebrochen · keine Bewertung der Synchronisation")
            else:
                self.progress_detail.setText("Aufgabe abgebrochen")
            self.activity_label.setText("Abgebrochen · bereits abgeschlossene Dateien bleiben erhalten")
            self.statusBar().showMessage("Aufgabe abgebrochen", 5000)
            self._log(f"ABGEBROCHEN: Verarbeitung angehalten · Auslöser={reason or 'unbekannt'}")
            if self._current_job_is_batch and isinstance(result_value, dict) and callback:
                # Display finished/skipped/failed episodes even after cancellation.
                callback(result_value)
            self._finish_queued_task(interrupted=True)
            if getattr(self, "_prewarm_refresh_pending", False):
                QTimer.singleShot(0, self._refresh_after_prewarm)
            return
        self.activity_label.setText("Bereit")
        self.progress_detail.setText("Aufgabe abgeschlossen")
        self._refresh_track_actions()
        if callback:
            callback(result_value)
        self._finish_queued_task()
        if getattr(self, "_prewarm_refresh_pending", False):
            QTimer.singleShot(0, self._refresh_after_prewarm)

    def cancel_current_job(self):
        worker = self.worker
        if worker is None:
            return
        reason = "user_button"
        if self._current_job_is_batch:
            answer = QMessageBox.question(
                self, "Gesamten Stapel wirklich stoppen?",
                "Die Analyse der aktuellen Episode wird gestoppt, die übrigen Folgen "
                "werden nicht mehr bearbeitet. Bereits exportierte Dateien bleiben erhalten.\n\n"
                "Möchtest du den gesamten Stapel tatsächlich abbrechen?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._log("Stapel-Abbruchdialog verworfen; Verarbeitung läuft weiter.")
                return
            reason = "user_button_confirmed"
        self._log(f"Abbruch ausdrücklich angefordert: {reason}")
        worker.cancel(reason=reason)
        self.progress_detail.setText("Abbruch angefordert · laufender Schritt wird beendet")
        self.activity_label.setText("Abbruch angefordert …")
        self.cancel_button.setEnabled(False)

    def _set_controls_enabled(self, enabled: bool):
        self.add_folder_button.setEnabled(enabled)
        self.network_button.setEnabled(enabled)
        self.remove_folder_button.setEnabled(enabled)
        self.scan_button.setEnabled(enabled)
        self.batch_button.setEnabled(enabled and bool(self.groups))
        self.preview_button.setEnabled(enabled and bool(self.selected_group) and bool(self.alignments))
        self.root_list.setEnabled(enabled)
        self.output_dir_edit.setEnabled(enabled)
        self.output_browse_button.setEnabled(enabled)
        self.organize_output_check.setEnabled(enabled)
        self.skip_existing_check.setEnabled(enabled)
        self.resume_batch_button.setEnabled(enabled)
        self.root_languages_button.setEnabled(enabled)
        self.episode_table.setEnabled(enabled)
        self.episode_search.setEnabled(enabled)
        self.episode_filter.setEnabled(enabled)
        self.detail_scroll.setEnabled(enabled)
        if hasattr(self, "profile_quick_combo"):
            self.profile_quick_combo.setEnabled(enabled)
        if hasattr(self, "queue_room"):
            self._refresh_inline_queue()
        if hasattr(self, "review_panel"):
            self.review_panel.set_processing_enabled(enabled)
        # Track checks remain visually stable; export buttons are recalculated on completion.
        self.analyze_button.setEnabled(enabled and bool(self._active_track_rows()))
        self.export_button.setEnabled(enabled and bool(self._active_track_rows()) and all(
            (self.selected_master, row.path) in self.alignments for row in self._active_track_rows()
        ))

    def _toggle_log_view(self, visible: bool):
        self.log_view.setVisible(bool(visible))
        self.log_toggle_button.setText("Details ausblenden" if visible else "Protokoll")

    def _log(self, text: str):
        safe = text.replace("\x00", "")
        self._logger.info("%s", safe)
        self.log_view.append(safe)

    def closeEvent(self, event):
        if self._prewarm_worker and self._prewarm_worker.isRunning():
            self._prewarm_worker.cancel()
            if not self._prewarm_worker.wait(3000):
                QMessageBox.information(
                    self, "Metadaten werden noch geladen",
                    "Der Hintergrund-Cache wird gerade beendet. Bitte warte, bis die laufenden Dateioperationen abgeschlossen sind, und schließe das Fenster dann erneut."
                )
                event.ignore()
                return
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, "Job läuft", "Eine Aufgabe ist noch aktiv. Trotzdem schließen?",
                QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.cancel(reason="window_close")
            self.worker.wait(3000)
            if self.worker.isRunning():
                QMessageBox.information(
                    self, "Job wird beendet",
                    "Die aktuelle Medienanalyse konnte noch nicht beendet werden. "
                    "Bitte schließe das Fenster nach Abschluss der Aufgabe."
                )
                event.ignore()
                return
        if self._track_override_timer.isActive():
            self._track_override_timer.stop()
            self._save_settings()
        if not self._confirm_replace_project():
            event.ignore()
            return
        if not self.review_panel.stop_workers():
            QMessageBox.information(self, "Bildvorschau wird beendet",
                                    "FFmpeg beendet gerade den Framevergleich. Bitte kurz warten.")
            event.ignore()
            return
        self._save_settings()
        self._session_cache_dir.cleanup()
        event.accept()


def main() -> int:
    configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("dubsync-studio")
    app.setApplicationDisplayName(APP_TITLE)
    app.setWindowIcon(QIcon(str(APP_LOGO)))
    # Helps desktop shells group the window with dubsync-studio.desktop.
    if hasattr(app, "setDesktopFileName"):
        app.setDesktopFileName("dubsync-studio")
    app.setOrganizationName("DubSyncStudio")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()
