"""Exercise the native workspace without reading the user's media or preferences."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea
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


def test_episode_onboarding_proposes_one_explicit_action_per_state(window, monkeypatch):
    called = []
    monkeypatch.setattr(window, 'add_folder', lambda: called.append('folder'))
    assert 'Noch keine Medienquellen' in window.library_empty.text()
    assert window.library_empty_action.text() == 'Medienordner auswählen'
    window.library_empty_action.click()
    assert called == ['folder']

    window.roots = ['/tmp/media-source']
    window._refresh_roots()
    assert 'Quellen ausgewählt' in window.library_empty.text()
    assert window.library_empty_action.text() == 'Bibliothek scannen'
    monkeypatch.setattr(window, 'scan_library', lambda: called.append('scan'))
    window.library_empty_action.click()
    assert called == ['folder', 'scan']

    window.groups = [EpisodeGroup('one', 'Example S01E01', ['de.mkv', 'en.mkv'])]
    window.episode_table.setRowCount(1)
    window.episode_search.setText('missing')
    window._filter_episodes()
    assert window.episode_surface.currentIndex() == 0
    assert window.library_empty_action.text() == 'Filter zurücksetzen'
    window.library_empty_action.click()
    assert window.episode_search.text() == ''
    assert window.episode_filter.currentData() == 'all'
    assert window.episode_surface.currentIndex() == 1

    window._set_controls_enabled(False)
    assert not window.library_empty_action.isEnabled()


def test_small_studio_keeps_first_action_visible_and_rest_scrollable(window):
    window.resize(980, 680)
    QApplication.processEvents()
    studio = window._page_stack.widget(0)
    assert isinstance(studio, QScrollArea)
    assert studio.verticalScrollBar().maximum() > 0
    action_pos = window.library_empty_action.mapTo(
        studio.viewport(), window.library_empty_action.rect().topLeft()
    )
    assert 0 <= action_pos.y()
    assert action_pos.y() + window.library_empty_action.height() < studio.viewport().height()
    studio.ensureWidgetVisible(window.to_export_button)
    assert studio.verticalScrollBar().value() > 0

    window.resize(1440, 900)
    QApplication.processEvents()
    assert studio.verticalScrollBar().maximum() == 0


@pytest.mark.parametrize('width,height', [(980, 680), (1440, 900)])
def test_review_focus_keeps_session_and_restores_panes(window, width, height):
    window.resize(width, height)
    QApplication.processEvents()
    splitter = window.studio_splitter
    initial = splitter.sizes()
    assert all(size > 0 for size in initial)
    window.output_dir_edit.setText('/tmp/focus-export')
    window.episode_search.setText('episode')
    was_dirty = window.project_dirty

    window.review_focus_button.click()
    QApplication.processEvents()
    assert window.review_focus_button.isChecked()
    assert splitter.widget(0).isHidden() and splitter.widget(2).isHidden()
    assert splitter.sizes()[1] > initial[1]
    assert window.review_focus_button.text() == 'Drei Bereiche anzeigen'

    window._navigate_to(1)
    assert not window.review_focus_button.isVisible()
    window._navigate_to(0)
    assert window.review_focus_button.isVisible() and window.review_focus_button.isChecked()
    assert splitter.widget(0).isHidden() and splitter.widget(2).isHidden()

    window.review_focus_button.click()
    QApplication.processEvents()
    assert not window.review_focus_button.isChecked()
    assert not splitter.widget(0).isHidden() and not splitter.widget(2).isHidden()
    assert all(abs(actual - prior) <= 2 for actual, prior in zip(splitter.sizes(), initial))
    assert window.output_dir_edit.text() == '/tmp/focus-export'
    assert window.episode_search.text() == 'episode'
    assert window.project_dirty == was_dirty


def test_long_episode_and_audio_titles_retain_full_access(window, monkeypatch):
    monkeypatch.setattr(window, '_start_metadata_prewarm', lambda: None)
    display_name = 'Eine sehr lange Serie mit Episodentitel und mehreren Sprachen — S01E123 — Director Cut'
    # Synthetic paths must not start the real asynchronous FFprobe selection worker.
    window.episode_table.blockSignals(True)
    window._scan_done([EpisodeGroup('long', display_name, ['/tmp/de.mkv', '/tmp/en.mkv'])])
    window.episode_table.blockSignals(False)
    title = window.episode_table.item(0, 0)
    assert title.text() == display_name
    assert title.toolTip() == display_name

    path = '/tmp/very-long-source-filename-with-source-language-and-suffix-1234567890.mkv'
    long_track = 'Lange Originaltonspur – Audiodeskription und mehrsprachige Redaktion'
    info = MediaInfo(path, 120, 'matroska', 1000,
                     [StreamInfo(1, 'audio', language='deu', title=long_track, channels=2)],
                     0, 1, 0)
    row = SourceRow(path, info, parent=window.detail_content)
    source_title = next(label for label in row.findChildren(QLabel)
                        if label.objectName() == 'sourceName')
    assert source_title.toolTip() == path
    assert source_title.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert long_track in row.audio_combo.itemText(0)
    assert row.audio_combo.itemData(0, Qt.ItemDataRole.ToolTipRole) == row.audio_combo.itemText(0)


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


def test_batch_empty_state_shows_output_setup_before_scrolling(window):
    window.resize(1440, 900)
    window._navigate_to(1)
    QApplication.processEvents()
    assert window.queue_room.queue_surface.height() == 72
    assert window.output_dir_edit.isVisible()
    assert window.output_dir_edit.mapTo(window, window.output_dir_edit.rect().topLeft()).y() < 730
    assert window.export_overview_label.text() == 'Noch keine mehrsprachigen Episoden'
    window.queue_room.studio_button.click()
    assert window._page_stack.currentIndex() == 0


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
