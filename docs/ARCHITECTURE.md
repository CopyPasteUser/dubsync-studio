# Architecture

DubSync Studio separates Qt presentation from media processing:

- `dubsync/gui/main_window.py`: polished Qt workflow, NAS-picker flow, residual plot, synchronized audio preview, per-track Undo/Redo, accessibility settings, ETA and background metadata prewarm.
- `dubsync/core/scanner.py`: episode grouping from filenames and folder layout.
- `dubsync/core/probe.py`: FFprobe metadata model plus bounded concurrent probing.
- `dubsync/core/network.py`: Linux mount-table recognition for SMB/CIFS, NFS, SSHFS and GVFS sources.
- `dubsync/core/fingerprint.py`: cached small-window BLAKE2 signatures for fast cache identity checks without full decode/full-file reads.
- `dubsync/core/alignment.py`: frame sampling, perceptual hashes, monotonic sequence alignment, robust affine time fit, and confidence metrics.
- `dubsync/core/export.py`: FFmpeg filter graph, audio adjustment, metadata, atomic output replacement, and output verification.
- `dubsync/core/batch.py`: single-folder/season/series batch orchestration, cache use, output planning, storage-space preflight, and skip-existing behavior.
- `dubsync/core/cache.py`: bounded SQLite alignment cache with sampled content signatures and analysis-profile keys, legacy JSON migration, private permissions, and a small in-memory LRU.
- `dubsync/core/project.py`: schema-validated `.dubsync` project files and atomic writes; includes source rules, per-track settings/corrections and master overrides.
- `dubsync/core/history.py`: persistent SQLite job history, bounded valid-JSON metadata, deterministic newest-first ordering and interrupted-job recovery.
- `dubsync/core/logging_setup.py`: per-user rotating log and exception hooks.
- `dubsync/cli.py`: headless version and environment diagnostics without importing Qt unless launching the GUI.

## Release invariants

1. Source files are never modified by export.
2. Export paths cannot be the same as inputs.
3. Existing destination files require explicit overwrite authorization.
4. Output is written to a temporary file and atomically promoted only after probing and stream-count/runtime checks pass.
5. Batch export respects saved per-track audio-stream/language/title/corrections and per-episode master overrides.
6. Uncertain affine alignment is surfaced through confidence/warning data; it is not represented as guaranteed synchronization.
7. Long operations run on QThread workers with explicit cancellation; failures are logged and recorded in the job journal.
8. Persistent state uses XDG locations and restrictive file permissions where supported.
9. NAS authentication stays in the desktop file manager; share credentials are never stored in project files.
10. CI includes an offscreen PySide6 main-window smoke test in addition to the media integration tests.

## Current synchronization scope

The automatic mapper estimates a single affine relationship per added track. This supports constant start offsets and approximately uniform drift. It flags a poor fit, low coverage, unusual scale or uncertain anchors; it does not yet automatically reconstruct arbitrary piecewise mappings across different cuts. Such episodes must be reviewed rather than exported as guaranteed correct.
