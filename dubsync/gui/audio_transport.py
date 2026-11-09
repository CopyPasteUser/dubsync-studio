"""Bounded in-app transport for locally extracted WAV audition windows.

Does not decode original media, alter alignment evidence, or issue exports.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QWidget


class AudioPreviewTransport(QWidget):
    """Playback and seek of a *completed*, at most 60-second local preview."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = None
        self.audio_output = None
        self._dragging = False
        self._source_path: Path | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.toggle = QPushButton("Abspielen")
        self.toggle.setAccessibleName("Hörprobe abspielen oder pausieren")
        self.toggle.setEnabled(False)
        layout.addWidget(self.toggle)
        self.stop_button = QPushButton("Stopp")
        self.stop_button.setAccessibleName("Hörprobe stoppen")
        self.stop_button.setEnabled(False)
        layout.addWidget(self.stop_button)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("Wiedergabeposition innerhalb der Hörprobe")
        self.seek.setRange(0, 0)
        layout.addWidget(self.seek, 1)
        self.position_label = QLabel("00:00 / 00:00")
        self.position_label.setObjectName("mutedText")
        layout.addWidget(self.position_label)
        # Offscreen Qt has no sound server; initializing PipeWire-backed
        # QAudioOutput there can crash within the native audio plugin.
        if QGuiApplication.platformName() == "offscreen":
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)
            self.player.durationChanged.connect(self._duration_changed)
            self.player.positionChanged.connect(self._position_changed)
            self.player.playbackStateChanged.connect(self._state_changed)
            self.player.errorOccurred.connect(self._playback_error)
            self.toggle.clicked.connect(self.toggle_playback)
            self.stop_button.clicked.connect(self.stop)
            self.seek.sliderPressed.connect(self._start_scrub)
            self.seek.sliderReleased.connect(self._finish_scrub)
        except (ImportError, OSError):
            # The existing external-player fallback is used by MainWindow.
            self.player = None

    @property
    def available(self) -> bool:
        return self.player is not None

    def play_clip(self, path: str) -> bool:
        if self.player is None or not Path(path).is_file():
            return False
        self.stop()
        self._source_path = Path(path)
        self.player.setSource(QUrl.fromLocalFile(str(self._source_path)))
        self.toggle.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.player.play()
        return True

    def toggle_playback(self):
        if self.player is None or self._source_path is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self):
        if self.player is not None:
            self.player.stop()

    def release(self):
        if self.player is not None:
            self.stop()
            self.player.setSource(QUrl())
        self._source_path = None

    def _start_scrub(self):
        self._dragging = True

    def _finish_scrub(self):
        self._dragging = False
        if self.player is not None:
            self.player.setPosition(self.seek.value())

    def _duration_changed(self, milliseconds: int):
        self.seek.setMaximum(max(0, min(60_000, milliseconds)))
        self._position_changed(self.seek.value())

    def _position_changed(self, milliseconds: int):
        if not self._dragging:
            self.seek.setValue(max(0, min(self.seek.maximum(), milliseconds)))
        self.position_label.setText(
            f"{self.seek.value() // 60_000:02d}:{self.seek.value() // 1000 % 60:02d} / "
            f"{self.seek.maximum() // 60_000:02d}:{self.seek.maximum() // 1000 % 60:02d}")

    def _state_changed(self, _state):
        if self.player is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer
        self.toggle.setText("Pause" if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                            else "Abspielen")

    def _playback_error(self, code, _description):
        from PySide6.QtMultimedia import QMediaPlayer
        if code != QMediaPlayer.Error.NoError:
            self.toggle.setEnabled(False)
            self.position_label.setText("Audio nicht verfügbar")
