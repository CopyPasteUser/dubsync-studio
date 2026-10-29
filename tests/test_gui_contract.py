"""Static contracts for the GUI when PySide6 is not installed in test workers."""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "dubsync" / "gui" / "main_window.py"


class GuiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = GUI.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.text, filename=str(GUI))

    def test_settings_menu_is_connected_to_real_handlers(self):
        self.assertIn('addMenu("Einstellungen")', self.text)
        self.assertIn('QAction("Leistung & Analyse…", self)', self.text)
        self.assertIn('performance_action.triggered.connect(self._open_settings)', self.text)
        self.assertIn('diagnostics_action.triggered.connect(self._show_diagnostics)', self.text)

    def test_library_search_and_status_filter_are_wired(self):
        self.assertIn('self.episode_search.textChanged.connect(lambda _text: self.episode_filter_timer.start())', self.text)
        self.assertIn('self.episode_filter_timer.timeout.connect(self._filter_episodes)', self.text)
        self.assertIn('self.episode_filter.currentIndexChanged.connect(self._filter_episodes)', self.text)
        self.assertIn('self.episode_table.setRowHidden(index, not shown)', self.text)

    def test_settings_reach_analysis_and_batch_export(self):
        self.assertIn('hardware_accel=self.performance_settings["hardware_accel"]', self.text)
        self.assertIn('"max_samples": self.performance_settings["max_samples"]', self.text)
        self.assertIn('"cache_enabled": False', self.text)

    def test_dialog_documents_gpu_fallback_truthfully(self):
        alignment = (ROOT / "dubsync" / "core" / "alignment.py").read_text(encoding="utf-8")
        self.assertIn('CPU-Fallback', alignment)
        self.assertIn('Matcher selbst und die Zeitankerberechnung laufen auf der CPU', self.text)

    def test_report_export_and_shortcuts_exist(self):
        self.assertIn('Bibliotheksbericht exportieren', self.text)
        self.assertIn('setShortcut("F5")', self.text)
        self.assertIn('def _save_library_report(self)', self.text)

    def test_smallest_video_is_default_master_and_output_size_is_estimated(self):
        self.assertIn('choose_smallest_video(known_videos).path', self.text)
        self.assertIn('estimate_output_size_bytes(', self.text)
        self.assertIn('automatisch kleinste Videodatei je Episode', self.text)

    def test_system_adaptive_profile_and_audio_bitrate_are_connected(self):
        self.assertIn('self.preset_combo.addItem(f"Auto – empfiehlt {choice} für diese Hardware", "system")', self.text)
        self.assertIn('"preset": preset, "auto_system_profile": preset == "system"', self.text)
        self.assertNotIn('self.audio_bitrate.currentIndexChanged.connect(self._mark_custom)', self.text)
        self.assertIn('"audio_bitrate_kbps": self.performance_settings["audio_bitrate_kbps"]', self.text)
        self.assertIn('audio_bitrate_kbps=self.performance_settings["audio_bitrate_kbps"]', self.text)

    def test_stable_release_has_project_save_load_and_job_history(self):
        self.assertIn('APP_VERSION = "2.0.0b2"', self.text)
        self.assertIn('QAction("Projekt öffnen…", self)', self.text)
        self.assertIn('QAction("Projekt speichern", self)', self.text)
        self.assertIn('def _open_project(self)', self.text)
        self.assertIn('def _save_project(self)', self.text)
        self.assertIn('self.track_overrides', self.text)
        self.assertIn('self.master_overrides', self.text)
        self.assertIn('def _reset_master_override(self, group_key: str)', self.text)
        self.assertIn('JobHistoryDialog(entries, self).exec()', self.text)
        self.assertIn('self.history.start(kind, label', self.text)
        self.assertIn('self.history.finish(job_id, status', self.text)
        self.assertIn('configure_logging()', self.text)
        self.assertIn('QAction("Projekt öffnen…", self)', self.text)
        self.assertIn('Ungespeicherte Projektänderungen', self.text)
        self.assertIn('def _open_log_file(self)', self.text)

    def test_batch_uses_persisted_track_and_master_overrides(self):
        self.assertIn('"track_overrides": dict(self.track_overrides)', self.text)
        self.assertIn('"master_overrides": dict(self.master_overrides)', self.text)
        self.assertIn('self.batch_queue.enqueue(', self.text)
        self.assertIn('self.batch_queue.run(', self.text)
        self.assertIn('def _alignment_value_changed(self, *_args)', self.text)
        self.assertIn('def _reset_alignment(self)', self.text)
        self.assertIn('"manual_alignment": bool(row.manual_alignment)', self.text)
        self.assertIn('if override.get("manual_alignment")', self.text)

    def test_network_nas_sources_and_mount_awareness_are_wired(self):
        self.assertIn('self.network_button.clicked.connect(self.add_network_source)', self.text)
        self.assertIn('def add_network_source(self)', self.text)
        self.assertIn('smb", "sftp", "dav", "davs', self.text)
        self.assertIn('network_mount_for_path(resolved)', self.text)
        self.assertIn('Eingehängten Ordner auswählen', self.text)
        network = (ROOT / "dubsync" / "core" / "network.py").read_text(encoding="utf-8")
        self.assertIn('"cifs", "smb3", "nfs", "nfs4"', network)
        self.assertIn('def network_mount_type_label(filesystem: str)', network)
        self.assertEqual((ROOT / "dubsync" / "assets" / "dubsync-studio.svg").read_bytes(),
                         (ROOT / "assets" / "dubsync-studio.svg").read_bytes())

    def test_fast_probe_fingerprint_and_cache_prewarm(self):
        self.assertIn('return probe_many(', self.text)
        self.assertIn('fingerprint_many(', self.text)
        self.assertIn('def _start_metadata_prewarm(self)', self.text)
        self.assertIn('prewarm_metadata', self.text)
        self.assertIn('Metadaten-Worker', self.text)

    def test_residual_visualization_sync_preview_and_undo_redo(self):
        self.assertIn('class AlignmentResidualPlot(QWidget)', self.text)
        self.assertIn('class SyncPreviewDialog(QDialog)', self.text)
        self.assertIn('def preview_sync_audio(self)', self.text)
        self.assertIn('def undo_alignment(self)', self.text)
        self.assertIn('def redo_alignment(self)', self.text)
        self.assertIn('def _undo_selected_alignment(self)', self.text)
        self.assertIn('def _redo_selected_alignment(self)', self.text)
        self.assertIn('Ctrl+Alt+Z', self.text)
        self.assertIn('Ctrl+Alt+Y', self.text)

    def test_branding_accessibility_and_eta_are_present(self):
        logo = ROOT / "assets" / "dubsync-studio.svg"
        self.assertTrue(logo.is_file())
        self.assertIn('DubSync Studio', logo.read_text(encoding="utf-8"))
        self.assertIn('setWindowIcon(QIcon(str(APP_LOGO)))', self.text)
        self.assertIn('def _toggle_high_contrast(self, enabled: bool)', self.text)
        self.assertIn('setAccessibleDescription', self.text)
        self.assertIn('Restzeit ~', self.text)

    def test_legacy_balanced_settings_migrate_to_system_recommendation(self):
        self.assertIn('"audio_bitrate_kbps" not in raw_performance', self.text)
        self.assertIn('if old_preset == "balanced":', self.text)
        self.assertIn('refreshed = default_performance_settings()', self.text)
        self.assertIn('source_info=probed_snapshot.get(str(Path(source).resolve()))', self.text)
        self.assertIn('target_info=master_info', self.text)


    def test_pause_resume_and_progress_plot_are_connected(self):
        self.assertIn("class ProgressHistoryPlot(QWidget)", self.text)
        self.assertIn("def toggle_pause_current_job(self)", self.text)
        self.assertIn("self.pause_button.clicked.connect(self.toggle_pause_current_job)", self.text)
        self.assertIn("self.pause_event = threading.Event()", self.text)
        self.assertIn("def test_batch_rejects_duplicate_output_destinations_before_any_probe", (ROOT / "tests" / "test_core.py").read_text(encoding="utf-8"))

    def test_workflow_design_and_drag_drop_are_wired(self):
        self.assertIn('class SourceDropList(QListWidget)', self.text)
        self.assertIn('self.root_list.pathsDropped.connect(self._handle_dropped_paths)', self.text)
        self.assertIn('QLabel("QUELLEN")', self.text)
        self.assertIn('QLabel("ABGLEICH")', self.text)
        self.assertIn('QLabel("EPISODE")', self.text)
        self.assertIn('QFrame#workflowStrip', (ROOT / 'dubsync/gui/theme.py').read_text())
        self.assertIn('dropHint', self.text)
        self.assertIn('def _source_context_menu(self, position)', self.text)
        self.assertIn('def _open_project_file(self, filename: str)', self.text)



class BatchSafetyUiContracts(unittest.TestCase):
    """Ensure the cancellation UX and episode-order explanation remain wired."""

    @classmethod
    def setUpClass(cls):
        cls.gui = GUI.read_text(encoding="utf-8")

    def test_batch_cancel_needs_confirmation(self):
        self.assertIn('Gesamten Stapel wirklich stoppen?', self.gui)
        self.assertIn('Möchtest du den gesamten Stapel tatsächlich abbrechen?', self.gui)
        self.assertIn('reason = "user_button_confirmed"', self.gui)
        self.assertIn('self.cancel_button.setAutoDefault(False)', self.gui)
        self.assertIn('self.cancel_button.setDefault(False)', self.gui)

    def test_batch_progress_has_explicit_episode_numbers(self):
        self.assertIn('Die Stapelposition (z. B. 1/13) ist NICHT die Episodennummer.', self.gui)
        self.assertIn('mapping_preview', self.gui)
        self.assertIn('Stapel-Zuordnung (Dateinamen, nicht Verarbeitungsreihenfolge)', self.gui)
        self.assertIn('batch_priority', self.gui)

    def test_partial_batch_summary_is_displayed_after_cancellation(self):
        self.assertIn('self._current_job_is_batch and isinstance(result_value, dict) and callback', self.gui)
        self.assertIn('callback(result_value)', self.gui)


if __name__ == "__main__":
    unittest.main(verbosity=2)
