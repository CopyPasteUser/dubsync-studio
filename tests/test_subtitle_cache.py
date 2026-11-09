"""Persistent subtitle reuse stays safe, private and provider-specific."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dubsync.core.models import MediaInfo, StreamInfo
from dubsync.core.subtitle_cache import SubtitleCache
from dubsync.core.subtitles import SubtitleOptions, prepare_subtitles


CAPTION = b"1\n00:00:01,000 --> 00:00:02,000\nUntertitel A\n"
OTHER = b"1\n00:00:01,000 --> 00:00:02,000\nUntertitel B\n"


class SubtitleCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dubsync-subtitle-cache-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {
            "XDG_CACHE_HOME": str(self.root / "xdg"),
            "DUBSYNC_SUBTITLE_CACHE_DIR": str(self.root / "cache"),
            "DUBSYNC_SUBTITLE_CACHE": "1",
            "DUBSYNC_WHISPER_MODEL": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.master = self.root / "Episode.S01E02.mkv"
        self.master.write_bytes(b"example binary video for cache identity")
        self.info = MediaInfo(str(self.master), 100.0, "matroska", 1,
                              [StreamInfo(0, "video", codec_name="h264"),
                               StreamInfo(1, "audio", codec_name="aac")], 1, 1, 0)
        self.cache = SubtitleCache()

    def _prepare(self, *, mode="download", language="deu", model="small", directory="working"):
        target_dir = self.root / directory
        target_dir.mkdir(exist_ok=True)
        return prepare_subtitles(self.master, self.info,
                                 SubtitleOptions(mode=mode, language=language,
                                                 whisper_model=model),
                                 target_dir, "ffmpeg")

    def test_online_cache_skips_second_provider_request_after_temp_cleanup(self):
        with (patch("dubsync.core.subtitles._subdl_candidates",
                    side_effect=lambda *_args, **_kwargs: iter([self.master])) as provider,
              patch("dubsync.core.subtitles._convert_to_srt",
                    side_effect=lambda _ffmpeg, _source, destination, **_kw:
                    destination.write_bytes(CAPTION))):
            self.assertEqual(self._prepare().read_bytes(), CAPTION)
            self.assertEqual(self._prepare(directory="second-run").read_bytes(), CAPTION)
        provider.assert_called_once()
        cached = list((self.root / "cache").glob("*.srt"))
        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / "cache").stat().st_mode & 0o777, 0o700)

    def test_generation_cache_reuses_asr_but_uses_separate_models(self):
        with (patch("dubsync.core.subtitles._generate", return_value=self.master) as generate,
              patch("dubsync.core.subtitles._convert_to_srt",
                    side_effect=lambda _ffmpeg, _source, destination, **_kw:
                    destination.write_bytes(CAPTION))):
            self._prepare(mode="generate", model="small")
            self._prepare(mode="generate", model="small", directory="again")
            self._prepare(mode="generate", model="medium", directory="other-model")
            self._prepare(mode="generate", language="eng", directory="other-lang")
        self.assertEqual(generate.call_count, 3)

    def test_source_change_invalidates_reuse(self):
        with (patch("dubsync.core.subtitles._subdl_candidates",
                    side_effect=lambda *_args, **_kwargs: iter([self.master])) as provider,
              patch("dubsync.core.subtitles._convert_to_srt",
                    side_effect=lambda _ffmpeg, _source, destination, **_kw:
                    destination.write_bytes(CAPTION))):
            self._prepare()
            self.master.write_bytes(b"a different cut with different bytes and length")
            self._prepare(directory="changed-source")
        self.assertEqual(provider.call_count, 2)

    def test_local_sidecar_remains_higher_priority_than_cached_online(self):
        with (patch("dubsync.core.subtitles._subdl_candidates",
                    side_effect=lambda *_args, **_kwargs: iter([self.master])) as provider,
              patch("dubsync.core.subtitles._convert_to_srt",
                    side_effect=lambda _ffmpeg, source, destination, **_kw:
                    destination.write_bytes(OTHER if source.suffix == ".srt" else CAPTION))):
            self._prepare(mode="auto")
            self.master.with_suffix(".de.srt").write_bytes(OTHER)
            selected = self._prepare(mode="auto", directory="local-override")
        self.assertEqual(selected.read_bytes(), OTHER)
        provider.assert_called_once()

    def test_corrupt_and_expired_entries_fall_back_to_provider(self):
        with (patch("dubsync.core.subtitles._subdl_candidates",
                    side_effect=lambda *_args, **_kwargs: iter([self.master])) as provider,
              patch("dubsync.core.subtitles._convert_to_srt",
                    side_effect=lambda _ffmpeg, _source, destination, **_kw:
                    destination.write_bytes(CAPTION))):
            self._prepare()
            entry = next((self.root / "cache").glob("*.srt"))
            entry.write_text("garbled", encoding="utf-8")
            self._prepare(directory="corrupt")
            expired = time.time() - 15 * 24 * 3600
            os.utime(entry, (expired, expired))
            self._prepare(directory="expired")
        self.assertEqual(provider.call_count, 3)

    def test_disable_cache_avoids_all_cache_writes(self):
        with (patch.dict(os.environ, {"DUBSYNC_SUBTITLE_CACHE": "0"}),
              patch("dubsync.core.subtitles._subdl_candidates",
                    side_effect=lambda *_args, **_kwargs: iter([self.master])) as provider,
              patch("dubsync.core.subtitles._convert_to_srt",
                    side_effect=lambda _ffmpeg, _source, destination, **_kw:
                    destination.write_bytes(CAPTION))):
            self._prepare()
            self._prepare(directory="again")
        self.assertEqual(provider.call_count, 2)
        self.assertFalse((self.root / "cache").exists())

    def test_cache_rejects_symlink_and_path_traversal_keys(self):
        external = self.root / "outside.srt"
        external.write_bytes(OTHER)
        cache_dir = self.root / "cache"
        cache_dir.mkdir()
        key = self.cache.key(self.master, 100.0, "deu", "subdl")
        (cache_dir / (key + ".srt")).symlink_to(external)
        target = self.root / "output.srt"
        self.assertFalse(self.cache.load(key, target, duration=100.0, strategy="subdl"))
        self.cache.store("../../outside", external)
        self.assertEqual(external.read_bytes(), OTHER)
        self.assertFalse(target.exists())

    def test_enforces_entry_limit_without_touching_unrelated_files(self):
        cache_dir = self.root / "cache"
        sample = self.root / "valid.srt"
        sample.write_bytes(CAPTION)
        unrelated = cache_dir / "notes.srt"
        cache_dir.mkdir()
        unrelated.write_bytes(OTHER)
        with patch("dubsync.core.subtitle_cache._MAX_ENTRIES", 2):
            for number in range(3):
                sample.write_bytes(CAPTION + str(number).encode("ascii"))
                self.cache.store(f"{number:064x}", sample)
                # Deterministic order despite coarse clock resolution.
                entry = cache_dir / (f"{number:064x}.srt")
                os.utime(entry, (time.time() + number, time.time() + number))
            self.cache._prune()
        entries = list(cache_dir.glob("[0-9]*.srt"))
        self.assertEqual(len(entries), 2)
        self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
