# DubSync Studio 4.0 — performance and macOS-inspired native UX

Status: implementation integrated in draft PR #37; release acceptance remains open. See [current status](DEVELOPMENT_STATUS.md). Keep the Qt stack,
Python orchestration, FFmpeg and optional Rust hot paths. Preserve all existing
features and independent evidence/export checks. No automatic reuse of alignment
decisions, weakened episode identity, source mutation or unattended overwrite.

| Sprint | Deliverable | Exit criteria |
| --- | --- | --- |
| S01 Performance Foundation | Versioned alignment/cache and GUI baseline, real-media test matrix | Reproducible reports, evidence parity, isolated state, local suite; record real target measurements |
| S02 Media Engine | Measured decoder calibration/cache and native fingerprint hot paths | CPU/GPU parity, invalidation/fallback, no quality regression; compare S01 reports |
| S03 Batch & Export | Bounded CPU/RAM/storage scheduler, optional exact audio stream copy | NAS and local throughput, cancellation/resume, verified staged output, codec/timestamp checks |
| S04 Native UI Architecture | Extract controllers and views, reusable models, lazy secondary pages | Existing Qt contracts, state persistence, responsive navigation, lower startup/PSS |
| S05 Studio Experience | Finder-like library, media-first workspace, contextual inspector, toolbar, light/dark | All existing features reachable; keyboard, focus, 980x680 and scaling QA |
| S06 Release Engineering | Regression matrix, installer, migration, real hardware/NAS/audio QA | Release checklist, no unresolved critical safety regressions, published measured results |

## S01: collect a baseline

From a source checkout with the existing environment installed:

```bash
./.venv/bin/python scripts/performance_baseline.py --repeat 3 > baseline-same.json
./.venv/bin/python scripts/performance_baseline.py --scenario shifted --repeat 3 > baseline-shifted.json
./.venv/bin/python scripts/performance_baseline.py --suite gui --repeat 3 > baseline-gui.json
./.venv/bin/python -m pytest -q tests/test_performance_baseline.py
```

Use the same recipe, Python/FFmpeg/native build, hardware, storage and power mode
before and after each change. Record the exact revision and whether the worktree
is clean. On representative owned files, pass SOURCE MASTER explicitly. NAS
measurements are opt-in and can be expensive; do not run concurrently with other
NAS tests. Use --decoder auto for a separate GPU qualification run and --workers
2 for a separate concurrency run. The default is one FFmpeg CPU decoder.
Retain raw samples; compare medians and ranges, not a single best run.

The alignment runner uses separate private XDG directories per repetition and
measures an empty frame cache followed by a verified warm cache hit on both
inputs. This is **not** a cold operating-system disk cache. It checks complete
alignment evidence equality and fails if cache status is unexpected. The report
does not include media paths, raw diagnostic payloads or frame/audio data.
CPU seconds include the benchmark process and waited-for children; PSS is
a parent-only snapshot, not process-tree peak RAM. Pipeline phases are the
existing diagnostic wall times (matching includes residual overhead).
GUI measurement is an empty-session Qt show/event-drain proxy, not true
first-paint/compositor latency; navigation is empty workspace switching, not
populated episode selection. Offscreen results do not establish physical QA.
Set QT_QPA_PLATFORM=xcb or wayland explicitly on a real desktop if available.

## Target matrix and remaining measurements

Record actual hardware and FFmpeg version alongside reports. Use the ThinkPad
class (constrained CPU/RAM), a stronger SSD workstation, Intel/AMD/NVIDIA
decoders where available, and explicit SMB/NFS paths. Include identical
editions, offsets, drift, independent cuts, wrong episodes/shared intros,
long HEVC, multiple audio tracks, and missing media. Run existing real export
and resume tests separately. S01 does not measure complete export throughput,
populated episode-switch latency, actual first paint, process-tree peak PSS or
physical playback; these are pending validation/measurement, not passed gates.

Initial engineering targets (subject to measured baseline): startup under
1.5 s, cached episode switch under 100 ms, idle CPU under 0.5% of one core,
idle PSS under 150 MiB, 20% improvement on representative analysis workloads
without weakened evidence, and no functional regressions. Do not impose
machine-specific timing thresholds in portable unit tests.

## UI direction

Keep Linux window-manager semantics and native Qt accessibility. Use a library
sidebar, compact toolbar, central evidence workspace, collapsible inspector and
persistent job status. Put hardware, cache and subtitle provider configuration
in preferences or export-specific controls. Preserve optional subtitles,
manual segments, A/B listening, project state, keyboard commands and explicit
preflight. Use semantic light/dark palette tokens instead of global blur or
decorative animation. Keep current 3.0 beta gates until physical validation.

## Unified integration

All five initial sprint PRs are consolidated in `integration/4.0-studio`.
Use [integration acceptance and remaining gates](INTEGRATION_4_0.md) for the
current evidence, offline command and no-go decisions; historical sprint
reports describe earlier partial measurements, not final release approval.
