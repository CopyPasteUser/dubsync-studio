#!/usr/bin/env python3
"""Create reproducible offscreen QA screenshots, with explicitly synthetic data.

Never reads a real video or uses the user's persisted settings/state. This is
for layout documentation, not evidence of a successful real-world export.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QTableWidgetItem
    from dubsync.core.models import AlignmentResult, Anchor, EpisodeGroup, MediaInfo, StreamInfo
    from dubsync.gui.main_window import MainWindow

    project = Path(__file__).resolve().parents[1]
    screenshots = project / "docs" / "screenshots"
    screenshots.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dubsync-2-layout-qa-") as directory:
        for name in ("CONFIG", "CACHE", "STATE"):
            os.environ[f"XDG_{name}_HOME"] = str(Path(directory) / name.lower())
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        try:
            window.resize(1440, 900)
            master = "/synthetisch/de/Beispielfolge-S01E01.mkv"
            source = "/synthetisch/en/Beispielfolge-S01E01.mkv"
            group = EpisodeGroup("demo", "DEMO · S01E01 · synthetische Daten", [master, source])
            window.groups = [group]
            window.selected_group = group
            window.media_cache = {
                path: MediaInfo(path, 180, "matroska", 15_000_000 + i * 1_000_000,
                                [StreamInfo(0, "video", codec_name="h264", width=1920, height=1080),
                                 StreamInfo(1, "audio", codec_name="aac", language="deu" if i == 0 else "eng")],
                                1, 1, 0)
                for i, path in enumerate(group.files)
            }
            window.alignments[(master, source)] = AlignmentResult(
                source_path=source, target_path=master, slope=1.0, offset=7.0,
                anchors=[Anchor(i * 3.0, i * 3.0 + 7.0, 4) for i in range(1, 57)],
                confidence=.93, inlier_ratio=.94, coverage=.90,
                median_hamming=4.0, rms_error=.1,
                source_duration=180, target_duration=180, sample_interval=.5,
            )
            window.episode_table.blockSignals(True)
            window.episode_table.setRowCount(1)
            for index, value in enumerate((group.display_name, "2", "DE · EN", "Demo")):
                window.episode_table.setItem(0, index, QTableWidgetItem(value))
            window.episode_table.selectRow(0)
            window.episode_table.blockSignals(False)
            window.episode_surface.setCurrentIndex(1)
            window.episode_count.setText("1 Demo-Episode · keine echten Medien geöffnet")
            window._render_group(group)
            window._refresh_workflow_summary()
            window.show()
            app.processEvents()
            window.grab().save(str(screenshots / "studio-review-2.png"))
            window.review_panel.review_tabs.setCurrentIndex(1)
            app.processEvents()
            window.grab().save(str(screenshots / "studio-frames-2.png"))
            window._navigate_to(1)
            app.processEvents()
            window.grab().save(str(screenshots / "studio-queue-2.png"))
            export = window._page_stack.currentWidget()
            export.ensureWidgetVisible(window.profile_tradeoff)
            app.processEvents()
            window.grab().save(str(screenshots / "studio-hardware-presets-2.png"))
        finally:
            window.project_dirty = False
            window.close()
            window.deleteLater()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
