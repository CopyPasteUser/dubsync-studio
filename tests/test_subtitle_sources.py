"""Offline regression tests for subtitle provider ordering and hostile archives."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from dubsync.core.models import MediaInfo, StreamInfo
from dubsync.core.subtitles import (SubtitleError, SubtitleOptions, _subdl_candidates,
                                   _download, _download_video_captions, _extract_archive,
                                   _find_archive, _read_https, prepare_subtitles)


CAPTIONS = b"1\n00:00:01,000 --> 00:00:02,000\nHallo Welt!\n"


class SubtitleSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dubsync-subtitle-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Keep repeatable tests independent of a developer's persistent cache.
        environment = patch.dict(os.environ, {
            "XDG_CACHE_HOME": str(self.root / "xdg-cache"),
            "DUBSYNC_SUBTITLE_CACHE_DIR": str(self.root / "subtitle-cache"),
            "DUBSYNC_SUBTITLE_CACHE": "1",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.master = self.root / "Show.S01E02.mkv"
        self.master.write_bytes(b"not used by mocked converter")
        self.info = MediaInfo(str(self.master), 100.0, "matroska", 1,
                              [StreamInfo(0, "video", codec_name="h264"),
                               StreamInfo(1, "audio", codec_name="aac")], 1, 1, 0)

    def _archive(self, name: str, files: dict[str, bytes]) -> Path:
        path = self.root / name
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for filename, data in files.items():
                archive.writestr(filename, data)
        return path

    def test_zip_prefers_requested_episode_language_and_never_extracts_paths(self):
        archive = self._archive("Show.S01E02.de.zip", {
            "../overwrite.txt": b"not a subtitle",
            "English/Show.S01E02.en.srt": CAPTIONS,
            "Season/Show.S01E01.de.srt": CAPTIONS,
            "Season/Show.S01E02.de.srt": CAPTIONS.replace(b"Hallo", b"Richtig"),
        })
        target = self.root / "temp"
        target.mkdir()
        chosen = _extract_archive(archive, self.master, "deu", target)
        self.assertEqual(chosen.read_bytes(), CAPTIONS.replace(b"Hallo", b"Richtig"))
        self.assertEqual(chosen.parent, target)
        self.assertFalse((self.root / "overwrite.txt").exists())
        self.assertEqual(_find_archive(self.master, target, "deu"), chosen)

    def test_zip_rejects_bad_episode_and_oversized_entries(self):
        target = self.root / "temp"
        target.mkdir()
        wrong = self._archive("wrong.zip", {"Show.S01E03.de.srt": CAPTIONS})
        with self.assertRaisesRegex(SubtitleError, "Keine passende"):
            _extract_archive(wrong, self.master, "deu", target)
        huge = self._archive("huge.zip", {"Show.S01E02.srt": b"x" * (5 * 1024 * 1024 + 1)})
        with self.assertRaises(SubtitleError):
            _extract_archive(huge, self.master, "deu", target)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg benötigt")
    def test_real_ffmpeg_normalizes_zipped_sidecar(self):
        self._archive("Show.S01E02.de.zip", {"Show.S01E02.de.srt": CAPTIONS})
        prepared = prepare_subtitles(self.master, self.info,
                                     SubtitleOptions(mode="auto", language="deu"),
                                     self.root, shutil.which("ffmpeg"))
        self.assertIn("Hallo Welt!", prepared.read_text(encoding="utf-8"))

    def test_automatic_tries_all_existing_sources_before_generation(self):
        order = []
        progress = []

        def missing(name):
            def fallback(*_args, **_kwargs):
                order.append(name)
                raise SubtitleError("not available")
            return fallback

        def convert(_ffmpeg, _source, destination, **_kwargs):
            destination.write_bytes(CAPTIONS)

        with (patch("dubsync.core.subtitles._find_sidecar", return_value=None),
              patch("dubsync.core.subtitles._find_archive", side_effect=missing("zip")),
              patch("dubsync.core.subtitles._embedded_stream", return_value=None),
              patch("dubsync.core.subtitles._download", side_effect=missing("subliminal")),
              patch("dubsync.core.subtitles._subdl_candidates", side_effect=missing("subdl")),
              patch("dubsync.core.subtitles._generate", side_effect=lambda *_args, **_kwargs:
                    (order.append("whisper") or self.master)),
              patch("dubsync.core.subtitles._convert_to_srt", side_effect=convert)):
            selected = prepare_subtitles(self.master, self.info,
                                         SubtitleOptions(mode="auto", language="deu"),
                                         self.root, "ffmpeg", progress=progress.append)
        self.assertEqual(selected.read_bytes(), CAPTIONS)
        self.assertEqual(order, ["zip", "subdl", "subliminal", "whisper"])
        self.assertIn("Subliminal", progress[-2])
        self.assertIn("Whisper", progress[-1])

    def test_online_mode_prefers_subdl_and_skips_subliminal_and_whisper(self):
        with (patch("dubsync.core.subtitles._download") as subliminal,
              patch("dubsync.core.subtitles._subdl_candidates", return_value=iter([self.master])) as subdl,
              patch("dubsync.core.subtitles._generate") as whisper,
              patch("dubsync.core.subtitles._convert_to_srt",
                    side_effect=lambda _f, _p, destination, **kw: destination.write_bytes(CAPTIONS))):
            prepare_subtitles(self.master, self.info,
                              SubtitleOptions(mode="download", language="deu"),
                              self.root, "ffmpeg")
        subdl.assert_called_once()
        subliminal.assert_not_called()
        whisper.assert_not_called()

    def test_online_mode_falls_back_to_subliminal_if_subdl_unavailable(self):
        with (patch("dubsync.core.subtitles._subdl_candidates", side_effect=SubtitleError("no key")),
              patch("dubsync.core.subtitles._download", return_value=self.master) as subliminal,
              patch("dubsync.core.subtitles._generate") as whisper,
              patch("dubsync.core.subtitles._convert_to_srt",
                    side_effect=lambda _f, _p, destination, **kw: destination.write_bytes(CAPTIONS))):
            prepare_subtitles(self.master, self.info,
                              SubtitleOptions(mode="download", language="deu"),
                              self.root, "ffmpeg")
        subliminal.assert_called_once()
        whisper.assert_not_called()

    def test_subliminal_uses_parallel_providers_and_a_release_match_floor(self):
        captured = []

        def fake_run(command, **_kwargs):
            captured.append(command)
            (self.root / "Show.S01E02.de.srt").write_bytes(CAPTIONS)

        with (patch("dubsync.core.subtitles._find_program", return_value="subliminal"),
              patch("dubsync.core.subtitles._run", side_effect=fake_run)):
            self.assertTrue(_download(self.master, self.root, "deu").is_file())
        command = captured[0]
        self.assertEqual(command[command.index("--max-workers") + 1], "4")
        self.assertEqual(command[command.index("--min-score") + 1], "65")

    def test_auto_stops_at_subdl_before_whisper(self):
        with (patch("dubsync.core.subtitles._find_sidecar", return_value=None),
              patch("dubsync.core.subtitles._find_archive", side_effect=SubtitleError("no ZIP")),
              patch("dubsync.core.subtitles._embedded_stream", return_value=None),
              patch("dubsync.core.subtitles._download", side_effect=SubtitleError("no Subliminal")),
              patch("dubsync.core.subtitles._subdl_candidates", return_value=iter([self.master])) as subdl,
              patch("dubsync.core.subtitles._generate") as whisper,
              patch("dubsync.core.subtitles._convert_to_srt",
                    side_effect=lambda _f, _p, destination, **kw: destination.write_bytes(CAPTIONS))):
            prepare_subtitles(self.master, self.info,
                              SubtitleOptions(mode="auto", language="deu"),
                              self.root, "ffmpeg")
        subdl.assert_called_once()
        whisper.assert_not_called()

    def test_find_only_mode_never_invokes_speech_recognition(self):
        with (patch("dubsync.core.subtitles._find_sidecar", return_value=None),
              patch("dubsync.core.subtitles._find_archive", side_effect=SubtitleError("no ZIP")),
              patch("dubsync.core.subtitles._embedded_stream", return_value=None),
              patch("dubsync.core.subtitles._download", side_effect=SubtitleError("offline")),
              patch("dubsync.core.subtitles._subdl_candidates", side_effect=SubtitleError("offline")),
              patch("dubsync.core.subtitles._generate") as whisper):
            with self.assertRaisesRegex(SubtitleError, "Keine Untertitel verfügbar"):
                prepare_subtitles(self.master, self.info,
                                  SubtitleOptions(mode="existing", language="deu"),
                                  self.root, "ffmpeg")
        whisper.assert_not_called()

    def test_missing_whisper_backend_never_extracts_hours_of_audio(self):
        with (patch.dict(os.environ, {"DUBSYNC_WHISPER_MODEL": ""}),
              patch("dubsync.core.subtitles._find_program", return_value=None),
              patch("dubsync.core.subtitles._run") as external_command):
            with self.assertRaisesRegex(SubtitleError, "whisper-cli"):
                prepare_subtitles(self.master, self.info,
                                  SubtitleOptions(mode="generate", language="deu"),
                                  self.root, "ffmpeg")
        external_command.assert_not_called()

    def test_fast_whisper_uses_memory_aware_int8_batching(self):
        commands = []

        def fake_program(name):
            return "/usr/bin/whisper-ctranslate2" if name == "whisper-ctranslate2" else None

        def fake_run(command, **_kwargs):
            commands.append(command)
            if command[0].endswith("whisper-ctranslate2"):
                (self.root / "speech.srt").write_bytes(CAPTIONS)

        with (patch.dict(os.environ, {"DUBSYNC_WHISPER_MODEL": ""}),
              patch("dubsync.core.subtitles._find_program", side_effect=fake_program),
              patch("dubsync.core.performance.available_memory_gib", return_value=16),
              patch("dubsync.core.subtitles._run", side_effect=fake_run),
              patch("dubsync.core.subtitles._convert_to_srt",
                    side_effect=lambda _f, src, destination, **kw: destination.write_bytes(src.read_bytes()))):
            output = prepare_subtitles(self.master, self.info,
                                       SubtitleOptions(mode="generate", language="deu", whisper_model="small"),
                                       self.root, "ffmpeg")
        self.assertEqual(output.read_bytes(), CAPTIONS)
        self.assertEqual(len(commands), 2)
        command = commands[1]
        self.assertIn("--batched", command)
        self.assertEqual(command[command.index("--batch_size") + 1], "8")
        self.assertEqual(command[command.index("--compute_type") + 1], "int8")
        self.assertEqual(command[command.index("--language") + 1], "de")

    def test_fast_whisper_reduces_batch_on_memory_limited_hosts(self):
        with (patch.dict(os.environ, {"DUBSYNC_WHISPER_MODEL": ""}),
              patch("dubsync.core.subtitles._find_program",
                    side_effect=lambda name: "fast" if name == "whisper-ctranslate2" else None),
              patch("dubsync.core.performance.available_memory_gib", return_value=2),
              patch("dubsync.core.subtitles._run",
                    side_effect=lambda command, **kw: (self.root / "speech.srt").write_bytes(CAPTIONS)
                    if command[0] == "fast" else None) as execution,
              patch("dubsync.core.subtitles._convert_to_srt",
                    side_effect=lambda _f, src, destination, **kw: destination.write_bytes(src.read_bytes()))):
            prepare_subtitles(self.master, self.info, SubtitleOptions(mode="generate"),
                              self.root, "ffmpeg")
        fast_command = execution.call_args_list[-1].args[0]
        self.assertEqual(fast_command[fast_command.index("--batch_size") + 1], "1")

    def test_invalid_fast_transcript_retries_native_backend_on_same_audio(self):
        model = self.root / "ggml-small.bin"
        model.write_bytes(b"model fixture")
        commands = []

        def find_program(name):
            return {"whisper-ctranslate2": "fast", "whisper-cli": "native"}.get(name)

        def fake_run(command, **_kwargs):
            commands.append(command)
            if command[0] == "fast":
                (self.root / "speech.srt").write_text("No timestamp\n", encoding="utf-8")
            elif command[0] == "native":
                (self.root / "speech.srt").write_bytes(CAPTIONS)

        with (patch.dict(os.environ, {"DUBSYNC_WHISPER_MODEL": str(model)}),
              patch("dubsync.core.subtitles._find_program", side_effect=find_program),
              patch("dubsync.core.performance.available_memory_gib", return_value=8),
              patch("dubsync.core.subtitles._run", side_effect=fake_run),
              patch("dubsync.core.subtitles._convert_to_srt",
                    side_effect=lambda _f, src, destination, **kw: destination.write_bytes(src.read_bytes()))):
            prepare_subtitles(self.master, self.info,
                              SubtitleOptions(mode="generate", language="deu"),
                              self.root, "ffmpeg")
        self.assertEqual([command[0] for command in commands], ["ffmpeg", "fast", "native"])

    def test_subdl_search_selects_exact_episode_and_never_leaks_key(self):
        observed = []
        result = {"status": True, "subtitles": [
            {"full_season": True, "unpack_files": [
                {"season": 1, "episode": 1, "language": "DE", "format": "srt",
                 "url": "/subtitle/1/1"},
                {"season": 1, "episode": 2, "language": "DE", "format": "srt",
                 "url": "/subtitle/1/2"}]},
        ]}

        def fake_fetch(host, path, **kw):
            observed.append((host, path, kw.get("parameters")))
            return json.dumps(result).encode() if host == "api.subdl.com" else CAPTIONS

        with (patch.dict(os.environ, {"DUBSYNC_SUBDL_API_KEY": "secret-test-key"}),
              patch("dubsync.core.subtitles._read_https", side_effect=fake_fetch)):
            path = next(_subdl_candidates(self.master, self.root, "deu"))
        self.assertEqual(path.read_bytes(), CAPTIONS)
        self.assertEqual(observed[1][0:2], ("dl.subdl.com", "/subtitle/1/2"))
        self.assertEqual(observed[0][2]["file_name"], self.master.name)

        with (patch.dict(os.environ, {"DUBSYNC_SUBDL_API_KEY": "secret-test-key"}),
              patch("dubsync.core.subtitles._read_https", side_effect=SubtitleError("network"))):
            with self.assertRaisesRegex(SubtitleError, "network"):
                next(_subdl_candidates(self.master, self.root, "deu"))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg benötigt")
    def test_subdl_retries_bad_timing_then_uses_next_release(self):
        # The master explicitly identifies its release; ranking should prefer it.
        self.master = self.root / "Show.S01E02.1080p.mkv"
        self.master.write_bytes(b"not used by mocked provider")
        fetched = []
        result = {"status": True, "subtitles": [
            {"release_name": "Other.Show.S01E02.720p", "url": "/subtitle/1.srt",
             "season": 1, "episode": 2, "format": "srt"},
            {"release_name": "Show.S01E02.1080p", "url": "/subtitle/2.srt",
             "season": 1, "episode": 2, "format": "srt"},
        ]}

        def fake_fetch(host, path, **_kwargs):
            if host == "api.subdl.com":
                return json.dumps(result).encode()
            fetched.append(path)
            return (b"1\n00:00:01,000 --> 00:05:00,000\nBad timing\n"
                    if path.endswith("2.srt") else CAPTIONS)

        with (patch.dict(os.environ, {"DUBSYNC_SUBDL_API_KEY": "key"}),
              patch("dubsync.core.subtitles._read_https", side_effect=fake_fetch)):
            subtitles = prepare_subtitles(self.master, self.info,
                                          SubtitleOptions(mode="subdl", language="deu"),
                                          self.root, shutil.which("ffmpeg"))
        self.assertEqual(fetched, ["/subtitle/2.srt", "/subtitle/1.srt"])
        self.assertIn("Hallo Welt", subtitles.read_text(encoding="utf-8"))

    def test_subdl_limits_download_attempts(self):
        fetched = []
        result = {"status": True, "subtitles": [
            {"url": f"/subtitle/{i}.srt", "season": 1, "episode": 2, "format": "srt"}
            for i in range(8)]}

        def fake_fetch(host, path, **_kwargs):
            if host == "api.subdl.com":
                return json.dumps(result).encode()
            fetched.append(path)
            raise SubtitleError("Provider unavailable")

        with (patch.dict(os.environ, {"DUBSYNC_SUBDL_API_KEY": "key"}),
              patch("dubsync.core.subtitles._read_https", side_effect=fake_fetch)):
            with self.assertRaisesRegex(SubtitleError, "3 Kandidat"):
                list(_subdl_candidates(self.master, self.root, "deu"))
        self.assertEqual(len(fetched), 3)

    def test_provider_urls_are_restricted(self):
        for path in ("//evil.example/", "/subtitle/../etc/passwd", "/subtitle/foo?key=bad"):
            with self.subTest(path=path), self.assertRaisesRegex(SubtitleError, "Downloadpfad"):
                _read_https("dl.subdl.com", path)

    def test_provider_http_failure_redacts_api_key(self):
        failure = HTTPError("https://api.subdl.com/api/v1/subtitles?api_key=SENSITIVE",
                            403, "forbidden", None, None)
        with patch("dubsync.core.subtitles.build_opener") as opener:
            opener.return_value.open.side_effect = failure
            with self.assertRaises(SubtitleError) as error:
                _read_https("api.subdl.com", "/api/v1/subtitles",
                            parameters={"api_key": "SENSITIVE"})
        self.assertNotIn("SENSITIVE", str(error.exception))

    def test_yt_dlp_writes_only_manually_requested_text_captions(self):
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            (self.root / "caption.de.vtt").write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHallo\n")

        with (patch("dubsync.core.subtitles._find_program", return_value="yt-dlp"),
              patch("dubsync.core.subtitles._run", side_effect=fake_run)):
            found = _download_video_captions("https://videos.example/1", self.root, "deu")
        self.assertEqual(found.name, "caption.de.vtt")
        self.assertIn("--skip-download", commands[0])
        self.assertIn("--no-write-auto-subs", commands[0])
        with self.assertRaisesRegex(SubtitleError, "HTTPS"):
            _download_video_captions("file:///etc/passwd", self.root, "deu")


if __name__ == "__main__":
    unittest.main()
