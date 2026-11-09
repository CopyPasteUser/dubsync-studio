# DubSync Studio — optional subtitles

[Back to README](../README.md) · [Installation](INSTALL_LINUX.md)

Subtitle tracks are optional and switchable in the player. They are **not burned into the picture** and do not require video re-encoding.

## Optional, switchable subtitles (no video re-encoding)

In **Ausgabe-Ziel → Optionale Untertitelspur**, select a source and language:

- **Off** (default) keeps the current output format unchanged.
- **Automatic** tries lightweight local checks first (matching `.de.srt` /
  `.en.srt` / `.srt`, `.ass`, `.vtt` or ZIP alongside the picture master;
  embedded text subtitles), then searches **SubDL** if its key is configured,
  then **Subliminal** with its multiple providers, and **only then** starts
  Whisper transcription. Missing local files cost only quick path checks;
  no media is decoded or uploaded during those checks.
  Missing providers or bad candidates do not block the next source. You can
  explicitly select any source, including a personal subtitle file/ZIP.
- **Find only (without AI)** uses the same local and online search order, but
  never starts Whisper. This is the fast option when an existing, human-made
  subtitle is preferred to a transcription, even if no provider has a match.
- **Online** searches two independent providers:
  [Subliminal](https://github.com/Diaoul/subliminal) and
  [SubDL](https://subdl.com/at/api-doc). Install Subliminal in the DubSync
  environment with `./.venv/bin/python -m pip install -e '.[subtitle-search]'`.
  This enables its keyless providers (including Podnapisi and TVSubtitles),
  while other providers may require separate accounts. Subliminal uses up to
  four search workers and a minimum matching score to reduce wrong releases.
  For SubDL, create an API key in your SubDL
  account and set `DUBSYNC_SUBDL_API_KEY=...` in the environment **before**
  launching DubSync. Keys are not saved in DubSync settings or logs. Online
  searching requires a selected language. Both providers may lack a match or
  return subtitles from another cut; check the result before archiving. SubDL
  tests up to **three candidate releases** in best filename-match order and
  skips corrupt, unreadable and grossly mistimed captions before considering
  Whisper. Search results and downloaded text are bounded in size.
- **Video URL** (single export only) obtains existing human-authored captions
  from a supported video site through optional
  [yt-dlp](https://github.com/yt-dlp/yt-dlp). Install with
  `./.venv/bin/python -m pip install yt-dlp`, select a language, choose
  **Untertitel von Video-URL**, and paste the HTTPS URL. Only subtitles are
  downloaded; DubSync does not download the linked video or automatically
  generate captions through yt-dlp. The captions must correspond to the
  picture master's timeline.
- **ZIP archives** can be selected directly, or automatically discovered as
  `Movie.zip` / `Movie.de.zip` alongside the picture master. DubSync reads the
  archive without unpacking arbitrary paths and picks a matching text caption;
  it rejects oversized archives and obviously different episodes.
- **Generate** transcribes the **picture master's audio** locally, without
  uploading the media. For **highest CPU throughput**, install optional
  [whisper-ctranslate2](https://github.com/Softcatala/whisper-ctranslate2) with
  `./.venv/bin/python -m pip install -e '.[subtitle-fast]'`. DubSync selects
  int8 quantization and an automatic VAD/batch profile based on available
  host/container RAM. Batching is capped at 8; small-memory hosts use smaller
  batches. The first model use may require a one-time model download.
  Alternatively, install
  [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (`whisper-cli`) and set
  `DUBSYNC_WHISPER_MODEL=/path/to/ggml-small.bin` before starting DubSync.
  Alternatively install `openai-whisper` in the venv for the `whisper` CLI.
  `small` is the default trade-off; `medium` is slower and may be more accurate.
  Backends are tried in order: batched CTranslate2, local whisper.cpp, then
  Python OpenAI Whisper; fallback reuses the already-extracted WAV.
  This is **speech transcription, not automatic translation** into another
  language. On episodes with multiple languages, select the appropriate
  picture-master audio language. Whisper errors and provider failures are
  reported rather than silently skipping requested subtitles. In automatic
  mode, transcription is deliberately the **last resort**.

The resulting MKV has an **additional, non-default SRT text track**: enable it
in Jellyfin, VLC or another player when needed. Existing subtitle streams remain
intact, and the original video is copied bit-for-bit without a new video encode.
DubSync intentionally does not retime subtitles from a different edition to
match unpaired/edited scenes. Online results and machine transcriptions should
be reviewed for timing and correctness before permanent archiving. Batch jobs
remember their subtitle recipe; one manually picked file or pasted video URL
cannot accidentally be applied to an entire season.
