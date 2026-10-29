"""Native 2.0 inspection workbench: evidence timeline and on-demand frame comparison.

The review is deliberately read-only. A frame is a visual aid, never an export
authorization or a substitute for the core visual-coverage safety checks.
"""
from __future__ import annotations

import math
from collections import Counter
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QSlider, QTabWidget, QVBoxLayout, QWidget,
)

from dubsync.core.models import AlignmentResult, EpisodeGroup, MediaInfo
from dubsync.gui.theme import TOKENS


def formatted_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def review_position(alignment: AlignmentResult | None, target_time: float,
                    source_duration: float) -> tuple[float | None, str]:
    """Map master time into source time, without claiming an unverified match.

    A no-match interval in a piecewise mapping must stay empty. Never display
    a guessed source frame there as if it corresponded to the picture master.
    """
    if alignment is None:
        time_point = target_time
        state = "Nomineller Zeitvergleich · kein Bildabgleich vorhanden"
    else:
        time_point = alignment.source_time(target_time)
        state = ("Visuell geprüfte Zuordnung" if alignment.safe_for_automatic_export
                 else "Vorläufige Zuordnung · Sicherheitsschwelle nicht erreicht")
    if time_point is None:
        return None, "Kein zugeordnetes Bild · Abschnitt bleibt im Export stumm"
    if not math.isfinite(time_point) or not 0 <= time_point < source_duration:
        return None, "Kein Quellbild an dieser Position"
    return time_point, state


def _extract_frame(path: str, seconds: float, *, timeout: float,
                   cancelled: threading.Event) -> bytes:
    """Extract one bounded still from a local file/mount, without writing media."""
    # This process only runs after an explicit user click. It never scans NAS
    # paths at startup, and FFmpeg protocol access stays restricted to files.
    process = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-protocol_whitelist", "file,pipe", "-ss", f"{seconds:.3f}",
         "-i", str(path), "-map", "0:v:0", "-an", "-sn", "-dn",
         "-frames:v", "1", "-vf", "scale=640:360:force_original_aspect_ratio=decrease",
         "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    started = time.monotonic()
    try:
        # communicate() drains the pipes; short checkpoints let closing the
        # window kill the subprocess instead of blocking on an absent NAS.
        while True:
            if cancelled.is_set():
                raise RuntimeError("Bildvorschau abgebrochen")
            try:
                output, error = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() - started > timeout:
                    raise RuntimeError("Bildvorschau: Zeitüberschreitung (Quelle erreichbar?)")
        if process.returncode != 0 or not output:
            description = error.decode("utf-8", errors="replace").strip()[-240:]
            raise RuntimeError(description or "FFmpeg konnte keinen Videoframe lesen")
        if len(output) > 3_000_000:
            raise RuntimeError("Vorschaubild zu groß")
        return output
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


@dataclass(frozen=True)
class Frames:
    master: bytes
    source: bytes | None


class FrameReviewWorker(QThread):
    ready = Signal(object)
    error = Signal(str)

    def __init__(self, master: str, source: str, master_time: float,
                 source_time: float | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.master = master
        self.source = source
        self.master_time = master_time
        self.source_time = source_time
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        try:
            master = _extract_frame(self.master, self.master_time,
                                    timeout=12, cancelled=self._cancelled)
            source = (_extract_frame(self.source, self.source_time,
                                     timeout=12, cancelled=self._cancelled)
                      if self.source_time is not None and not self._cancelled.is_set() else None)
            if not self._cancelled.is_set():
                self.ready.emit(Frames(master, source))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            if not self._cancelled.is_set():
                self.error.emit(str(exc))


class FrameViewport(QLabel):
    """Keep the original decoded pixmap so resizing never compounds scaling."""

    def __init__(self, text: str = "Vorschau auf Abruf", parent=None):
        super().__init__(text, parent)
        self._original: QPixmap | None = None

    def show_frame(self, data: bytes | None) -> bool:
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            self.reset_image("Kein zugeordnetes Videobild")
            return False
        self._original = pixmap
        self.setText("")
        self._scale_to_view()
        return True

    def reset_image(self, text: str) -> None:
        self._original = None
        self.setPixmap(QPixmap())
        self.setText(text)

    def _scale_to_view(self) -> None:
        if self._original is not None:
            self.setPixmap(self._original.scaled(max(1, self.width()), max(1, self.height()),
                                                  Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._scale_to_view()


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
        self.setMinimumHeight(125)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Synchronisations-Timeline")
        self.setToolTip("Klick: Position · Pfeiltasten: ±5 s · Bild auf/ab: ±30 s · Plus/Minus: Zoom")

    def set_data(self, duration: float, alignment: AlignmentResult | None) -> None:
        self.duration = max(0.0, float(duration))
        self.alignment = alignment
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
        painter.drawText(15, 90, "MANUELL · NICHT VERIFIZIERT" if manual else
                         "BILDANKER · DUNKLE LÜCKEN UNGEPRÜFT")
        painter.fillRect(QRectF(15, 96, width, 8), QColor(TOKENS["raised"]))
        if self.alignment:
            safe = self.alignment.safe_for_automatic_export
            valid = [segment for segment in self.alignment.segments if segment.enabled]
            if valid:
                for segment in valid:
                    left = max(15.0, self._x(segment.target_start, width))
                    right = min(15.0 + width, self._x(segment.target_end, width))
                    if right > left:
                        painter.fillRect(QRectF(left, 40, right - left, 23), QColor(TOKENS["selection"]))
                        painter.fillRect(QRectF(left, 96, right - left, 8),
                                         QColor(TOKENS["accent"] if safe else TOKENS["warning"]))
            # A linear result has verified anchors, not a continuous known-good
            # bar. Paint only observed points; gaps remain conspicuously dark.
            tolerance = max(.75, self.alignment.sample_interval * 1.2)
            for anchor in self.alignment.anchors:
                if anchor.hamming_distance > 15:
                    continue
                if (not 0 <= anchor.source_time <= self.alignment.source_duration or
                        abs(anchor.target_time - self.alignment.target_time(anchor.source_time)) > tolerance):
                    continue
                if self.alignment.segments and not any(
                    s.enabled and s.source_start <= anchor.source_time <= s.source_end and
                    s.target_start <= anchor.target_time <= s.target_end
                    for s in self.alignment.segments
                ):
                    continue
                if (anchor.target_time < self.window_start or
                        anchor.target_time > self.window_start + self._visible_span()):
                    continue
                painter.setPen(QPen(QColor(TOKENS["warning"] if manual else TOKENS["accent"]), 1.4))
                x = self._x(anchor.target_time, width)
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


class StudioReviewPanel(QFrame):
    """Synchronized review, populated by the main window's selected episode."""

    compareRequested = Signal(str, str, float, object)
    sourceChanged = Signal(str)
    audioRequested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("reviewPanel")
        self.setMinimumWidth(290)
        self.setAccessibleName("Studio-Vergleich und Bildanker")
        self._master: str | None = None
        self._source: str | None = None
        self._media: dict[str, MediaInfo] = {}
        self._alignment: AlignmentResult | None = None
        self._generation = 0
        self._frame_worker: FrameReviewWorker | None = None
        self._can_load = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 13, 13, 13)
        layout.setSpacing(10)
        heading = QLabel("ABGLEICH-MONITOR")
        heading.setObjectName("eyebrow")
        layout.addWidget(heading)
        self.episode_label = QLabel("Keine Episode ausgewählt")
        self.episode_label.setObjectName("sectionTitle")
        self.episode_label.setWordWrap(True)
        layout.addWidget(self.episode_label)
        source_line = QHBoxLayout()
        source_line.addWidget(QLabel("Vergleich"))
        self.source_combo = QComboBox()
        self.source_combo.setAccessibleName("Quellfassung für Bildvergleich")
        self.source_combo.currentIndexChanged.connect(self._source_selected)
        source_line.addWidget(self.source_combo, 1)
        layout.addLayout(source_line)
        self.quality_label = QLabel("Scanne eine Folge und wähle eine Fassung für die Analyse.")
        self.quality_label.setObjectName("reviewVerdict")
        self.quality_label.setWordWrap(True)
        layout.addWidget(self.quality_label)
        self.review_tabs = QTabWidget()
        self.review_tabs.setObjectName("reviewTabs")
        self.review_tabs.setAccessibleName("Abgleich-Monitor: Zeitachse oder Standbildvergleich")
        layout.addWidget(self.review_tabs, 1)
        timeline_page = QWidget()
        timeline_layout = QVBoxLayout(timeline_page)
        timeline_layout.setContentsMargins(5, 12, 5, 5)
        timeline_layout.setSpacing(9)
        self.review_tabs.addTab(timeline_page, "Zeitachse")
        frames_page = QWidget()
        frames_layout = QVBoxLayout(frames_page)
        frames_layout.setContentsMargins(5, 12, 5, 5)
        frames_layout.setSpacing(9)
        self.review_tabs.addTab(frames_page, "Framevergleich")

        self.timeline = AlignmentTimeline()
        self.timeline.positionChanged.connect(self._timeline_moved)
        timeline_layout.addWidget(self.timeline, 1)
        position_row = QHBoxLayout()
        self.position_label = QLabel("00:00 / 00:00")
        self.position_label.setObjectName("mutedText")
        position_row.addWidget(self.position_label)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.setAccessibleName("Position auf Bildmaster-Zeitachse")
        self.seek.valueChanged.connect(self._seek_changed)
        position_row.addWidget(self.seek, 1)
        zoom_out = QPushButton("−")
        zoom_out.setAccessibleName("Timeline verkleinern")
        zoom_out.clicked.connect(lambda: self.timeline.set_zoom(self.timeline.zoom / 2))
        zoom_in = QPushButton("+")
        zoom_in.setAccessibleName("Timeline vergrößern")
        zoom_in.clicked.connect(lambda: self.timeline.set_zoom(self.timeline.zoom * 2))
        position_row.addWidget(zoom_out)
        position_row.addWidget(zoom_in)
        timeline_layout.addLayout(position_row)

        controls = QHBoxLayout()
        self.compare_button = QPushButton("Frames laden")
        self.compare_button.setObjectName("primaryButton")
        self.compare_button.setEnabled(False)
        self.compare_button.setToolTip("Lädt exakt zwei Standbilder auf Knopfdruck – keine automatische NAS-Abfrage")
        self.compare_button.clicked.connect(self._request_comparison)
        controls.addWidget(self.compare_button, 1)
        preview = QPushButton("Hörprobe …")
        preview.clicked.connect(self.audioRequested)
        controls.addWidget(preview)
        frames_layout.addLayout(controls)
        self.frame_status = QLabel("Kein Frame geladen. Die Timeline zeigt nur Messdaten.")
        self.frame_status.setObjectName("mutedText")
        self.frame_status.setWordWrap(True)
        frames_layout.addWidget(self.frame_status)
        self.frames = []
        frames_row = QHBoxLayout()
        frames_row.setSpacing(8)
        for caption in ("BILDMASTER", "SPRACHFASSUNG"):
            panel = QFrame()
            panel.setObjectName("previewFrame")
            panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            frame_layout = QVBoxLayout(panel)
            frame_layout.setContentsMargins(7, 7, 7, 7)
            frame_layout.setSpacing(5)
            title = QLabel(caption)
            title.setObjectName("eyebrow")
            frame_layout.addWidget(title)
            preview = FrameViewport("Vorschau auf Abruf")
            preview.setObjectName("previewImage")
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setMinimumHeight(118)
            preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
            preview.setWordWrap(True)
            frame_layout.addWidget(preview, 1)
            frames_row.addWidget(panel, 1)
            self.frames.append(preview)
        frames_layout.addLayout(frames_row, 1)

    def reset(self, message: str = "Keine Episode ausgewählt") -> None:
        self._generation += 1
        self._master = None
        self._source = None
        self._media = {}
        self._alignment = None
        self.episode_label.setText(message)
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.blockSignals(False)
        self.timeline.set_data(0, None)
        self.compare_button.setEnabled(False)
        self._clear_frames()
        self.quality_label.setText("Fassungen und Analyseergebnisse erscheinen nach dem Scan.")
        self.quality_label.setProperty("state", "unknown")
        self.quality_label.style().unpolish(self.quality_label)
        self.quality_label.style().polish(self.quality_label)

    def show_group(self, group: EpisodeGroup, master: str | None,
                   media: dict[str, MediaInfo], alignments: dict[tuple[str, str], AlignmentResult]) -> None:
        self._generation += 1
        previous = self._source
        self.episode_label.setText(group.display_name)
        self._master = master
        self._media = media
        candidates = [path for path in group.files if path != master and path in media and media[path].video]
        names = Counter(Path(path).name for path in candidates)
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for path in candidates:
            file = Path(path)
            label = f"{file.parent.name} / {file.name}" if names[file.name] > 1 else file.name
            self.source_combo.addItem(label, path)
            self.source_combo.setItemData(self.source_combo.count() - 1, path, Qt.ItemDataRole.ToolTipRole)
        if previous in candidates:
            self.source_combo.setCurrentIndex(candidates.index(previous))
        self.source_combo.blockSignals(False)
        self._source = self.source_combo.currentData()
        self._set_alignment(alignments.get((master or "", self._source or "")))

    def refresh(self, alignments: dict[tuple[str, str], AlignmentResult]) -> None:
        updated = alignments.get((self._master or "", self._source or ""))
        if updated == self._alignment:
            return
        position, zoom = self.timeline.position, self.timeline.zoom
        self._set_alignment(updated)
        self.timeline.set_zoom(zoom)
        self.timeline.set_position(position)
        self.seek.blockSignals(True)
        self.seek.setValue(int(self.timeline.position / max(.001, self.timeline.duration) * 1000))
        self.seek.blockSignals(False)
        self.position_label.setText(f"{formatted_time(self.timeline.position)} / {formatted_time(self.timeline.duration)}")

    def _source_selected(self, _index: int) -> None:
        self._source = self.source_combo.currentData()
        self._generation += 1
        self._clear_frames()
        if self._source:
            self.sourceChanged.emit(self._source)

    def _set_alignment(self, alignment: AlignmentResult | None) -> None:
        self._alignment = alignment
        duration = self._media[self._master].duration if self._master in self._media else 0.0
        self.timeline.set_data(duration, alignment)
        self.seek.blockSignals(True)
        self.seek.setValue(0)
        self.seek.blockSignals(False)
        self.compare_button.setEnabled(bool(self._can_load and self._source and self._master and duration > 0 and
                                            self._frame_worker is None))
        self._clear_frames()
        if alignment is None:
            self.quality_label.setText("NOCH NICHT ANALYSIERT · Bildvergleich nur nominell, kein Match-Nachweis")
            self.quality_label.setProperty("state", "unknown")
        else:
            verdict = "AUTO-EXPORT FREIGEGEBEN" if alignment.safe_for_automatic_export else "PRÜFUNG ERFORDERLICH"
            self.quality_label.setText(
                f"{verdict} · {alignment.confidence:.0%} Konfidenz · "
                f"{len(alignment.anchors)} Anker · {alignment.coverage:.0%} Abdeckung\n"
                f"Größte Lücke Mitte {alignment.max_unverified_middle_ratio:.1%} · "
                f"Rand {alignment.max_unverified_edge_ratio:.1%}"
            )
            self.quality_label.setProperty("state", "safe" if alignment.safe_for_automatic_export else "unsafe")
        self.quality_label.style().unpolish(self.quality_label)
        self.quality_label.style().polish(self.quality_label)
        self._position_changed(0)
        self.frame_status.setText("Bildvorschau auf Abruf · die Timeline zeigt nur Messdaten.")

    def _position_changed(self, seconds: float) -> None:
        # In-flight frames belong to their original timestamp. Once the cursor
        # moves, discard the result rather than mislabel it as the new position.
        self._generation += 1
        self.position_label.setText(f"{formatted_time(seconds)} / {formatted_time(self.timeline.duration)}")
        self.frame_status.setText("Position geändert · Frames bei Bedarf neu laden")
        for label in self.frames:
            label.reset_image("Noch kein Frame an dieser Position")

    def _seek_changed(self, value: int) -> None:
        position = self.timeline.duration * value / 1000
        self.timeline.set_position(position)
        self._position_changed(position)

    def _timeline_moved(self, seconds: float) -> None:
        self.seek.blockSignals(True)
        self.seek.setValue(int(seconds / max(0.001, self.timeline.duration) * 1000))
        self.seek.blockSignals(False)
        self._position_changed(seconds)

    def _clear_frames(self) -> None:
        for label in self.frames:
            label.reset_image("Vorschau auf Abruf")
        self.frame_status.setText("Die Vorschau liest erst nach Klick Mediendaten.")

    def set_processing_enabled(self, enabled: bool) -> None:
        self._can_load = enabled
        self.compare_button.setEnabled(bool(enabled and self._master and self._source and
                                            self.timeline.duration > 0 and self._frame_worker is None))

    def _request_comparison(self) -> None:
        if self._frame_worker is not None or not self._master or not self._source:
            return
        media = self._media.get(self._source)
        if media is None:
            return
        source_time, state = review_position(self._alignment, self.timeline.position, media.duration)
        self._clear_frames()
        self.frame_status.setText(f"Lade Standbilder … {state}")
        self.compare_button.setEnabled(False)
        generation = self._generation
        worker = FrameReviewWorker(self._master, self._source, self.timeline.position, source_time, self)
        self._frame_worker = worker

        def success(result: Frames) -> None:
            if generation != self._generation:
                return
            for image, data in zip(self.frames, (result.master, result.source)):
                image.show_frame(data)
            self.frame_status.setText(f"Framevergleich bei {formatted_time(self.timeline.position)} · {state}")

        def failed(message: str) -> None:
            if generation == self._generation:
                self.frame_status.setText(f"Bildvergleich nicht möglich: {message}")

        def finished() -> None:
            if self._frame_worker is worker:
                self._frame_worker = None
            worker.deleteLater()
            self.compare_button.setEnabled(bool(self._can_load and self._master and self._source and
                                                self.timeline.duration > 0))

        worker.ready.connect(success)
        worker.error.connect(failed)
        worker.finished.connect(finished)
        worker.start()

    def stop_workers(self) -> bool:
        worker = self._frame_worker
        if worker is None:
            return True
        worker.cancel()
        return worker.wait(3500)
