"""FFprobe verification must reject damaged exports before atomic promotion."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from dubsync.core.export import ExportError, _verify_mux_output, export_multilingual
from dubsync.core.models import AlignmentResult, Anchor, MediaInfo, StreamInfo, TrackChoice
from dubsync.core.probe import probe_media


class ExportVerificationTests(unittest.TestCase):
    def setUp(self):
        video = StreamInfo(0, "video", codec_name="h264", width=1920, height=1080, fps=23.976)
        audio = StreamInfo(1, "audio", codec_name="ac3", channels=6)
        subtitles = StreamInfo(2, "subtitle", codec_name="subrip")
        attachment = StreamInfo(3, "attachment", codec_name="ttf")
        self.master = MediaInfo("master.mkv", 1327.0, "matroska", 1000,
                                [video, audio, subtitles, attachment], 1, 1, 1)
        self.checked = copy.deepcopy(self.master)
        self.checked.path = "output.mkv"
        self.checked.streams.append(StreamInfo(4, "audio", codec_name="aac", channels=2))
        self.checked.audio_streams = 2

    def test_preserved_master_streams_and_new_aac_are_accepted(self):
        _verify_mux_output(self.master, self.checked, 1)

    def test_changed_video_and_audio_properties_are_rejected(self):
        for field, value in (("codec_name", "hevc"), ("width", 1280), ("fps", 25.0)):
            with self.subTest(stream="video", field=field):
                checked = copy.deepcopy(self.checked)
                setattr(checked.video[0], field, value)
                with self.assertRaises(ExportError):
                    _verify_mux_output(self.master, checked, 1)
        for field, value in (("codec_name", "aac"), ("channels", 2)):
            with self.subTest(stream="original_audio", field=field):
                checked = copy.deepcopy(self.checked)
                setattr(checked.audio[0], field, value)
                with self.assertRaises(ExportError):
                    _verify_mux_output(self.master, checked, 1)

    def test_missing_or_changed_streams_are_rejected(self):
        cases = (
            ("subtitle_missing", lambda m: setattr(m, "subtitle_streams", 0)),
            ("subtitle_changed", lambda m: setattr(next(s for s in m.streams if s.codec_type == "subtitle"), "codec_name", "ass")),
            ("attachment_missing", lambda m: setattr(next(s for s in m.streams if s.codec_type == "attachment"), "codec_type", "data")),
            ("unexpected_dub_codec", lambda m: setattr(m.audio[-1], "codec_name", "flac")),
            ("missing_audio", lambda m: setattr(m, "audio_streams", 1)),
            ("incorrect_duration", lambda m: setattr(m, "duration", 1100.0)),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                checked = copy.deepcopy(self.checked)
                mutate(checked)
                with self.assertRaises(ExportError):
                    _verify_mux_output(self.master, checked, 1)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg/FFprobe erforderlich")
class RealMuxVerificationTests(unittest.TestCase):
    def test_real_mux_preserves_master_and_adds_verified_aac(self):
        with tempfile.TemporaryDirectory(prefix="dubsync-mux-verify-") as directory:
            root = Path(directory)
            master, alternate, output = (root / name for name in
                                         ("master.mkv", "alternate.wav", "export.mkv"))
            subprocess.run([
                "ffmpeg", "-hide_banner", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12:duration=4",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "mpeg4", "-c:a", "aac",
                "-shortest", str(master),
            ], check=True, capture_output=True, timeout=60)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-v", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=4",
                str(alternate),
            ], check=True, capture_output=True, timeout=60)
            alignment = AlignmentResult(
                source_path=str(alternate), target_path=str(master), slope=1.0, offset=0.0,
                anchors=[Anchor(i * 0.25, i * 0.25, 0) for i in range(16)],
                confidence=0.99, inlier_ratio=1.0, coverage=1.0, median_hamming=0,
                rms_error=0, source_duration=4, target_duration=4, sample_interval=0.25,
            )
            self.assertTrue(alignment.safe_for_automatic_export)
            exported = export_multilingual(str(master), [TrackChoice(
                path=str(alternate), audio_ordinal=0, language="eng", alignment=alignment,
            )], str(output))
            self.assertEqual(exported, str(output))
            checked = probe_media(exported)
            self.assertEqual([v.codec_name for v in checked.video], ["mpeg4"])
            self.assertEqual([(a.codec_name, a.channels) for a in checked.audio],
                             [("aac", 1), ("aac", 1)])
            self.assertTrue(master.is_file())
            self.assertTrue(alternate.is_file())


if __name__ == "__main__":
    unittest.main()
