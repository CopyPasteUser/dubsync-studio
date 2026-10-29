# Architecture

DubSync Studio separates Qt presentation from media processing:

- `dubsync/gui/main_window.py`: polished Qt workflow, NAS-picker flow, residual plot, synchronized audio preview, per-track Undo/Redo, accessibility settings, ETA and background metadata prewarm.
- `dubsync/gui/workbench.py`: read-only 2.0 review center, measured-anchor timeline and explicit, cancellable FFmpeg still-frame reads. It never decides export safety.
- `dubsync/gui/queue_room.py`: in-page 2.0 job table and explicit actions; persisted transitions and resume proof remain in `core/job_queue.py`.
- `dubsync/core/scanner.py`: episode grouping from filenames and folder layout.
- `dubsync/core/probe.py`: FFprobe metadata model plus bounded concurrent probing.
- `dubsync/core/network.py`: Linux mount-table recognition for SMB/CIFS, NFS, SSHFS and GVFS sources.
- `dubsync/core/fingerprint.py`: cached small-window BLAKE2 signatures for fast cache identity checks without full decode/full-file reads.
- `dubsync/core/alignment.py`: frame sampling, perceptual hashes, monotonic sequence alignment, robust affine time fit, and confidence metrics.
- `dubsync/core/piecewise.py`: independently validated monotone cut segments when one global affine map fails; unverified regions are never extrapolated as matching scenes.
- `dubsync/core/ffmpeg_sampling.py`: bounded FFmpeg 32×32 grayscale frame extraction, optional hardware decode, per-file CPU/GPU speed and fingerprint comparison; always keeps a CPU fallback.
- `dubsync/core/frame_cache.py`: bounded SQLite pHash/time sample cache, protected by fresh, distributed file-content/metadata checks; has no alignment/export decision state.
- `dubsync/core/export.py`: FFmpeg filter graph, audio adjustment, metadata, atomic output replacement, and output verification.
- `dubsync/core/batch.py`: single-folder/season/series batch orchestration, output planning, storage-space preflight, skip-existing behavior and fresh episode/identity validation. The result cache is disabled during batch export.
- `dubsync/core/batch_resume.py`: optional fail-closed, recipe-bound batch journal verification. Stores privacy-conscious source/output signatures, not old alignment decisions; checks output stream presence before skipping.
- `dubsync/core/cache.py`: legacy bounded SQLite alignment-result cache with sampled content signatures and analysis-profile keys. Retained for compatibility, but disabled for export decisions.
- `dubsync/core/project.py`: schema-validated `.dubsync` project files and atomic writes; includes source rules, per-track settings/corrections and master overrides.
- `dubsync/core/history.py`: persistent SQLite job history, bounded valid-JSON metadata, deterministic newest-first ordering and interrupted-job recovery.
- `dubsync/core/job_queue.py`: persistent per-batch job specifications and state, explicit restart/retry actions, journal ownership and conservative CPU/RAM concurrency budgets. Unlike the read-only job history, a queue entry can be executed again with full media revalidation.
- `dubsync/core/logging_setup.py`: per-user rotating log and exception hooks.
- `dubsync/cli.py`: headless version and environment diagnostics without importing Qt unless launching the GUI.

## Release invariants

1. Source files are never modified by export.
2. Export paths cannot be the same as inputs.
3. Existing destination files require explicit overwrite authorization.
4. Output is written to a temporary file and atomically promoted only after probing verifies preserved video properties, original audio streams, subtitles, attachments, new AAC streams and runtime.
5. Batch export respects saved per-track audio-stream/language/title/corrections and per-episode master overrides.
6. Uncertain affine alignment is surfaced through confidence/warning data; it is not represented as guaranteed synchronization. Automatic export also requires trusted visual anchors across the shared timeline: no unverified interior gap above 20 % and no unverified leading/trailing edge above 15 %. Extra material outside the actual overlap does not count toward the edge limit.
7. Long operations run on QThread workers with explicit cancellation; failures are logged and recorded in the job journal.
8. Persistent state uses XDG locations and restrictive file permissions where supported.
9. NAS authentication stays in the desktop file manager; share credentials are never stored in project files.
10. CI includes an offscreen PySide6 main-window smoke test in addition to the media integration tests.
11. Cached frame fingerprints may skip repeated decoding, but every analysis recomputes visual correspondence, quality gates, episode checks and authorization. The sampled file signature is not a full-file cryptographic identity; disabling the frame cache remains supported.
12. Explicit batch resumption requires a matching journal recipe, unchanged source/output sampled fingerprints, identical resolved target path and fresh output media probe. Unverified files follow the normal existing-file policy; a journal never authorizes overwriting.
13. 1.6 queue snapshots are stored privately in user-owned SQLite files; reopening the application never starts old jobs. Recovery uses a fresh journal with overwrite disabled, manual journal compatibility and fresh source checks. The GUI re-confirms queued first-run overwrite authorization after restart.
14. 2.0 timeline and still-frame inspection cannot grant export permission. Unpaired scenes stay unpaired, manual intervals are marked provisional, previews have a bounded read and timeout and no view automatically reads source frames on selection.

## Current synchronization scope

The default automatic mapper estimates one affine relationship per added track; it supports constant offsets and approximately uniform drift. If the monotone frame matcher also demonstrates clean offset discontinuities, a conservative piecewise mode can keep two to five independently proven segments. Each segment has its own affine map; unmatched master intervals are silent in the extra audio track and unmatched source intervals are skipped. Safety requires independent visual diversity, multiple trustworthy anchors per segment, monotonic source/master order, and bounded gaps/overall coverage. A manually edited offset/scale deliberately returns to the single-affine model and is no longer automatically exportable. The manual Qt segment editor can specify 1–12 chronologically monotone sections, mark them inactive and split them; this is a *structural* validation only, never independent visual proof. `manual_segments` forces an explicit single-track export confirmation, and the batch scheduler skips manually edited sources. Project files persist only manual section coordinates/active flags and both input file signatures, not assumed visual confidence; the original automatic alignment must be recomputed on reopen. Arbitrary edits, complex reordered scenes and large unverified regions are not claimed to be automatically repaired.

The monotone frame matcher uses NumPy-vectorized Hamming distances and a prefix-minimum dynamic-programming recurrence. Its output is checked against the original scalar recurrence in regression tests. The speedup applies to frame matching only: decoding, filesystem throughput and audio processing have separate bottlenecks.

When the initial correspondence is unsafe, the alternative affine feature
matcher considers the two strongest visual matches per source frame. It uses
one bounded NumPy Hamming vector per frame and evaluates candidate time models
with vectorized residual and prefix-maximum selection; repeated identical scenes
retain the original stable target-order tie break. A separate scalar reference
in tests verifies equivalent selections for different intros, edits and drift.
Diagnostics expose `candidate_scan_seconds` and `hypothesis_scoring_seconds`
for this fallback. None of these optimizations bypass export-confidence gates.

## Runtime throughput and hardware selection

`core/performance.py` owns `sampling_worker_policy`. Each pair may request two
decode workers, but actual execution requires sufficient available CPU/RAM,
both paths outside recognized NAS mounts, and no competing automatic
GPU-performance benchmark. The policy is evaluated *per pair*, not merely at
settings time. Concurrent workers keep independent in-memory fingerprint caches
and short-lived SQLite connections; parallel FFmpeg runs divide the visible
logical-thread budget. If either worker fails, the sibling receives a local
cancellation signal and the alignment fails safely.

Linux VAAPI can select a sysfs-verified AMD (`0x1002`) or Intel (`0x8086`)
DRM render node. Node selection is not proof of real hardware decoding; a
failed FFmpeg request falls back to OpenCV/CPU. Windows uses D3D11VA without
a vendor-specific adapter selector. Matching and safety validation always
run on the CPU. The `pipeline_profile` diagnostic contains measured phase
durations and effective worker count without disclosing media bytes or full
paths. `--profile-alignment` forces fresh decoding, performs all normal safety
checks, and never exports media.

For local videos with at least 45 seconds of content, automatic decode can
benchmark up to four hardware candidates (per accessible DRM render node on
Linux), each against a *single* CPU-FFmpeg sample baseline. Candidates are
discarded if speed gain, number of frames, fingerprint agreement or median
Hamming distance fail existing thresholds; the fastest remaining candidate
is selected. On recognized network mounts the multiple decode benchmark is
skipped. The known working OpenCV decoder and all alignment/export safety
validation remain the fallback, even when FFmpeg reports hardware interfaces.
