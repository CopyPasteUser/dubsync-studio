"""Exercise the native workspace without reading the user's media or preferences."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea
from dubsync.gui.main_window import MainWindow, SourceRow
from dubsync.core.models import EpisodeGroup, MediaInfo, StreamInfo

_APP = None

@pytest.fixture
def window(tmp_path, monkeypatch):
    for name in ('CONFIG', 'CACHE', 'STATE'):
        monkeypatch.setenv(f'XDG_{name}_HOME', str(tmp_path / name))
    global _APP
    _APP = QApplication.instance() or QApplication([])
    app = _APP
    w = MainWindow()
    w.show()
    app.processEvents()
    yield w
    w.project_dirty = False
    w.close()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_navigation_preserves_fields_and_profile(window):
    window.output_dir_edit.setText('/tmp/exports')
    index = window.profile_quick_combo.findData('fast')
    window.profile_quick_combo.setCurrentIndex(index)
    for i in (1, 2, 0):
        QTest.mouseClick(window._nav_buttons[i], Qt.MouseButton.LeftButton)
        assert window._page_stack.currentIndex() == i
        assert sum(button.isChecked() for button in window._nav_buttons) == 1
    assert window.output_dir_edit.text() == '/tmp/exports'
    assert window.performance_settings['preset'] == 'fast'
    window._nav_buttons[0].setFocus()
    QTest.keyClick(window._nav_buttons[0], Qt.Key.Key_Right)
    assert window._page_stack.currentIndex() == 1


def test_no_results_and_clear_restore_rows_immediately(window, monkeypatch):
    monkeypatch.setattr(window, '_start_metadata_prewarm', lambda: None)
    window.episode_table.blockSignals(True)
    window._scan_done([EpisodeGroup('one', 'Example S01E01', ['de.mkv', 'en.mkv'])])
    window.episode_table.blockSignals(False)
    window.episode_search.setText('missing')
    window._filter_episodes()
    assert window.episode_surface.currentIndex() == 0
    assert 'Keine passenden' in window.library_empty.text()
    window.episode_search.clear()
    assert window.episode_surface.currentIndex() == 1
    assert not window.episode_table.isRowHidden(0)
    assert not window.episode_filter_timer.isActive()


def test_switching_light_medium_strong_preserves_analysis_and_gpu_auto(window):
    window.performance_settings["sample_interval"] = 0.5
    window.performance_settings["max_samples"] = 1700
    window.performance_settings["audio_bitrate_kbps"] = 160
    window._apply_performance_settings(log_change=False)
    budgets = []
    for preset in ("fast", "balanced", "quality"):
        index = window.profile_quick_combo.findData(preset)
        window.profile_quick_combo.setCurrentIndex(index)
        options = window.performance_settings
        budgets.append(options["cpu_threads"])
        assert options["preset"] == preset
        assert options["hardware_accel"] == "auto"
        assert (options["sample_interval"], options["max_samples"],
                options["audio_bitrate_kbps"]) == (0.5, 1700, 160)
    assert budgets == sorted(budgets)


def test_sources_collapse_without_removing_paths(window):
    window.roots = ['/tmp/a', '/tmp/b']
    window._refresh_roots()
    window.sources_toggle.setChecked(True)
    assert not window.root_list.isHidden()
    window.sources_toggle.click()
    assert window.root_list.isHidden()
    assert window.root_list.count() == 2
    assert window.sources_panel.height() == 54
    window.sources_toggle.click()
    assert not window.root_list.isHidden()


def test_small_window_can_scroll_to_last_track_and_export_options(window):
    window.resize(980, 680)
    window._clear_detail('')
    info = MediaInfo('/tmp/test.mkv', 120, 'matroska', 1000,
                     [StreamInfo(1, 'audio', language='eng', channels=2)], 0, 1, 0)
    rows = [SourceRow(info.path, info, parent=window.detail_content) for _ in range(3)]
    for row in rows:
        window.detail_layout.addWidget(row)
    for _ in range(4):
        QApplication.processEvents()
    assert rows[0].height() >= rows[0].minimumSizeHint().height()
    bar = window.detail_scroll.verticalScrollBar()
    assert bar.maximum() > 0
    window.detail_scroll.ensureWidgetVisible(rows[-1].reset_alignment_button)
    assert bar.value() > 0
    window._navigate_to(1)
    assert isinstance(window._page_stack.currentWidget(), QScrollArea)
    for _ in range(3):
        QApplication.processEvents()
    export = window._page_stack.currentWidget()
    export.ensureWidgetVisible(window.profile_quick_combo)
    assert window.profile_quick_combo.isVisible()


def test_busy_and_error_feedback_keep_recovery_accessible(window):
    window._set_controls_enabled(False)
    assert not window.scan_button.isEnabled()
    assert not window.profile_quick_combo.isEnabled()
    assert not window.batch_button.isEnabled()
    window._navigate_to(2)
    assert window.diagnostic_button.isEnabled()
    window._set_analysis_insight('Analyse fehlgeschlagen', 'Quelle prüfen und erneut analysieren.', 'error')
    assert not window.insight_panel.isHidden()
    assert window.insight_panel.property('state') == 'error'
    window._set_controls_enabled(True)
    assert window.scan_button.isEnabled()
