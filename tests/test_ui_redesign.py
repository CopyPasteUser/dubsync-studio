"""Regressionsschutz fuer den neuen Workflow und die realen Leistungsprofile."""
from pathlib import Path
from dubsync.core.performance import settings_for_preset, recommended_profile

ROOT = Path(__file__).resolve().parents[1]


def test_profiles_differ_on_eight_thread_steam_machine():
    # i7-7700 class hardware, without relying on CI CPU quotas.
    specs = {"physical_cores": 4, "logical_cpus": 8, "ram_gib": 32.0}
    light = settings_for_preset("fast", specs)
    medium = settings_for_preset("balanced", specs)
    strong = settings_for_preset("quality", specs)
    assert [p["cpu_threads"] for p in (light, medium, strong)] == [2, 4, 7]
    assert [p["max_samples"] for p in (light, medium, strong)] == [1200, 1200, 1200]
    assert [p["probe_workers"] for p in (light, medium, strong)] == [1, 2, 4]
    assert [p["sample_interval"] for p in (light, medium, strong)] == [0.75, 0.75, 0.75]
    assert [p["audio_bitrate_kbps"] for p in (light, medium, strong)] == [128, 128, 128]
    assert recommended_profile(specs) == "balanced"


def test_profiles_constrain_ram_and_cpu():
    specs = {"physical_cores": 2, "logical_cpus": 2, "ram_gib": 4.0}
    fast = settings_for_preset("fast", specs)
    balanced = settings_for_preset("balanced", specs)
    quality = settings_for_preset("quality", specs)
    assert [p["max_samples"] for p in (fast, balanced, quality)] == [1200] * 3
    assert all(1 <= p["cpu_threads"] <= 2 for p in (fast, balanced, quality))
    assert all(p["probe_workers"] == 1 for p in (fast, balanced, quality))


def test_redesign_is_wired_and_preserves_episode_safety_code():
    gui = (ROOT / "dubsync/gui/main_window.py").read_text()
    batch = (ROOT / "dubsync/core/batch.py").read_text()
    assert "def _build_workspace_shell(" in gui
    assert '"Studio", "Episode wählen · Bildanker prüfen' in gui
    assert '"Stapelzentrale", "Aufträge kontrollieren' in gui
    assert '"Aktivität & Diagnose", "Jede Episode' in gui
    assert 'splitter.insertWidget(1, review_scroll)' in gui
    assert 'self.queue_room = QueueControlRoom()' in gui
    assert 'self.profile_quick_combo.currentIndexChanged.connect(self._quick_profile_changed)' in gui
    assert 'self._navigate_to(2)' in gui
    assert 'self._page_stack.setCurrentIndex(page)' in gui
    assert 'self.batch_button.clicked.connect(self.export_batch)' in gui
    assert '"Stapel-Gesamtprotokoll öffnen"' in gui
    assert '"Leicht", "fast"' in gui and '"Stark", "quality"' in gui
    assert 'cross_episode' in batch or 'content_identity' in batch
