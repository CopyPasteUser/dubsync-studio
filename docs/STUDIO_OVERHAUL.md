# DubSync Studio · Feature and UX overhaul

Updated 2026-11-09. This is a staged implementation plan for the existing
**native PySide6 workstation**, not a promise that the 3.0 release has been
accepted. Source and export safety are specified separately in
[`ARCHITECTURE.md`](ARCHITECTURE.md); [`UX-CONTRACT.md`](../UX-CONTRACT.md)
owns the implemented interaction contracts.

## Product direction

**A quiet multilingual editing bench:** the episode is the unit of work, the
reference video and alternate-language audio are the material, and every
alignment claim has visible evidence. Preserve the graphite/sea-glass identity
and native keyboard conventions. Avoid decorative dashboards, extra windows
that obscure the active episode, or automatic actions based on unverified audio.

The main journey is **sources → episode → visual/audio evidence → explicit
single export**, with a separate **batch queue → inspection → resume** journey.
The permanent job dock must remain available throughout both. All changes use
existing controls and engine APIs before introducing another UI framework.

## Baseline inspection (Qt offscreen, 1440×900)

| Area | Observed issue | Design direction | Safety boundary |
| --- | --- | --- | --- |
| Studio | Large simultaneous empty library, evidence monitor and track inspector compete for attention before the first scan | Clarify one next action and preserve all three panes once a group is selected | No implicit scanning or source edits |
| Batch | Two tall blank states pushed destination, subtitle controls and performance settings below the visible area | Compact empty queue with a direct path back to Studio; show output settings earlier | Empty saved queue must never auto-start |
| Batch summary | “Episodes in batch” included groups with two files even when no persisted job existed | Distinguish *detected multilingual episodes* from *queued jobs* | Never represent a detected pair as approved for export |
| Evidence | Timeline, frames, waveforms, A/B playback and manual segment editor live in adjacent but distinct controls | Tighten reference cursor/switch behavior, labels and failure recovery | Never turn preview correspondence into export proof |
| Diagnostics | Activity is complete but lengthy; explanations require switching views | Surface concise causes near the episode, keep full detail in Activity | No green success for a failed or cancelled job |
| Settings | Long subtitle/performance forms compete with the job list | Separate primary output choices from advanced tuning, keep both reachable on short screens | No silent changes to processing presets or subtitles |

These are code and offscreen-viewport observations. They are not evidence of
real GPU usage, smooth audio on physical devices or reliable NAS recovery.

## Incremental delivery

### Phase 1 · Batch information architecture — implemented; physical QA pending

- Collapse the empty queue to a single action row with **Zum Studio** instead
  of reserving a large blank table and disabled job actions.
- Keep queue load errors visible, including when no rows could be loaded.
- Bound the populated queue's table height; its own viewport scrolls.
- Remove the empty-state stretch, so output destination and subtitle controls
  appear earlier without changing their behavior.
- Use accurate copy: multilingual groups are not persisted queued jobs.
- Tests: empty/populated/error transitions; keyboard-capable native button;
  narrow scroll and 1440×900 visibility checks.

### Phase 2 · Episode-first Studio — onboarding and focused comparison implemented; remaining work open

- Distinguish no sources, sources ready to scan, and active filters without
  results; expose one explicit next action in the episode list. This is
  implemented and covered by native Qt state-transition tests.
- The 980×680 Studio work surface scrolls independently of the persistent job
  dock, while 1440×900 remains unscrolled; a compact first action stays visible.
- **Vergleich vergrößern** (`Alt+0`) temporarily hides the episode list and track
  inspector so the evidence monitor can use the full work area. Switching to
  Batch/Activity and back retains focus mode; leaving it restores the original
  splitter sizes and all in-memory edits. Full episode and audio-track labels
  remain accessible via native tooltips; source paths are selectable.
- Keep selection, evidence cursor, track configuration, and unsaved project
  state when navigating between pages. Do not add automatic background work.
- Continue refining the normal 980×680 three-pane hierarchy, including
  language tags, manually edited segments and large track counts.
- Show what is *verified*, *provisional*, *unmapped* and *blocked* in text as
  well as color, tied to the engine's current proof data.

### Phase 3 · Synchronized review feature consolidation

- Place frame comparison, segment editing and optional continuous A/B audio
  around the same reference cursor and selected source.
- Keep exact cancellation, source/segment boundary handling and private
  bounded preview storage. Never interpolate absent dialogue.
- Investigate true gapless audio only with a validated device/output backend;
  the current Qt WAV handoff can have a short pause.
- Exercise stale preview responses, rapid track switches, unavailable media,
  multiple cuts and explicit undo/reset.

### Phase 4 · Export and batch decision surfaces

- Show selected output, subtitles and resource policy before starting a job,
  without hiding the existing independent single-export preflight.
- Tighten blocked/caution/ready explanation, explicit overwrite consent,
  batch recovery and NAS disconnection messaging.
- Preserve source immutability, output verification and atomic promotion.

### Phase 5 · Cohesive visual and accessibility pass

- Normalize type hierarchy, spacing, focus, disabled/busy/empty/error states
  through `gui/theme.py`, not screen-local conflicting styles.
- Inspect Studio, Batch and Activity at 980×680, 1280×720 and 1440×900,
  high contrast, keyboard-only, long German copy and scaling.
- Compare on actual XFCE/GNOME/KDE; validate audio, FFmpeg, Intel/AMD/NVIDIA
  hardware and SMB/NFS failures before claiming the stable 3.0 milestone.

## Acceptance and rollout rules

1. Each phase must retain existing editing/export functionality, deterministic
   tests and a clean CI run. A phase is merged independently only when green.
2. New UI may **summarize** engine evidence; only the existing exporter decides
   whether a file is safe to produce. No green badge from cached heuristics.
3. No source file deletion, implicit queue start, unconfirmed overwrite,
   hidden permission changes or automatic background audio generation.
4. Report UI screenshots and hardware test gaps honestly. A successful
   headless Qt screenshot is layout evidence, not physical QA.
5. Use actual measured startup, RAM, seek and decoder timings for subsequent
   performance changes; preserve the CPU/NumPy fallback.
