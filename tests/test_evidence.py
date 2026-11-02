"""Evidence navigation remains read-only and never changes export eligibility."""
from __future__ import annotations

import math
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dubsync.core.evidence import evidence_spans, export_hold_reasons, review_issues
from dubsync.core.models import AlignmentResult, Anchor, TimelineSegment


def alignment(segments=None, *, manual=False) -> AlignmentResult:
    return AlignmentResult(
        source_path="source", target_path="master", slope=1, offset=0,
        anchors=[Anchor(float(i), float(i), 1) for i in (2, 4, 6, 8, 22, 24, 26, 28)],
        confidence=.92, inlier_ratio=.9, coverage=.9, median_hamming=1,
        rms_error=.1, source_duration=30, target_duration=40,
        sample_interval=1, segments=segments or [], manual_segments=manual,
    )


class EvidenceMapTests(unittest.TestCase):
    def test_gaps_between_piecewise_segments_stay_unmapped(self):
        result = alignment([
            TimelineSegment(0, 10, 0, 10, 8, 1, .1, 8),
            TimelineSegment(20, 30, 20, 30, 8, 1, .1, 8),
        ])
        spans = evidence_spans(result)
        self.assertEqual(sum(span.duration for span in spans), 40)
        self.assertTrue(any(span.status == "unmapped" and
                            math.isclose(span.start, 10) and math.isclose(span.end, 20)
                            for span in spans))
        self.assertTrue(any(span.status == "sampled" for span in spans))
        self.assertTrue(any(span.status == "unverified" for span in spans))
        self.assertFalse(result.safe_for_automatic_export)

    def test_affine_unobserved_area_is_not_called_verified(self):
        result = alignment()
        spans = evidence_spans(result)
        self.assertEqual(spans[-1].status, "unmapped")
        self.assertEqual(spans[-1].start, 30)
        self.assertEqual(spans[-1].end, 40)
        self.assertIn("unverified", {span.status for span in spans})
        self.assertTrue(all(span.duration > 0 for span in spans))

    def test_issue_priority_and_manual_hold(self):
        result = alignment(manual=True)
        issues = review_issues(result)
        self.assertTrue(issues)
        self.assertIn("unmapped", {span.status for span in issues})
        self.assertTrue(issues[0].duration >= issues[-1].duration)
        self.assertIn("Manuell", export_hold_reasons(result)[0])
        self.assertFalse(result.safe_for_automatic_export)

    def test_nonfinite_input_is_safe_to_display(self):
        result = alignment()
        result.anchors.append(Anchor(float("nan"), 12, 0))
        result.target_duration = float("nan")
        self.assertEqual(evidence_spans(result), [])


@unittest.skipUnless(os.environ.get("QT_QPA_PLATFORM") == "offscreen", "Requires offscreen Qt")
class EvidenceNavigationTests(unittest.TestCase):
    def test_selected_issue_jumps_without_authorizing_export(self):
        from PySide6.QtWidgets import QApplication
        from dubsync.gui.workbench import StudioReviewPanel
        from dubsync.core.models import EpisodeGroup, MediaInfo, StreamInfo

        app = QApplication.instance() or QApplication([])
        result = alignment()
        master, source = "master", "source"
        group = EpisodeGroup("demo", "Demo", [master, source])
        media = {path: MediaInfo(path, 40, "matroska", 100,
                                 [StreamInfo(0, "video")], 1, 0, 0)
                 for path in (master, source)}
        panel = StudioReviewPanel()
        try:
            panel.show_group(group, master, media, {(master, source): result})
            self.assertGreater(panel.issue_combo.count(), 0)
            self.assertTrue(panel.issue_button.isEnabled())
            panel.issue_button.click()
            self.assertGreater(panel.timeline.position, 0)
            self.assertFalse(result.safe_for_automatic_export)
            app.processEvents()
        finally:
            panel.close()
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
