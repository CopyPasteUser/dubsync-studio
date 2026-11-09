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
DubSync can optionally repair a global subtitle offset or common framerate drift,
but cannot guarantee a match across deleted, inserted or reordered scenes.
Online results and machine transcriptions should be reviewed for timing and
correctness before permanent archiving. Batch jobs
remember their subtitle recipe; one manually picked file or pasted video URL
cannot accidentally be applied to an entire season.

## Optional automatic timing correction

Install the lightweight optional synchronizer for downloaded SubDL, Subliminal
or manually linked video captions:

```bash
./.venv/bin/python -m pip install -e '.[subtitle-sync]'
```

When available, [ffsubsync](https://github.com/smacke/ffsubsync) compares the
picture master's speech-activity pattern with the candidate's SRT timestamps.
For long episodes DubSync uses six short segments spread across the runtime,
with at most two parallel workers; no full transcription or video re-encoding
is required. The correction is limited to a 25-second search offset,
20-second quality threshold and 3% maximum framerate deviation, and runs with
ffsubsync's built-in low-confidence safeguard. DubSync validates the corrected
SRT again and preserves the original candidate if correction fails. No media
is uploaded. Existing local/embedded subtitles and freshly generated Whisper
captions are not modified by this optional stage.

Set `DUBSYNC_SUBTITLE_AUTO_SYNC=0` before launching DubSync to disable it.
An absent synchronizer never blocks subtitle search or export. Clearly wrong
SubDL episode filenames, invalid/unsorted timecodes and obvious short excerpts
are rejected before embedding. This does **not** prove every online subtitle
matches the exact cut; inspect the result in a player before archiving.

## Reproducible export QA

Run `./.venv/bin/python scripts/acceptance_subtitles.py --json` from the
repository to generate an isolated synthetic episode with two audio tracks and
existing subtitles. The harness exports both with and without another optional
SRT track, verifies its language and non-default disposition, hashes the
**copied video bitstream** across all three MKVs, checks source files were not
modified and reports measured mux times. It uses private temporary files and
never accesses the user's NAS or online providers. A passing synthetic test
does not establish that a live SubDL release, Whisper transcript or ffsubsync
timing adjustment matches the real episode; those require representative
media/provider QA before 1.0 release acceptance.

## Persistent subtitle cache

**Fast repeats / resume:** Successfully validated **online and generated** SRT
tracks are reused from a private persistent cache when the same picture-master
file, cut, language, provider and (for ASR) Whisper model are requested again.
The source file's size, modification time, sampled content fingerprint and
duration form the identity; it is not just a filename lookup. This avoids
repeat provider requests and expensive transcription when exporting again or
resuming a batch. Local sidecars, ZIPs and embedded tracks are always checked
**before** the expensive cache so newly added or modified captions take
precedence. Online results expire after 14 days; generated results after 60
days. Cache storage is capped at 256 tracks / 128 MiB, with a 5 MiB per-track
limit and private filesystem permissions. The usual location is
`~/.cache/dubsync-studio/subtitles/` (or `$XDG_CACHE_HOME/dubsync-studio/subtitles/`).
Set `DUBSYNC_SUBTITLE_CACHE=0` to disable it, or
`DUBSYNC_SUBTITLE_CACHE_DIR=/private/path` to choose a different cache folder.
Removing that dedicated folder while DubSync is closed clears saved captions;
it does not remove any original media, exported MKVs or user-supplied files.
