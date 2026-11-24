# 4.0 Integration — offline acceptance and release gates

**Status: NO GO for release.** The five draft sprint branches (#32–#36) have
been integrated into one reviewable branch with their histories preserved.
The first integration commit is `51b2a6c033e20e3d3aa652d2acb0b76558e2268d`.
The original 36 changed files were matched against the sprint blob hashes.
Do not merge the individual sprint PRs independently of this integration.
The 3.0 beta version metadata stays unchanged until actual release acceptance.

## Implemented on the integration branch

- S01: isolated baseline, cold/warm sample-cache and exact evidence digests;
  empty GUI navigation and synthetic preprobed episode-render measurements.
- S02: bounded FFmpeg frame batching; matching decoder qualification thread
  budget; validated per-episode master fingerprint reuse, with fresh
  source identity reads and fresh alignment decisions.
- S03: compact atomic batch journals, exact zero-delay audio filter fast path,
  bounded current-episode metadata prefetch (no parallel NAS reads), and a
  real exporter benchmark harness with PCM/video/timestamp evidence.
- S04: extracted Qt task, plot, source and timeline components; indexed
  visible anchors; explicit button-group cleanup and deferred hidden queue
  data refresh.
- S05: light/dark and high contrast palettes; library and inspector surfaces,
  toolbar, independent inspector collapse and display-only track filtering.
  Existing export eligibility uses every selected track, not visible rows.
- S06: revision-pinned offline acceptance, full pytest inventory, Rust and
  portable wheel checks, baseline/candidate alignment/export comparisons,
  GUI metrics and screenshots in both appearances.

## Verified in the restricted integration workspace

`python -m pytest -q tests -k 'not cold_warm_real_pipeline'` on the
reconstructed focused subset: **45 passed, 1 deselected, 95 subtests passed**.
This is NOT a full repository checkout or suite. The production
`ffmpeg_sampling.py` and `frame_cache.py` files were reconstructed with
matching Git blob IDs. The sampler's required `_cpu_count` function was
reconstructed from the actual source; the rest of performance.py was not
loaded. The focused tests cover real FFmpeg compressed media, raw frame
parity, cancellation, truncated pipes, SQLite memory/persistent cache
admission, metadata scheduling contracts, palette and timeline projection,
and offline acceptance subprocess/worktree failure handling.

The 24 additional synthetic AAC comparisons cover 44.1/48 kHz,
mono/stereo/5.1/7.1 and three tempo factors. Decoded PCM hashes matched;
a separate real FFprobe run also matched packet timing digests in all 24.
This verifies the filter change in these fixtures, not the whole production
export flow or a real season. See S02–S04 reports for prior microbenchmarks.
The isolated master-cache benchmark (1,800 pairs, 30 repetitions) measured
median cache-get 3.025 ms via SQLite vs 0.048 ms in RAM, excluding file
identity reads and matching. No overall application speedup is claimed.

The actual acceptance runner's local preflight returned nonzero with:
missing Cargo, PySide6, pytest-qt and wheel. Direct GitHub Git checkout is
blocked by DNS here. No physical GPU, NAS, compositor, audio or full
repository integration verification is available in this environment.

## One-command acceptance on a prepared Linux machine

Use a clean checkout of this branch and an existing interpreter with the
project's test dependencies installed. FFmpeg, FFprobe, Cargo and xvfb-run
must be present; the Rust toolchain/dependencies and wheel build dependencies
must already be available offline. Then run:

```bash
python scripts/acceptance_4_0.py
```

The script creates a private evidence directory in /tmp and two detached
worktrees at the resolved baseline and candidate SHAs. It builds native
libraries with CARGO_NET_OFFLINE, runs native tests, source checks, full
pytest (including pytest-style Qt tests that unittest discovery misses),
builds and verifies the wheel without pip index access, compares the same
benchmark harness between revisions, and captures synthetic Qt screenshots.
It removes the private worktrees, not the user's checkout. The JSON manifest
records each step, stdout/stderr, failed steps, all test skips and raw
measurements. It does not install dependencies, merge, push, tag or deploy.
A successful command intentionally reports local checks passed / review
required; release_authorized stays false.

Optional output directory: `--output /absolute/new/private-directory`
(outside the repository, must not exist). Optional `--repeat 2..5`.
Use the exact same interpreter, host and resource configuration for both
revisions. All timings are measurements, not a machine-independent pass
threshold. Raw logs may contain local filesystem paths; review before sharing.

## Required gates before release or merge

- [x] All five original sprint changes collected into one branch.
- [x] Focused source-identical helper/sampler/cache tests and synthetic
  audio parity; cross-revision acceptance harness implemented.
- [ ] Full checkout: all Python/Qt/Rust regression suites green, each skip
  explained; wheel verified, no unresolved integration regression.
- [ ] Exact alignment, copied video, decoded audio and packet timing parity
  across baseline and candidate; inspect performance medians and RAM.
- [ ] Actual Qt screenshot review at 980x680, 1440x900 and scaling, light/dark,
  inspector/focus, keyboard and recovery/error states. Verify complete
  track editor access and no UI regressions on a physical desktop.
- [ ] Real owned-media CPU/GPU and SMB/NFS tests, long HEVC, offsets/drift,
  independent cuts, wrong episodes, multi-track audio, optional subtitles,
  cancellation/resume and output verification.
- [ ] Address any failed gate on this branch, rerun the complete acceptance
  on the new exact SHA, review evidence; only then merge and prepare release.

Optional optimization hypotheses remain open rather than being misreported
as complete: native pHash/SIMD, GPU-native scaling, lossless alternate-audio
stream copy, and parallel episode export. They must be justified by actual
end-to-end profiling and preserve the existing evidence, AAC/default export,
timestamp, storage and recovery contracts. A different design direction
requires updating the roadmap explicitly, not silently counting an omitted
feature as finished. Broader MainWindow decomposition and additional visual
fidelity/polish remain subject to the actual UI acceptance.

All integration commits retain [skip ci]; no Actions run requested.


## Follow-up export integration (2026-11-24)

The production exporter, models, FFprobe parser and language module were
reconstructed byte-for-byte in the restricted local workspace. The initial
no-subtitle comparison used an explicitly disabled subtitle module; after
the lazy options/backend split, all six production exporter tests ran
without that stand-in. The entire repository and subtitle-enabled flow
remain unverified here.

A real 30-second, three-dub export with FFmpeg 7.1.5 reproduced an existing
positive-delay timestamp discontinuity. Two of four original-main exports
had 1,387 packets in the +500 ms track and a maximum gap of 0.458 s. Two
other baseline runs had 1,409 packets and a 0.001 s maximum gap. After
normalizing the sample clock immediately after the positive adelay, all
three candidate runs had 1,409 packets and a 0.001 s maximum gap. Copied
video, decoded PCM, per-stream packet timing and metadata exactly matched
both continuous baseline references; every input SHA-256 was unchanged.
The candidate median was 1.068 s, baseline median 1.253 s, but those are
small synthetic samples including defective baseline outputs, not a
production throughput claim. Piecewise filter timing and real source
timestamp discontinuities still require full acceptance.

The benchmark now records every original result, compares packet sequences
per stream rather than arbitrary interleaving, and requires repeatable
continuous candidate output. The acceptance comparator requires at least
one continuous original reference and exact equality to all continuous
original references. It fails explicitly if no valid reference exists.

Additional changes:
- Source alignment must match the selected source as well as master, even
  for manual low-confidence exports.
- Bounded progress-reader queue; cancellation is polled during silent or
  partial progress output and after stdout EOF. Verified outputs are
  promoted atomically without clobbering concurrent writers unless overwrite
  is explicitly authorized. Linux renameat2 / POSIX hardlink fallback;
  unsupported target filesystems fail closed. Physical SMB/NFS testing
  remains required.
- Cancel before publication preserves existing output; notification errors
  after the commit point do not misreport a committed output as failed.
- SubtitleOptions/SubtitleError are separated from acquisition code.
  Existing subtitle imports remain compatible through re-exports; the
  normal export path does not load acquisition backends.

Final focused validation on the reconstructed subset, split into four runs
because the combined process exceeded this environment's time limit:
33 + 23 + 5 + 1 = **62 passed**, **97 subtests passed**. The full alignment
pipeline benchmark was deselected; this is not a complete repository run.
Six production exporter tests include real three-dub exports, source
binding, late cancellation, concurrent output, import isolation and
post-commit callback failure. No subtitle-enabled, Qt, Rust or target
hardware acceptance is claimed.

The direct GitHub/PyPI DNS check still fails, no Qt/Cargo packages are
available in local caches, and no GitHub Actions were intentionally started.
The merge/release status remains NO GO until the integrated checkout and
physical gates are satisfied.


## Follow-up: piecewise export and real local subtitle acceptance

A 10-second manual section export reproduced a 0.958 s packet gap in the
positive-delay single-section path (426 packets). Applying the same sample
clock correction to both single- and multi-section filters produced 471
packets and a maximum gap of 0.001 s in both synthetic cases. The actual
exporter integration test also checks decoded PCM in deliberately unmatched
and matched regions. All seven exporter integration tests passed, including
the two new section subcases.

The complete subtitle acquisition module, cache, fingerprint module, original
export validation tests, subtitle cache tests and existing subtitle acceptance
script/test were reconstructed with exact Git blob IDs. No stand-in module
was used. The original export validation suite passed (8 tests / 11 subtests),
the subtitle cache suite passed (9 tests), and the subtitle acceptance test
passed (1 test). A separate invocation of acceptance_subtitles.py --json
confirmed bitstream-identical master video, unchanged source files, subtitle
counts source=1/off=1/on=2, and the additional track not set as default.
Provider and ASR cache tests use mocks; no actual online provider or
speech-recognition quality claim is made.

The offline runner now removes inherited DUBSYNC_* overrides, explicitly
enables the native matcher, disables optional automatic subtitle sync in the
default isolated environment, and records the dedicated subtitle acceptance
JSON. All seven acceptance harness contract tests passed, including a new
environment-isolation regression. These are targeted successful runs, not a
full repository test suite.

The new results extend the earlier focused count from 62 to 82 tests and from
97 to 110 subtests (one new section test/two subcases, 18 original subtitle and
export validation tests/11 subcases, one isolation test). The actual full
alignment, Qt, Rust, physical desktop/GPU/NAS and full-checkout integration
gates remain open. GitHub archive retrieval and direct dependency resolution
are still unavailable in this workspace. Release remains NO GO.
